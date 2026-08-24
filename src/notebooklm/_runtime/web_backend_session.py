"""Backend-private web session seeded from immutable provider generations."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from .._auth.cookie_types import CookieJar
from .._kernel import Kernel
from .._loop_affinity import assert_bound_loop
from .._loop_bound import LoopBoundPrimitive
from .._web_cookie_provider import (
    WebCookieGeneration,
    WebCookieSession,
    WebCookieSessionState,
    WebCookieSessionTransition,
)
from ..types import ConnectionLimits


@dataclass(frozen=True, slots=True)
class _BackendSessionTransition:
    state: WebCookieSessionState | None
    _kernel: Kernel

    def install(self, generation: WebCookieGeneration) -> bool:
        installed = self._kernel.installed_generation
        if installed is not None and generation.generation <= installed:
            return False
        self._kernel._install_generation_unchecked(generation)
        return True


class WebBackendSession(LoopBoundPrimitive):
    """Own the mutable HTTP session used only for backend execution."""

    def __init__(
        self,
        *,
        kernel: Kernel,
        timeout: float,
        connect_timeout: float,
        limits: ConnectionLimits,
    ) -> None:
        self._kernel = kernel
        self._timeout = timeout
        self._connect_timeout = connect_timeout
        self._limits = limits
        self._close_task: asyncio.Task[None] | None = None

    @property
    def kernel(self) -> Kernel:
        """Return the concrete private kernel for runtime collaborators."""
        return self._kernel

    @property
    def is_open(self) -> bool:
        return self._kernel.http_client is not None

    def assert_open(self) -> None:
        """Preserve the legacy pre-open runtime error surface."""
        self._kernel.get_http_client()

    async def open(self, generation: WebCookieGeneration) -> None:
        """Clone one generation before constructing the private HTTP client."""
        if self.is_open:
            assert_bound_loop(self._bound_loop)
            return
        self.set_bound_loop(asyncio.get_running_loop())
        self._close_task = None
        self._kernel.install_generation(generation)
        await self._kernel.open(
            auth=None,
            timeout=self._timeout,
            connect_timeout=self._connect_timeout,
            limits=self._limits,
            capture_cookie_snapshot=lambda _jar: None,
        )

    def detach(self) -> WebCookieSessionState | None:
        """Copy the private jar together with the generation that seeded it."""
        generation = self._kernel.installed_generation
        if generation is None:
            return None
        return WebCookieSessionState(
            cookies=CookieJar.from_httpx(self._kernel.get_cookies()),
            generation=generation,
        )

    @asynccontextmanager
    async def generation_transition(self) -> AsyncIterator[WebCookieSessionTransition]:
        """Drain older attempts and keep admission closed through installation."""
        async with self._kernel.generation_transition():
            yield _BackendSessionTransition(self.detach(), self._kernel)

    async def close(self) -> None:
        """Close once; cancellation of a waiter does not cancel teardown."""
        assert_bound_loop(self._bound_loop)
        task = self._close_task
        if task is None or (task.done() and not task.cancelled() and task.exception() is not None):
            task = asyncio.create_task(self._kernel.aclose())
            self._close_task = task
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            raise
        except BaseException:
            if task.done():
                self._close_task = None
            raise


def _assert_web_session(session: WebBackendSession) -> None:
    """Static structural check kept out of runtime execution."""
    _: WebCookieSession = session


__all__ = ["WebBackendSession"]
