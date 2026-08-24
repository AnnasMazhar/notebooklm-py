"""Web implementation of the private semantic backend port.

P1 assembles this backend without routing a production feature through it.
Its four P2.1 handlers intentionally reuse the current request builders,
strict row adapters, and public-model decoders until the P3 codec split.
Removal: P3 replaces the compatibility model-to-record projections below with
direct wire-to-record codecs; the backend and its semantic port remain.
"""

from __future__ import annotations

import reprlib
from collections.abc import Callable
from types import MappingProxyType
from typing import Any, cast

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
from .._notebook_payloads import build_get_notebook_params
from .._operations import CallPolicy, Operation, OperationDef
from .._records import (
    NotebookChatSessionRecord,
    NotebookChatSettingsRecord,
    NotebookGetInput,
    NotebookGetResult,
    NotebookListInput,
    NotebookListResult,
    NotebookPremiumFeaturesRecord,
    NotebookRecord,
    SourceGetInput,
    SourceGetResult,
    SourceListInput,
    SourceListResult,
    SourceRecord,
)
from .._rpc_executor import RpcExecutor
from .._source.listing import SourceLister
from .._types.sources import _SOURCE_TYPE_CODE_MAP, SourceType
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
    """Removal: P3 folds this deadline handoff into direct web bindings."""

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
        raise_on_null_status: bool = False,
    ) -> Any:
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
            raise self._translate_error(operation.key, exc) from exc
        except (RPCError, NetworkError) as exc:
            raise self._translate_error(operation.key, exc) from exc

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
        caller = _DeadlineRpcCaller(self, deadline, Operation.SOURCE_LIST)
        sources = await SourceLister(cast(Any, caller)).list(
            value.notebook_id,
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
        caller = _DeadlineRpcCaller(self, deadline, Operation.SOURCE_GET)
        sources = await SourceLister(cast(Any, caller)).list(value.notebook_id)
        records = tuple(_source_record(source) for source in sources)
        return SourceGetResult(
            source=next((source for source in records if source.id == value.source_id), None)
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
            message=str(exc),
            operation=operation,
            outcome_unknown=False,
            diagnostics=cls._error_diagnostics(exc, reason),
            reason=reason,
        )


__all__ = ["WebRpcBackend"]
