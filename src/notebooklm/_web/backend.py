"""Web implementation of the private semantic backend port.

P1 assembles this backend. P2.1 routes four notebook/source reads through it;
P2.2 routes three notebook mutation handlers; and P2.3 stages the facade-inert
URL/YouTube source composite. These bindings intentionally reuse
the current request builders, strict row adapters, and public-model decoders
until the P3 codec split.
Removal: P3 replaces the compatibility model-to-record projections below with
direct wire-to-record codecs; the backend and its semantic port remain.
"""

from __future__ import annotations

import asyncio
import logging
import reprlib
import time
from collections.abc import Callable
from types import MappingProxyType
from typing import Any, cast
from urllib.parse import urlparse

from .._backend import (
    BackendCapabilities,
    BackendContractError,
    BackendDeadlineExceededError,
    BackendError,
    BackendErrorReason,
    BackendKind,
    UnsupportedOperationError,
)
from .._deadline import RuntimeDeadline
from .._idempotency import (
    _CreateResultKind,
    _IdempotentCreateResult,
    idempotent_create,
    mark_unconfirmed,
)
from .._notebook_payloads import (
    build_create_notebook_params,
    build_get_notebook_params,
    build_update_notebook_params,
)
from .._operations import CallPolicy, Operation, OperationDef
from .._records import (
    NotebookChatSessionRecord,
    NotebookChatSettingsRecord,
    NotebookCreateInput,
    NotebookCreateResult,
    NotebookDeleteInput,
    NotebookDeleteResult,
    NotebookGetInput,
    NotebookGetResult,
    NotebookListInput,
    NotebookListResult,
    NotebookPremiumFeaturesRecord,
    NotebookRecord,
    NotebookUpdateInput,
    NotebookUpdateResult,
    SourceAddCommitState,
    SourceAddTitleState,
    SourceAddUrlInput,
    SourceAddUrlReceipt,
    SourceAddUrlResult,
    SourceGetInput,
    SourceGetResult,
    SourceListInput,
    SourceListResult,
    SourceRecord,
)
from .._rpc_executor import RpcExecutor
from .._settings import build_get_user_settings_params, extract_account_limits
from .._source.add import SourceAddService, honor_requested_title_if_fresh
from .._source.listing import SourceLister
from .._source.polling import SourcePoller
from .._source.upload_payloads import build_rename_source_params, build_template_block
from .._types.sources import _SOURCE_TYPE_CODE_MAP, SourceType
from .._url_utils import is_youtube_url
from ..exceptions import (
    AuthError,
    ClientError,
    DecodingError,
    NetworkError,
    RateLimitError,
    RPCError,
    RPCResponseTooLargeError,
    RPCTimeoutError,
    ServerError,
    SourceAddError,
    SourceNotFoundError,
    UnknownRPCMethodError,
)
from ..rpc import RPCMethod, safe_index
from ..rpc.types import (
    drive_source_status_to_str,
    share_permission_to_str,
    source_status_to_str,
)
from ..types import Notebook, Source
from .registry import WEB_OPERATION_REGISTRY, WEB_SUPPORTED_OPERATIONS

notebook_logger = logging.getLogger("notebooklm._notebooks")
source_logger = logging.getLogger("notebooklm").getChild("_sources")

_CREATE_NOTEBOOK_QUOTA_RPC_CODE = 3

_WEB_ERROR_REASONS: dict[type[object], BackendErrorReason] = {
    AuthError: BackendErrorReason.AUTH,
    ClientError: BackendErrorReason.CLIENT,
    DecodingError: BackendErrorReason.DECODING,
    NetworkError: BackendErrorReason.NETWORK,
    RateLimitError: BackendErrorReason.RATE_LIMIT,
    RPCResponseTooLargeError: BackendErrorReason.RESPONSE_TOO_LARGE,
    RPCError: BackendErrorReason.RPC,
    ServerError: BackendErrorReason.SERVER,
    RPCTimeoutError: BackendErrorReason.TIMEOUT,
    UnknownRPCMethodError: BackendErrorReason.UNKNOWN_RPC_METHOD,
}

_SAFE_REASON_DIAGNOSTICS: dict[BackendErrorReason, tuple[str, ...]] = {
    BackendErrorReason.AUTH: ("recoverable",),
    BackendErrorReason.CLIENT: ("status_code",),
    BackendErrorReason.DECODING: (),
    BackendErrorReason.NETWORK: (),
    BackendErrorReason.RATE_LIMIT: ("retry_after",),
    BackendErrorReason.RESPONSE_TOO_LARGE: ("limit_bytes", "bytes_read"),
    BackendErrorReason.RPC: (),
    BackendErrorReason.SERVER: ("status_code",),
    BackendErrorReason.TIMEOUT: ("timeout_seconds",),
    BackendErrorReason.UNKNOWN_RPC_METHOD: ("path", "source", "data_at_failure"),
}


def _enum_label(value: object | None) -> str | None:
    """Return a backend-neutral enum label without preserving its wire code."""
    name = getattr(value, "name", None)
    return name.lower() if isinstance(name, str) else None


def _notebook_record(notebook: Notebook) -> NotebookRecord:
    premium = notebook.premium_features
    settings = notebook.chat_settings
    return NotebookRecord(
        id=notebook.id,
        title=notebook.title,
        created_at=notebook.created_at,
        sources_count=notebook.sources_count,
        is_owner=notebook.is_owner,
        role=(share_permission_to_str(notebook.role) if notebook.role is not None else None),
        last_viewed_at=notebook.last_viewed_at,
        emoji=notebook.emoji,
        premium_features=(
            NotebookPremiumFeaturesRecord(
                can_edit_advanced_settings=premium.can_edit_advanced_settings,
                can_edit_guidebook_config=premium.can_edit_guidebook_config,
                can_view_analytics=premium.can_view_analytics,
            )
            if premium is not None
            else None
        ),
        chat_sessions=tuple(
            NotebookChatSessionRecord(id=session.id) for session in notebook.chat_sessions
        ),
        chat_settings=(
            NotebookChatSettingsRecord(
                goal=_enum_label(settings.goal) or "unknown",
                response_length=_enum_label(settings.response_length) or "unknown",
                custom_prompt=settings.custom_prompt,
            )
            if settings is not None
            else None
        ),
    )


def _source_record(source: Source) -> SourceRecord:
    type_code = source._type_code
    kind = (
        SourceType.UNKNOWN
        if type_code is None
        else _SOURCE_TYPE_CODE_MAP.get(type_code, SourceType.UNKNOWN)
    )
    unrecognized_kind: int | str | None = (
        type_code if type_code is not None and type_code not in _SOURCE_TYPE_CODE_MAP else None
    )
    return SourceRecord(
        id=source.id,
        title=source.title,
        url=source.url,
        kind=kind.value,
        unrecognized_kind=unrecognized_kind,
        created_at=source.created_at,
        status=source_status_to_str(source.status),
        drive_document_id=source.drive_document_id,
        drive_status=(
            drive_source_status_to_str(source.drive_status)
            if source.drive_status is not None
            else None
        ),
        download_url=source.download_url,
        viewer_url=source.viewer_url,
        content_mime=source.content_mime,
        word_count=source.word_count,
        revision_id=source.revision_id,
        revision_timestamp=source.revision_timestamp,
        last_modified_at=source.last_modified_at,
    )


class _DeadlineRpcCaller:
    """Bind one semantic operation and absolute deadline to legacy RPC helpers."""

    __slots__ = ("_backend", "_deadline", "_operation")

    def __init__(
        self,
        backend: WebRpcBackend,
        deadline: RuntimeDeadline | None,
        operation: Operation,
    ) -> None:
        self._backend = backend
        self._deadline = deadline
        self._operation = operation

    async def rpc_call(
        self,
        method: RPCMethod,
        params: list[Any],
        source_path: str = "/",
        allow_null: bool = False,
        _is_retry: bool = False,
        *,
        disable_internal_retries: bool = False,
        operation_variant: str | None = None,
        read_timeout: float | None = None,
        raise_on_null_status: bool = False,
    ) -> Any:
        # The semantic deadline is the only timeout authority for this composite.
        # A feature helper cannot replace it with a fresh relative timeout.
        del read_timeout
        return await self._backend._rpc_call(
            method,
            params,
            operation=self._operation,
            deadline=self._deadline,
            source_path=source_path,
            allow_null=allow_null,
            _is_retry=_is_retry,
            disable_internal_retries=disable_internal_retries,
            operation_variant=operation_variant,
            raise_on_null_status=raise_on_null_status,
        )


class WebRpcBackend:
    """Typed semantic binding over the existing shared :class:`RpcExecutor`."""

    def __init__(
        self,
        executor: RpcExecutor,
        *,
        transport_factory: Callable[..., object],
    ) -> None:
        self._executor = executor
        self._transport_factory = transport_factory
        self._capabilities = BackendCapabilities(
            supported_operations=WEB_SUPPORTED_OPERATIONS,
        )
        self._closed = False

    @property
    def kind(self) -> BackendKind:
        return BackendKind.WEB

    @property
    def capabilities(self) -> BackendCapabilities:
        return self._capabilities

    async def invoke(
        self,
        operation: OperationDef[Any, Any],
        value: Any,
        *,
        deadline: RuntimeDeadline | None,
    ) -> Any:
        """Validate and dispatch one typed operation through its web binding."""
        binding = WEB_OPERATION_REGISTRY.get(operation.key)
        if binding is None or not binding.is_supported:
            raise UnsupportedOperationError(operation.key, self.kind)
        if binding.definition != operation:
            raise BackendContractError(
                f"non-canonical definition supplied for {operation.key.value}"
            )
        if type(value) is not operation.input_type:
            raise BackendContractError(
                f"{operation.key.value} requires {operation.input_type.__name__}, "
                f"got {type(value).__name__}"
            )
        if deadline is not None and not isinstance(deadline, RuntimeDeadline):
            raise BackendContractError("deadline must be RuntimeDeadline or None")
        if self._closed:
            raise BackendContractError("WebRpcBackend is closed")
        if deadline is not None and deadline.expired():
            raise BackendDeadlineExceededError(
                operation.key,
                diagnostics=MappingProxyType(
                    {"timeout": deadline.timeout, "remaining": deadline.remaining()}
                ),
            )

        handler_name = binding.handler_name
        if handler_name is None:  # pragma: no cover - registry invariant
            raise BackendContractError(f"{operation.key.value} has no web handler")
        handler = getattr(self, handler_name)
        try:
            result = await handler(value, deadline=deadline)
        except BackendError:
            raise
        except RPCTimeoutError as exc:
            if deadline is not None and deadline.expired():
                diagnostics = dict(self._error_diagnostics(exc, BackendErrorReason.TIMEOUT))
                diagnostics.update({"timeout": deadline.timeout, "remaining": deadline.remaining()})
                raise BackendDeadlineExceededError(
                    operation.key,
                    outcome_unknown=operation.policy is not CallPolicy.READ,
                    diagnostics=MappingProxyType(diagnostics),
                ) from exc
            translated = self._translate_error(operation.key, exc)
            raise translated from exc
        except (RPCError, NetworkError) as exc:
            translated = self._translate_error(operation.key, exc)
            raise translated from exc


        if type(result) is not operation.output_type:
            raise BackendContractError(
                f"{operation.key.value} returned {type(result).__name__}, "
                f"expected {operation.output_type.__name__}"
            )
        return result

    async def close(self) -> None:
        """Close this dispatch surface without closing its client-owned executor."""
        self._closed = True

    async def _rpc_call(
        self,
        method: RPCMethod,
        params: list[Any],
        *,
        operation: Operation,
        deadline: RuntimeDeadline | None,
        source_path: str = "/",
        allow_null: bool = False,
        _is_retry: bool = False,
        disable_internal_retries: bool = False,
        operation_variant: str | None = None,
        raise_on_null_status: bool = False,
    ) -> Any:
        read_timeout: float | None = None
        if deadline is not None:
            read_timeout = deadline.remaining()
            if read_timeout <= 0.0:
                raise BackendDeadlineExceededError(
                    operation,
                    diagnostics=MappingProxyType(
                        {"timeout": deadline.timeout, "remaining": read_timeout}
                    ),
                )
        return await self._executor.rpc_call(
            method,
            params,
            source_path=source_path,
            allow_null=allow_null,
            _is_retry=_is_retry,
            disable_internal_retries=disable_internal_retries,
            operation_variant=operation_variant,
            read_timeout=read_timeout,
            raise_on_null_status=raise_on_null_status,
            _retry_deadline=deadline,
        )

    async def _notebook_list(
        self,
        value: NotebookListInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> NotebookListResult:
        del value
        result = await self._rpc_call(
            RPCMethod.LIST_NOTEBOOKS,
            [None, 1, None, [2]],
            operation=Operation.NOTEBOOK_LIST,
            deadline=deadline,
        )
        if not result:
            return NotebookListResult(notebooks=())
        if isinstance(result, list):
            raw_notebooks = safe_index(
                result,
                0,
                method_id=RPCMethod.LIST_NOTEBOOKS.value,
                source="WebRpcBackend._notebook_list",
            )
            if isinstance(raw_notebooks, list):
                return NotebookListResult(
                    notebooks=tuple(
                        _notebook_record(Notebook.from_api_response(row)) for row in raw_notebooks
                    )
                )
            if raw_notebooks is None:
                return NotebookListResult(notebooks=())
        raise DecodingError(
            "Unrecognized LIST_NOTEBOOKS payload shape",
            raw_response=reprlib.repr(result),
            method_id=RPCMethod.LIST_NOTEBOOKS.value,
        )

    async def _notebook_create(
        self,
        value: NotebookCreateInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> NotebookCreateResult:
        baseline_ids: set[str] | None
        baseline_error: Exception | None = None
        try:
            baseline = await self._notebook_list(
                NotebookListInput(),
                deadline=deadline,
            )
            baseline_ids = {notebook.id for notebook in baseline.notebooks}
        except Exception as exc:
            baseline_ids = None
            baseline_error = exc
            notebook_logger.warning(
                "create: baseline list() failed (%s); the idempotency probe can no "
                "longer tell a notebook this call created from one that was already "
                "there, so a transport failure will surface as an ambiguity error "
                "instead of recovering",
                type(exc).__name__,
                exc_info=True,
            )

        async def create() -> NotebookRecord:
            try:
                result = await self._rpc_call(
                    RPCMethod.CREATE_NOTEBOOK,
                    build_create_notebook_params(value.title),
                    operation=Operation.NOTEBOOK_CREATE,
                    deadline=deadline,
                    disable_internal_retries=True,
                )
            except RPCError as exc:
                limit_error = await self._notebook_limit_error(exc, deadline=deadline)
                if limit_error is not None:
                    raise limit_error from None
                raise
            return _notebook_record(Notebook.from_api_response(result))

        async def probe() -> NotebookRecord | None:
            try:
                current = await self._notebook_list(
                    NotebookListInput(),
                    deadline=deadline,
                )
            except (AuthError, RateLimitError, ServerError, NetworkError) as exc:
                notebook_logger.warning(
                    "create: probe list() failed with transport/auth error; "
                    "propagating so the caller can avoid a duplicate-resource retry"
                )
                mark_unconfirmed(exc)
                raise
            except BackendError:
                raise
            except Exception as exc:
                notebook_logger.warning(
                    "create: probe list() failed with a non-transport error (%s); the "
                    "create cannot be confirmed, so it will not be retried",
                    type(exc).__name__,
                    exc_info=True,
                )
                raise mark_unconfirmed(
                    RPCError(
                        "UNRESOLVED — do not blindly retry; check your notebook list "
                        f"first. Cannot confirm notebook with title {value.title!r}: the "
                        "create failed at the transport level and may or may not have "
                        "committed, and the idempotency probe that would settle it "
                        f"failed too ({type(exc).__name__}). No FURTHER attempt was made.",
                        method_id=RPCMethod.CREATE_NOTEBOOK.value,
                    )
                ) from exc
            matches = tuple(
                notebook for notebook in current.notebooks if notebook.title == value.title
            )
            if baseline_ids is not None:
                matches = tuple(notebook for notebook in matches if notebook.id not in baseline_ids)
            elif matches:
                raise mark_unconfirmed(
                    RPCError(
                        f"Cannot disambiguate notebook with title {value.title!r} — check your "
                        "notebook list before retrying: the pre-create baseline snapshot failed "
                        f"({type(baseline_error).__name__}), so "
                        f"{', '.join(f'{item.id} ({item.title!r})' for item in matches)} may "
                        "either predate this create or be the notebook it just created.",
                        method_id=RPCMethod.CREATE_NOTEBOOK.value,
                    )
                )
            if len(matches) == 1:
                return next(iter(matches))
            if len(matches) > 1:
                raise mark_unconfirmed(
                    RPCError(
                        f"Cannot disambiguate notebook with title {value.title!r}: "
                        f"probe found {len(matches)} new notebooks with this title",
                        method_id=RPCMethod.CREATE_NOTEBOOK.value,
                    )
                )
            return None

        result = await idempotent_create(
            create,
            probe,
            label=f"notebook.create[{value.title!r}]",
        )
        return NotebookCreateResult(notebook=result.value)

    async def _notebook_limit_error(
        self,
        error: RPCError,
        *,
        deadline: RuntimeDeadline | None,
    ) -> BackendError | None:
        if (
            error.method_id != RPCMethod.CREATE_NOTEBOOK.value
            or error.rpc_code != _CREATE_NOTEBOOK_QUOTA_RPC_CODE
        ):
            return None
        try:
            settings = await self._rpc_call(
                RPCMethod.GET_USER_SETTINGS,
                build_get_user_settings_params(),
                operation=Operation.NOTEBOOK_CREATE,
                deadline=deadline,
                source_path="/",
            )
            limit = extract_account_limits(settings).notebook_limit
        except Exception:
            return None
        if limit is None:
            return None
        try:
            listed = await self._notebook_list(NotebookListInput(), deadline=deadline)
        except Exception:
            return None
        owned_count = sum(1 for notebook in listed.notebooks if notebook.is_owner)
        if owned_count < max(limit - 1, 0):
            return None

        original = self._translate_error(Operation.NOTEBOOK_CREATE, error)
        return BackendError(
            message="notebook limit reached",
            operation=Operation.NOTEBOOK_CREATE,
            diagnostics=MappingProxyType(
                {
                    "current_count": owned_count,
                    "limit": limit,
                    "original_message": original.message,
                    "original_reason": original.reason.value
                    if original.reason is not None
                    else None,
                    "original_diagnostics": dict(original.diagnostics or {}),
                }
            ),
            reason=BackendErrorReason.NOTEBOOK_LIMIT,
        )

    async def _notebook_update(
        self,
        value: NotebookUpdateInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> NotebookUpdateResult:
        await self._rpc_call(
            RPCMethod.RENAME_NOTEBOOK,
            build_update_notebook_params(
                value.notebook_id,
                title=value.title,
                emoji=value.emoji,
            ),
            operation=Operation.NOTEBOOK_UPDATE,
            deadline=deadline,
            source_path="/",
            allow_null=True,
        )
        result = await self._rpc_call(
            RPCMethod.GET_NOTEBOOK,
            build_get_notebook_params(value.notebook_id),
            operation=Operation.NOTEBOOK_UPDATE,
            deadline=deadline,
            source_path=f"/notebook/{value.notebook_id}",
        )
        notebook_row = (
            safe_index(
                result,
                0,
                method_id=RPCMethod.GET_NOTEBOOK.value,
                source="WebRpcBackend._notebook_update",
            )
            if result and isinstance(result, list)
            else None
        )
        if not notebook_row:
            raise BackendError(
                message=f"Notebook not found: {value.notebook_id}",
                operation=Operation.NOTEBOOK_UPDATE,
                diagnostics=MappingProxyType(
                    {
                        "notebook_id": value.notebook_id,
                        "method_id": RPCMethod.GET_NOTEBOOK.value,
                    }
                ),
                reason=BackendErrorReason.NOTEBOOK_NOT_FOUND,
            )
        notebook = Notebook.from_api_response(notebook_row, include_chat_settings=True)
        if not notebook.id and not notebook.title:
            raise BackendError(
                message=f"Notebook not found: {value.notebook_id}",
                operation=Operation.NOTEBOOK_UPDATE,
                diagnostics=MappingProxyType(
                    {
                        "notebook_id": value.notebook_id,
                        "method_id": RPCMethod.GET_NOTEBOOK.value,
                    }
                ),
                reason=BackendErrorReason.NOTEBOOK_NOT_FOUND,
            )
        return NotebookUpdateResult(notebook=_notebook_record(notebook))

    async def _notebook_delete(
        self,
        value: NotebookDeleteInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> NotebookDeleteResult:
        await self._rpc_call(
            RPCMethod.DELETE_NOTEBOOK,
            [[value.notebook_id], [2]],
            operation=Operation.NOTEBOOK_DELETE,
            deadline=deadline,
        )
        return NotebookDeleteResult()

    async def _notebook_get(
        self,
        value: NotebookGetInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> NotebookGetResult:
        result = await self._rpc_call(
            RPCMethod.GET_NOTEBOOK,
            build_get_notebook_params(value.notebook_id),
            operation=Operation.NOTEBOOK_GET,
            deadline=deadline,
            source_path=f"/notebook/{value.notebook_id}",
        )
        notebook_row = (
            safe_index(
                result,
                0,
                method_id=RPCMethod.GET_NOTEBOOK.value,
                source="WebRpcBackend._notebook_get",
            )
            if result and isinstance(result, list)
            else None
        )
        if not notebook_row:
            return NotebookGetResult(notebook=None)
        notebook = Notebook.from_api_response(notebook_row, include_chat_settings=True)
        if not notebook.id and not notebook.title:
            return NotebookGetResult(notebook=None)
        return NotebookGetResult(notebook=_notebook_record(notebook))

    async def _source_list(
        self,
        value: SourceListInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> SourceListResult:
        notebook = await self._rpc_call(
            RPCMethod.GET_NOTEBOOK,
            [value.notebook_id, None, build_template_block(), None, 0],
            operation=Operation.SOURCE_LIST,
            deadline=deadline,
            source_path=f"/notebook/{value.notebook_id}",
        )
        sources = SourceLister(self._executor).normalize(
            value.notebook_id,
            notebook,
            strict=value.strict,
        )
        records = tuple(_source_record(source) for source in sources)
        if value.statuses is not None:
            records = tuple(record for record in records if record.status in value.statuses)
        if value.kinds is not None:
            records = tuple(record for record in records if record.kind in value.kinds)
        return SourceListResult(sources=records)

    async def _source_get(
        self,
        value: SourceGetInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> SourceGetResult:
        notebook = await self._rpc_call(
            RPCMethod.GET_NOTEBOOK,
            [value.notebook_id, None, build_template_block(), None, 0],
            operation=Operation.SOURCE_GET,
            deadline=deadline,
            source_path=f"/notebook/{value.notebook_id}",
        )
        sources = SourceLister(self._executor).normalize(value.notebook_id, notebook)
        records = tuple(_source_record(source) for source in sources)
        return SourceGetResult(
            source=next((source for source in records if source.id == value.source_id), None)
        )

    async def _source_add_url(
        self,
        value: SourceAddUrlInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> SourceAddUrlResult:
        """Run the staged generic/YouTube URL workflow under one deadline."""
        caller = _DeadlineRpcCaller(self, deadline, Operation.SOURCE_ADD_URL)
        adder = SourceAddService()
        lister = SourceLister(cast(Any, caller))
        poller = SourcePoller()

        def extract_youtube_video_id(url: str) -> str | None:
            return adder.extract_youtube_video_id(
                url,
                parse_url=urlparse,
                extract_video_id_from_parsed_url=adder.extract_video_id_from_parsed_url,
                is_valid_video_id=adder.is_valid_video_id,
                logger=source_logger,
            )

        async def wait_until_ready(
            notebook_id: str,
            source_id: str,
            *,
            timeout: float,
        ) -> Source:
            return await poller.wait_until_ready(
                notebook_id,
                source_id,
                timeout=timeout,
                get_source=lister.get,
                sleep=asyncio.sleep,
                monotonic=(deadline.monotonic if deadline is not None else time.monotonic),
                logger=source_logger,
                deadline=deadline,
            )

        async def rename_source(
            notebook_id: str,
            source_id: str,
            new_title: str,
        ) -> Source | None:
            result = await caller.rpc_call(
                RPCMethod.UPDATE_SOURCE,
                build_rename_source_params(source_id, new_title),
                source_path=f"/notebook/{notebook_id}",
                allow_null=True,
            )
            if result:
                return Source.from_api_response(
                    result,
                    method_id=RPCMethod.UPDATE_SOURCE.value,
                )
            source = await lister.get(notebook_id, source_id)
            if source is None:
                raise SourceNotFoundError(
                    source_id,
                    method_id=RPCMethod.UPDATE_SOURCE.value,
                )
            return source

        try:
            create_result = cast(
                _IdempotentCreateResult[Source],
                await adder.add_url(
                    value.notebook_id,
                    value.url,
                    wait=value.wait,
                    wait_timeout=value.wait_timeout,
                    add_youtube_source=lambda notebook_id, url: adder.add_youtube_source(
                        notebook_id,
                        url,
                        rpc=cast(Any, caller),
                    ),
                    add_url_source=lambda notebook_id, url: adder.add_url_source(
                        notebook_id,
                        url,
                        rpc=cast(Any, caller),
                    ),
                    list_sources=lister.list,
                    wait_until_ready=wait_until_ready,
                    extract_youtube_video_id=extract_youtube_video_id,
                    is_youtube_url=is_youtube_url,
                    logger=source_logger,
                    return_result=True,
                ),
            )
        except (SourceAddError, RPCError, NetworkError) as exc:
            if not getattr(exc, "unconfirmed", False):
                raise
            receipt = SourceAddUrlReceipt(
                commit_state=SourceAddCommitState.UNKNOWN,
                title_state=SourceAddTitleState.NOT_ATTEMPTED,
                outcome_unknown=True,
            )
            raise BackendError(
                message=str(exc),
                operation=Operation.SOURCE_ADD_URL,
                outcome_unknown=True,
                diagnostics=MappingProxyType({"receipt": receipt}),
            ) from exc

        source_before_title = create_result.value
        requested_title = value.requested_title
        normalized_title = requested_title.strip() if requested_title is not None else ""
        source = await honor_requested_title_if_fresh(
            rename_source,
            value.notebook_id,
            create_result,
            requested_title,
            source_logger,
            probe_proves_freshness=True,
        )
        if not normalized_title:
            title_state = SourceAddTitleState.NOT_REQUESTED
        elif source_before_title.title == normalized_title:
            title_state = SourceAddTitleState.UNCHANGED
        elif source.title == normalized_title:
            title_state = SourceAddTitleState.RENAMED
        else:
            title_state = SourceAddTitleState.RENAME_FAILED

        return SourceAddUrlResult(
            source=_source_record(source),
            receipt=SourceAddUrlReceipt(
                commit_state=(
                    SourceAddCommitState.CREATED
                    if create_result.kind is _CreateResultKind.CREATED
                    else SourceAddCommitState.RECONCILED
                ),
                title_state=title_state,
            ),
        )

    @staticmethod
    def _error_diagnostics(
        exc: RPCError | NetworkError,
        reason: BackendErrorReason,
    ) -> MappingProxyType[str, object]:
        diagnostics = {
            "method_id": getattr(exc, "method_id", None),
            "rpc_code": getattr(exc, "rpc_code", None),
            "found_ids": getattr(exc, "found_ids", None),
            "raw_response": getattr(exc, "raw_response", None),
        }
        diagnostics.update((name, getattr(exc, name)) for name in _SAFE_REASON_DIAGNOSTICS[reason])
        return MappingProxyType(diagnostics)

    @classmethod
    def _translate_error(cls, operation: Operation, exc: RPCError | NetworkError) -> BackendError:
        reason = _WEB_ERROR_REASONS.get(type(exc))
        if reason is None:
            raise BackendContractError(
                f"unclassified web error type {type(exc).__module__}.{type(exc).__qualname__}",
                operation=operation,
            ) from exc
        return BackendError(
            # Structured subclasses such as UnknownRPCMethodError append their
            # diagnostic fields in ``__str__``. Store only the base message so
            # the compatibility projector can reattach those fields exactly
            # once instead of duplicating the rendered suffix.
            message=str(exc.args[0]) if exc.args else "",
            operation=operation,
            outcome_unknown=bool(getattr(exc, "unconfirmed", False)),
            diagnostics=cls._error_diagnostics(exc, reason),
            reason=reason,
        )


__all__ = ["WebRpcBackend"]
