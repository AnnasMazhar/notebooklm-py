"""Test-only construction helper for the transitional semantic web backend."""

from __future__ import annotations

from typing import cast

from notebooklm._rpc_executor import RpcExecutor
from notebooklm._web.backend import WebRpcBackend


def _unused_transport_factory(**_kwargs: object) -> object:
    return object()


def build_web_backend(rpc: object) -> WebRpcBackend:
    """Wrap a fake ``rpc_call`` owner exactly as production assembly does."""
    return WebRpcBackend(
        cast(RpcExecutor, rpc),
        transport_factory=_unused_transport_factory,
    )


__all__ = ["build_web_backend"]
