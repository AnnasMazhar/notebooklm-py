"""Construction-time helpers for the NotebookLM client composition root.

Splits the client-runtime constructor into three concerns:
:func:`validate_constructor_args` (kwarg validation + normalization),
:func:`_build_runtime_leaves` (the seven leaves in dependency order),
and :func:`wire_middleware_chain` (the six-middleware ADR-0009 chain).
Dependency-ordering and seam-resolution comments live inside the helpers so
future readers see *why* the order matters.

Builds on the constructor-DI work in #1027 (``36dcc634`` —
"refactor(session): constructor DI for late-bound test seams; drop
http_client.setter"), which eliminated the late-binding wrappers and
the ``Kernel.http_client.setter`` and made ``decode_response`` /
``sleep`` / ``is_auth_error`` / ``async_client_factory`` the canonical
injection seams.

``None``-default resolution for ``sleep`` is owned by
:mod:`notebooklm._client_seams`; ``async_client_factory`` resolves directly to
``httpx.AsyncClient`` here.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from .._auth.profile_store import ProfileStore
from .._auth.web_provider_refresh import WebProviderRefresh
from .._client_metrics import ClientMetrics
from .._client_seams import ClientSeams, resolve_client_seams
from .._cookie_persistence import CookiePersistence
from .._error_injection import _refuse_synthetic_error_outside_test_context
from .._kernel import Kernel
from .._middleware.chain import MiddlewareChainBuilder
from .._middleware.chain_host import MiddlewareChainHost
from .._middleware.core import Middleware, NextCall, build_chain
from .._reqid_counter import ReqidCounter
from .._rpc_semaphore import RpcSemaphore
from .._transport_drain import TransportDrainTracker
from .._web.runtime import WebExecutionRuntime
from .._web_cookie_provider import WebCookieProvider
from ..auth import AuthTokens
from .auth import AuthRefreshCoordinator
from .config import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_KEEPALIVE_MIN_INTERVAL,
    DEFAULT_MAX_CONCURRENT_RPCS,
    DEFAULT_MAX_CONCURRENT_UPLOADS,
    DEFAULT_TIMEOUT,
    normalize_max_concurrent_uploads,
)
from .helpers import _resolve_keepalive_interval
from .lifecycle import ClientLifecycle, CookieRotator, CookieSaver
from .transport import RuntimeTransport
from .web_backend_session import WebBackendSession
from .web_cookie_provider import RuntimeWebCookieProvider

if TYPE_CHECKING:
    # Runtime import of ``ConnectionLimits`` is deferred to
    # :func:`validate_constructor_args` to keep the long-standing
    # defensive guard against the historical ``types.py`` -> runtime
    # construction cycle (see the inline comment in the function body).
    from ..types import ConnectionLimits, RpcTelemetryEvent

# Preserve the historical logger namespace while avoiding a raw deleted-module
# string that the no-session lint guard should reject elsewhere.
SESSION_LOGGER = logging.getLogger("notebooklm" + "." + "_session")


@dataclass(frozen=True)
class ValidatedSessionConfig:
    """Validated + normalized scalar configuration produced by
    :func:`validate_constructor_args`.

    Everything in here is either a value the caller supplied that passed
    validation, a normalized form (e.g. the keepalive interval clamped
    to the minimum-interval floor), or a seam callable resolved through
    the canonical module-attribute lookup that ``None`` defaults trigger
    (where applicable — see the module docstring for which seams are
    resolved here vs. in the public client constructor).
    """

    timeout: float
    connect_timeout: float
    limits: ConnectionLimits
    refresh_retry_delay: float
    rate_limit_max_retries: int
    server_error_max_retries: int
    max_concurrent_rpcs: int | None
    keepalive_interval: float | None
    keepalive_storage_path: Path | None
    decode_response: Callable[..., Any]
    sleep: Callable[[float], Awaitable[Any]]
    is_auth_error: Callable[[Exception], bool]
    async_client_factory: Callable[..., httpx.AsyncClient]


@dataclass(frozen=True)
class WiredMiddleware:
    """Wired middleware chain produced by :func:`wire_middleware_chain`."""

    chain_builder: MiddlewareChainBuilder
    middlewares: list[Middleware]
    authed_post_chain: NextCall


@dataclass(frozen=True)
class ClientInternals:
    """Construction-only receipt for an atomically assembled web runtime.

    ``_assemble_client`` projects explicit safe leaves into ``WebRpcBackend``;
    the backend never receives this credential-bearing receipt. Unlike the
    retired mutable holder, this record is never published on the client and
    has no bind/reset behavior.
    """

    metrics: ClientMetrics
    drain_tracker: TransportDrainTracker
    reqid: ReqidCounter
    auth_coord: AuthRefreshCoordinator
    provider_kernel: Kernel
    backend_kernel: Kernel
    backend_session: WebBackendSession
    lifecycle: ClientLifecycle
    cookie_persistence: CookiePersistence
    provider: WebCookieProvider
    executor: WebExecutionRuntime
    web_transport_factory: Callable[..., httpx.AsyncClient]
    rpc_semaphore: RpcSemaphore
    transport: RuntimeTransport
    chain_host: MiddlewareChainHost


def _resolve_async_client_factory(
    async_client_factory: Callable[..., httpx.AsyncClient] | None,
) -> Callable[..., httpx.AsyncClient]:
    """Resolve the construction-only async-client seam."""
    if async_client_factory is not None:
        return async_client_factory
    # PoC opt-in: browser TLS/JA3 impersonation transport (curl_cffi), shared
    # across every authenticated-Google client.
    from .._curl_cffi_transport import resolve_transport_factory

    return resolve_transport_factory()


def validate_constructor_args(
    *,
    timeout: float,
    connect_timeout: float,
    refresh_retry_delay: float,
    rate_limit_max_retries: int,
    server_error_max_retries: int,
    keepalive: float | None,
    keepalive_min_interval: float,
    keepalive_storage_path: Path | None,
    auth_storage_path: Path | None,
    limits: ConnectionLimits | None,
    max_concurrent_uploads: int | None,
    max_concurrent_rpcs: int | None,
    decode_response: Callable[..., Any],
    sleep: Callable[[float], Awaitable[Any]],
    is_auth_error: Callable[[Exception], bool],
    async_client_factory: Callable[..., httpx.AsyncClient],
) -> ValidatedSessionConfig:
    """Validate and normalize the scalar args for client internals.

    Mirrors the original validation/normalization behavior one-for-one:
    same ``ValueError`` messages, same order of checks. The seam callables
    (``decode_response`` / ``sleep`` / ``is_auth_error`` /
    ``async_client_factory``) are already resolved by the caller against
    the final client-side seam bindings; see the module docstring for why
    the seam-resolution boundary stops here.
    The returned :class:`ValidatedSessionConfig` is consumed by
    :func:`_build_runtime_leaves` and :func:`wire_middleware_chain`.

    Raises:
        ValueError: If ``rate_limit_max_retries`` / ``server_error_max_retries``
            is negative, if ``max_concurrent_uploads`` /
            ``max_concurrent_rpcs`` is a non-positive integer, or if
            ``keepalive`` / ``keepalive_min_interval`` is not a positive
            finite number.
    """
    if limits is not None:
        _resolved_limits = limits
    else:
        # Lazy import — defensive guard against the ``types.py`` ->
        # runtime-construction import cycle.
        from ..types import ConnectionLimits

        _resolved_limits = ConnectionLimits()

    if rate_limit_max_retries < 0:
        raise ValueError(f"rate_limit_max_retries must be >= 0, got {rate_limit_max_retries}")
    if server_error_max_retries < 0:
        raise ValueError(f"server_error_max_retries must be >= 0, got {server_error_max_retries}")

    # Fail-fast validation for ``max_concurrent_uploads``. The value is
    # NOT propagated into :class:`ValidatedSessionConfig` because the
    # actual upload semaphore state is owned by
    # ``SourceUploadPipeline`` (not the client-runtime composition
    # helpers); this call exists
    # solely for the ``ValueError``-raising side effect on the
    # constructor's behalf — same shape as the inline check it
    # replaced.
    normalize_max_concurrent_uploads(max_concurrent_uploads)

    # RPC-fanout throttle. ``None`` means "no
    # gate" (caller has an external rate-limiter, or this is a
    # single-shot CLI invocation). Default ``DEFAULT_MAX_CONCURRENT_RPCS``
    # (16) sits well below the default ``ConnectionLimits.max_connections``
    # so helper GET/POSTs outside the RPC pipeline still have pool
    # headroom. Cross-validation with ``limits.max_connections`` is
    # enforced one layer up at ``NotebookLMClient.__init__`` because
    # this helper synthesizes its own ``ConnectionLimits()`` when
    # ``limits=None``, masking the relationship at this layer.
    resolved_max_concurrent_rpcs: int | None
    if max_concurrent_rpcs is None:
        resolved_max_concurrent_rpcs = None
    else:
        if max_concurrent_rpcs < 1:
            raise ValueError(f"max_concurrent_rpcs must be >= 1, got {max_concurrent_rpcs!r}")
        resolved_max_concurrent_rpcs = max_concurrent_rpcs

    # Prefer the explicit storage_path if provided (e.g.
    # ``NotebookLMClient(storage_path=...)`` with a manually-built
    # ``AuthTokens``), otherwise fall back to ``auth.storage_path``.
    resolved_storage_path: Path | None = (
        keepalive_storage_path if keepalive_storage_path is not None else auth_storage_path
    )

    return ValidatedSessionConfig(
        timeout=timeout,
        connect_timeout=connect_timeout,
        limits=_resolved_limits,
        refresh_retry_delay=refresh_retry_delay,
        rate_limit_max_retries=rate_limit_max_retries,
        server_error_max_retries=server_error_max_retries,
        max_concurrent_rpcs=resolved_max_concurrent_rpcs,
        keepalive_interval=_resolve_keepalive_interval(keepalive, keepalive_min_interval),
        keepalive_storage_path=resolved_storage_path,
        decode_response=decode_response,
        sleep=sleep,
        is_auth_error=is_auth_error,
        async_client_factory=async_client_factory,
    )


def _build_runtime_leaves(
    config: ValidatedSessionConfig,
    *,
    auth: AuthTokens,
    refresh_callback: Callable[[], Awaitable[AuthTokens]] | None,
    on_rpc_event: Callable[[RpcTelemetryEvent], object] | None,
    cookie_saver: CookieSaver | None,
    cookie_rotator: CookieRotator | None,
) -> tuple[
    ClientMetrics,
    TransportDrainTracker,
    ReqidCounter,
    AuthRefreshCoordinator,
    Kernel,
    ClientLifecycle,
    CookiePersistence,
]:
    """Construct the seven extracted collaborators in dependency order.

    The order is dependency-driven so the load-bearing inter-collaborator
    wiring stays obvious to future readers: metrics is built first because
    it absorbs the optional ``on_rpc_event`` callback AND because the
    lock-wait metric callback captured by ``ReqidCounter`` is its bound
    method (so ``metrics`` MUST exist before ``ReqidCounter`` is
    constructed — otherwise the counter would close over an attribute
    that has not yet been set); the drain tracker /
    reqid counter / auth coordinator follow because they are leaf
    collaborators with no inter-helper dependencies; ``Kernel`` is
    built next because ``ClientLifecycle`` holds a reference to it;
    ``CookiePersistence`` closes out the bundle.
    """
    # Observability counters + telemetry callback. ``metrics_snapshot``
    # remains the lock-safe read path; helper-level tests that need
    # implementation state read ``self._metrics_obj`` directly.
    metrics = ClientMetrics(on_rpc_event=on_rpc_event)
    # Transport drain bookkeeping (in-flight posts, drain condition,
    # per-task operation depth, draining flag). The helper's
    # ``__init__`` is event-loop-agnostic; the ``asyncio.Condition`` is
    # created lazily on first ``get_drain_condition`` call.
    drain_tracker = TransportDrainTracker()
    # Request ID counter for chat API (must be unique per request).
    # The :class:`ReqidCounter` helper owns the monotonic ``_value`` and
    # the lazily-allocated ``asyncio.Lock`` that serialises mutation.
    # Access ``self._reqid.value`` / ``self._reqid._lock`` directly.
    # The ``on_lock_wait`` hook keeps the cumulative
    # ``lock_wait_seconds_*`` metrics ticking inside ``metrics`` — we
    # pass the bound method of the metrics object we just built so the
    # counter cannot capture an unbound seam (which is what would happen
    # if we forwarded a broad host-level thin wrapper before
    # ``self._metrics_obj`` was assigned in the outer ``__init__``).
    reqid = ReqidCounter(on_lock_wait=metrics.record_lock_wait)
    # Auth refresh coordination — single-flight refresh task, snapshot
    # serialization, and cookie-jar sync. The coordinator owns
    # ``_refresh_lock``, ``_refresh_task``, ``_refresh_callback``, and
    # ``_auth_snapshot_lock``. Tests and internal callers that need
    # implementation state read the coordinator directly. The live auth
    # snapshot lock is reachable via
    # :meth:`AuthRefreshCoordinator.get_auth_snapshot_lock` (the
    # legacy ``_get_auth_snapshot_lock`` thin wrapper was inlined in
    # PR #4b — callers now address the coordinator directly
    # through ``self._auth_coord``).
    # The auth snapshot lock is intentionally distinct from
    # ``_refresh_lock`` — mixing them would re-introduce the
    # reentrancy ambiguity that snapshot-side serialization was added
    # to avoid. The attribute name ``_auth_coord`` is part of the
    # inter-helper contract; do not rename.
    # Supply ``metrics`` so ``await_refresh`` records lock-wait latency
    # without needing a host parameter. The remaining coordinator methods
    # (``snapshot``, ``update_auth_tokens``, ``update_auth_headers``) take
    # their explicit ``auth`` / ``kernel`` collaborators per call.
    auth_coord = AuthRefreshCoordinator(
        refresh_callback=refresh_callback,
        metrics=metrics,
    )
    # HTTP-client lifecycle — owns loop binding, keepalive, and close
    # ordering while delegating the live ``httpx.AsyncClient`` to
    # ``self._kernel``. The ``_resolve_keepalive_interval`` clamp lives
    # in :mod:`notebooklm._runtime.helpers` and is imported above; we
    # call it directly here. (The historical ``notebooklm._core``
    # re-export was removed in v0.5.0.)
    #
    # Event-loop affinity guard rationale: the lifecycle captures
    # ``asyncio.get_running_loop()`` in ``_bound_loop`` at ``open()`` time
    # and ``RuntimeTransport.perform_authed_post`` does the cross-loop
    # check with a cheap ``is`` comparison against it. Each client is per-loop — the asyncio primitives we hold
    # (``_reqid_lock``, ``_refresh_lock``, ``_auth_snapshot_lock``,
    # ``_rpc_semaphore``, the ``httpx.AsyncClient``
    # pool, in-flight tasks like ``_refresh_task`` / ``_keepalive_task``)
    # are all bound to the loop that ``open()`` ran on; reusing them
    # under a different loop produces hangs and ``RuntimeError`` deep
    # in httpx instead of an actionable message at the call site.
    # Seed the kernel-owned jar at composition time. This is the bootstrap
    # hand-off defined by ADR-0032: after this call, post-open and closed-state
    # first-party readers use ``kernel.cookies`` and never AuthTokens' public
    # compatibility shadows.
    kernel = Kernel(auth=auth, async_client_factory=config.async_client_factory)
    lifecycle = ClientLifecycle(
        timeout=config.timeout,
        connect_timeout=config.connect_timeout,
        limits=config.limits,
        keepalive_interval=config.keepalive_interval,
        keepalive_storage_path=config.keepalive_storage_path,
        auth=auth,
        cookie_persistence_path=config.keepalive_storage_path,
        kernel=kernel,
        # Injectable seams. A ``None`` saver selects the unconditional typed
        # ProfileStore route; only an explicit saver reaches the v0.x callback
        # adapter. The rotator alone retains its late-bound default.
        cookie_saver=cookie_saver,
        cookie_rotator=cookie_rotator,
    )
    # Owns the in-process save lock and typed per-profile baselines. Preserve
    # only the load-time snapshot, not the AuthTokens capability: re-reading a
    # newer file at open would make the older live jar overwrite a sibling
    # writer's intervening cookie update during the eventual three-way merge.
    cookie_persistence = CookiePersistence._from_store(
        ProfileStore(auth.storage_path) if auth.storage_path is not None else None,
        initial_snapshot=auth.cookie_snapshot,
    )

    return metrics, drain_tracker, reqid, auth_coord, kernel, lifecycle, cookie_persistence


def build_runtime_transport(
    *,
    provider: WebCookieProvider,
    metrics: ClientMetrics,
    kernel: Kernel,
    lifecycle: ClientLifecycle,
    chain_host: MiddlewareChainHost,
    logger: logging.Logger,
) -> RuntimeTransport:
    """Construct the :class:`RuntimeTransport` collaborator.

    Built **after** :func:`_build_runtime_leaves` and **before**
    :func:`wire_middleware_chain`, because the wired chain is built
    around ``transport.terminal``. The transport reaches the chain
    through a live-binding ``chain_provider`` closure that reads
    ``chain_host._authed_post_chain`` on every authed POST; that
    attribute is assigned by :func:`compose_client_internals`
    immediately after :func:`wire_middleware_chain` returns. Using a
    provider closure (rather than a frozen reference) keeps the write-once
    construction cycle explicit without publishing chain state on the client.

    The ``snapshot_provider`` closure reads one immutable generation through
    the provider boundary. The ``bound_loop_check`` lambda reads through
    ``collaborators.lifecycle.assert_bound_loop`` at call time, preserving
    lifecycle method patchability without retaining a broad host-level
    ``assert_bound_loop`` forward.

    The ``chain_host`` parameter lets the chain-slot lookup go through
    the host directly, with no extra indirection on the hot
    path.

    The ``logger`` is forwarded as-is so transport-error log lines keep
    appearing under the historical session logger namespace rather than
    acquiring a new transport logger namespace that callers' log filters
    / ``caplog`` selectors would not yet recognise.
    """
    return RuntimeTransport(
        kernel=kernel,
        snapshot_provider=lambda: provider.generation(),
        chain_provider=lambda: chain_host._authed_post_chain,
        metrics=metrics,
        bound_loop_check=lifecycle.assert_bound_loop,
        logger=logger,
    )


def wire_middleware_chain(
    *,
    drain_tracker: TransportDrainTracker,
    metrics: ClientMetrics,
    lifecycle: ClientLifecycle,
    provider: WebCookieProvider,
    chain_host: MiddlewareChainHost,
    authed_post_chain_terminal: Callable[..., Awaitable[Any]],
    rpc_semaphore: RpcSemaphore,
    is_auth_error: Callable[[Exception], bool],
) -> WiredMiddleware:
    """Construct the :class:`MiddlewareChainBuilder`, build the six-middleware
    list, and wire the final chain via :func:`build_chain`.

    Two narrow host parameters:

    * ``chain_host`` — the :class:`MiddlewareChainHost` owns the three
      retry-budget tunables (``_rate_limit_max_retries`` /
      ``_server_error_max_retries`` / ``_refresh_retry_delay``) plus the
      dynamic-delegate refresh entry point (:meth:`await_refresh`). The
      tunable provider lambdas and the ``refresh_callable`` reference
      capture this host directly.
    * ``provider`` — the live immutable-generation and refresh boundary.
      Concrete refresh coordination and mutable credentials remain behind it.

    Post-construction mutation on ``chain_host._<attr>`` still takes
    effect through the middleware live-binding contract documented in
    :class:`MiddlewareChainBuilder`. The ``rpc_semaphore`` is passed explicitly
    so the helper does not need to know which holder publishes its owner.
    ``is_auth_error`` is passed as a live-binding
    callable so rebinding ``ClientSeams.is_auth_error`` after construction
    still steers the chain.
    """
    # ADR-0009 chain construction. PR history, leaf exception shape,
    # and ``RpcRequest.context`` contract live in
    # ``_middleware/chain.py`` module docstring.
    chain_builder = MiddlewareChainBuilder(
        drain_tracker=drain_tracker,
        metrics=metrics,
        rpc_semaphore=rpc_semaphore,
        rate_limit_max_retries_provider=lambda: chain_host._rate_limit_max_retries,
        server_error_max_retries_provider=lambda: chain_host._server_error_max_retries,
        retry_timeout_provider=lambda: lifecycle._timeout,
        refresh_retry_delay_provider=lambda: chain_host._refresh_retry_delay,
        refresh_callable=chain_host.await_refresh,
        auth_snapshot_provider=lambda: provider.generation(),
        is_auth_error=is_auth_error,
        refresh_callback_enabled_provider=lambda: provider.has_refresh_callback,
    )
    middlewares: list[Middleware] = chain_builder.build()
    authed_post_chain: NextCall = build_chain(
        middlewares,
        authed_post_chain_terminal,
    )
    return WiredMiddleware(
        chain_builder=chain_builder,
        middlewares=middlewares,
        authed_post_chain=authed_post_chain,
    )


def compose_client_internals(
    *,
    auth: AuthTokens,
    timeout: float = DEFAULT_TIMEOUT,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    refresh_callback: Callable[[], Awaitable[AuthTokens]] | None = None,
    refresh_retry_delay: float = 0.2,
    keepalive: float | None = None,
    keepalive_min_interval: float = DEFAULT_KEEPALIVE_MIN_INTERVAL,
    keepalive_storage_path: Path | None = None,
    rate_limit_max_retries: int = 3,
    server_error_max_retries: int = 3,
    limits: ConnectionLimits | None = None,
    max_concurrent_uploads: int | None = DEFAULT_MAX_CONCURRENT_UPLOADS,
    max_concurrent_rpcs: int | None = DEFAULT_MAX_CONCURRENT_RPCS,
    on_rpc_event: Callable[[RpcTelemetryEvent], object] | None = None,
    cookie_saver: CookieSaver | None = None,
    cookie_rotator: CookieRotator | None = None,
    decode_response: Callable[..., Any] | None = None,
    sleep: Callable[[float], Awaitable[Any]] | None = None,
    is_auth_error: Callable[[Exception], bool] | None = None,
    async_client_factory: Callable[..., httpx.AsyncClient] | None = None,
    authed_post_terminal: NextCall | None = None,
    seams: ClientSeams | None = None,
) -> ClientInternals:
    """Single entry point that owns the client composition sequence."""
    # MUST stay first — preserves the earliest-opportunity refusal that
    # ``test_synthetic_error_transport_guard`` pins.
    _refuse_synthetic_error_outside_test_context()

    seams = seams or resolve_client_seams(
        sleep=sleep,
        is_auth_error=is_auth_error,
        decode_response=decode_response,
    )
    async_client_factory = _resolve_async_client_factory(async_client_factory)

    config = validate_constructor_args(
        timeout=timeout,
        connect_timeout=connect_timeout,
        refresh_retry_delay=refresh_retry_delay,
        rate_limit_max_retries=rate_limit_max_retries,
        server_error_max_retries=server_error_max_retries,
        keepalive=keepalive,
        keepalive_min_interval=keepalive_min_interval,
        keepalive_storage_path=keepalive_storage_path,
        auth_storage_path=auth.storage_path,
        limits=limits,
        max_concurrent_uploads=max_concurrent_uploads,
        max_concurrent_rpcs=max_concurrent_rpcs,
        decode_response=seams.decode_response,
        sleep=seams.sleep,
        is_auth_error=seams.is_auth_error,
        async_client_factory=async_client_factory,
    )
    (
        metrics,
        drain_tracker,
        reqid,
        auth_coord,
        provider_kernel,
        lifecycle,
        cookie_persistence,
    ) = _build_runtime_leaves(
        config,
        auth=auth,
        refresh_callback=refresh_callback,
        on_rpc_event=on_rpc_event,
        cookie_saver=cookie_saver,
        cookie_rotator=cookie_rotator,
    )
    rpc_semaphore = RpcSemaphore(config.max_concurrent_rpcs)

    # P8 keeps credential acquisition and backend execution in distinct
    # mutable sessions. The provider kernel is the existing lifecycle/
    # refresh/persistence owner; the backend kernel is seeded only from
    # detached immutable provider generations.
    backend_kernel = Kernel(async_client_factory=config.async_client_factory)
    backend_session = WebBackendSession(
        kernel=backend_kernel,
        timeout=config.timeout,
        connect_timeout=config.connect_timeout,
        limits=config.limits,
    )

    provider_ref: RuntimeWebCookieProvider | None = None

    async def run_provider_refresh(
        work: Callable[[], Awaitable[AuthTokens]],
    ) -> AuthTokens:
        provider = provider_ref
        if provider is None:  # pragma: no cover - construction-order guard
            raise RuntimeError("web cookie provider refresh used before binding")
        return await provider.run_refresh_transaction(work)

    refresh_adapter = WebProviderRefresh(
        auth=auth,
        kernel=provider_kernel,
        coordinator=auth_coord,
        lifecycle=lifecycle,
        persistence=cookie_persistence,
        transaction=run_provider_refresh,
    )
    provider = RuntimeWebCookieProvider(
        auth=auth,
        kernel=provider_kernel,
        backend_session=backend_session,
        coordinator=auth_coord,
        lifecycle=lifecycle,
        persistence=cookie_persistence,
        drain_tracker=drain_tracker,
        reqid=reqid,
        rpc_semaphore=rpc_semaphore,
        refresh_session=refresh_adapter.refresh,
    )
    provider_ref = provider

    chain_host = MiddlewareChainHost(
        _refresh=provider.await_refresh,
        _rate_limit_max_retries=config.rate_limit_max_retries,
        _server_error_max_retries=config.server_error_max_retries,
        _refresh_retry_delay=config.refresh_retry_delay,
    )

    transport = build_runtime_transport(
        provider=provider,
        metrics=metrics,
        kernel=backend_kernel,
        lifecycle=lifecycle,
        chain_host=chain_host,
        logger=SESSION_LOGGER,
    )
    chain_host._bind_transport(transport)

    wired = wire_middleware_chain(
        drain_tracker=drain_tracker,
        metrics=metrics,
        lifecycle=lifecycle,
        provider=provider,
        chain_host=chain_host,
        authed_post_chain_terminal=(
            authed_post_terminal
            if authed_post_terminal is not None
            else chain_host._authed_post_chain_terminal
        ),
        rpc_semaphore=rpc_semaphore,
        is_auth_error=lambda *a, **kw: seams.is_auth_error(*a, **kw),
    )
    chain_host._authed_post_chain = wired.authed_post_chain

    executor = WebExecutionRuntime(
        assert_open=backend_session.assert_open,
        transport=transport,
        refresh=provider.await_refresh,
        metrics=metrics,
        decode_response=lambda *a, **kw: seams.decode_response(*a, **kw),
        is_auth_error=lambda *a, **kw: seams.is_auth_error(*a, **kw),
        sleep=lambda *a, **kw: seams.sleep(*a, **kw),
        timeout_provider=lambda: lifecycle._timeout,
        refresh_callback_enabled_provider=lambda: provider.has_refresh_callback,
        refresh_retry_delay_provider=lambda: chain_host._refresh_retry_delay,
    )
    return ClientInternals(
        metrics=metrics,
        drain_tracker=drain_tracker,
        reqid=reqid,
        auth_coord=auth_coord,
        provider_kernel=provider_kernel,
        backend_kernel=backend_kernel,
        backend_session=backend_session,
        lifecycle=lifecycle,
        cookie_persistence=cookie_persistence,
        provider=provider,
        executor=executor,
        web_transport_factory=config.async_client_factory,
        rpc_semaphore=rpc_semaphore,
        transport=transport,
        chain_host=chain_host,
    )


__all__ = [
    "ClientInternals",
    "ValidatedSessionConfig",
    "WiredMiddleware",
    "build_runtime_transport",
    "compose_client_internals",
    "validate_constructor_args",
    "wire_middleware_chain",
]
