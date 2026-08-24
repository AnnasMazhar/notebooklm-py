"""Focused regressions for the P8 provider/session ownership boundary."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from notebooklm._auth.cookie_types import CookieJar
from notebooklm._cookie_persistence import CookiePersistence
from notebooklm._kernel import Kernel
from notebooklm._runtime.auth import AuthRefreshCoordinator
from notebooklm._source.drive_import import DriveFetcher, DriveRef
from notebooklm._web_cookie_provider import WebCookieGeneration
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

    assert backend._backend_session.kernel is not provider._kernel
    assert backend._kernel is backend._backend_session.kernel

    backend_state = vars(backend)
    assert {"_provider", "_backend_session"} <= backend_state.keys()
    assert {"_auth_coord", "_cookie_persistence", "_lifecycle"}.isdisjoint(backend_state)
    assert not any(
        isinstance(value, (AuthTokens, AuthRefreshCoordinator, CookiePersistence))
        for value in backend_state.values()
    )


def test_backend_type_surface_is_protocol_narrow_and_shallow_repr_is_redacted() -> None:
    """Runtime introspection exposes ports and epochs, never credential values."""
    from notebooklm._runtime.web_backend_session import WebBackendSession
    from notebooklm._runtime.web_cookie_provider import RuntimeWebCookieProvider
    from notebooklm._web.backend import WebRpcBackend

    client = build_client_shell_for_tests(_auth(), async_client_factory=_session_factory)
    backend = client._backend
    provider = client._provider
    session = backend._backend_session

    assert type(provider) is RuntimeWebCookieProvider
    assert type(session) is WebBackendSession
    signature = inspect.signature(WebRpcBackend.__init__)
    assert signature.parameters["provider"].annotation == "WebCookieProvider | None"
    assert signature.parameters["session"].annotation == "WebCookieSession | None"

    introspection = repr((backend, vars(backend), provider, vars(provider), session, vars(session)))
    for secret in ("cookie-old", "csrf-old", "session-old"):
        assert secret not in introspection


@pytest.mark.asyncio
async def test_open_time_cookie_reload_publishes_before_backend_session_seed() -> None:
    """A provider-open cookie change advances one epoch before backend cloning."""
    factory_calls = 0

    def open_factory(**kwargs: Any) -> httpx.AsyncClient:
        nonlocal factory_calls
        factory_calls += 1
        client = _session_factory(**kwargs)
        if factory_calls == 1:
            client.cookies.set("SID", "cookie-open-reload", domain=".google.com", path="/")
        return client

    client = build_client_shell_for_tests(_auth(), async_client_factory=open_factory)
    provider = client._provider
    before = await provider.generation()

    await client.__aenter__()
    try:
        after = await provider.generation()
        assert after.generation == before.generation + 1
        assert _sid(after.cookies) == "cookie-open-reload"
        assert client._backend._backend_session.kernel.installed_generation == after.generation
        assert _sid(client._backend._backend_session.kernel.cookies) == "cookie-open-reload"
    finally:
        await client.close(drain=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [RuntimeError, asyncio.CancelledError])
async def test_failed_or_cancelled_provider_refresh_publishes_no_epoch(
    error_type: type[BaseException],
) -> None:
    """Mutable work that does not succeed never becomes a provider commit."""
    client = build_client_shell_for_tests(_auth(), async_client_factory=_session_factory)
    provider = client._provider
    before = await provider.generation()

    async def fake_refresh(*, allow_headless: bool = False) -> AuthTokens:
        del allow_headless

        async def work() -> AuthTokens:
            provider._kernel.cookies.set(
                "SID", "cookie-uncommitted", domain=".google.com", path="/"
            )
            provider.auth.csrf_token = "csrf-uncommitted"
            raise error_type("refresh did not commit")

        return await provider.run_refresh_transaction(work)

    provider._refresh_session = fake_refresh
    with pytest.raises(error_type):
        await provider.refresh()

    after = await provider.generation()
    assert after is before
    assert after.generation == before.generation
    assert (_sid(after.cookies), after.csrf_token) == ("cookie-old", "csrf-old")


def test_equal_or_stale_generation_preserves_backend_set_cookie() -> None:
    """Replaying a seed cannot erase response cookie churn at the same epoch."""
    kernel = Kernel(auth=_auth())
    current = WebCookieGeneration(
        csrf_token="csrf-current",
        session_id="session-current",
        authuser=0,
        account_email=None,
        cookies=CookieJar.from_httpx(httpx.Cookies({"SID": "cookie-seed"})),
        generation=4,
    )
    stale = WebCookieGeneration(
        csrf_token="csrf-stale",
        session_id="session-stale",
        authuser=0,
        account_email=None,
        cookies=CookieJar.from_httpx(httpx.Cookies({"SID": "cookie-stale"})),
        generation=3,
    )

    assert kernel.install_generation(current) is True
    kernel.cookies.set("SID", "cookie-from-response")

    assert kernel.install_generation(current) is False
    assert kernel.install_generation(stale) is False
    assert _sid(kernel.cookies) == "cookie-from-response"
    assert kernel.installed_generation == 4


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
async def test_custom_coordinator_refresh_success_publishes_one_provider_epoch() -> None:
    """A 401 callback outside ``provider.refresh`` still feeds the retry generation."""
    auth = _auth()

    async def custom_refresh() -> AuthTokens:
        auth.csrf_token = "csrf-custom"
        auth.session_id = "session-custom"
        auth.authuser = 6
        auth.account_email = "custom@example.com"
        return auth

    client = build_client_shell_for_tests(
        auth,
        refresh_callback=custom_refresh,
        async_client_factory=_session_factory,
    )
    provider = client._provider

    await client.__aenter__()
    try:
        before = await provider.generation()
        await client._backend._runtime._refresh()
        after = await provider.generation()

        assert after.generation == before.generation + 1
        assert (
            after.csrf_token,
            after.session_id,
            after.authuser,
            after.account_email,
        ) == ("csrf-custom", "session-custom", 6, "custom@example.com")
    finally:
        await client.close(drain=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [RuntimeError, asyncio.CancelledError])
async def test_custom_coordinator_refresh_failure_publishes_no_provider_epoch(
    error_type: type[BaseException],
) -> None:
    """Only successful custom callback work is eligible for publication."""
    auth = _auth()

    async def custom_refresh() -> AuthTokens:
        auth.csrf_token = "csrf-uncommitted-custom"
        raise error_type("custom refresh did not commit")

    client = build_client_shell_for_tests(
        auth,
        refresh_callback=custom_refresh,
        async_client_factory=_session_factory,
    )
    provider = client._provider

    await client.__aenter__()
    try:
        before = await provider.generation()
        with pytest.raises(error_type):
            await client._backend._runtime._refresh()
        assert await provider.generation() is before
    finally:
        await client.close(drain=False)


@pytest.mark.asyncio
async def test_cancelled_custom_refresh_waiter_cannot_drop_leader_success() -> None:
    """Publication belongs to the shared leader, not to one cancellable waiter."""
    auth = _auth()
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    async def custom_refresh() -> AuthTokens:
        started.set()
        await release.wait()
        auth.csrf_token = "csrf-custom-after-cancel"
        finished.set()
        return auth

    client = build_client_shell_for_tests(
        auth,
        refresh_callback=custom_refresh,
        async_client_factory=_session_factory,
    )
    provider = client._provider

    await client.__aenter__()
    waiter = asyncio.create_task(client._backend._runtime._refresh())
    await started.wait()
    before = await provider.generation()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    release.set()
    try:
        await asyncio.wait_for(finished.wait(), timeout=0.5)
        coordinator_leader = provider._coordinator._refresh_task
        assert coordinator_leader is not None
        await asyncio.gather(coordinator_leader, return_exceptions=True)
        await _wait_until(lambda: provider._current_generation.generation > before.generation)
        after = await provider.generation()
        assert after.generation == before.generation + 1
        assert after.csrf_token == "csrf-custom-after-cancel"
    finally:
        release.set()
        await client.close(drain=False)


@pytest.mark.asyncio
async def test_provider_refresh_callback_is_not_published_twice() -> None:
    """The production callback already commits inside the provider transaction."""
    client = build_client_shell_for_tests(_auth(), async_client_factory=_session_factory)
    provider = client._provider

    async def fake_refresh(*, allow_headless: bool = False) -> AuthTokens:
        del allow_headless

        async def work() -> AuthTokens:
            provider.auth.csrf_token = "csrf-provider-success"
            return provider.auth

        return await provider.run_refresh_transaction(work)

    provider._refresh_session = fake_refresh
    provider._coordinator._refresh_callback = client.refresh_auth

    await client.__aenter__()
    try:
        before = await provider.generation()
        await client._backend._runtime._refresh()
        after = await provider.generation()
        assert after.generation == before.generation + 1
        assert after.csrf_token == "csrf-provider-success"
    finally:
        await client.close(drain=False)


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
async def test_close_without_drain_cancels_a_hung_account_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Identity network I/O cannot hold the provider lock ahead of teardown."""
    client = build_client_shell_for_tests(_auth(), async_client_factory=_session_factory)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def probe(_client: httpx.AsyncClient, _authuser: int) -> str | None:
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    import notebooklm._runtime.web_cookie_provider as provider_module

    monkeypatch.setattr(provider_module, "_probe_authuser", probe)

    await client.__aenter__()
    identity = asyncio.create_task(client.get_account_email())
    await started.wait()
    try:
        await asyncio.wait_for(client.close(drain=False), timeout=0.5)
        await asyncio.wait_for(cancelled.wait(), timeout=0.5)
        assert client.is_connected is False
        with pytest.raises(asyncio.CancelledError):
            await identity
    finally:
        if not identity.done():
            identity.cancel()
        await asyncio.gather(identity, return_exceptions=True)
        if client.is_connected:
            await client.close(drain=False)


@pytest.mark.asyncio
async def test_post_close_account_lookup_preserves_network_free_policy() -> None:
    """Closed providers retain offline identity state but reject live probes."""
    auth = _auth()
    auth.account_email = "offline@example.com"
    client = build_client_shell_for_tests(auth, async_client_factory=_session_factory)

    await client.__aenter__()
    await client.close(drain=False)

    assert await client.get_account_email(live_fallback=False) == "offline@example.com"
    with pytest.raises(RuntimeError, match="provider is closing"):
        await client.get_account_email(live_fallback=True)


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

    class BackendSession:
        def __init__(self) -> None:
            self.calls = 0

        async def close(self) -> None:
            self.calls += 1

    provider = Provider()
    session = BackendSession()
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
async def test_direct_backend_reconciles_but_does_not_close_injected_provider() -> None:
    """A caller-owned provider survives while its detached backend jar is reconciled."""
    from notebooklm._web.backend import WebRpcBackend

    class Provider:
        def __init__(self) -> None:
            self.reconciled = 0
            self.closed = 0

        async def reconcile(self) -> None:
            self.reconciled += 1

        async def close(self) -> None:
            self.closed += 1

    class BackendSession:
        def __init__(self) -> None:
            self.closed = 0

        async def close(self) -> None:
            self.closed += 1

    provider = Provider()
    session = BackendSession()
    backend = WebRpcBackend(
        object(),  # type: ignore[arg-type]
        transport_factory=lambda **_kwargs: object(),
        provider=provider,  # type: ignore[arg-type]
        session=session,  # type: ignore[arg-type]
    )

    await backend.close()

    assert provider.reconciled == 1
    assert provider.closed == 0
    assert session.closed == 1


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

    class BackendSession:
        async def close(self) -> None:
            return None

    provider = Provider()
    backend = WebRpcBackend(
        object(),  # type: ignore[arg-type]
        transport_factory=lambda **_kwargs: object(),
        provider=provider,  # type: ignore[arg-type]
        session=BackendSession(),  # type: ignore[arg-type]
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
    first_backend_client = client._backend._backend_session.kernel.get_http_client()
    assert first_provider_client is not first_backend_client

    await client.close(drain=False)
    assert client.is_connected is False

    await client.__aenter__()
    try:
        assert client.is_connected is True
        assert client._provider._kernel.get_http_client() is not first_provider_client
        assert client._backend._backend_session.kernel.get_http_client() is not first_backend_client
    finally:
        await client.close(drain=False)


def test_owned_provider_and_private_session_reopen_on_a_new_event_loop() -> None:
    """Close-to-reopen replaces every loop-owned provider/session resource."""
    client = build_client_shell_for_tests(_auth(), async_client_factory=_session_factory)
    observations: list[tuple[asyncio.AbstractEventLoop, ...]] = []

    async def cycle() -> None:
        await client.__aenter__()
        try:
            provider = client._provider
            observations.append(
                (
                    asyncio.get_running_loop(),
                    provider._kernel.get_http_client(),
                    client._backend._backend_session.kernel.get_http_client(),
                    provider._get_refresh_transaction_lock(),
                    provider._get_base_refresh_lock(),
                    provider._get_joined_refresh_lock(),
                    provider._get_identity_lock(),
                )
            )
        finally:
            await client.close(drain=False)

    asyncio.run(cycle())
    asyncio.run(cycle())

    first, second = observations
    assert first[0] is not second[0]
    assert first[1] is not second[1]
    assert first[2] is not second[2]
    assert all(old is not new for old, new in zip(first[3:], second[3:], strict=True))


@pytest.mark.parametrize(
    ("entry", "kwargs", "untouched_slot"),
    [
        ("reconcile", {}, "_refresh_transaction_lock"),
        ("refresh", {}, "_base_refresh_lock"),
        ("await_refresh", {}, "_joined_refresh_lock"),
        ("get_account_email", {"live_fallback": False}, "_identity_lock"),
        ("close", {}, "_close_task"),
    ],
)
def test_provider_lock_paths_fail_fast_on_a_foreign_event_loop(
    entry: str,
    kwargs: dict[str, object],
    untouched_slot: str,
) -> None:
    """Every provider lock path rejects cross-loop use before allocating state."""
    client = build_client_shell_for_tests(_auth(), async_client_factory=_session_factory)
    provider = client._provider
    owner_loop = asyncio.new_event_loop()
    try:
        provider.set_bound_loop(owner_loop)

        async def misuse() -> None:
            await getattr(provider, entry)(**kwargs)

        with pytest.raises(RuntimeError, match="bound to a different event loop"):
            asyncio.run(misuse())
        assert getattr(provider, untouched_slot) is None
    finally:
        owner_loop.close()


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
            client._backend._backend_session.kernel.installed_generation
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
