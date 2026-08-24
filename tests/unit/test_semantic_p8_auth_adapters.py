"""P8 auth-side adapters preserve existing storage and refresh owners."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import httpx
import pytest

from notebooklm._auth import web_provider_refresh, web_provider_storage
from notebooklm._auth.cookie_types import Cookie, CookieJar
from notebooklm._auth.profile_store import ProfileStore
from notebooklm._auth.tokens import AuthTokens, FileLoadedAuth, InlineLoadedAuth
from notebooklm._auth.web_provider_refresh import WebProviderRefresh
from notebooklm._auth.web_provider_storage import (
    WebProviderBootstrap,
    load_web_provider_bootstrap,
)


def _auth() -> AuthTokens:
    return AuthTokens(
        cookies={"SID": "sid-secret"},
        csrf_token="csrf-secret",
        session_id="session-secret",
        cookie_jar=httpx.Cookies({"SID": "sid-secret"}),
    )


def _baseline() -> CookieJar:
    return CookieJar(
        (
            Cookie(
                name="SID",
                domain=".google.com",
                path="/",
                value="sid-secret",
            ),
        )
    )


@pytest.mark.asyncio
async def test_storage_adapter_delegates_inline_load_as_one_redacted_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _auth()
    load = AsyncMock(return_value=InlineLoadedAuth(auth))
    monkeypatch.setattr(web_provider_storage._auth_tokens, "_load_stored_auth", load)

    result = await load_web_provider_bootstrap(
        path=None,
        profile="work",
        allow_headless=True,
    )

    assert result == WebProviderBootstrap(auth=auth)
    assert result.store is None
    assert result.persistence_baseline is None
    rendered = repr(result)
    assert "sid-secret" not in rendered
    assert "csrf-secret" not in rendered
    assert "session-secret" not in rendered
    assert "WebProviderBootstrap" in rendered
    load.assert_awaited_once_with(
        path=None,
        profile="work",
        policy=web_provider_storage._auth_tokens.LoadPolicy(allow_headless=True),
        auth_type=AuthTokens,
    )


@pytest.mark.asyncio
async def test_storage_adapter_preserves_file_store_and_exact_baseline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    auth = _auth()
    store = ProfileStore(tmp_path / "storage_state.json")
    baseline = _baseline()
    load = AsyncMock(return_value=FileLoadedAuth(auth, store, baseline))
    monkeypatch.setattr(web_provider_storage._auth_tokens, "_load_stored_auth", load)

    result = await load_web_provider_bootstrap(
        path=store.path,
        profile=None,
        allow_headless=False,
    )

    assert result.auth is auth
    assert result.store is store
    assert result.persistence_baseline == baseline
    assert result.persistence_baseline is not baseline


def test_storage_bootstrap_is_frozen_and_requires_store_baseline_pair() -> None:
    auth = _auth()
    with pytest.raises(ValueError, match="present together"):
        WebProviderBootstrap(auth=auth, store=ProfileStore(Path("profile.json")))
    with pytest.raises(ValueError, match="present together"):
        WebProviderBootstrap(auth=auth, persistence_baseline=_baseline())
    bootstrap = WebProviderBootstrap(auth=auth)
    with pytest.raises(FrozenInstanceError):
        bootstrap.store = ProfileStore(Path("other.json"))  # type: ignore[misc]


def _refresh_adapter(
    *,
    auth: AuthTokens,
    coordinator: Any,
) -> WebProviderRefresh:
    return WebProviderRefresh(
        auth=auth,
        kernel=cast(Any, object()),
        coordinator=coordinator,
        lifecycle=cast(Any, object()),
        persistence=cast(Any, object()),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_headless", [False, True])
async def test_refresh_adapter_delegates_whole_transaction_without_join_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    allow_headless: bool,
) -> None:
    auth = _auth()

    class _Coordinator:
        has_refresh_callback = False

        async def await_refresh(self) -> None:  # pragma: no cover - must not run
            raise AssertionError("no refresh flight should be joined")

    coordinator = _Coordinator()
    refresh = AsyncMock(return_value=auth)
    monkeypatch.setattr(web_provider_refresh, "refresh_auth_session", refresh)
    adapter = _refresh_adapter(auth=auth, coordinator=coordinator)

    assert await adapter.refresh(allow_headless=allow_headless) is auth
    refresh.assert_awaited_once_with(
        auth=auth,
        kernel=adapter.kernel,
        auth_coord=coordinator,
        lifecycle=adapter.lifecycle,
        cookie_persistence=adapter.persistence,
        allow_headless=allow_headless,
    )


@pytest.mark.asyncio
async def test_wider_refresh_returns_successful_base_flight_without_rerun(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _auth()

    class _Coordinator:
        has_refresh_callback = True

        def __init__(self) -> None:
            self.calls = 0

        async def await_refresh(self) -> None:
            self.calls += 1

    coordinator = _Coordinator()
    refresh = AsyncMock()
    monkeypatch.setattr(web_provider_refresh, "refresh_auth_session", refresh)

    assert (
        await _refresh_adapter(auth=auth, coordinator=coordinator).refresh(allow_headless=True)
        is auth
    )
    assert coordinator.calls == 1
    refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_wider_refresh_reruns_full_policy_only_after_base_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _auth()

    class _Coordinator:
        has_refresh_callback = True

        async def await_refresh(self) -> None:
            raise ValueError("base recovery exhausted")

    coordinator = _Coordinator()
    refresh = AsyncMock(return_value=auth)
    monkeypatch.setattr(web_provider_refresh, "refresh_auth_session", refresh)
    adapter = _refresh_adapter(auth=auth, coordinator=coordinator)

    assert await adapter.refresh(allow_headless=True) is auth
    refresh.assert_awaited_once_with(
        auth=auth,
        kernel=adapter.kernel,
        auth_coord=coordinator,
        lifecycle=adapter.lifecycle,
        cookie_persistence=adapter.persistence,
        allow_headless=True,
    )


@pytest.mark.asyncio
async def test_wider_refresh_propagates_non_exhaustion_failures_without_rerun(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _auth()

    class _Coordinator:
        has_refresh_callback = True

        async def await_refresh(self) -> None:
            raise RuntimeError("coordinator failure")

    refresh = AsyncMock()
    monkeypatch.setattr(web_provider_refresh, "refresh_auth_session", refresh)

    with pytest.raises(RuntimeError, match="coordinator failure"):
        await _refresh_adapter(auth=auth, coordinator=_Coordinator()).refresh(allow_headless=True)
    refresh.assert_not_awaited()
