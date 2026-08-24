"""Test-only construction helper for the transitional semantic web backend."""

from __future__ import annotations

from typing import cast

from notebooklm._reqid_counter import ReqidCounter
from notebooklm._rpc_executor import RpcExecutor
from notebooklm._runtime.transport import RuntimeTransport
from notebooklm._web.backend import WebRpcBackend


def _unused_transport_factory(**_kwargs: object) -> object:
    return object()


def build_web_backend(
    rpc: object,
    *,
    source_uploader: object | None = None,
    chat_transport: object | None = None,
    chat_reqid: object | None = None,
    chat_timeout: float | None = None,
    chat_response_max_bytes: int | None = None,
) -> WebRpcBackend:
    """Wrap a fake ``rpc_call`` owner exactly as production assembly does."""
    return WebRpcBackend(
        cast(RpcExecutor, rpc),
        transport_factory=_unused_transport_factory,
        source_uploader=source_uploader,
        chat_transport=cast(RuntimeTransport | None, chat_transport),
        chat_reqid=cast(ReqidCounter | None, chat_reqid),
        chat_timeout=chat_timeout,
        chat_response_max_bytes=chat_response_max_bytes,
    )


__all__ = ["build_web_backend"]
