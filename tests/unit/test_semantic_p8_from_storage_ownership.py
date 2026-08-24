"""P8 regressions for legacy ``from_storage`` provider ownership."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from notebooklm._auth import tokens as auth_tokens_module
from notebooklm._auth.cookie_types import CookieJar
from notebooklm._auth.profile_store import ProfileStore
from notebooklm.auth import AuthTokens
from notebooklm.client import NotebookLMClient


def _auth(path: Path) -> AuthTokens:
    return AuthTokens(
        cookies={("SID", ".google.com", "/"): "cookie"},
        csrf_token="csrf",
        session_id="session",
        storage_path=path,
        cookie_jar=CookieJar.from_domain_map({("SID", ".google.com", "/"): "cookie"}).to_httpx(),
    )


@pytest.mark.asyncio
async def test_file_loaded_custom_client_without_provider_still_builds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P8 baseline registration remains conditional for custom subclasses."""
    path = tmp_path / "storage_state.json"
    auth = _auth(path)
    store = ProfileStore(path)
    baseline = CookieJar()

    async def load(**_kwargs: Any) -> auth_tokens_module.FileLoadedAuth:
        return auth_tokens_module.FileLoadedAuth(auth, store, baseline)

    monkeypatch.setattr(auth_tokens_module, "_load_stored_auth", load)

    class BareClient(NotebookLMClient):
        def __init__(self, loaded_auth: AuthTokens, **_kwargs: Any) -> None:
            self.seen_auth = loaded_auth

    built = cast(BareClient, await BareClient.from_storage(str(path))._build())
    assert built.seen_auth is auth
    assert not hasattr(built, "_provider")
