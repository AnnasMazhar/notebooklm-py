"""Tests for the atomic backend-owned client runtime assembly."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import Any

import pytest

from notebooklm._client_seams import ClientSeams
from notebooklm._runtime.init import ClientInternals, compose_client_internals
from notebooklm.auth import AuthTokens
from notebooklm.client import NotebookLMClient
from tests._helpers.client_factory import build_client_shell_for_tests


def _make_auth() -> AuthTokens:
    return AuthTokens(
        cookies={"SID": "x", "__Secure-1PSIDTS": "y"},
        csrf_token="csrf",
        session_id="sid",
    )


def test_compose_client_internals_returns_complete_frozen_runtime() -> None:
    runtime = compose_client_internals(auth=_make_auth(), max_concurrent_rpcs=4)

    assert isinstance(runtime, ClientInternals)
    assert runtime.executor._transport is runtime.transport
    assert runtime.chain_host._transport is runtime.transport
    assert runtime.rpc_semaphore.max_concurrent_rpcs == 4
    with pytest.raises(dataclasses.FrozenInstanceError):
        runtime.transport = runtime.transport  # type: ignore[misc]


@pytest.mark.parametrize("factory", [NotebookLMClient, build_client_shell_for_tests])
def test_client_construction_publishes_one_runtime_to_backend(factory: Any) -> None:
    client = factory(_make_auth(), max_concurrent_rpcs=3)
    backend = client._backend

    assert isinstance(client._seams, ClientSeams)
    assert backend._runtime is client.notebooks._legacy_rpc
    assert backend._chat_transport is backend._runtime._transport
    assert backend._rpc_semaphore is not None
    assert backend._rpc_semaphore.max_concurrent_rpcs == 3
    assert not hasattr(client, "_composed")
    assert not hasattr(client, "_collaborators")
    assert not hasattr(client, "_rpc_executor")


@pytest.mark.parametrize("factory", [NotebookLMClient, build_client_shell_for_tests])
def test_invalid_max_concurrent_rpcs_rejected_before_runtime_publication(factory: Any) -> None:
    with pytest.raises(ValueError, match="max_concurrent_rpcs must be >= 1, got 0"):
        factory(_make_auth(), max_concurrent_rpcs=0)


def test_compose_client_internals_refuses_synthetic_error_first(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("NOTEBOOKLM_VCR_RECORD_ERRORS", "5xx")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    with (
        caplog.at_level(logging.WARNING, logger="notebooklm._core"),
        pytest.raises(RuntimeError, match="NOTEBOOKLM_VCR_RECORD_ERRORS"),
    ):
        compose_client_internals(auth=_make_auth())


def _seams() -> ClientSeams:
    return ClientSeams(
        decode_response=lambda *_a, **_kw: None,
        sleep=asyncio.sleep,
        is_auth_error=lambda _exc: False,
    )


def test_atomic_runtime_preserves_explicit_leaf_seam_late_binding() -> None:
    seams = _seams()
    runtime = compose_client_internals(auth=_make_auth(), seams=seams)
    decoded: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    slept: list[float] = []

    def decode(*args: Any, **kwargs: Any) -> str:
        decoded.append((args, kwargs))
        return "rebound"

    async def sleep(delay: float) -> None:
        slept.append(delay)

    seams.decode_response = decode
    seams.is_auth_error = lambda exc: isinstance(exc, KeyError)
    seams.sleep = sleep

    assert runtime.executor._decode_response("payload", "id", allow_null=False) == "rebound"
    assert decoded
    assert runtime.executor._is_auth_error(KeyError("auth")) is True
    asyncio.run(runtime.executor._sleep(0.25))
    assert slept == [0.25]


@pytest.mark.parametrize("delay", [0.0, 0.99])
def test_refresh_retry_delay_is_selected_at_construction(delay: float) -> None:
    client = build_client_shell_for_tests(_make_auth(), refresh_retry_delay=delay)
    assert client._backend._runtime._refresh_retry_delay_provider() == delay


def test_executor_timeout_provider_reads_runtime_lifecycle() -> None:
    runtime = compose_client_internals(auth=_make_auth())
    runtime.lifecycle._timeout = 99.0
    assert runtime.executor._timeout_provider() == 99.0


def test_runtime_rpc_semaphore_unbounded_path() -> None:
    from contextlib import nullcontext

    runtime = compose_client_internals(auth=_make_auth(), max_concurrent_rpcs=None)
    assert isinstance(runtime.rpc_semaphore.get(), type(nullcontext()))


def test_runtime_rpc_semaphore_rebind_discards_stale_primitive() -> None:
    runtime = compose_client_internals(auth=_make_auth(), max_concurrent_rpcs=2)

    async def bind_and_build() -> None:
        runtime.rpc_semaphore.set_bound_loop(asyncio.get_running_loop())
        async with runtime.rpc_semaphore.get():
            pass

    asyncio.run(bind_and_build())
    first = runtime.rpc_semaphore._semaphore
    assert first is not None

    async def rebind() -> None:
        runtime.rpc_semaphore.set_bound_loop(asyncio.get_running_loop())
        assert runtime.rpc_semaphore._semaphore is None
        async with runtime.rpc_semaphore.get():
            pass

    asyncio.run(rebind())
    assert runtime.rpc_semaphore._semaphore is not first
