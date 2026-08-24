"""Refresh-policy adapter for the web cookie provider.

The provider owns when one cookie generation refreshes; the established auth
subsystem still owns how.  ``WebProviderRefresh`` therefore keeps the current
base-policy single-flight / wider-policy join-then-rerun rule while delegating
the complete recovery ladder to ``refresh_auth_session``.  No recovery rung,
profile transaction, or master-token operation is reimplemented here.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .._cookie_persistence import CookiePersistence
from .._kernel import Kernel
from .._runtime.auth import AuthRefreshCoordinator
from .._runtime.lifecycle import ClientLifecycle
from .session import refresh_auth_session
from .tokens import AuthTokens

RefreshTransaction = Callable[
    [Callable[[], Awaitable[AuthTokens]]],
    Awaitable[AuthTokens],
]


async def _run_direct(work: Callable[[], Awaitable[AuthTokens]]) -> AuthTokens:
    return await work()


@dataclass(frozen=True, slots=True, repr=False)
class WebProviderRefresh:
    """Bind existing refresh collaborators behind one provider-side callable."""

    auth: AuthTokens = field(repr=False)
    kernel: Kernel = field(repr=False)
    coordinator: AuthRefreshCoordinator = field(repr=False)
    lifecycle: ClientLifecycle = field(repr=False)
    persistence: CookiePersistence = field(repr=False)
    transaction: RefreshTransaction = field(default=_run_direct, repr=False)

    async def _refresh_session(self, *, allow_headless: bool) -> AuthTokens:
        async def work() -> AuthTokens:
            return await refresh_auth_session(
                auth=self.auth,
                kernel=self.kernel,
                auth_coord=self.coordinator,
                lifecycle=self.lifecycle,
                cookie_persistence=self.persistence,
                allow_headless=allow_headless,
            )

        return await self.transaction(work)

    async def refresh(self, *, allow_headless: bool = False) -> AuthTokens:
        """Refresh with the established base/wider policy interaction.

        The ordinary policy runs the whole transaction directly.  A wider
        headless-enabled caller first joins the coordinator's base-policy
        flight; only ``ValueError`` (the established exhausted-base signal)
        causes one wider-policy rerun.  Cancellation and every other failure
        propagate without starting independent recovery work.
        """
        if not allow_headless or not self.coordinator.has_refresh_callback:
            return await self._refresh_session(allow_headless=allow_headless)
        try:
            await self.coordinator.await_refresh()
        except ValueError:
            return await self._refresh_session(allow_headless=True)
        return self.auth
