"""Notebook operations API."""

import logging
from typing import Any

from ._backend import BackendAdapter, BackendError
from ._backend_compat import project_backend_error
from ._notebook_metadata import (
    NotebookMetadataService,
    NotebookSourceLister,
    create_default_source_lister,
)
from ._notebook_mutation_service import NotebookMutationService
from ._notebook_payloads import (
    _PROMPT_SUGGESTIONS_DEFAULT_MODE,
    build_get_notebook_params,
    build_prompt_suggestions_params,
)
from ._notebook_payloads import build_create_notebook_params as build_create_notebook_params
from ._projectors import project_notebook_description
from ._read_services import NotebookReadService
from ._row_adapters.notebooks import PromptSuggestionRow, unwrap_prompt_suggestions
from ._row_adapters.sources import SourceRow
from ._runtime.contracts import RpcCaller
from ._sharing_manager import ShareManager
from ._web.codec.notebooks import decode_notebook_description
from .exceptions import (
    ClientError,
    NotebookNotFoundError,
    ValidationError,
)
from .rpc import GrpcStatusCode, RPCMethod, normalize_grpc_status, safe_index
from .types import (
    Notebook,
    NotebookDescription,
    NotebookMetadata,
    PromptSuggestion,
    SuggestedTopic,
)

logger = logging.getLogger(__name__)


def _extract_summary(outer: Any) -> str:
    """Extract the summary string from a SUMMARIZE ``result[0]`` payload.

    The expected shape is ``[[summary_string, ...], ...]`` — i.e. the summary
    lives at ``outer[0][0]``. Only a genuinely *absent* summary is treated as
    routinely-optional: a brand-new, source-less notebook has no summary yet,
    so the server returns ``None`` at ``outer``, an empty ``outer``, or an
    explicitly-null summary slot (``outer[0] is None``). Those three shapes
    short-circuit to ``""`` so a healthy "no summary yet" response doesn't
    surface as schema drift.

    Everything else descends through ``safe_index``: a *present-but-malformed*
    payload — a scalar ``outer`` (e.g. ``123``), or a non-``None`` ``outer[0]``
    that isn't the expected ``[summary_string, ...]`` list — is genuine drift
    and raises ``UnknownRPCMethodError`` with method_id + source rather than
    silently becoming an empty summary (which would mask the wire-schema move).

    Returns:
        The summary string, or ``""`` when the payload omits the summary
        slot (the caller is responsible for treating an empty summary as
        "no description available").
    """
    # Genuinely-absent summary (no payload, empty payload, or null slot) is the
    # routine "no summary yet" case — return "" without logging drift.
    if outer is None:
        return ""
    if isinstance(outer, list) and (
        not outer
        or safe_index(
            outer, 0, method_id=RPCMethod.SUMMARIZE.value, source="_notebooks._extract_summary"
        )
        is None
    ):
        return ""
    # Descend outer[0][0] via safe_index. A scalar ``outer`` or a malformed
    # ``outer[0]`` (present, non-None, but not the expected list) raises drift
    # at the failing step rather than silently returning "".
    summary_val = safe_index(
        outer,
        0,
        0,
        method_id=RPCMethod.SUMMARIZE.value,
        source="_notebooks._extract_summary",
    )
    if summary_val is None:
        return ""
    return str(summary_val)


def _extract_suggested_topics(outer: Any) -> list[SuggestedTopic]:
    """Extract suggested topics from a SUMMARIZE ``result[0]`` payload.

    The expected shape is ``[..., [[[question, prompt, ...], ...], ...], ...]``
    — the topics list lives at ``outer[1][0]``, and each topic is itself a
    list whose first two entries are ``question`` and ``prompt``.

    The outer ``[1]`` slot is treated as routinely-optional (a notebook with
    no topics legitimately omits it, so missing-slot is not "drift"); the
    inner ``[0]`` descent goes through ``safe_index`` so genuine schema
    drift surfaces with method_id + source. Per-topic shape checks log a
    debug diagnostic and skip malformed entries rather than abort, because
    a partial response (some valid topics + some drift) is more useful to
    callers than an empty list.

    Returns:
        List of :class:`SuggestedTopic`. Empty when the payload omits the
        slot or when every topic entry fails shape validation.
    """
    # outer[1] is routinely absent/empty when a notebook has no topics;
    # use a plain guard rather than safe_index so that case doesn't log
    # a drift warning on every healthy "no topics" response. Still log
    # a DEBUG record so partial descriptions remain observable to anyone
    # tailing logs while diagnosing a notebook with missing topics.
    if not isinstance(outer, list) or len(outer) < 2:
        logger.debug("_extract_suggested_topics: Partial description — no outer[1] slot")
        return []

    topics_container = safe_index(
        outer, 1, method_id=RPCMethod.SUMMARIZE.value, source="_notebooks._extract_suggested_topics"
    )
    if not isinstance(topics_container, list) or len(topics_container) == 0:
        logger.debug(
            "_extract_suggested_topics: Partial description — outer[1] is empty or non-list"
        )
        return []

    topics_list = safe_index(
        topics_container,
        0,
        method_id=RPCMethod.SUMMARIZE.value,
        source="_notebooks._extract_suggested_topics",
    )
    if not isinstance(topics_list, list):
        if topics_list is not None:
            logger.debug(
                "_extract_suggested_topics: expected list at outer[1][0], got %s",
                type(topics_list).__name__,
            )
        return []

    topics: list[SuggestedTopic] = []
    for index, topic in enumerate(topics_list):
        if not isinstance(topic, list) or len(topic) < 2:
            logger.debug(
                "_extract_suggested_topics: skipping malformed topic at index %d (type=%s)",
                index,
                type(topic).__name__,
            )
            continue
        # ``topic`` is guarded to a list of len >= 2 above, so these slot reads
        # cannot fail; ``safe_index`` keeps the position knowledge on the
        # schema-drift seam without changing behaviour.
        question = safe_index(
            topic,
            0,
            method_id=RPCMethod.SUMMARIZE.value,
            source="_notebooks._extract_suggested_topics",
        )
        prompt = safe_index(
            topic,
            1,
            method_id=RPCMethod.SUMMARIZE.value,
            source="_notebooks._extract_suggested_topics",
        )
        topics.append(
            SuggestedTopic(
                question=str(question) if question else "",
                prompt=str(prompt) if prompt else "",
            )
        )
    return topics


class NotebooksAPI:
    """Operations on NotebookLM notebooks.

    Provides methods for listing, creating, getting, deleting, and renaming
    notebooks, as well as getting AI-generated descriptions.

    Usage:
        async with NotebookLMClient.from_storage() as client:
            notebooks = await client.notebooks.list()
            new_nb = await client.notebooks.create("My Research")
            await client.notebooks.rename(new_nb.id, "Better Title")
    """

    def __init__(
        self,
        rpc: RpcCaller,
        sources_api: NotebookSourceLister | None = None,
        *,
        metadata_service: NotebookMetadataService | None = None,
        share_manager: ShareManager | None = None,
        _backend: BackendAdapter | None = None,
    ) -> None:
        """Initialize the notebooks API.

        Args:
            rpc: RPC dispatch surface (typically the shared client session).
            sources_api: Optional source lister for cross-API metadata composition.
            metadata_service: Optional explicit metadata service for tests or advanced wiring.
            share_manager: Optional explicit legacy share manager for tests or advanced wiring.
            _backend: Private semantic backend supplied by the client composition root.
        """
        self._rpc = rpc
        self._read_service = NotebookReadService(_backend) if _backend is not None else None
        self._mutation_service = NotebookMutationService(_backend) if _backend is not None else None
        self._sources = sources_api or create_default_source_lister(self._rpc)
        self._metadata_service = metadata_service or NotebookMetadataService(
            # Keep notebook lookup late-bound so tests and advanced callers that
            # replace ``api.get`` after construction still affect get_metadata().
            get_notebook=lambda notebook_id: self.get(notebook_id),
            source_lister=self._sources,
        )
        self._share_manager = share_manager or ShareManager(self._rpc)
        # CREATE_NOTEBOOK volunteers its newly-created ChatSession, while
        # GET_NOTEBOOK omits it. Keep that one-shot hint until ChatAPI consumes
        # it so the first ask need not immediately re-fetch the same id through
        # hPTbtc (#2133). The cache is scoped to this client instance and each
        # entry is popped on first use; closing the client releases any hints
        # from notebooks that were created without a subsequent ask.
        self._created_chat_session_ids: dict[str, str] = {}

    def _require_read_service(self) -> NotebookReadService:
        """Return the composition-root service for the migrated read slice."""
        if self._read_service is None:
            raise RuntimeError("NotebooksAPI semantic read backend was not configured")
        return self._read_service

    def _require_mutation_service(self) -> NotebookMutationService:
        """Return the composition-root service for migrated notebook mutations."""
        if self._mutation_service is None:
            raise RuntimeError("NotebooksAPI semantic mutation backend was not configured")
        return self._mutation_service

    def _take_created_chat_session_id(self, notebook_id: str) -> str | None:
        """Consume CREATE_NOTEBOOK's volunteered current chat-session id."""
        return self._created_chat_session_ids.pop(notebook_id, None)

    async def _rpc_call(
        self,
        method: RPCMethod,
        params: list[Any],
        source_path: str = "/",
        allow_null: bool = False,
        _is_retry: bool = False,
        *,
        disable_internal_retries: bool = False,
        operation_variant: str | None = None,
    ) -> Any:
        """Delegate through the current RPC caller for late-bound overrides."""
        return await self._rpc.rpc_call(
            method,
            params,
            source_path=source_path,
            allow_null=allow_null,
            _is_retry=_is_retry,
            disable_internal_retries=disable_internal_retries,
            operation_variant=operation_variant,
        )

    async def get_source_ids(self, notebook_id: str) -> list[str]:
        """Extract all source IDs from a notebook.

        Fetches notebook data and extracts source IDs for use with chat and
        artifact generation when targeting specific sources.

        Args:
            notebook_id: The notebook ID.

        Returns:
            List of source IDs. Empty list when the notebook has no sources or
            when get_source_ids encounters a schema/validation mismatch while
            extracting IDs.

        Note:
            RPC, auth, and network errors raised by ``get_raw()`` propagate to
            the caller; only local source-shape validation failures are caught
            below and converted to an empty list. Per-row id-envelope
            decoding (including the drive-backed ``[None, True, [id]]``
            shape) is delegated to
            :class:`notebooklm._row_adapters.sources.SourceRow`; this method only
            performs the envelope walk down to ``notebook[0][1]``.
        """
        notebook_data = await self.get_raw(notebook_id)

        source_ids: list[str] = []
        if not notebook_data or not isinstance(notebook_data, list):
            return source_ids

        # Schema-drift detection points: log WARNING at each isinstance/len
        # guard that fails on a non-empty response (real drift surfaces here,
        # not at the safety-net except below).
        # ``notebook_data`` is a non-empty list here (guarded above), so the
        # ``[0]`` read cannot fail; the ``[1]`` read below is gated by
        # ``len(notebook_info) > 1``. Both descents route through ``safe_index``
        # — the sanctioned schema-drift seam — so position knowledge stays out
        # of open-coded subscripts. The reads are all length-guarded, so
        # ``safe_index`` never actually raises here; the ``except`` below remains
        # defense-in-depth (now genuinely unreachable, as noted).
        method_id = RPCMethod.GET_NOTEBOOK.value
        try:
            notebook_info = safe_index(
                notebook_data, 0, method_id=method_id, source="NotebooksAPI.get_source_ids"
            )
            if not isinstance(notebook_info, list):
                # notebook_data is already known to be a non-empty list here
                # (guarded by `if not notebook_data` above).
                logger.warning(
                    "get_source_ids: notebook_data[0] shape unexpected for %s "
                    "(schema drift?). top-type=%s",
                    notebook_id,
                    type(notebook_info).__name__,
                )
                return source_ids

            if len(notebook_info) <= 1:
                # The sources slot is *absent*, which is not the same thing as
                # present-and-null below: a healthy envelope carries the slot
                # (the #2131 report shows ``len=11`` on an empty notebook), so a
                # response too short to hold one is a truncated shape worth
                # surfacing.
                logger.warning(
                    "get_source_ids: notebook_info has no sources slot for %s "
                    "(schema drift?). len=%d",
                    notebook_id,
                    len(notebook_info),
                )
                return source_ids

            sources = safe_index(
                notebook_info, 1, method_id=method_id, source="NotebooksAPI.get_source_ids"
            )
            if sources is None:
                # Slot present, explicitly null: a genuinely empty notebook
                # elides its sources as ``None`` rather than ``[]``. A valid
                # empty state, not a malformed response, so it must not reach
                # the drift warning below (#2131). This is the same split the
                # sibling walk over this slot already makes — reject the short
                # envelope first, then accept a present ``None``
                # (``_source/listing.py``, issue #1159).
                return source_ids
            if not isinstance(sources, list):
                logger.warning(
                    "get_source_ids: notebook_info[1] not list for %s (schema drift?). len=%d",
                    notebook_id,
                    len(notebook_info),
                )
                return source_ids
            for source in sources:
                if not (isinstance(source, list) and source):
                    continue
                # Per-row id-envelope decoding is delegated to SourceRow:
                # ``SourceRow.id`` returns ``""`` for malformed envelopes
                # (matching legacy ``isinstance(first, list) and first``)
                # and stringifies non-string ids. The legacy code here
                # additionally required ``isinstance(sid, str)``; that
                # check was inconsistent with the sibling
                # ``_source.listing._extract_source_id`` path (which
                # accepts any non-None id via ``str(src_id)`` at the
                # ``Source(id=...)`` boundary). Unifying both call sites
                # through ``SourceRow.id`` aligns behavior — integer-ids
                # (none observed in Google's wire today) would now be
                # stringified rather than silently dropped.
                row = SourceRow.from_entry(source, method_id=RPCMethod.GET_NOTEBOOK.value)
                sid = row.id
                if sid:
                    source_ids.append(sid)
        except (IndexError, TypeError) as e:
            # Defense-in-depth: guards above should make this unreachable.
            logger.warning(
                "get_source_ids: unexpected exception despite guards for %s: %s",
                notebook_id,
                e,
                exc_info=True,
            )

        return source_ids

    async def suggest_prompts(
        self,
        notebook_id: str,
        *,
        source_ids: list[str] | None = None,
        mode: int = _PROMPT_SUGGESTIONS_DEFAULT_MODE,
        query: str | None = None,
    ) -> list[PromptSuggestion]:
        """Get AI-suggested prompts for a notebook.

        Backed by ``GeneratePromptSuggestions`` (``otmP3b``): a *general*
        notebook-prompt endpoint whose ``mode`` selects the product surface to
        suggest for. With the default ``mode=4`` the server suggests chat
        questions to ask :meth:`ChatAPI.ask`; other modes target other surfaces
        (critique, audio/debate, quiz, flashcards). The server returns a short
        list of ``{title, prompt}`` suggestions, each ``prompt`` a ready-to-send
        multi-line instruction.

        Args:
            notebook_id: The notebook to suggest prompts for.
            source_ids: Source ids to scope the suggestions to. ``None``
                (default) uses **all** of the notebook's sources.
            mode: The required ``C0`` int "mode/surface" enum, inclusive range
                ``1..10`` (``0`` / omitted makes the server return ``INTERNAL``).
                It selects which studio surface/format the prompts are written for
                (#1726, live-verified): ``1`` audio deep-dive, ``2`` audio brief,
                ``3`` video explainer, ``4`` (default) chat "ask about the content"
                questions, ``5`` audio critique, ``6`` audio debate, ``8`` quiz,
                ``9`` flashcards, ``10`` video short (``7`` unidentified). Stays a
                plain int, not a named enum, since the bundle exposes the values
                but not Google's member names. See
                ``_PROMPT_SUGGESTIONS_DEFAULT_MODE`` for the full map + method.
            query: Optional free-text steer for the kind of prompts to suggest.
                An empty / whitespace-only string is treated as no steer.

        Returns:
            A list of :class:`~notebooklm.types.PromptSuggestion`. An empty /
            degenerate server response yields ``[]`` (suggestions are
            best-effort UI sugar — an absent payload does not raise).

        Raises:
            ValidationError: if ``mode`` is outside the inclusive ``1..10`` range
                (caught before any network call, so a bad mode never costs an
                RPC).

        .. versionadded:: 0.8.0
        """
        logger.debug("Suggesting prompts for notebook %s (mode=%d)", notebook_id, mode)
        # Validate the mode up front (before the source-id fetch) so a bad value
        # fails fast without a wasted round-trip; the builder's ValueError is
        # re-raised as the public ValidationError for a uniform error contract.
        try:
            build_prompt_suggestions_params(notebook_id, [], mode=mode)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        if source_ids is None:
            source_ids = await self.get_source_ids(notebook_id)

        params = build_prompt_suggestions_params(notebook_id, source_ids, mode=mode, query=query)
        result = await self._rpc.rpc_call(
            RPCMethod.SUGGEST_PROMPTS,
            params,
            source_path=f"/notebook/{notebook_id}",
            allow_null=True,
        )

        rows = unwrap_prompt_suggestions(result, source="suggest_prompts")
        # ``is_well_formed`` only gates on row LENGTH (>= 2 slots), not on the
        # field values, mirroring ``ReportSuggestionRow``: a length-ok row whose
        # title/prompt degrade to "" (a non-string leaf) still maps to a
        # ``PromptSuggestion("", "")``. Real traffic always carries string
        # leaves, so this is a best-effort tolerance for a degenerate server
        # payload, not an expected output — callers should not treat an empty
        # title/prompt as meaningful.
        return [
            PromptSuggestion(title=row.title, prompt=row.prompt)
            for row in map(PromptSuggestionRow, rows)
            if row.is_well_formed
        ]

    async def list(self) -> list[Notebook]:
        """List notebooks (most-recently-viewed first).

        .. note::
            The backing RPC is ``ListRecentlyViewedProjects`` — results are
            ordered most-recently-viewed first (live-observed). It is not
            independently confirmed whether this can ever omit an *owned*
            notebook; in practice it matches the set shown on the NotebookLM
            home page.

        Returns:
            List of Notebook objects.
        """
        logger.debug("Listing notebooks")
        public_error: Exception | None = None
        try:
            return await self._require_read_service().list()
        except BackendError as error:
            # WebRpcBackend deliberately exposes only the neutral BackendError
            # vocabulary. At this public compatibility facade, reconstruct the
            # exact pre-migration RPC/Network exception class and its reviewed
            # structured diagnostics without reaching through ``__cause__``.
            public_error = project_backend_error(error)
        raise public_error

    async def create(self, title: str) -> Notebook:
        """Create a new notebook.

        Args:
            title: The title for the new notebook.

        Returns:
            The created Notebook object.

        Idempotency:
            Wraps the underlying CREATE_NOTEBOOK RPC in a
            probe-then-retry loop. On a transient transport failure
            (5xx / 429 / network), the wrapper lists notebooks and
            checks whether a new notebook with the requested title
            appeared since the call started. If exactly one match is
            found, that notebook is returned without re-issuing the
            create. If zero matches, the create is retried. If more
            than one matches, the wrapper raises an :class:`RPCError`
            because the situation is ambiguous (concurrent creates by
            other clients) and the caller must intervene.

            "Appeared since the call started" is measured against a
            pre-create snapshot of the notebook ids. If that snapshot
            could not be taken, the probe cannot attribute *any* match
            and raises :class:`RPCError` on the first one rather than
            adopting a notebook it may not have created (#2232). The
            raised error carries the ``unconfirmed`` marker; see
            docs/python-api.md#idempotency.
        """
        logger.debug("Creating notebook: %s", title)
        public_error: Exception | None = None
        try:
            notebook = await self._require_mutation_service().create(title)
        except BackendError as error:
            public_error = project_backend_error(error)
        else:
            if notebook.id and notebook.chat_sessions:
                self._created_chat_session_ids[notebook.id] = notebook.chat_sessions[0].id
            logger.debug("Created notebook: %s", notebook.id)
            return notebook
        # Raise outside the private BackendError catch frame so a reviewed
        # reconstructed quota cause/context graph remains the public graph.
        assert public_error is not None
        raise public_error

    async def get(self, notebook_id: str) -> Notebook:
        """Get notebook details.

        Args:
            notebook_id: The notebook ID.

        Returns:
            Notebook object with details.

        Raises:
            NotebookNotFoundError: If the notebook does not exist. Both backend
                signals are handled, so the ADR-0019 contract holds either way:
                a proper RPC error (gRPC status ``5``, surfaced by the decoder
                as ``ClientError`` and translated below), or the historical
                empty / degenerate payload with no RPC error at all, which the
                post-validation further down still catches.
        """
        public_error: Exception | None = None
        try:
            notebook = await self._require_read_service().get(notebook_id)
        except BackendError as error:
            public_error = project_backend_error(error)

        if isinstance(public_error, ClientError):
            # Translate the status-5 rejection into this method's documented
            # miss signal: ``ClientError`` and ``NotebookNotFoundError`` are
            # siblings under ``RPCError``, not ancestor/descendant, so
            # ``get_or_none``'s ``except`` never sees it (#2132, ADR-0019).
            # Narrow on purpose -- ``PERMISSION_DENIED`` comes through this
            # same branch and must keep propagating.
            #
            # ``detail`` carries the decoder's guidance onto the typed error
            # rather than leaving it on ``__cause__``: status 5 also means
            # "belongs to a different signed-in account" (#114 / #294),
            # ``server/_errors.py`` promises the 404 body keeps that verbatim,
            # and every adapter renders ``str(exc)``.
            if normalize_grpc_status(public_error.rpc_code) is GrpcStatusCode.NOT_FOUND:
                raise NotebookNotFoundError(
                    notebook_id,
                    method_id=RPCMethod.GET_NOTEBOOK.value,
                    raw_response=public_error.raw_response,
                    rpc_code=public_error.rpc_code,
                    found_ids=public_error.found_ids,
                    detail=str(public_error),
                ) from public_error
        if public_error is not None:
            raise public_error
        if notebook is None:
            raise NotebookNotFoundError(
                notebook_id,
                method_id=RPCMethod.GET_NOTEBOOK.value,
            )
        return notebook

    async def get_or_none(self, notebook_id: str) -> Notebook | None:
        """Get notebook details, returning ``None`` when it does not exist.

        The sanctioned ``None``-on-miss lookup (ADR-0019): a companion to
        :meth:`get`, which raises :class:`~notebooklm.exceptions.NotebookNotFoundError`
        on a miss. This catches *only* that genuine-absence signal and returns
        ``None``; transport, auth, and decode faults — including the broader
        :class:`~notebooklm.exceptions.RPCError` subtree
        :class:`NotebookNotFoundError` also inherits — propagate unchanged.

        Status-5 policy: **both** its meanings collapse to ``None`` here. The
        backend sends that one status whether the notebook is absent or lives
        under a *different* signed-in account (#114 / #294), so the
        account-routing guidance is unobservable on this API by construction.
        Use :meth:`get` when that matters — it raises with the guidance in the
        message, the ``rpc_code``, and the reconstructed rejection as ``__cause__``.
        ``PERMISSION_DENIED`` is folded in neither place.

        Args:
            notebook_id: The notebook ID.

        Returns:
            The :class:`~notebooklm.types.Notebook`, or ``None`` if not found.
        """
        try:
            return await self.get(notebook_id)
        except NotebookNotFoundError:
            return None

    async def delete(self, notebook_id: str) -> None:
        """Delete a notebook.

        Idempotent: deleting an already-absent notebook succeeds (returns
        ``None``) and never raises ``NotebookNotFoundError``. Real failures
        (``403``/``5xx``/auth/transport) still propagate.

        Args:
            notebook_id: The notebook ID to delete.

        .. versionchanged:: 0.7.0
            **Breaking change:** previously returned a hardcoded ``True``;
            now returns ``None`` (issue #1211). ``if await notebooks.delete(...):``
            no longer enters its block.
        """
        logger.debug("Deleting notebook: %s", notebook_id)
        public_error: Exception | None = None
        try:
            await self._require_mutation_service().delete(notebook_id)
            return
        except BackendError as error:
            public_error = project_backend_error(error)
        assert public_error is not None
        raise public_error

    async def rename(self, notebook_id: str, new_title: str) -> Notebook:
        """Rename a notebook.

        Args:
            notebook_id: The notebook ID.
            new_title: The new title for the notebook.

        Returns:
            The renamed Notebook object (fetched after rename).
        """
        return await self.update(notebook_id, title=new_title)

    async def set_emoji(self, notebook_id: str, emoji: str) -> Notebook:
        """Set a notebook's display emoji and return the refreshed notebook."""
        return await self.update(notebook_id, emoji=emoji)

    async def update(
        self,
        notebook_id: str,
        *,
        title: str | None = None,
        emoji: str | None = None,
    ) -> Notebook:
        """Update a notebook's title and/or emoji in one mutation.

        ``None`` means preserve the existing property; an empty string is sent
        verbatim and can therefore clear the emoji. At least one property must
        be supplied.
        """
        logger.debug("Updating notebook %s (title=%r, emoji=%r)", notebook_id, title, emoji)
        public_error: Exception | None = None
        try:
            return await self._require_mutation_service().update(
                notebook_id,
                title=title,
                emoji=emoji,
            )
        except BackendError as error:
            public_error = project_backend_error(error)
        assert public_error is not None
        raise public_error

    async def get_summary(self, notebook_id: str) -> str:
        """Get raw summary text for a notebook.

        For parsed summary with topics, use get_description() instead.

        Args:
            notebook_id: The notebook ID.

        Returns:
            Raw summary text string.
        """
        params = [notebook_id, [2]]
        result = await self._rpc.rpc_call(
            RPCMethod.SUMMARIZE,
            params,
            source_path=f"/notebook/{notebook_id}",
        )
        # Response structure: [[[summary_string, ...], topics, ...]]. ``result[0]``
        # is the ``outer`` payload that ``_extract_summary`` descends, so delegate
        # to it: empty/None/null-slot → "" and present-but-malformed → drift,
        # identically to ``get_description`` (single source of truth — #1485).
        if not isinstance(result, list) or not result:
            return ""
        # ``result`` is a non-empty list here; ``safe_index`` keeps the
        # envelope-unwrap position on the schema-drift seam (cannot raise here).
        return _extract_summary(
            safe_index(
                result, 0, method_id=RPCMethod.SUMMARIZE.value, source="NotebooksAPI.get_summary"
            )
        )

    async def get_description(self, notebook_id: str) -> NotebookDescription:
        """Get AI-generated summary and suggested topics for a notebook.

        This provides a high-level overview of what the notebook contains,
        similar to what's shown in the Chat panel when opening a notebook.

        .. note::
            The backing RPC is ``GenerateNotebookGuide`` — it produces the
            notebook *guide*: a short summary plus suggested starter questions
            (each ``SuggestedTopic`` carries the question and its chat prompt),
            rather than a freeform summary alone.

        Args:
            notebook_id: The notebook ID.

        Returns:
            NotebookDescription with summary and suggested topics.

        Example:
            desc = await client.notebooks.get_description(notebook_id)
            print(desc.summary)
            for topic in desc.suggested_topics:
                print(f"Q: {topic.question}")
        """
        # Get raw summary data
        params = [notebook_id, [2]]
        result = await self._rpc.rpc_call(
            RPCMethod.SUMMARIZE,
            params,
            source_path=f"/notebook/{notebook_id}",
        )

        return project_notebook_description(decode_notebook_description(result))

    async def remove_from_recent(self, notebook_id: str) -> None:
        """Remove a notebook from the recently viewed list.

        Args:
            notebook_id: The notebook ID to remove from recent.
        """
        params = [notebook_id]
        await self._rpc.rpc_call(
            RPCMethod.REMOVE_RECENTLY_VIEWED,
            params,
            allow_null=True,
        )

    async def get_raw(self, notebook_id: str) -> Any:
        """Get raw notebook data from API.

        This returns the raw API response, useful for accessing data
        not parsed into the Notebook dataclass (like sources list).

        Args:
            notebook_id: The notebook ID.

        Returns:
            Raw API response data.
        """
        params = build_get_notebook_params(notebook_id)
        return await self._rpc.rpc_call(
            RPCMethod.GET_NOTEBOOK,
            params,
            source_path=f"/notebook/{notebook_id}",
        )

    def get_share_url(self, notebook_id: str, artifact_id: str | None = None) -> str:
        """Get share URL for a notebook or artifact.

        This does NOT toggle sharing - it just returns the URL format.
        Use :meth:`SharingAPI.set_public` (``client.sharing.set_public``) to
        enable/disable sharing.

        Args:
            notebook_id: The notebook ID.
            artifact_id: Optional artifact ID for a deep-link URL.

        Returns:
            The share URL string.
        """
        return self._share_manager.get_share_url(notebook_id, artifact_id)

    async def get_metadata(self, notebook_id: str) -> NotebookMetadata:
        """Get notebook metadata with sources list.

        This combines notebook details with a simplified sources list,
        useful for export/overview of notebook contents.

        Uses asyncio.gather to fetch notebook and sources concurrently
        for better performance.

        Args:
            notebook_id: The notebook ID.

        Returns:
            NotebookMetadata with notebook details and simplified sources list.

        Example:
            metadata = await client.notebooks.get_metadata(notebook_id)
            print(f"Notebook: {metadata.title}")
            print(f"Sources: {len(metadata.sources)}")
            # Export to JSON
            import json
            print(json.dumps(metadata.to_dict(), indent=2))
        """
        return await self._metadata_service.get_metadata(notebook_id)
