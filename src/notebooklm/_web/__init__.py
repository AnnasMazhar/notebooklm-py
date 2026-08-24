"""Private web-protocol implementation of the semantic backend port."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .backend import WebRpcBackend


def __getattr__(name: str) -> Any:
    """Load the backend lazily so leaf codecs cannot create import cycles."""
    if name == "WebRpcBackend":
        from .backend import WebRpcBackend

        return WebRpcBackend
    raise AttributeError(name)


__all__ = ["WebRpcBackend"]
