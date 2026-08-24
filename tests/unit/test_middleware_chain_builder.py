"""Unit tests for MiddlewareChainBuilder — pins ADR-0009 ordering at builder level."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from notebooklm._middleware.auth_refresh import AuthRefreshMiddleware
from notebooklm._middleware.context import RPC_CONTEXT_RPC_QUEUE_WAIT_SECONDS
from notebooklm._middleware.drain import DrainMiddleware
from notebooklm._middleware.metrics import MetricsMiddleware
from notebooklm._middleware.retry import RetryMiddleware
from notebooklm._middleware.semaphore import SemaphoreMiddleware
from notebooklm._middleware.tracing import TracingMiddleware
from notebooklm._rpc_semaphore import RpcSemaphore
from tests._fixtures.chain import make_request


def _builder_kwargs():
    """Return kwargs sufficient to instantiate MiddlewareChainBuilder."""

    async def _snapshot():
        return MagicMock()

    return {
        "drain_tracker": MagicMock(),
        "metrics": MagicMock(),
        "rpc_semaphore": RpcSemaphore(None),
        "rate_limit_max_retries_provider": lambda: 3,
        "server_error_max_retries_provider": lambda: 3,
        "retry_timeout_provider": lambda: 30.0,
        "refresh_retry_delay_provider": lambda: 0.0,
        "refresh_callable": lambda: None,
        "auth_snapshot_provider": _snapshot,
        "is_auth_error": lambda exc: False,
        "refresh_callback_enabled_provider": lambda: True,
    }


def test_builder_returns_adr_009_order():
    from notebooklm._middleware.chain import MiddlewareChainBuilder

    chain = MiddlewareChainBuilder(**_builder_kwargs()).build()

    assert len(chain) == 6
    assert isinstance(chain[0], DrainMiddleware)
    assert isinstance(chain[1], MetricsMiddleware)
    assert isinstance(chain[2], SemaphoreMiddleware)
    assert isinstance(chain[3], RetryMiddleware)
    assert isinstance(chain[4], AuthRefreshMiddleware)
    assert isinstance(chain[5], TracingMiddleware)


@pytest.mark.asyncio
async def test_semaphore_records_queue_wait_when_inner_call_fails() -> None:
    middleware = SemaphoreMiddleware(RpcSemaphore(1))
    request = make_request()
    inner = AsyncMock(side_effect=RuntimeError("inner failed"))

    with pytest.raises(RuntimeError, match="inner failed"):
        await middleware(request, inner)

    assert request.context[RPC_CONTEXT_RPC_QUEUE_WAIT_SECONDS] >= 0.0
    inner.assert_awaited_once_with(request)


@pytest.mark.asyncio
async def test_semaphore_records_queue_wait_when_acquisition_fails() -> None:
    class FailingRpcSemaphore(RpcSemaphore):
        def get(self):
            raise RuntimeError("acquisition failed")

    middleware = SemaphoreMiddleware(FailingRpcSemaphore(1))
    request = make_request()
    inner = AsyncMock()

    with pytest.raises(RuntimeError, match="acquisition failed"):
        await middleware(request, inner)

    assert request.context[RPC_CONTEXT_RPC_QUEUE_WAIT_SECONDS] >= 0.0
    inner.assert_not_awaited()
