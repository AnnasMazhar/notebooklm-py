"""Characterization tests for Phase 8 (P8): the web cookie-provider boundary.

Governed by ADR-0035 and docs/plan/2026-08-13-semantic-backend-refactor.md (P8).
P8 separates credential acquisition/persistence from one open web backend
session: ``WebCookieProvider`` returns an immutable cookie/account-route
generation, ``WebRpcBackend`` clones a generation into its own private HTTP
session, and existing profile storage / refresh / recovery / master-token work
is *adapted* behind the provider rather than duplicated.

Nothing here demands P8 be implemented. Each test pins a behaviour P8 must
equality-preserve, or records the shape P8 inherits, so the extraction is a
code-motion diff rather than a redesign:

1. Immutable atomic cookie/account-route generation (and today's two-part
   mechanism, which is the gap P8 closes).
2. ``AuthTokens`` identity + bootstrap compatibility shadows.
3. Provider/backend ownership and close rules.
4. ``from_storage`` ownership — a convenience factory closes only what it made.
5. Profile paths, locking, CAS, atomic writes, and permissions.
6. Refresh single-flight, generation fencing, recovery, and master-token reuse.
7. Account routing.
8. Secret redaction.

The static ownership inventories live in
``tests/_guardrails/test_semantic_p8_provider_boundary_audit.py``.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import inspect
import json
import os
import stat
import sys
import textwrap
from collections.abc import Iterator
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import httpx
import pytest

from notebooklm._atomic_io import _atomic_write_json_unchecked, atomic_write_json
from notebooklm._auth import paths as auth_paths
from notebooklm._auth import single_flight
from notebooklm._auth.account import authuser_query, format_authuser_value
from notebooklm._auth.cookie_types import CookieJar
from notebooklm._auth.cookies import _clone_cookie_jar
from notebooklm._backend import BackendContractError, BackendKind
from notebooklm._kernel import Kernel
from notebooklm._request_types import AuthSnapshot
from notebooklm._runtime.auth import AuthRefreshCoordinator
from notebooklm.auth import AuthTokens
from notebooklm.client import NotebookLMClient, _FromStorageContext
from tests._fixtures.web_backend import build_web_backend


def _make_auth(**overrides: Any) -> AuthTokens:
    """Build an inline ``AuthTokens`` with no disk or network access."""
    kwargs: dict[str, Any] = {
        "cookies": {"SID": "test-sid", "HSID": "test-hsid"},
        "csrf_token": "test-csrf",
        "session_id": "test-session",
    }
    kwargs.update(overrides)
    return AuthTokens(**kwargs)


def _jar(**cookies: str) -> httpx.Cookies:
    jar = httpx.Cookies()
    for name, value in cookies.items():
        jar.set(name, value, domain=".google.com", path="/")
    return jar


# -----------------------------------------------------------------------------
# 1. Immutable atomic cookie/account-route generation
# -----------------------------------------------------------------------------


def test_stored_generation_installs_cookies_and_route_together() -> None:
    """One install advances cookies, both shadows, the route, and the generation."""
    auth = _make_auth()
    target = auth.cookie_jar
    assert target is not None

    changed = auth._replace_profile_session(
        target_cookie_jar=target,
        source_cookie_jar=_jar(SID="rotated-sid"),
        expected_cookie_jar=CookieJar.from_httpx(target),
        expected_authuser=0,
        expected_account_email=None,
        expected_generation=0,
        authuser=2,
        account_email="owner@example.com",
    )

    assert changed is True, "an authuser/email change must be reported as a route change"
    assert auth.authuser == 2
    assert auth.account_email == "owner@example.com"
    assert auth._profile_session_generation == 1
    # Both public compatibility shadows advanced with the live jar.
    assert auth.cookie_jar is target
    assert dict(target) == {"SID": "rotated-sid"}
    assert {key[0] for key in auth.cookies} == {"SID"}


def test_stored_generation_reports_no_route_change_when_identity_is_stable() -> None:
    """Cookies may advance without the account route changing."""
    auth = _make_auth(authuser=1, account_email="owner@example.com")
    target = auth.cookie_jar
    assert target is not None

    changed = auth._replace_profile_session(
        target_cookie_jar=target,
        source_cookie_jar=_jar(SID="rotated-sid"),
        expected_cookie_jar=CookieJar.from_httpx(target),
        expected_authuser=1,
        expected_account_email="owner@example.com",
        expected_generation=0,
        authuser=1,
        account_email="owner@example.com",
    )

    assert changed is False
    assert auth._profile_session_generation == 1


@pytest.mark.parametrize(
    "stale",
    [
        pytest.param({"expected_generation": 7}, id="stale-generation"),
        pytest.param({"expected_authuser": 9}, id="stale-authuser"),
        pytest.param({"expected_account_email": "other@example.com"}, id="stale-email"),
        pytest.param({"expected_cookie_jar": CookieJar()}, id="stale-cookies"),
    ],
)
def test_late_generation_cannot_replace_newer_state(stale: dict[str, Any]) -> None:
    """P8 criterion: a late result cannot overwrite newer state.

    Every axis of the expectation is fenced. A mismatch on any one of them
    returns ``None`` and mutates nothing, so the caller retries against the
    state it actually observed.
    """
    auth = _make_auth()
    target = auth.cookie_jar
    assert target is not None
    before_cookies = dict(target)

    kwargs: dict[str, Any] = {
        "target_cookie_jar": target,
        "source_cookie_jar": _jar(SID="rotated-sid"),
        "expected_cookie_jar": CookieJar.from_httpx(target),
        "expected_authuser": 0,
        "expected_account_email": None,
        "expected_generation": 0,
        "authuser": 3,
        "account_email": "late@example.com",
    }
    kwargs.update(stale)

    assert auth._replace_profile_session(**kwargs) is None
    assert dict(target) == before_cookies
    assert (auth.authuser, auth.account_email) == (0, None)
    assert auth._profile_session_generation == 0


def test_generation_install_has_no_await_boundary() -> None:
    """The install is synchronous, so cancellation cannot tear a generation."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(AuthTokens._replace_profile_session)))
    assert not [node for node in ast.walk(tree) if isinstance(node, ast.Await)], (
        "an await inside the install would let a cancelled task leave cookies "
        "from one generation beside a route from another"
    )
    assert not inspect.iscoroutinefunction(AuthTokens._replace_profile_session)


def test_todays_atomic_read_is_two_mechanisms_not_one_generation() -> None:
    """Record the gap P8 closes: the frozen snapshot carries no cookie axis.

    ``AuthSnapshot`` is immutable and covers the four routing/token scalars
    under the snapshot lock. The cookie axis is aligned separately, by the
    no-await rule at the transport terminal. P8's immutable generation is where
    those two mechanisms become one value; until then this test states the
    current split explicitly so the merge is a deliberate change.
    """
    snapshot_fields = {field.name for field in dataclasses.fields(AuthSnapshot)}
    assert snapshot_fields == {"csrf_token", "session_id", "authuser", "account_email"}
    assert "cookies" not in snapshot_fields and "cookie_jar" not in snapshot_fields

    snapshot = AuthSnapshot(csrf_token="c", session_id="s", authuser=0, account_email=None)
    with pytest.raises(FrozenInstanceError):
        snapshot.authuser = 1  # type: ignore[misc]

    # The lock that makes the four scalars coherent is distinct from the
    # single-flight lock; P8 must not collapse them while merging the axes.
    coordinator = AuthRefreshCoordinator()
    assert coordinator._auth_snapshot_lock is None
    assert coordinator._refresh_lock is None


@pytest.mark.asyncio
async def test_coordinator_serializes_route_install_against_snapshot_reads() -> None:
    """Snapshot reads and route installs share one lock, so neither tears."""
    auth = _make_auth()
    target = auth.cookie_jar
    assert target is not None
    coordinator = AuthRefreshCoordinator()

    changed = await coordinator.install_profile_session(
        auth=auth,
        target_cookie_jar=target,
        source_cookie_jar=_jar(SID="rotated-sid"),
        expected_cookie_jar=CookieJar.from_httpx(target),
        expected_authuser=0,
        expected_account_email=None,
        expected_generation=0,
        authuser=4,
        account_email="owner@example.com",
    )
    assert changed is True

    snapshot = await coordinator.snapshot(auth=auth)
    assert (snapshot.authuser, snapshot.account_email) == (4, "owner@example.com")


# -----------------------------------------------------------------------------
# 2. AuthTokens identity and bootstrap compatibility
# -----------------------------------------------------------------------------


def test_auth_tokens_remains_the_bootstrap_and_compatibility_surface() -> None:
    """ADR-0032/0034 runway: the two public shadows stay, and stay in sync."""
    auth = _make_auth()
    replacement = _jar(SID="replacement-sid", OSID="replacement-osid")

    auth.replace_cookie_jar(replacement)

    assert auth.cookie_jar is replacement
    assert {key[0] for key in auth.cookies} == {"SID", "OSID"}
    assert auth.jar.names() == CookieJar.from_httpx(replacement).names()


def test_kernel_copies_the_bootstrap_shadow_instead_of_aliasing_it() -> None:
    """The runtime session owns its own jar container from the first moment.

    This is the ownership rule P8 generalizes: a provider generation is cloned
    into the backend's private session, never shared by reference.
    """
    auth = _make_auth()
    kernel = Kernel(auth=auth)

    assert kernel.cookies is not auth.cookie_jar
    assert dict(kernel.cookies) == dict(auth.cookie_jar or httpx.Cookies())

    kernel.cookies.set("EXTRA", "kernel-only", domain=".google.com", path="/")
    assert "EXTRA" not in dict(auth.cookie_jar or httpx.Cookies())


def test_cookie_generation_clone_gives_each_holder_its_own_container() -> None:
    """``_clone_cookie_jar`` is the existing copy-on-return P8 reuses."""
    source = _jar(SID="shared-sid")
    clone = _clone_cookie_jar(source)

    assert clone is not source
    assert clone.jar is not source.jar
    assert dict(clone) == dict(source)

    source.jar.clear()
    assert dict(clone) == {"SID": "shared-sid"}


@pytest.mark.asyncio
async def test_client_auth_identity_invariant_holds_across_the_graph() -> None:
    """ADR-0016: every consumer aliases the one mutable ``AuthTokens``."""
    from tests._helpers.client_factory import build_client_shell_for_tests

    auth = _make_auth()
    client = build_client_shell_for_tests(auth=auth)

    assert client.auth is auth
    assert client._auth is auth
    assert client.get_account_authuser() == auth.authuser


# -----------------------------------------------------------------------------
# 3. Provider / backend ownership and close rules
# -----------------------------------------------------------------------------


def test_backend_does_not_own_an_http_session_today() -> None:
    """Pre-P8 the backend borrows client-owned RPC and streamed-Chat collaborators."""
    backend = build_web_backend(object())

    assert backend.kind is BackendKind.WEB
    assert set(vars(backend)) == {
        "_executor",
        "_transport_factory",
        "_chat_transport",
        "_chat_reqid",
        "_chat_timeout",
        "_chat_response_max_bytes",
        "_source_uploader",
        "_capabilities",
        "_closed",
    }
    assert not any(
        isinstance(value, (httpx.AsyncClient, httpx.Cookies, Kernel))
        for value in vars(backend).values()
    )


@pytest.mark.asyncio
async def test_backend_close_does_not_close_dependencies_it_did_not_create() -> None:
    """An injected dependency is caller-owned; ``close()`` only closes the seam.

    P8 keeps this rule and extends it to the provider: an injected provider
    outlives the backend, and only a convenience factory closes one it built.
    """
    closed: list[str] = []

    class _RecordingExecutor:
        async def close(self) -> None:  # pragma: no cover - must never run
            closed.append("executor")

    backend = build_web_backend(_RecordingExecutor())

    await backend.close()
    assert closed == []
    assert backend._closed is True

    # Idempotent: closing twice is not an error and still closes nothing else.
    await backend.close()
    assert closed == []


@pytest.mark.asyncio
async def test_backend_rejects_work_after_close() -> None:
    """A closed backend fails loudly rather than reviving its session."""
    from notebooklm._operations import Operation
    from notebooklm._records import NotebookListInput
    from notebooklm._web.registry import WEB_OPERATION_REGISTRY

    backend = build_web_backend(object())
    await backend.close()

    definition = WEB_OPERATION_REGISTRY[Operation.NOTEBOOK_LIST].definition
    with pytest.raises(BackendContractError, match="closed"):
        await backend.invoke(definition, NotebookListInput(), deadline=None)


def test_client_members_needing_a_p8_owner_are_still_client_owned() -> None:
    """The auth-shaped client members P8 must re-home all still exist here.

    P7's acceptance criteria require every public client member to keep an
    owner. These five are the auth/provider subset: P8 decides whether each is
    served by the provider, the backend, or the client facade.
    """
    for name in (
        "auth",
        "refresh_auth",
        "get_account_email",
        "get_account_authuser",
        "close",
    ):
        assert hasattr(NotebookLMClient, name), f"{name} lost its owner before P8"

    refresh_signature = inspect.signature(NotebookLMClient.refresh_auth)
    assert "allow_headless" in refresh_signature.parameters
    assert refresh_signature.parameters["allow_headless"].kind is inspect.Parameter.KEYWORD_ONLY


# -----------------------------------------------------------------------------
# 4. ``from_storage`` ownership
# -----------------------------------------------------------------------------


class _FakeClient:
    """Minimal client stand-in that records its own lifecycle calls."""

    def __init__(self) -> None:
        self.entered = 0
        self.exited = 0

    async def __aenter__(self) -> _FakeClient:
        self.entered += 1
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self.exited += 1


class _StubbedFromStorageContext(_FromStorageContext):
    """``_FromStorageContext`` with the auth-loading build replaced.

    ``_FromStorageContext`` uses ``__slots__``, so the build seam is overridden
    by subclassing rather than by attribute assignment — which also keeps the
    real ``__aenter__``/``__aexit__`` ownership logic under test.
    """

    __slots__ = ()

    async def _build(self) -> Any:
        assert self._client is not None
        return self._client


def _context_over(client: _FakeClient) -> _FromStorageContext:
    context = _StubbedFromStorageContext(NotebookLMClient)
    context._client = client  # type: ignore[assignment]
    return context


@pytest.mark.asyncio
async def test_from_storage_closes_only_the_client_it_opened() -> None:
    """The convenience factory owns exactly what it constructed and entered."""
    client = _FakeClient()
    context = _context_over(client)

    async with context:
        assert client.entered == 1
        assert context._owns_close is True

    assert client.exited == 1


@pytest.mark.asyncio
async def test_from_storage_does_not_close_a_client_it_only_built() -> None:
    """The legacy await path hands ownership to the caller."""
    client = _FakeClient()
    context = _context_over(client)

    built = await context._build()
    assert built is client
    assert context._owns_close is False

    await context.__aexit__(None, None, None)
    assert client.exited == 0, "a built-but-unentered client is the caller's to close"


def test_from_storage_preserves_the_legacy_web_client_construction_path() -> None:
    """P8 keeps ``from_storage`` constructing and owning provider + backend."""
    assert isinstance(NotebookLMClient.from_storage(profile="unused"), _FromStorageContext)
    assert set(_FromStorageContext.__slots__) == {"_cls", "_kwargs", "_client", "_owns_close"}


# -----------------------------------------------------------------------------
# 5. Profile paths, locking, CAS, atomic writes, permissions
# -----------------------------------------------------------------------------


def test_profile_lock_siblings_stay_four_distinct_files() -> None:
    """The bootstrap lock is held across the storage acquire; they cannot merge."""
    base = Path("/tmp/nblm-profile/storage_state.json")
    derived = [
        auth_paths._storage_state_lock_path(base),
        auth_paths._rotation_lock_path(base),
        auth_paths._refresh_lock_path(base),
        auth_paths._bootstrap_lock_path(base),
    ]

    assert len(set(derived)) == 4
    assert all(
        path is not None and path.name.startswith(".storage_state.json.") for path in derived
    )
    assert auth_paths._rotation_lock_path(None) is None
    assert auth_paths._refresh_lock_path(None) is None


def test_canonical_storage_key_collapses_spellings_of_one_profile(tmp_path: Path) -> None:
    """Coalescing keys off a canonical path is what makes single-flight single."""
    profile = tmp_path / "storage_state.json"
    profile.write_text("{}", encoding="utf-8")

    direct = auth_paths.canonical_storage_key(profile)
    indirect = auth_paths.canonical_storage_key(tmp_path / "." / "storage_state.json")

    assert direct == indirect
    assert auth_paths.canonical_storage_key(None) is None


def test_bare_atomic_write_refuses_the_profile_document(tmp_path: Path) -> None:
    """CAS discipline: a lockless write to ``storage_state.json`` is refused."""
    with pytest.raises(ValueError, match="storage_state.json"):
        atomic_write_json(tmp_path / "storage_state.json", {"cookies": []})
    # Case-insensitive, because APFS/NTFS resolve casings to the same file.
    with pytest.raises(ValueError, match="storage_state.json"):
        atomic_write_json(tmp_path / "Storage_State.JSON", {"cookies": []})


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_credential_writes_land_atomically_at_owner_only_permissions(tmp_path: Path) -> None:
    """Credential documents are written 0o600 and leave no temp file behind."""
    target = tmp_path / "master_token.json"
    _atomic_write_json_unchecked(target, {"secret": "value"})

    assert json.loads(target.read_text(encoding="utf-8")) == {"secret": "value"}
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
    assert sorted(p.name for p in tmp_path.iterdir()) == ["master_token.json"]


# -----------------------------------------------------------------------------
# 6. Refresh single-flight, generation fencing, recovery, master-token reuse
# -----------------------------------------------------------------------------


@pytest.fixture
def flight() -> Iterator[single_flight.SingleFlight]:
    """A clean process-global single-flight registry for one test."""
    single_flight._reset_for_tests()
    yield single_flight.SingleFlight.process_default()
    single_flight._reset_for_tests()


@pytest.mark.asyncio
async def test_refresh_is_single_flight_across_concurrent_callers(
    flight: single_flight.SingleFlight,
) -> None:
    """N concurrent callers share ONE leader; followers join the same flight."""
    started = 0
    release = asyncio.Event()

    async def _work() -> str:
        nonlocal started
        started += 1
        await release.wait()
        return "refreshed"

    key = ("/profile/storage_state.json", "refresh")
    leader_flag, leader = flight.claim(key, _work)
    follower_flag, follower = flight.claim(key, _work)

    assert leader_flag is True and follower_flag is False
    assert follower is leader

    release.set()
    assert await flight.await_flight(leader) == "refreshed"
    assert started == 1


@pytest.mark.asyncio
async def test_a_stale_epoch_claim_is_refused_under_one_lock_hold(
    flight: single_flight.SingleFlight,
) -> None:
    """Generation fence: a waiter whose epoch went stale does no redundant work."""
    path_key = "/profile/storage_state.json"
    epoch_before = flight.read_success_epoch(path_key)

    flight.note_success(path_key)  # a sibling refreshed while we waited

    async def _never() -> str:  # pragma: no cover - must never run
        raise AssertionError("a stale-epoch claim must not spawn work")

    refused = flight.claim_if_epoch_current(
        (path_key, "refresh"),
        _never,
        path_key=path_key,
        epoch_before=epoch_before,
    )
    assert refused is None
    assert flight.read_success_epoch(path_key) == epoch_before + 1

    # A caller that captured the CURRENT epoch is still allowed to lead.
    async def _work() -> str:
        return "ok"

    claimed = flight.claim_if_epoch_current(
        (path_key, "refresh"),
        _work,
        path_key=path_key,
        epoch_before=flight.read_success_epoch(path_key),
    )
    assert claimed is not None
    is_leader, live = claimed
    assert is_leader is True
    assert await flight.await_flight(live) == "ok"


@pytest.mark.asyncio
async def test_a_failed_flight_does_not_bump_the_epoch(
    flight: single_flight.SingleFlight,
) -> None:
    """Only success advances the generation, so failures stay retryable."""
    path_key = "/profile/storage_state.json"

    async def _boom() -> str:
        raise RuntimeError("refresh failed")

    _leader, live = flight.claim((path_key, "refresh"), _boom)
    with pytest.raises(RuntimeError, match="refresh failed"):
        await flight.await_flight(live)

    assert flight.read_success_epoch(path_key) == 0


def test_recovery_and_master_token_rungs_have_single_owners() -> None:
    """P8 adapts the existing ladder; it does not re-implement any rung."""
    from notebooklm._auth import recovery
    from notebooklm._auth.master_token_bootstrap import MasterTokenBootstrapper

    for rung in ("try_storage_cookie_reload", "try_headless_reauth", "try_master_token_reauth"):
        assert inspect.iscoroutinefunction(getattr(recovery, rung)), rung

    # The audited transaction owner, not a set of primitives the caller assembles.
    for transaction in (
        "bootstrap_from_oauth_token",
        "remint_from_stored_token",
        "bootstrap_storage",
    ):
        assert inspect.iscoroutinefunction(getattr(MasterTokenBootstrapper, transaction)), (
            transaction
        )


# -----------------------------------------------------------------------------
# 7. Account routing
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("authuser", "email", "expected"),
    [
        (0, None, "0"),
        (3, None, "3"),
        (0, "owner@example.com", "owner@example.com"),
        (3, "owner@example.com", "owner@example.com"),
        (3, "  ", "3"),
        (3, "  owner@example.com  ", "owner@example.com"),
    ],
)
def test_account_route_prefers_the_stable_email_over_the_index(
    authuser: int, email: str | None, expected: str
) -> None:
    """Email wins because Google account indices move when accounts sign out."""
    assert format_authuser_value(authuser, email) == expected
    assert _make_auth(authuser=authuser, account_email=email).account_route == expected


def test_account_route_query_is_url_encoded() -> None:
    """The route reaches the wire through one encoder, not string formatting."""
    assert authuser_query(0, "owner+tag@example.com") == "authuser=owner%2Btag%40example.com"
    assert authuser_query(2, None) == "authuser=2"


# -----------------------------------------------------------------------------
# 8. Secret redaction
# -----------------------------------------------------------------------------


def test_the_bootstrap_surface_redacts_every_credential_axis() -> None:
    """A generation is credential-bearing; its repr must leak no axis of it."""
    auth = AuthTokens(
        cookies={("SID", ".google.com", "/"): "sid-secret-value"},
        csrf_token="csrf-secret-value",
        session_id="session-secret-value",
        authuser=2,
        account_email="owner@example.com",
    )

    rendered = repr(auth)

    for secret in ("sid-secret-value", "csrf-secret-value", "session-secret-value"):
        assert secret not in rendered
    # Non-credential identity survives, so the repr still says which profile.
    assert "authuser=2" in rendered
    assert "owner@example.com" in rendered
    assert "<1 redacted>" in rendered
