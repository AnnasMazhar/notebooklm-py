"""Loop-bound concurrency owner for logical web RPC calls."""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager, nullcontext
from typing import Any

from ._loop_affinity import assert_bound_loop
from ._loop_bound import LoopBoundPrimitive


class RpcSemaphore(LoopBoundPrimitive):
    """Own the lazy per-client RPC gate and its event-loop binding.

    One context is entered per logical RPC outside retry and auth-refresh, so
    every attempt in that logical call retains the same concurrency slot.
    ``None`` disables the gate without introducing a loop-bound primitive.
    """

    def __init__(
        self,
        max_concurrent_rpcs: int | None,
    ) -> None:
        if max_concurrent_rpcs is not None and max_concurrent_rpcs < 1:
            raise ValueError(f"max_concurrent_rpcs must be >= 1, got {max_concurrent_rpcs!r}")
        self.max_concurrent_rpcs = max_concurrent_rpcs
        self._semaphore: asyncio.Semaphore | None = None

    def _on_loop_rebind(
        self,
        old: asyncio.AbstractEventLoop | None,
        new: asyncio.AbstractEventLoop | None,
    ) -> None:
        """Discard the cached gate whenever its loop binding changes."""
        self._semaphore = None

    def reset_after_open(self) -> None:
        """Discard the lazy gate so a reopened client rebuilds it in its loop."""
        self._semaphore = None

    def get(self) -> AbstractAsyncContextManager[Any]:
        """Return the lazy gate, or a no-op context for the unbounded mode."""
        if self.max_concurrent_rpcs is None:
            return nullcontext()
        assert_bound_loop(self._bound_loop)
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.max_concurrent_rpcs)
        return self._semaphore


__all__ = ["RpcSemaphore"]
