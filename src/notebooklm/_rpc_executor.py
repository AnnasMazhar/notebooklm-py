"""Compatibility adapter for the backend-owned web execution runtime.

``NotebookLMClient.rpc_call`` is an explicitly retained raw web escape hatch,
so its historical ``RpcExecutor`` object remains importable. The execution
implementation itself lives in :mod:`notebooklm._web.runtime`; semantic
operations reach that runtime through ``WebRpcBackend``.
"""

from __future__ import annotations

from ._web.runtime import DecodeResponse, WebExecutionRuntime

__all__ = ["DecodeResponse", "RpcExecutor"]


class RpcExecutor(WebExecutionRuntime):
    """Backward-compatible name for :class:`WebExecutionRuntime`.

    This subclass intentionally adds no execution behavior. It keeps stable
    construction and private characterization seams while ensuring the only
    implementation authority is ``_web.runtime``.
    """
