"""Stored-auth bootstrap adapter for the web cookie provider.

P8 keeps profile path resolution, loading, locking, CAS, and persistence in
their established auth-layer owners.  This module is the narrow construction
adapter: it delegates the complete stored-auth transaction to
``tokens._load_stored_auth`` and projects its existing inline/file result into
one provider-side bootstrap value.

Nothing here reads or writes a profile document directly.  In particular,
``ProfileStore`` remains the path-owned transaction capability and the
provider receives the exact store and persistence baseline produced by the
canonical loader.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import tokens as _auth_tokens
from .cookie_types import CookieJar
from .profile_store import ProfileStore
from .tokens import AuthTokens


@dataclass(frozen=True, slots=True, repr=False)
class WebProviderBootstrap:
    """Redacted provider construction result from one stored-auth load."""

    auth: AuthTokens = field(repr=False)
    store: ProfileStore | None = field(default=None, repr=False)
    persistence_baseline: CookieJar | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.auth, AuthTokens):
            raise TypeError("auth must be an AuthTokens")
        if self.store is not None and not isinstance(self.store, ProfileStore):
            raise TypeError("store must be a ProfileStore or None")
        if self.persistence_baseline is not None and not isinstance(
            self.persistence_baseline, CookieJar
        ):
            raise TypeError("persistence_baseline must be a CookieJar or None")
        if (self.store is None) != (self.persistence_baseline is None):
            raise ValueError("store and persistence_baseline must be present together")
        if self.persistence_baseline is not None:
            object.__setattr__(
                self,
                "persistence_baseline",
                CookieJar(tuple(self.persistence_baseline)),
            )


async def load_web_provider_bootstrap(
    *,
    path: Path | None,
    profile: str | None,
    allow_headless: bool,
) -> WebProviderBootstrap:
    """Run the canonical stored-auth transaction for provider construction."""
    loaded = await _auth_tokens._load_stored_auth(
        path=path,
        profile=profile,
        policy=_auth_tokens.LoadPolicy(allow_headless=allow_headless),
        auth_type=AuthTokens,
    )
    if isinstance(loaded, _auth_tokens.InlineLoadedAuth):
        return WebProviderBootstrap(auth=loaded.auth)
    if isinstance(loaded, _auth_tokens.FileLoadedAuth):
        return WebProviderBootstrap(
            auth=loaded.auth,
            store=loaded.store,
            persistence_baseline=loaded.persistence_baseline,
        )
    raise AssertionError("unknown stored-auth load result")
