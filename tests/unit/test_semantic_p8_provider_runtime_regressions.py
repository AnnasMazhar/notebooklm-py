"""Focused regressions for the P8 provider/session ownership boundary."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from notebooklm._auth.cookie_types import CookieJar
from notebooklm._cookie_persistence import CookiePersistence
from notebooklm._runtime.auth import AuthRefreshCoordinator
from notebooklm._source.drive_import import DriveFetcher, DriveRef
from notebooklm.auth import AuthTokens
from tests._helpers.client_factory import build_client_shell_for_tests


def _auth(*, sid: str = "cookie-old") -> AuthTokens:
    return AuthTokens(
        cookies={("SID", ".google.com", "/"): sid},
        csrf_token="csrf-old",
        session_id="session-old",
        authuser=0,
        account_email=None,
    )


def _session_factory(**kwargs: Any) -> httpx.AsyncClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok", request=request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)


async def _wait_until(predicate: Callable[[], bool], *, turns: int = 100) -> None:
    for _ in range(turns):
        if predicate():
            return
        await asyncio.sleep(0)
    pytest.fail("condition did not become true")


def _sid(cookies: CookieJar | httpx.Cookies) -> str | None:
    jar = cookies.to_httpx() if isinstance(cookies, CookieJar) else cookies
    return jar.get("SID")


def test_provider_and_backend_own_distinct_kernels_and_narrow_backend_state() -> None:
    """P8 is an extraction, not two names for the same credential graph."""
    client = build_client_shell_for_tests(_auth(), async_client_factory=_session_factory)
    backend = client._backend
    provider = client._provider

    assert backend._session.kernel is not provider._kernel
    assert backend._kernel is backend._session.kernel

    backend_state = vars(backend)
    assert {"_provider", "_session"} <= backend_state.keys()
    assert {"_auth_coord", "_cookie_persistence", "_lifecycle"}.isdisjoint(backend_state)
    assert not any(
        isinstance(value, (AuthTokens, AuthRefreshCoordinator, CookiePersistence))
        for value in backend_state.values()
    )


@pytest.mark.asyncio
async def test_direct_refresh_is_single_flight_and_publishes_one_atomic_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancelled waiter cannot tear or duplicate the provider's refresh commit."""
    client = build_client_shell_for_tests(_auth(), async_client_factory=_session_factory)
    provider = client._provider
    before = await provider.generation()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def refresh_auth_session(*, auth: AuthTokens, kernel: Any, **_kwargs: Any) -> AuthTokens:
        nonlocal calls
        calls += 1
        kernel.get_cookies().set("SID", "cookie-new", domain=".google.com", path="/")
        # Deliberately expose an in-progress mutable graph. Readers must keep
        # receiving the last immutable commit until the whole transaction lands.
        started.set()
        await release.wait()
        auth.csrf_token = "csrf-new"
        auth.session_id = "session-new"
        auth.authuser = 7
        auth.account_email = "owner@example.com"
        return auth

    import notebooklm._auth.web_provider_refresh as refresh_module

    monkeypatch.setattr(refresh_module, "refresh_auth_session", refresh_auth_session)

    cancelled_waiter = asyncio.create_task(client.refresh_auth())
    surviving_waiter = asyncio.create_task(client.refresh_auth())
    await started.wait()

    middle = await provider.generation()
    assert middle is before
    assert (
        _sid(middle.cookies),
        middle.csrf_token,
        middle.session_id,
        middle.authuser,
        middle.account_email,
        middle.generation,
    ) == ("cookie-old", "csrf-old", "session-old", 0, None, before.generation)

    cancelled_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_waiter

    release.set()
    assert await surviving_waiter is client.auth

    after = await provider.generation()
    assert calls == 1
    assert after.generation == before.generation + 1
    assert (
        _sid(after.cookies),
        after.csrf_token,
        after.session_id,
        after.authuser,
        after.account_email,
    ) == ("cookie-new", "csrf-new", "session-new", 7, "owner@example.com")


@pytest.mark.asyncio
async def test_keepalive_rotation_publishes_a_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider-side keepalive rotation must reach the next backend attempt."""
    del monkeypatch  # the injected rotator is the supported deterministic seam
    rotated = asyncio.Event()

    async def rotate(client: httpx.AsyncClient, _path: object) -> None:
        client.cookies.set("SID", "cookie-rotated", domain=".google.com", path="/")
        rotated.set()

    client = build_client_shell_for_tests(
        _auth(),
        keepalive=0.01,
        keepalive_min_interval=0.01,
        cookie_rotator=rotate,
        async_client_factory=_session_factory,
    )
    provider = client._provider
    before = await provider.generation()

    await client.__aenter__()
    try:
        await asyncio.wait_for(rotated.wait(), timeout=1.0)
        await _wait_until(lambda: provider._current_generation.generation > before.generation)
        after = await provider.generation()
        assert after.generation == before.generation + 1
        assert _sid(after.cookies) == "cookie-rotated"
    finally:
        await client.close(drain=False)


@pytest.mark.asyncio
async def test_close_without_drain_cancels_a_hung_direct_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider serialization must not put fire-and-forget close behind refresh I/O."""
    client = build_client_shell_for_tests(_auth(), async_client_factory=_session_factory)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def refresh_auth_session(**_kwargs: Any) -> AuthTokens:
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    import notebooklm._auth.web_provider_refresh as refresh_module

    monkeypatch.setattr(refresh_module, "refresh_auth_session", refresh_auth_session)

    await client.__aenter__()
    refresh = asyncio.create_task(client.refresh_auth())
    await started.wait()
    try:
        await asyncio.wait_for(client.close(drain=False), timeout=0.5)
        await asyncio.wait_for(cancelled.wait(), timeout=0.5)
        assert client.is_connected is False
    finally:
        if not refresh.done():
            refresh.cancel()
        await asyncio.gather(refresh, return_exceptions=True)
        if client.is_connected:
            await client.close(drain=False)


@pytest.mark.asyncio
async def test_provider_close_failure_is_retryable() -> None:
    """A failed awaited close must not permanently latch the provider as closed."""
    from notebooklm._web.backend import WebRpcBackend

    class Provider:
        def __init__(self) -> None:
            self.calls = 0

        async def close(self) -> None:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transient provider close failure")

    class Session:
        def __init__(self) -> None:
            self.calls = 0

        async def close(self) -> None:
            self.calls += 1

    provider = Provider()
    session = Session()
    backend = WebRpcBackend(
        object(),  # type: ignore[arg-type]
        transport_factory=lambda **_kwargs: object(),
        provider=provider,  # type: ignore[arg-type]
        session=session,  # type: ignore[arg-type]
        owns_provider=True,
    )

    with pytest.raises(RuntimeError, match="transient provider close failure"):
        await backend.close()
    await backend.close()

    assert provider.calls == 2
    assert session.calls == 2


@pytest.mark.asyncio
async def test_provider_close_waiter_cancellation_does_not_cancel_teardown() -> None:
    """Cancellation detaches one waiter; a later close joins the same teardown."""
    from notebooklm._web.backend import WebRpcBackend

    class Provider:
        def __init__(self) -> None:
            self.calls = 0
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.finished = asyncio.Event()

        async def close(self) -> None:
            self.calls += 1
            self.started.set()
            await self.release.wait()
            self.finished.set()

    class Session:
        async def close(self) -> None:
            return None

    provider = Provider()
    backend = WebRpcBackend(
        object(),  # type: ignore[arg-type]
        transport_factory=lambda **_kwargs: object(),
        provider=provider,  # type: ignore[arg-type]
        session=Session(),  # type: ignore[arg-type]
        owns_provider=True,
    )

    waiter = asyncio.create_task(backend.close())
    await provider.started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    provider.release.set()
    await asyncio.wait_for(provider.finished.wait(), timeout=0.5)
    await backend.close()
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_owned_provider_and_private_session_support_close_reopen() -> None:
    """The established client close-to-reopen lifecycle survives P8 extraction."""
    client = build_client_shell_for_tests(_auth(), async_client_factory=_session_factory)

    await client.__aenter__()
    first_provider_client = client._provider._kernel.get_http_client()
    first_backend_client = client._backend._session.kernel.get_http_client()
    assert first_provider_client is not first_backend_client

    await client.close(drain=False)
    assert client.is_connected is False

    await client.__aenter__()
    try:
        assert client.is_connected is True
        assert client._provider._kernel.get_http_client() is not first_provider_client
        assert client._backend._session.kernel.get_http_client() is not first_backend_client
    finally:
        await client.close(drain=False)


@pytest.mark.asyncio
async def test_direct_upload_uses_one_committed_generation_during_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upload cookies and account route cannot come from different generations."""
    client = build_client_shell_for_tests(_auth(), async_client_factory=_session_factory)
    provider = client._provider
    uploader = client._source_uploader
    refresh_started = asyncio.Event()
    release_refresh = asyncio.Event()
    observations: list[tuple[str | None, str | None]] = []

    async def refresh_auth_session(*, auth: AuthTokens, kernel: Any, **_kwargs: Any) -> AuthTokens:
        # Mutable compatibility state changes before the provider commit.
        kernel.get_cookies().set("SID", "cookie-new", domain=".google.com", path="/")
        auth.authuser = 7
        auth.account_email = "owner@example.com"
        refresh_started.set()
        await release_refresh.wait()
        auth.csrf_token = "csrf-new"
        auth.session_id = "session-new"
        return auth

    class UploadClient:
        def __init__(self, cookies: httpx.Cookies) -> None:
            self.cookies = cookies

        async def __aenter__(self) -> UploadClient:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def post(
            self, url: str, *, headers: dict[str, str], content: object
        ) -> httpx.Response:
            del content
            route = parse_qs(urlparse(url).query).get("authuser", [None])[0]
            assert headers["x-goog-authuser"] == (route or "0")
            observations.append((_sid(self.cookies), route))
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                headers={
                    "x-goog-upload-url": (
                        "https://notebooklm.google.com/upload/_/?upload_id=p8-generation"
                    )
                },
                request=request,
            )

    def upload_factory(*, cookies: httpx.Cookies, **_kwargs: Any) -> UploadClient:
        return UploadClient(cookies)

    import notebooklm._auth.web_provider_refresh as refresh_module

    monkeypatch.setattr(refresh_module, "refresh_auth_session", refresh_auth_session)
    uploader._async_client_factory = upload_factory

    await client.__aenter__()
    refresh = asyncio.create_task(client.refresh_auth())
    await refresh_started.wait()
    try:
        await uploader.start_resumable_upload("nb", "x.pdf", 3, "src", "application/pdf")
        assert observations[-1] == ("cookie-old", "0")

        release_refresh.set()
        await refresh
        await uploader.start_resumable_upload("nb", "x.pdf", 3, "src", "application/pdf")
        assert observations[-1] == ("cookie-new", "owner@example.com")
        assert (
            client._backend._session.kernel.installed_generation
            == (await provider.generation()).generation
        )
    finally:
        release_refresh.set()
        await asyncio.gather(refresh, return_exceptions=True)
        await client.close(drain=False)


@pytest.mark.asyncio
async def test_drive_fetch_uses_one_committed_generation_during_refresh(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive cookies and account route cannot come from different generations."""
    client = build_client_shell_for_tests(_auth(), async_client_factory=_session_factory)
    uploader = client._source_uploader
    refresh_started = asyncio.Event()
    release_refresh = asyncio.Event()
    observations: list[tuple[str | None, str | None]] = []

    async def refresh_auth_session(*, auth: AuthTokens, kernel: Any, **_kwargs: Any) -> AuthTokens:
        kernel.get_cookies().set("SID", "cookie-new", domain=".google.com", path="/")
        auth.authuser = 7
        auth.account_email = "owner@example.com"
        refresh_started.set()
        await release_refresh.wait()
        auth.csrf_token = "csrf-new"
        auth.session_id = "session-new"
        return auth

    def drive_factory(cookies: httpx.Cookies, timeout: httpx.Timeout) -> httpx.AsyncClient:
        sid = _sid(cookies)

        async def handler(request: httpx.Request) -> httpx.Response:
            route = parse_qs(request.url.query.decode()).get("authuser", [None])[0]
            observations.append((sid, route))
            return httpx.Response(
                200,
                headers={
                    "content-type": "application/pdf",
                    "content-disposition": 'attachment; filename="atomic.pdf"',
                },
                content=b"%PDF-p8-generation",
                request=request,
            )

        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            cookies=cookies,
            timeout=timeout,
        )

    import notebooklm._auth.web_provider_refresh as refresh_module

    monkeypatch.setattr(refresh_module, "refresh_auth_session", refresh_auth_session)
    service = uploader.create_drive_import_service()
    fetcher = service._fetch
    assert isinstance(fetcher, DriveFetcher)
    fetcher._client_factory = drive_factory
    fetcher._temp_dir = tmp_path

    await client.__aenter__()
    refresh = asyncio.create_task(client.refresh_auth())
    await refresh_started.wait()
    try:
        first = await fetcher(DriveRef("A" * 20))
        first.path.unlink()
        assert observations[-1] == ("cookie-old", "0")

        release_refresh.set()
        await refresh
        second = await fetcher(DriveRef("A" * 20))
        second.path.unlink()
        assert observations[-1] == ("cookie-new", "owner@example.com")
    finally:
        release_refresh.set()
        await asyncio.gather(refresh, return_exceptions=True)
        await client.close(drain=False)
