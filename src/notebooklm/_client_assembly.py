"""Single client-assembly seam shared by production and the test factory.

:func:`_assemble_client` is the ONE place that wires a
:class:`~notebooklm.client.NotebookLMClient` instance: auth normalization,
seam resolution, collaborator composition (via
:func:`notebooklm._runtime.init.compose_client_internals`), the upload
pipeline, and every feature API. Two callers exist:

1. ``NotebookLMClient.__init__`` (production) — delegates its whole body
   here, passing only its public kwargs.
2. ``tests/_helpers/client_factory.build_client_shell_for_tests`` — calls
   ``NotebookLMClient.__new__`` and then this function with the
   test-only injection seams (``decode_response`` / ``sleep`` /
   ``is_auth_error`` / ``async_client_factory`` plus ``refresh_callback`` /
   ``refresh_retry_delay`` / ``connect_timeout`` /
   ``keepalive_storage_path``).

History: the test factory previously duplicated this wiring by hand
against ``NotebookLMClient.__new__``. That drifted twice — issue #1196
(the open-time upload-semaphore loop reset needed ``_source_uploader``)
and issue #1225 (the open-time ChatAPI conversation-lock reset needed
``chat``) — each time silently stranding the shell until a test happened
to exercise the missing attribute. Sharing one assembly function makes
that whole drift class structurally impossible;
``tests/_guardrails/test_client_factory_parity.py`` pins the remaining
edges (attributes added *outside* this function).

This module is private: it is not exported from ``notebooklm`` and the
test-only parameters MUST NOT be promoted to ``NotebookLMClient``'s
public constructor (see the seam policy in ``_client_seams``).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from ._artifacts import ArtifactsAPI
from ._chat import ChatAPI
from ._client_seams import resolve_client_seams
from ._collections import CollectionsAPI
from ._deadline import RuntimeDeadlineFactory
from ._labels import LabelsAPI
from ._mind_map import NoteBackedMindMapService
from ._mind_maps_api import MindMapsAPI
from ._note_service import LegacyNoteBackedService, NoteService
from ._notebooks import NotebooksAPI
from ._notes import NotesAPI
from ._research import ResearchAPI
from ._runtime.config import (
    AUTO_READ_TIMEOUT,
    DEFAULT_CHAT_RESPONSE_MAX_BYTES,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_KEEPALIVE_MIN_INTERVAL,
    DEFAULT_MAX_CONCURRENT_RPCS,
    DEFAULT_MAX_CONCURRENT_UPLOADS,
    DEFAULT_TIMEOUT,
    resolve_chat_read_timeout,
    validate_read_timeout_kwarg,
)
from ._runtime.init import compose_client_internals
from ._runtime.lifecycle import CookieRotator, CookieSaver
from ._settings import SettingsAPI
from ._sharing import SharingAPI
from ._sharing_manager import ShareManager
from ._source.upload import SourceUploadPipeline
from ._sources import SourcesAPI
from ._studio import MindMapFamilyService, StudioCatalog
from ._web.backend import WebRpcBackend
from .auth import AuthTokens

if TYPE_CHECKING:
    from ._middleware.core import NextCall
    from .client import NotebookLMClient
    from .types import ConnectionLimits, RpcTelemetryEvent


class _UnsetType:
    """Sentinel type: resolve the production default inside ``_assemble_client``.

    Used where ``None`` is itself a meaningful caller value
    (``refresh_callback=None`` means "no refresh callback";
    ``keepalive_storage_path=None`` skips the constructor-level
    canonicalization and lets ``compose_client_internals`` apply its own
    raw ``auth.storage_path`` fallback — the historical test-shell
    behavior), so the production default ("use ``client.refresh_auth``" /
    "derive the canonicalized path from ``auth.storage_path``") needs a
    distinct marker.
    """


_UNSET = _UnsetType()


def _assemble_client(
    client: NotebookLMClient,
    *,
    auth: AuthTokens,
    timeout: float = DEFAULT_TIMEOUT,
    storage_path: Path | None = None,
    keepalive: float | None = None,
    keepalive_min_interval: float = DEFAULT_KEEPALIVE_MIN_INTERVAL,
    rate_limit_max_retries: int = 3,
    server_error_max_retries: int = 3,
    limits: ConnectionLimits | None = None,
    max_concurrent_uploads: int | None = DEFAULT_MAX_CONCURRENT_UPLOADS,
    max_concurrent_rpcs: int | None = DEFAULT_MAX_CONCURRENT_RPCS,
    upload_timeout: httpx.Timeout | None = None,
    on_rpc_event: Callable[[RpcTelemetryEvent], object] | None = None,
    cookie_saver: CookieSaver | None = None,
    cookie_rotator: CookieRotator | None = None,
    chat_timeout: float | None = AUTO_READ_TIMEOUT,
    import_research_timeout: float | None = AUTO_READ_TIMEOUT,
    chat_response_max_bytes: int | None = DEFAULT_CHAT_RESPONSE_MAX_BYTES,
    # --- Production-default overrides (test factory only) -----------------
    # ``NotebookLMClient.__init__`` never passes these; the sentinels
    # resolve to the exact behavior the constructor had when this logic
    # lived inline. The test factory forwards its caller's values
    # explicitly to preserve the historical shell semantics (e.g.
    # ``refresh_callback=None`` → no auth refresh coordination).
    refresh_callback: Callable[[], Awaitable[AuthTokens]] | None | _UnsetType = _UNSET,
    refresh_retry_delay: float = 0.2,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    keepalive_storage_path: Path | None | _UnsetType = _UNSET,
    # --- Test-only injection seams (see ``_client_seams`` docstring) ------
    decode_response: Callable[..., Any] | None = None,
    sleep: Callable[[float], Awaitable[Any]] | None = None,
    is_auth_error: Callable[[Exception], bool] | None = None,
    async_client_factory: Callable[..., httpx.AsyncClient] | None = None,
    authed_post_terminal: NextCall | None = None,
) -> None:
    """Wire every constructor-set attribute onto ``client``.

    This is the production assembly path — ``NotebookLMClient.__init__``
    is a thin delegate to this function — and simultaneously the seam the
    canonical test factory builds on, so the two can never drift apart
    (incidents #1196 / #1225). Any new constructor-time attribute MUST be
    set here, not in ``__init__`` after the delegation call; the parity
    gate ``tests/_guardrails/test_client_factory_parity.py`` fails
    otherwise.
    """
    # Normalize the effective storage path onto the auth object so every
    # downstream code path (refresh_auth, lifecycle on-close save,
    # the keepalive loop) writes to the same file. Without this, an
    # explicit ``storage_path=`` kwarg only reaches the keepalive loop
    # while ``auth.storage_path is None`` causes refresh and on-close
    # saves to silently skip persistence. ``dataclasses.replace`` instead
    # of in-place mutation so a caller reusing ``AuthTokens`` across
    # multiple clients (with different storage paths) doesn't see one
    # client's path leak into another.
    # Type-coerce only (``Path(...)``) — deliberately NOT
    # ``expanduser().resolve()``: the caller-provided ``storage_path`` and
    # ``auth.storage_path`` stay as supplied (see the keepalive NOTE
    # below); without the coercion a ``str`` argument would compare
    # unequal to an identical ``Path`` and bind a raw ``str`` onto
    # ``auth.storage_path``.
    if storage_path is not None:
        storage_path = Path(storage_path)
        if auth.storage_path != storage_path:
            auth = dataclasses.replace(auth, storage_path=storage_path)

    # ``auth`` is handed once to the backend-owned runtime and every
    # auth-sensitive leaf captures that identical mutable object. The public
    # client no longer publishes a second protocol-runtime owner.

    # Production default: the client's own ``refresh_auth`` bound method.
    # The test factory overrides this (typically with ``None`` or a fake)
    # to keep shells network-free.
    if isinstance(refresh_callback, _UnsetType):
        refresh_callback = client.refresh_auth

    # Canonicalize the keepalive storage path so different representations
    # of the same physical file (relative vs absolute, ``~`` shorthand,
    # symlink components) hash to the same key in the in-process rotation
    # dedupe (``_get_poke_lock`` / ``_try_claim_rotation`` /
    # ``_rotation_lock_path`` in auth.py). The auth refresh path already
    # canonicalizes at ``auth.py:_fetch_tokens_with_refresh`` via
    # ``Path(p).expanduser().resolve()``; this mirrors it so two clients
    # pointing at the same file via different path syntaxes share one
    # ``_LAST_POKE_ATTEMPT_MONOTONIC`` entry instead of bypassing dedupe
    # and firing duplicate ``RotateCookies`` POSTs.
    # NOTE: the public ``storage_path`` argument and ``auth.storage_path``
    # are intentionally left as the caller provided them — only the
    # internal-derived keepalive storage path is canonicalized. The test
    # factory passes its own ``keepalive_storage_path`` explicitly, which
    # bypasses THIS canonicalizing derivation (preserving the historical
    # shell semantics); an explicit ``None`` still falls through to
    # ``compose_client_internals``' own raw ``auth.storage_path``
    # fallback downstream.
    if isinstance(keepalive_storage_path, _UnsetType):
        derived_keepalive_path: Path | None = auth.storage_path
        if derived_keepalive_path is not None:
            derived_keepalive_path = Path(derived_keepalive_path).expanduser().resolve()
        keepalive_storage_path = derived_keepalive_path

    # Cross-validate the RPC throttle against the underlying httpx pool
    # before the collaborator builder swallows the ``limits=None``
    # sentinel into its own ``ConnectionLimits()`` synthesis.
    # Performed here so the constraint is enforced uniformly regardless
    # of whether the caller passed an explicit ``ConnectionLimits``
    # instance or relied on the default — scalar config validation
    # can't see the caller's intent once the default has been substituted.
    # Skip when either side opts out (``max_concurrent_rpcs is None``
    # means "no gate"; we deliberately don't second-guess the caller's
    # external-throttle setup).
    if max_concurrent_rpcs is not None:
        from .types import ConnectionLimits

        effective_limits = limits if limits is not None else ConnectionLimits()
        if max_concurrent_rpcs > effective_limits.max_connections:
            raise ValueError(
                "max_concurrent_rpcs must be <= limits.max_connections "
                f"(got max_concurrent_rpcs={max_concurrent_rpcs}, "
                f"max_connections={effective_limits.max_connections}). "
                "A semaphore wider than the connection pool surfaces "
                "saturation as opaque httpx.PoolTimeout instead of "
                "clean back-pressure."
            )
    if chat_response_max_bytes is not None and chat_response_max_bytes < 1:
        raise ValueError(
            f"chat_response_max_bytes must be >= 1 when supplied (got {chat_response_max_bytes!r})"
        )
    # Both per-RPC read windows are validated here, at the one seam every
    # construction path funnels through (constructor, ``from_storage``, the
    # canonical test factory). A zero/negative window is accepted verbatim by
    # ``httpx.Timeout`` and would otherwise surface only as an instant,
    # unexplained transport timeout on every affected RPC (#2205).
    chat_timeout = validate_read_timeout_kwarg(chat_timeout, name="chat_timeout")
    import_research_timeout = validate_read_timeout_kwarg(
        import_research_timeout, name="import_research_timeout"
    )

    # This function is the only composition root. ``compose_client_internals``
    # returns a complete frozen construction receipt which WebRpcBackend
    # immediately unpacks before the client is published.
    #
    # The public NotebookLMClient kwarg surface is unchanged — the
    # five seam kwargs (``decode_response`` / ``sleep`` /
    # ``is_auth_error`` / ``async_client_factory`` /
    # ``authed_post_terminal``) live on ``compose_client_internals`` and
    # this private assembly function only.
    #
    # TEST-ONLY injection points: production passes ``None`` for all
    # three runtime seams here (and never supplies either construction
    # seam), so they always resolve to the
    # canonical module bindings. The non-``None`` paths exist solely
    # for deterministic test injection — see ``_client_seams`` module
    # docstring. Do not promote any of them to a public kwarg without
    # a production caller that varies them.
    client._seams = resolve_client_seams(
        decode_response=decode_response,
        sleep=sleep,
        is_auth_error=is_auth_error,
    )
    internals = compose_client_internals(
        auth=auth,
        timeout=timeout,
        connect_timeout=connect_timeout,
        refresh_callback=refresh_callback,
        refresh_retry_delay=refresh_retry_delay,
        keepalive=keepalive,
        keepalive_min_interval=keepalive_min_interval,
        keepalive_storage_path=keepalive_storage_path,
        rate_limit_max_retries=rate_limit_max_retries,
        server_error_max_retries=server_error_max_retries,
        limits=limits,
        max_concurrent_uploads=max_concurrent_uploads,
        max_concurrent_rpcs=max_concurrent_rpcs,
        on_rpc_event=on_rpc_event,
        # Injectable seams — pass-through to the lifecycle. A ``None`` cookie
        # saver selects the canonical typed store path; a ``None`` rotator
        # preserves its historical late-bound default.
        cookie_saver=cookie_saver,
        cookie_rotator=cookie_rotator,
        async_client_factory=async_client_factory,
        authed_post_terminal=authed_post_terminal,
        seams=client._seams,
    )
    # ADR-0014 Rule 2: the upload pipeline takes its three runtime
    # collaborators (``rpc`` + ``drain`` + ``lifecycle``) directly
    # instead of via a composite-runtime adapter. ``Kernel`` and
    # ``AuthMetadata`` continue to flow as separate parameters per
    # the ADR-0014 Rule 6 example. This assembly function is
    # the composition root that knows these internals;
    # ``SourcesAPI`` no longer reads them back off a broad host.
    source_uploader = SourceUploadPipeline(
        rpc=internals.executor,
        drain=internals.drain_tracker,
        lifecycle=internals.lifecycle,
        kernel=internals.backend_kernel,
        # Direct upload/Drive HTTP legs use the provider's reconciled-generation
        # transaction so a matching registration-RPC Set-Cookie is published
        # before their one immutable cookie/route value is cloned. Ordinary RPC
        # transport keeps its separate cached, lock-free generation read.
        generation_provider=internals.provider.reconciled_generation,
        generation_installer=internals.backend_kernel.install_generation,
        upload_timeout=upload_timeout,
        max_concurrent_uploads=max_concurrent_uploads,
        record_upload_queue_wait=internals.metrics.record_upload_queue_wait,
    )
    # The provider is a first-class compatibility owner outside ``_web``.
    # ``WebRpcBackend`` receives the same object only for close ownership; all
    # auth/account facade methods delegate here without teaching the backend
    # package about credential material.
    client._provider = internals.provider
    # Assemble the private semantic port once every backend-owned collaborator
    # is available. The resolved transport factory remains a construction
    # parameter rather than a backend kind/capability.
    client._backend = WebRpcBackend(
        internals.executor,
        transport_factory=internals.web_transport_factory,
        source_uploader=source_uploader,
        chat_transport=internals.transport,
        chat_reqid=internals.reqid,
        chat_timeout=resolve_chat_read_timeout(chat_timeout, timeout),
        chat_response_max_bytes=chat_response_max_bytes,
        # Match WebExecutionRuntime's established live timeout-provider
        # contract. Each semantic call captures the current client timeout
        # once; an already-started RuntimeDeadline remains immutable even if a
        # later test/internal reconfiguration changes the lifecycle scalar.
        deadline_factory=RuntimeDeadlineFactory(lambda: internals.lifecycle._timeout),
        metrics=internals.metrics,
        drain_tracker=internals.drain_tracker,
        reqid=internals.reqid,
        chain_host=internals.chain_host,
        provider=internals.provider,
        session=internals.backend_session,
        owns_provider=True,
    )
    # Hold the uploader as a first-class client attribute so the
    # open-time loop-affinity reset (issue #1196 upload variant) can
    # reach it independently of the ``client.sources`` feature surface:
    # the upload semaphore is a lazily-built loop-bound
    # ``asyncio.Semaphore`` that must be discarded on close→reopen, the
    # same as the RPC semaphore. ``__aenter__`` threads this into
    # ``ClientLifecycle.open`` which calls
    # ``set_bound_loop`` / ``reset_after_open`` on it.
    client._source_uploader = source_uploader
    # Per ADR-0014 Rule 3: simple features take their RpcCaller dependency
    # directly from the composition root's executor.
    client.sources = SourcesAPI(
        internals.executor,
        uploader=source_uploader,
        upload_timeout=upload_timeout,
        max_concurrent_uploads=max_concurrent_uploads,
        _backend=client._backend,
    )
    client.notebooks = NotebooksAPI(
        internals.executor,
        sources_api=client.sources,
        share_manager=ShareManager(backend=client._backend),
        _backend=client._backend,
    )
    # P6.3 note wiring keeps semantic NOTE_* ownership disjoint from the
    # deferred MIND_MAP_* slice. NotesAPI receives the backend-neutral
    # NoteService; note-backed artifact/mind-map callers retain the explicitly
    # named legacy RPC service until MindMapsAPI migrates.
    note_service = NoteService(backend=client._backend)
    legacy_note_backed = LegacyNoteBackedService(internals.executor)
    mind_maps = NoteBackedMindMapService(legacy_note_backed)
    # P5.8: the artifacts compatibility facade owns no native RPC authority.
    # It receives the semantic backend plus the drain/lifecycle collaborators
    # used by its lifecycle-terminal polling service.
    client.artifacts = ArtifactsAPI(
        drain=internals.drain_tracker,
        lifecycle=internals.lifecycle,
        notebooks=client.notebooks,
        mind_maps=mind_maps,
        note_service=legacy_note_backed,
        storage_path=storage_path,
        _backend=client._backend,
    )
    # P6.1: ChatAPI keeps loop-bound orchestration and client-local state, but
    # delegates all six semantic operations to the client-owned backend.
    client.chat = ChatAPI(
        backend=client._backend,
        loop_guard=internals.lifecycle,
        notebooks=client.notebooks,
        created_chat_sessions=client.notebooks,
    )
    client.notes = NotesAPI(
        notes=note_service,
        mind_maps=mind_maps,
    )
    # Unified mind-map surface over two semantic services. Note-backed flows
    # share the client-scoped NoteService; interactive flows use the Studio
    # family and its typed MIND_MAP_* bindings. The legacy adapter above remains
    # only for artifact/download compatibility outside MindMapsAPI.
    mind_map_catalog = StudioCatalog(backend=client._backend)
    mind_map_studio = MindMapFamilyService(
        backend=client._backend,
        catalog=mind_map_catalog,
        wait_for_completion=client.artifacts.wait_for_completion,
    )
    client.mind_maps = MindMapsAPI(
        notes=note_service,
        studio=mind_map_studio,
    )
    # Research runs entirely on the semantic backend. Source reconciliation
    # receives the already-composed SourcesAPI explicitly; the facade owns no
    # RpcCaller compatibility dependency.
    client.research = ResearchAPI(
        source_lister=client.sources,
        base_timeout=timeout,
        import_research_timeout=import_research_timeout,
        _backend=client._backend,
    )
    client.settings = SettingsAPI(_backend=client._backend)
    # Sharing is fully migrated to the semantic backend: it takes the
    # client-owned adapter and no RpcCaller at all (P6.5).
    client.sharing = SharingAPI(_backend=client._backend)
    # Source labels. Takes a narrow ``list_sources`` callable (not the whole
    # SourcesAPI) for the membership->Source join in ``labels.sources()``;
    # wired after ``client.sources`` exists. Same client/bound loop (ADR-0004).
    client.labels = LabelsAPI(client._backend, list_sources=client.sources.list)
    # Collections (account-level notebook groups). Takes a narrow ``list_notebooks``
    # callable for the membership->Notebook join in ``collections.notebooks()``;
    # wired after ``client.notebooks`` exists. Same client/bound loop (ADR-0004).
    client.collections = CollectionsAPI(client._backend, list_notebooks=client.notebooks.list)
