"""Web implementation of the private semantic backend port.

P1 assembles this backend. P2.1 routes four notebook/source reads through it;
P2.2 routes three notebook mutation handlers; P2.3 routes the live URL/YouTube
source composite; P5.1 routes Studio catalog list/get; P5.2 routes Audio; P5.3
routes Quiz/Flashcards; P5.4 routes Report/Video generation; and P6.3 routes
plain-note CRUD. These bindings reuse
current request builders and strict row adapters; P3 web codecs terminate
response grammar in neutral records before public compatibility projection.
"""

from __future__ import annotations

import asyncio
import logging
import reprlib
import time
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any, Literal, TypeVar, cast
from urllib.parse import urlparse

import httpx

from .._artifact.payloads import (
    build_audio_artifact_params,
    build_flashcards_artifact_params,
    build_quiz_artifact_params,
)
from .._backend import (
    BackendCapabilities,
    BackendContractError,
    BackendDeadlineExceededError,
    BackendError,
    BackendErrorReason,
    BackendKind,
    UnsupportedOperationError,
    mark_backend_outcome_unknown,
)
from .._deadline import RuntimeDeadline
from .._env import get_default_language
from .._idempotency import (
    _CreateResultKind,
    _IdempotentCreateResult,
    idempotent_create,
    mark_unconfirmed,
)
from .._mind_map import NoteBackedMindMapService
from .._note_service import LegacyNoteBackedService
from .._notebook_payloads import (
    build_create_notebook_params,
    build_get_notebook_params,
    build_update_notebook_params,
)
from .._operations import CallPolicy, Operation, OperationDef
from .._projectors import project_source
from .._records import (
    ArtifactGetInput,
    ArtifactGetResult,
    ArtifactListInput,
    ArtifactListResult,
    ArtifactRecord,
    AudioGenerateInput,
    AudioGenerateResult,
    GenerationStatusRecord,
    InteractiveGenerateInput,
    InteractiveGenerateResult,
    NotebookCreateInput,
    NotebookCreateResult,
    NotebookDeleteInput,
    NotebookDeleteResult,
    NotebookGetInput,
    NotebookGetResult,
    NotebookListInput,
    NotebookListResult,
    NotebookRecord,
    NotebookUpdateInput,
    NotebookUpdateResult,
    NoteCreateInput,
    NoteCreateResult,
    NoteDeleteInput,
    NoteDeleteResult,
    NoteGetInput,
    NoteGetResult,
    NoteListInput,
    NoteListResult,
    NoteUpdateInput,
    NoteUpdateResult,
    SourceAddCommitState,
    SourceAddFailureKind,
    SourceAddFailureRecord,
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
from .._row_adapters.artifacts import unwrap_artifact_rows
from .._row_adapters.sources import SourceRow
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
    IdempotencyVariantError,
    NetworkError,
    NotebookLMError,
    RateLimitError,
    RPCError,
    RPCResponseTooLargeError,
    RPCTimeoutError,
    ServerError,
    SourceAddError,
    SourceNotFoundError,
    SourceProcessingError,
    SourceTimeoutError,
    UnknownRPCMethodError,
)
from ..rpc import (
    ARTIFACT_STATUS_SUGGESTED_WIRE_NAME,
    AudioFormat,
    AudioLength,
    GrpcStatusCode,
    QuizDifficulty,
    QuizQuantity,
    RPCMethod,
    normalize_grpc_status,
    safe_index,
)
from ..rpc.types import artifact_status_to_str, drive_source_status_to_str, source_status_to_str
from ..types import Source
from .codec.artifacts import decode_artifact, decode_mind_map_artifact
from .codec.notebooks import decode_notebook
from .codec.notes import decode_created_note, decode_note, decode_notes
from .codec.sources import decode_source
from .registry import WEB_OPERATION_REGISTRY, WEB_SUPPORTED_OPERATIONS
from .studio_documents import StudioDocumentWebHandlers

notebook_logger = logging.getLogger("notebooklm._notebooks")
source_logger = logging.getLogger("notebooklm").getChild("_sources")
artifact_logger = logging.getLogger("notebooklm._artifact.listing")

_CREATE_NOTEBOOK_QUOTA_RPC_CODE = 3

_WEB_ERROR_REASONS: dict[type[object], BackendErrorReason] = {
    AuthError: BackendErrorReason.AUTH,
    ClientError: BackendErrorReason.CLIENT,
    DecodingError: BackendErrorReason.DECODING,
    IdempotencyVariantError: BackendErrorReason.IDEMPOTENCY_VARIANT,
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
    BackendErrorReason.IDEMPOTENCY_VARIANT: (),
    BackendErrorReason.NETWORK: (),
    BackendErrorReason.RATE_LIMIT: ("retry_after",),
    BackendErrorReason.RESPONSE_TOO_LARGE: ("limit_bytes", "bytes_read"),
    BackendErrorReason.RPC: (),
    BackendErrorReason.SERVER: ("status_code",),
    BackendErrorReason.TIMEOUT: ("timeout_seconds",),
    BackendErrorReason.UNKNOWN_RPC_METHOD: ("path", "source", "data_at_failure"),
}


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
        kind_present=type_code is not None,
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


_QUIZ_QUANTITIES = {member.name.lower(): member for member in QuizQuantity}
_QUIZ_DIFFICULTIES = {member.name.lower(): member for member in QuizDifficulty}
_InteractiveOptionT = TypeVar("_InteractiveOptionT", QuizQuantity, QuizDifficulty)


def _capture_public_failure(
    exc: Exception,
    *,
    operation: Operation,
    _seen: frozenset[int] = frozenset(),
) -> SourceAddFailureRecord:
    """Capture the bounded, serializable public library-error graph."""
    if id(exc) in _seen or len(_seen) >= 8:
        raise BackendContractError(
            "public failure graph is cyclic or exceeds eight nodes",
            operation=operation,
        ) from exc
    seen = _seen | {id(exc)}

    kind_by_type: dict[type[BaseException], SourceAddFailureKind] = {
        SourceAddError: SourceAddFailureKind.SOURCE_ADD,
        SourceNotFoundError: SourceAddFailureKind.SOURCE_NOT_FOUND,
        SourceProcessingError: SourceAddFailureKind.SOURCE_PROCESSING,
        SourceTimeoutError: SourceAddFailureKind.SOURCE_TIMEOUT,
        AuthError: SourceAddFailureKind.AUTH,
        ClientError: SourceAddFailureKind.CLIENT,
        DecodingError: SourceAddFailureKind.DECODING,
        NetworkError: SourceAddFailureKind.NETWORK,
        RateLimitError: SourceAddFailureKind.RATE_LIMIT,
        RPCResponseTooLargeError: SourceAddFailureKind.RESPONSE_TOO_LARGE,
        RPCError: SourceAddFailureKind.RPC,
        RPCTimeoutError: SourceAddFailureKind.RPC_TIMEOUT,
        ServerError: SourceAddFailureKind.SERVER,
        UnknownRPCMethodError: SourceAddFailureKind.UNKNOWN_RPC_METHOD,
        ConnectionError: SourceAddFailureKind.BUILTIN_CONNECTION,
        BrokenPipeError: SourceAddFailureKind.BUILTIN_BROKEN_PIPE,
        ConnectionAbortedError: SourceAddFailureKind.BUILTIN_CONNECTION_ABORTED,
        ConnectionRefusedError: SourceAddFailureKind.BUILTIN_CONNECTION_REFUSED,
        ConnectionResetError: SourceAddFailureKind.BUILTIN_CONNECTION_RESET,
        OSError: SourceAddFailureKind.BUILTIN_OS,
        IndexError: SourceAddFailureKind.BUILTIN_INDEX,
        KeyError: SourceAddFailureKind.BUILTIN_KEY,
        RuntimeError: SourceAddFailureKind.BUILTIN_RUNTIME,
        TimeoutError: SourceAddFailureKind.BUILTIN_TIMEOUT,
        TypeError: SourceAddFailureKind.BUILTIN_TYPE,
        ValueError: SourceAddFailureKind.BUILTIN_VALUE,
        httpx.HTTPStatusError: SourceAddFailureKind.HTTPX_STATUS,
        httpx.RequestError: SourceAddFailureKind.HTTPX_REQUEST,
        httpx.TransportError: SourceAddFailureKind.HTTPX_TRANSPORT,
        httpx.TimeoutException: SourceAddFailureKind.HTTPX_TIMEOUT,
        httpx.ConnectTimeout: SourceAddFailureKind.HTTPX_CONNECT_TIMEOUT,
        httpx.ReadTimeout: SourceAddFailureKind.HTTPX_READ_TIMEOUT,
        httpx.WriteTimeout: SourceAddFailureKind.HTTPX_WRITE_TIMEOUT,
        httpx.PoolTimeout: SourceAddFailureKind.HTTPX_POOL_TIMEOUT,
        httpx.NetworkError: SourceAddFailureKind.HTTPX_NETWORK,
        httpx.ConnectError: SourceAddFailureKind.HTTPX_CONNECT,
        httpx.ReadError: SourceAddFailureKind.HTTPX_READ,
        httpx.WriteError: SourceAddFailureKind.HTTPX_WRITE,
        httpx.CloseError: SourceAddFailureKind.HTTPX_CLOSE,
        httpx.ProxyError: SourceAddFailureKind.HTTPX_PROXY,
        httpx.ProtocolError: SourceAddFailureKind.HTTPX_PROTOCOL,
        httpx.LocalProtocolError: SourceAddFailureKind.HTTPX_LOCAL_PROTOCOL,
        httpx.RemoteProtocolError: SourceAddFailureKind.HTTPX_REMOTE_PROTOCOL,
        httpx.UnsupportedProtocol: SourceAddFailureKind.HTTPX_UNSUPPORTED_PROTOCOL,
        httpx.TooManyRedirects: SourceAddFailureKind.HTTPX_TOO_MANY_REDIRECTS,
        httpx.DecodingError: SourceAddFailureKind.HTTPX_DECODING,
    }
    kind = kind_by_type.get(type(exc))
    if kind is None:
        raise BackendContractError(
            f"unsupported public failure type {type(exc).__module__}.{type(exc).__qualname__}",
            operation=operation,
        ) from exc

    scalar_args = tuple(exc.args)
    if not all(isinstance(item, (str, int, float, bool, type(None))) for item in scalar_args):
        raise BackendContractError(
            "public failure args are not scalar",
            operation=operation,
        ) from exc

    # Preserve the public library graph.  Builtin/httpx leaf internals can
    # contain arbitrary third-party exception objects; their exact reviewed
    # leaf type/data is retained below, but that unbounded internal graph is
    # intentionally not replayed.
    capture_links = isinstance(exc, NotebookLMError)
    explicit = exc.__cause__ if capture_links else None
    context = exc.__context__ if capture_links else None
    source_add_cause = exc.cause if isinstance(exc, SourceAddError) else None
    if source_add_cause is not None and explicit is not None and source_add_cause is not explicit:
        raise BackendContractError(
            "SourceAddError has different cause attribute and explicit cause",
            operation=operation,
        ) from exc
    cause = source_add_cause or explicit
    original_error = getattr(exc, "original_error", None)
    if original_error is not None and not isinstance(original_error, Exception):
        raise BackendContractError(
            "public failure original_error is not an exception",
            operation=operation,
        ) from exc
    cause_is_original = cause is not None and cause is original_error
    context_is_cause = context is not None and context is cause
    context_is_original = context is not None and context is original_error

    found_ids = tuple(getattr(exc, "found_ids", ()) or ())
    if not all(isinstance(item, (str, int)) for item in found_ids):
        raise BackendContractError(
            "public failure found_ids are not strings or integers",
            operation=operation,
        ) from exc

    raw_response = getattr(exc, "raw_response", None)
    if raw_response is not None and not isinstance(raw_response, str):
        raw_response = repr(raw_response)
    data_at_failure = getattr(exc, "data_at_failure", None)
    if data_at_failure is not None and not isinstance(data_at_failure, str):
        data_at_failure = repr(data_at_failure)
    request: httpx.Request | None = None
    if isinstance(exc, (httpx.HTTPStatusError, httpx.RequestError)):
        try:
            request = exc.request
        except RuntimeError:
            pass

    return SourceAddFailureRecord(
        kind=kind,
        message=str(exc.args[0]) if exc.args else "",
        args=scalar_args,
        url=(exc.url if isinstance(exc, SourceAddError) else None),
        unconfirmed=bool(getattr(exc, "unconfirmed", False)),
        source_id=getattr(exc, "source_id", None),
        stage=getattr(exc, "stage", None),
        method_id=getattr(exc, "method_id", None),
        raw_response=raw_response,
        rpc_code=getattr(exc, "rpc_code", None),
        found_ids=found_ids,
        recoverable=(getattr(exc, "recoverable", None) if isinstance(exc, AuthError) else None),
        retry_after=(
            getattr(exc, "retry_after", None) if isinstance(exc, RateLimitError) else None
        ),
        status_code=(
            getattr(exc, "status_code", None)
            if isinstance(exc, (ClientError, ServerError))
            else (exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None)
        ),
        timeout_seconds=(exc.timeout_seconds if isinstance(exc, RPCTimeoutError) else None),
        limit_bytes=(exc.limit_bytes if isinstance(exc, RPCResponseTooLargeError) else None),
        bytes_read=(exc.bytes_read if isinstance(exc, RPCResponseTooLargeError) else None),
        status=(exc.status if isinstance(exc, SourceProcessingError) else None),
        timeout=(exc.timeout if isinstance(exc, SourceTimeoutError) else None),
        last_status=(exc.last_status if isinstance(exc, SourceTimeoutError) else None),
        path=(exc.path if isinstance(exc, UnknownRPCMethodError) else None),
        source=(exc.source if isinstance(exc, UnknownRPCMethodError) else None),
        data_at_failure=data_at_failure,
        request_method=request.method if request is not None else None,
        request_url=str(request.url) if request is not None else None,
        original_error=(
            _capture_public_failure(original_error, operation=operation, _seen=seen)
            if isinstance(original_error, Exception)
            else None
        ),
        cause=(
            _capture_public_failure(cause, operation=operation, _seen=seen)
            if isinstance(cause, Exception) and not cause_is_original
            else None
        ),
        context=(
            _capture_public_failure(context, operation=operation, _seen=seen)
            if isinstance(context, Exception) and not context_is_cause and not context_is_original
            else None
        ),
        cause_is_original=cause_is_original,
        context_is_cause=context_is_cause,
        context_is_original=context_is_original,
        explicit_cause=explicit is not None,
        suppress_context=exc.__suppress_context__,
    )


_AUDIO_FORMATS = {
    "deep_dive": AudioFormat.DEEP_DIVE,
    "brief": AudioFormat.BRIEF,
    "critique": AudioFormat.CRITIQUE,
    "debate": AudioFormat.DEBATE,
}
_AUDIO_LENGTHS = {
    "short": AudioLength.SHORT,
    "default": AudioLength.DEFAULT,
    "long": AudioLength.LONG,
}


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
        timeout_error: RPCTimeoutError | None = None
        try:
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
        except BackendDeadlineExceededError:
            timeout_error = RPCTimeoutError(
                f"Request timed out calling {method.name}",
                method_id=method.value,
                timeout_seconds=(self._deadline.timeout if self._deadline is not None else None),
            )
        # Raise outside the private deadline-error frame. The legacy composite
        # can now apply its ordinary uncertainty policy without leaking a
        # BackendError into the closed public failure graph.
        assert timeout_error is not None
        raise timeout_error


class WebRpcBackend(StudioDocumentWebHandlers):
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
                    {
                        "timeout": deadline.timeout,
                        "remaining": deadline.remaining(),
                        "timeout_seconds": deadline.timeout,
                    }
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
                diagnostics["public_error_failure"] = _capture_public_failure(
                    exc,
                    operation=operation.key,
                )
                raise BackendDeadlineExceededError(
                    operation.key,
                    outcome_unknown=operation.policy is not CallPolicy.READ,
                    diagnostics=MappingProxyType(diagnostics),
                ) from exc
            translated = self._translate_error(operation.key, exc)
            raise translated from exc
        except NotebookLMError as exc:
            # Catch the closed library family rather than a broad ``RPCError``
            # wrap.  ``_translate_error`` still accepts only the exact reviewed
            # transport types and fails closed for any semantic exception.
            if not isinstance(exc, (RPCError, NetworkError, IdempotencyVariantError)):
                raise BackendContractError(
                    f"unclassified web error type {type(exc).__module__}.{type(exc).__qualname__}",
                    operation=operation.key,
                ) from exc
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
        outcome_unknown_on_expiry: bool = False,
    ) -> Any:
        read_timeout: float | None = None
        if deadline is not None:
            read_timeout = deadline.remaining()
            if read_timeout <= 0.0:
                raise BackendDeadlineExceededError(
                    operation,
                    # No native call was dispatched in this phase. Uncertainty
                    # is therefore false unless the composite explicitly says
                    # an earlier phase may already have committed.
                    outcome_unknown=outcome_unknown_on_expiry,
                    diagnostics=MappingProxyType(
                        {
                            "timeout": deadline.timeout,
                            "remaining": read_timeout,
                            "timeout_seconds": deadline.timeout,
                            "method_id": method.value,
                        }
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
                    notebooks=tuple(decode_notebook(row) for row in raw_notebooks)
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
            return decode_notebook(result)

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
            except BackendError as exc:
                raise mark_backend_outcome_unknown(exc) from exc
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
            notebook_logger.debug(
                "Could not fetch account limits after CREATE_NOTEBOOK failure; "
                "leaving original RPC error unchanged",
                exc_info=True,
            )
            return None
        if limit is None:
            return None
        try:
            listed = await self._notebook_list(NotebookListInput(), deadline=deadline)
        except Exception:
            notebook_logger.debug(
                "Could not list notebooks after CREATE_NOTEBOOK failure; "
                "leaving original RPC error unchanged",
                exc_info=True,
            )
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
        try:
            result = await self._rpc_call(
                RPCMethod.GET_NOTEBOOK,
                build_get_notebook_params(value.notebook_id),
                operation=Operation.NOTEBOOK_UPDATE,
                deadline=deadline,
                source_path=f"/notebook/{value.notebook_id}",
                outcome_unknown_on_expiry=True,
            )
        except ClientError as exc:
            if normalize_grpc_status(exc.rpc_code) is not GrpcStatusCode.NOT_FOUND:
                raise
            diagnostics = dict(self._error_diagnostics(exc, BackendErrorReason.CLIENT))
            diagnostics.update(
                {
                    "notebook_id": value.notebook_id,
                    "method_id": RPCMethod.GET_NOTEBOOK.value,
                    "detail": str(exc),
                    "original_message": str(exc.args[0]) if exc.args else str(exc),
                }
            )
            raise BackendError(
                message=f"Notebook not found: {value.notebook_id}",
                operation=Operation.NOTEBOOK_UPDATE,
                diagnostics=MappingProxyType(diagnostics),
                reason=BackendErrorReason.NOTEBOOK_NOT_FOUND,
            ) from exc
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
        notebook = decode_notebook(notebook_row, include_chat_settings=True)
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
        return NotebookUpdateResult(notebook=notebook)

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
        notebook = decode_notebook(notebook_row, include_chat_settings=True)
        if not notebook.id and not notebook.title:
            return NotebookGetResult(notebook=None)
        return NotebookGetResult(notebook=notebook)

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
        records = tuple(
            SourceLister(self._executor).normalize_records(
                value.notebook_id,
                notebook,
                strict=value.strict,
            )
        )
        if value.statuses is not None:
            records = tuple(record for record in records if record.status in value.statuses)
        if value.kinds is not None:
            records = tuple(record for record in records if record.kind in value.kinds)
        return SourceListResult(sources=records)

    async def _artifact_catalog_records(
        self,
        notebook_id: str,
        *,
        operation: Operation,
        deadline: RuntimeDeadline | None,
        include_mind_maps: bool,
    ) -> tuple[ArtifactRecord, ...]:
        result = await self._rpc_call(
            RPCMethod.LIST_ARTIFACTS,
            [
                [2],
                notebook_id,
                f'NOT artifact.status = "{ARTIFACT_STATUS_SUGGESTED_WIRE_NAME}"',
            ],
            operation=operation,
            deadline=deadline,
            source_path=f"/notebook/{notebook_id}",
            allow_null=True,
        )
        if isinstance(result, list):
            rows = unwrap_artifact_rows(
                result,
                method_id=RPCMethod.LIST_ARTIFACTS.value,
                source="WebRpcBackend._artifact_catalog_records",
            )
        elif not result:
            rows = []
        else:
            raise DecodingError(
                "Unrecognized LIST_ARTIFACTS payload shape",
                raw_response=reprlib.repr(result),
                method_id=RPCMethod.LIST_ARTIFACTS.value,
            )

        artifacts = [decode_artifact(row) for row in rows if isinstance(row, list) and row]
        if include_mind_maps:
            caller = _DeadlineRpcCaller(self, deadline, operation)
            mind_maps = NoteBackedMindMapService(LegacyNoteBackedService(cast(Any, caller)))
            try:
                mind_map_rows = await mind_maps.list_mind_maps(notebook_id)
                artifacts.extend(
                    artifact
                    for row in mind_map_rows
                    if (artifact := decode_mind_map_artifact(row)) is not None
                )
            except DecodingError:
                raise
            except RPCError as exc:
                artifact_logger.warning("Failed to fetch mind maps: %s", exc)
        return tuple(artifacts)

    async def _artifact_list(
        self,
        value: ArtifactListInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> ArtifactListResult:
        records = await self._artifact_catalog_records(
            value.notebook_id,
            operation=Operation.ARTIFACT_LIST,
            deadline=deadline,
            include_mind_maps=value.family in {None, "mind_map"},
        )
        return ArtifactListResult(artifacts=records)

    async def _artifact_get(
        self,
        value: ArtifactGetInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> ArtifactGetResult:
        records = await self._artifact_catalog_records(
            value.notebook_id,
            operation=Operation.ARTIFACT_GET,
            deadline=deadline,
            include_mind_maps=True,
        )
        return ArtifactGetResult(
            artifact=next((item for item in records if item.id == value.artifact_id), None)
        )

    async def _audio_generate(
        self,
        value: AudioGenerateInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> AudioGenerateResult:
        if value.audio_format is not None and value.audio_format not in _AUDIO_FORMATS:
            raise BackendContractError(
                f"unrecognized audio format {value.audio_format!r}",
                operation=Operation.ARTIFACT_GENERATE_AUDIO,
            )
        if value.audio_length is not None and value.audio_length not in _AUDIO_LENGTHS:
            raise BackendContractError(
                f"unrecognized audio length {value.audio_length!r}",
                operation=Operation.ARTIFACT_GENERATE_AUDIO,
            )
        source_ids = value.source_ids
        if source_ids is None:
            notebook = await self._rpc_call(
                RPCMethod.GET_NOTEBOOK,
                build_get_notebook_params(value.notebook_id),
                operation=Operation.ARTIFACT_GENERATE_AUDIO,
                deadline=deadline,
                source_path=f"/notebook/{value.notebook_id}",
            )
            source_ids = self._audio_source_ids(notebook)

        result = await self._rpc_call(
            RPCMethod.CREATE_ARTIFACT,
            build_audio_artifact_params(
                value.notebook_id,
                list(source_ids),
                language=(get_default_language() if value.language is None else value.language),
                instructions=value.instructions,
                audio_format=(
                    None if value.audio_format is None else _AUDIO_FORMATS[value.audio_format]
                ),
                audio_length=(
                    None if value.audio_length is None else _AUDIO_LENGTHS[value.audio_length]
                ),
            ),
            operation=Operation.ARTIFACT_GENERATE_AUDIO,
            deadline=deadline,
            source_path=f"/notebook/{value.notebook_id}",
            allow_null=True,
            operation_variant=None,
            raise_on_null_status=True,
        )
        if result is None:
            raise self._artifact_feature_unavailable(
                Operation.ARTIFACT_GENERATE_AUDIO,
                "audio",
            )

        method_id = RPCMethod.CREATE_ARTIFACT.value
        artifact_id = safe_index(
            result,
            0,
            0,
            method_id=method_id,
            source="_parse_generation_result",
        )
        if artifact_id is None:
            raise self._artifact_feature_unavailable(
                Operation.ARTIFACT_GENERATE_AUDIO,
                "artifact",
            )
        if not artifact_id:
            raise DecodingError(
                "No artifact id (source=_parse_generation_result)",
                method_id=method_id,
            )
        status_code = safe_index(
            result,
            0,
            4,
            method_id=method_id,
            source="_parse_generation_result",
        )
        status = "pending" if status_code is None else artifact_status_to_str(status_code)
        return AudioGenerateResult(
            GenerationStatusRecord(task_id=cast(str, artifact_id), status=status)
        )

    @staticmethod
    def _artifact_feature_unavailable(
        operation: Operation,
        artifact_type: str,
    ) -> BackendError:
        return BackendError(
            message=f"{artifact_type.replace('_', ' ').capitalize()} generation is unavailable",
            operation=operation,
            diagnostics=MappingProxyType(
                {
                    "artifact_type": artifact_type,
                    "method_id": RPCMethod.CREATE_ARTIFACT.value,
                    "raw_response": None,
                }
            ),
            reason=BackendErrorReason.ARTIFACT_FEATURE_UNAVAILABLE,
        )

    @staticmethod
    def _audio_source_ids(notebook: object) -> tuple[str, ...]:
        """Preserve the facade's tolerant source-id extraction semantics."""

        if not notebook or not isinstance(notebook, list):
            return ()
        notebook_info = safe_index(
            notebook,
            0,
            method_id=RPCMethod.GET_NOTEBOOK.value,
            source="NotebooksAPI.get_source_ids",
        )
        if not isinstance(notebook_info, list) or len(notebook_info) <= 1:
            return ()
        sources = safe_index(
            notebook_info,
            1,
            method_id=RPCMethod.GET_NOTEBOOK.value,
            source="NotebooksAPI.get_source_ids",
        )
        if not isinstance(sources, list):
            return ()
        source_ids: list[str] = []
        for source in sources:
            if isinstance(source, list) and source:
                source_id = SourceRow.from_entry(
                    source,
                    method_id=RPCMethod.GET_NOTEBOOK.value,
                ).id
                if source_id:
                    source_ids.append(source_id)
        return tuple(source_ids)

    async def _quiz_generate(
        self,
        value: InteractiveGenerateInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> InteractiveGenerateResult:
        return await self._interactive_generate(
            value,
            operation=Operation.ARTIFACT_GENERATE_QUIZ,
            family="quiz",
            deadline=deadline,
        )

    async def _flashcards_generate(
        self,
        value: InteractiveGenerateInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> InteractiveGenerateResult:
        return await self._interactive_generate(
            value,
            operation=Operation.ARTIFACT_GENERATE_FLASHCARDS,
            family="flashcards",
            deadline=deadline,
        )

    async def _interactive_generate(
        self,
        value: InteractiveGenerateInput,
        *,
        operation: Operation,
        family: Literal["quiz", "flashcards"],
        deadline: RuntimeDeadline | None,
    ) -> InteractiveGenerateResult:
        quantity = self._interactive_option(
            value.quantity,
            _QUIZ_QUANTITIES,
            parameter="quantity",
            operation=operation,
        )
        difficulty = self._interactive_option(
            value.difficulty,
            _QUIZ_DIFFICULTIES,
            parameter="difficulty",
            operation=operation,
        )
        source_ids = value.source_ids
        if source_ids is None:
            notebook = await self._rpc_call(
                RPCMethod.GET_NOTEBOOK,
                build_get_notebook_params(value.notebook_id),
                operation=operation,
                deadline=deadline,
                source_path=f"/notebook/{value.notebook_id}",
            )
            source_ids = self._interactive_source_ids(value.notebook_id, notebook)

        if family == "quiz":
            builder = build_quiz_artifact_params
        elif family == "flashcards":
            builder = build_flashcards_artifact_params
        else:  # pragma: no cover - closed Literal and registry
            raise BackendContractError(
                f"unsupported interactive artifact family {family!r}",
                operation=operation,
            )
        result = await self._rpc_call(
            RPCMethod.CREATE_ARTIFACT,
            builder(
                value.notebook_id,
                list(source_ids),
                instructions=value.instructions,
                quantity=quantity,
                difficulty=difficulty,
            ),
            operation=operation,
            deadline=deadline,
            source_path=f"/notebook/{value.notebook_id}",
            allow_null=True,
            operation_variant=None,
            raise_on_null_status=True,
        )
        if result is None:
            raise self._artifact_feature_unavailable(operation, family)

        method_id = RPCMethod.CREATE_ARTIFACT.value
        artifact_id = safe_index(
            result,
            0,
            0,
            method_id=method_id,
            source="_parse_generation_result",
        )
        if artifact_id is None:
            raise self._artifact_feature_unavailable(operation, "artifact")
        if not artifact_id:
            raise DecodingError(
                "No artifact id (source=_parse_generation_result)",
                method_id=method_id,
            )
        status_code = safe_index(
            result,
            0,
            4,
            method_id=method_id,
            source="_parse_generation_result",
        )
        status = "pending" if status_code is None else artifact_status_to_str(status_code)
        return InteractiveGenerateResult(
            GenerationStatusRecord(task_id=cast(str, artifact_id), status=status)
        )

    @staticmethod
    def _interactive_option(
        value: str | None,
        options: Mapping[str, _InteractiveOptionT],
        *,
        parameter: str,
        operation: Operation,
    ) -> _InteractiveOptionT | None:
        if value is None:
            return None
        option = options.get(value)
        if option is None:
            raise BackendContractError(
                f"unrecognized interactive {parameter} {value!r}",
                operation=operation,
            )
        return option

    @staticmethod
    def _interactive_source_ids(notebook_id: str, notebook: object) -> tuple[str, ...]:
        """Preserve the facade's tolerant source-id extraction semantics."""

        source_ids: list[str] = []
        if not notebook or not isinstance(notebook, list):
            return tuple(source_ids)

        method_id = RPCMethod.GET_NOTEBOOK.value
        try:
            notebook_info = safe_index(
                notebook,
                0,
                method_id=method_id,
                source="NotebooksAPI.get_source_ids",
            )
            if not isinstance(notebook_info, list):
                notebook_logger.warning(
                    "get_source_ids: notebook_data[0] shape unexpected for %s "
                    "(schema drift?). top-type=%s",
                    notebook_id,
                    type(notebook_info).__name__,
                )
                return tuple(source_ids)
            if len(notebook_info) <= 1:
                notebook_logger.warning(
                    "get_source_ids: notebook_info has no sources slot for %s "
                    "(schema drift?). len=%d",
                    notebook_id,
                    len(notebook_info),
                )
                return tuple(source_ids)

            sources = safe_index(
                notebook_info,
                1,
                method_id=method_id,
                source="NotebooksAPI.get_source_ids",
            )
            if sources is None:
                return tuple(source_ids)
            if not isinstance(sources, list):
                notebook_logger.warning(
                    "get_source_ids: notebook_info[1] not list for %s (schema drift?). len=%d",
                    notebook_id,
                    len(notebook_info),
                )
                return tuple(source_ids)
            for source in sources:
                if not (isinstance(source, list) and source):
                    continue
                source_id = SourceRow.from_entry(source, method_id=method_id).id
                if source_id:
                    source_ids.append(source_id)
        except (IndexError, TypeError) as error:
            notebook_logger.warning(
                "get_source_ids: unexpected exception despite guards for %s: %s",
                notebook_id,
                error,
                exc_info=True,
            )
        return tuple(source_ids)

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
        records = tuple(SourceLister(self._executor).normalize_records(value.notebook_id, notebook))
        return SourceGetResult(
            source=next((source for source in records if source.id == value.source_id), None)
        )

    async def _note_list(
        self,
        value: NoteListInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> NoteListResult:
        result = await self._rpc_call(
            RPCMethod.GET_NOTES_AND_MIND_MAPS,
            [value.notebook_id],
            operation=Operation.NOTE_LIST,
            deadline=deadline,
            source_path=f"/notebook/{value.notebook_id}",
            allow_null=True,
        )
        return NoteListResult(decode_notes(result, value.notebook_id))

    async def _note_get(
        self,
        value: NoteGetInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> NoteGetResult:
        result = await self._rpc_call(
            RPCMethod.GET_NOTES_AND_MIND_MAPS,
            [value.notebook_id],
            operation=Operation.NOTE_GET,
            deadline=deadline,
            source_path=f"/notebook/{value.notebook_id}",
            allow_null=True,
        )
        return NoteGetResult(decode_note(result, value.notebook_id, value.note_id))

    async def _note_create(
        self,
        value: NoteCreateInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> NoteCreateResult:
        result = await self._rpc_call(
            RPCMethod.CREATE_NOTE,
            [value.notebook_id, "", [1], None, value.title],
            operation=Operation.NOTE_CREATE,
            deadline=deadline,
            source_path=f"/notebook/{value.notebook_id}",
            operation_variant="plain",
        )
        return NoteCreateResult(
            decode_created_note(result, value.notebook_id, value.title, value.content)
        )

    async def _note_update(
        self,
        value: NoteUpdateInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> NoteUpdateResult:
        await self._rpc_call(
            RPCMethod.UPDATE_NOTE,
            [value.notebook_id, value.note_id, [[[value.content, value.title, [], 0]]]],
            operation=Operation.NOTE_UPDATE,
            deadline=deadline,
            source_path=f"/notebook/{value.notebook_id}",
            allow_null=True,
        )
        return NoteUpdateResult()

    async def _note_delete(
        self,
        value: NoteDeleteInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> NoteDeleteResult:
        await self._rpc_call(
            RPCMethod.DELETE_NOTE,
            [value.notebook_id, None, [value.note_id]],
            operation=Operation.NOTE_DELETE,
            deadline=deadline,
            source_path=f"/notebook/{value.notebook_id}",
            allow_null=True,
        )
        return NoteDeleteResult()

    async def _source_add_url(
        self,
        value: SourceAddUrlInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> SourceAddUrlResult:
        """Run the live generic/YouTube URL workflow with optional outer budgeting."""
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
                return project_source(
                    decode_source(result, method_id=RPCMethod.UPDATE_SOURCE.value)
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
        except NotebookLMError as exc:
            outcome_unknown = bool(getattr(exc, "unconfirmed", False))
            receipt = SourceAddUrlReceipt(
                commit_state=(
                    SourceAddCommitState.UNKNOWN if outcome_unknown else SourceAddCommitState.FAILED
                ),
                title_state=SourceAddTitleState.NOT_ATTEMPTED,
                outcome_unknown=outcome_unknown,
            )
            raise BackendError(
                message=str(exc.args[0]) if exc.args else "",
                operation=Operation.SOURCE_ADD_URL,
                outcome_unknown=outcome_unknown,
                diagnostics=MappingProxyType(
                    {
                        "receipt": receipt,
                        "source_add_failure": _capture_public_failure(
                            exc,
                            operation=Operation.SOURCE_ADD_URL,
                        ),
                    }
                ),
                reason=BackendErrorReason.SOURCE_ADD,
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
        exc: RPCError | NetworkError | IdempotencyVariantError,
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
    def _translate_error(
        cls,
        operation: Operation,
        exc: RPCError | NetworkError | IdempotencyVariantError,
    ) -> BackendError:
        reason = _WEB_ERROR_REASONS.get(type(exc))
        if reason is None:
            raise BackendContractError(
                f"unclassified web error type {type(exc).__module__}.{type(exc).__qualname__}",
                operation=operation,
            ) from exc
        diagnostics = dict(cls._error_diagnostics(exc, reason))
        if isinstance(exc, (RPCError, NetworkError)):
            diagnostics["public_error_failure"] = _capture_public_failure(
                exc,
                operation=operation,
            )
        return BackendError(
            # Structured subclasses such as UnknownRPCMethodError append their
            # diagnostic fields in ``__str__``. Store only the base message so
            # the compatibility projector can reattach those fields exactly
            # once instead of duplicating the rendered suffix.
            message=str(exc.args[0]) if exc.args else "",
            operation=operation,
            outcome_unknown=bool(getattr(exc, "unconfirmed", False)),
            diagnostics=MappingProxyType(diagnostics),
            reason=reason,
        )


__all__ = ["WebRpcBackend"]
