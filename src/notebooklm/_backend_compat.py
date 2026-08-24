"""Project neutral backend failures onto the legacy public exception contract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

import httpx

from ._backend import BackendContractError, BackendError, BackendErrorReason
from ._records import SourceAddFailureKind, SourceAddFailureRecord
from .exceptions import (
    AuthError,
    ClientError,
    DecodingError,
    NetworkError,
    NotebookLimitError,
    NotebookNotFoundError,
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


def _preserve_outcome(error: BackendError, projected: Exception) -> Exception:
    if error.outcome_unknown:
        projected.unconfirmed = True  # type: ignore[attr-defined]
    return projected


def _diagnostics(error: BackendError) -> Mapping[str, object]:
    diagnostics = error.diagnostics
    if diagnostics is None:
        raise BackendContractError(
            "backend compatibility error lacks diagnostics",
            operation=error.operation,
        )
    return diagnostics


def _optional(
    error: BackendError,
    diagnostics: Mapping[str, object],
    name: str,
    expected: type[object] | tuple[type[object], ...],
) -> object | None:
    value = diagnostics.get(name)
    if value is not None and not isinstance(value, expected):
        raise BackendContractError(
            f"backend compatibility diagnostic {name!r} has invalid type {type(value).__name__}",
            operation=error.operation,
        )
    return value


def _required_int(
    error: BackendError,
    diagnostics: Mapping[str, object],
    name: str,
) -> int | None:
    value = _optional(error, diagnostics, name, int)
    if isinstance(value, bool):
        raise BackendContractError(
            f"backend compatibility diagnostic {name!r} must not be bool",
            operation=error.operation,
        )
    return cast(int | None, value)


def _rpc_diagnostics(error: BackendError) -> dict[str, Any]:
    diagnostics = _diagnostics(error)
    method_id = _optional(error, diagnostics, "method_id", str)
    raw_response = _optional(error, diagnostics, "raw_response", str)
    rpc_code = _optional(error, diagnostics, "rpc_code", (str, int))
    found_ids = diagnostics.get("found_ids")
    if found_ids is None:
        normalized_found_ids: list[str] = []
    elif isinstance(found_ids, list) and all(isinstance(item, str) for item in found_ids):
        normalized_found_ids = found_ids
    else:
        raise BackendContractError(
            "backend compatibility diagnostic 'found_ids' must be list[str] or None",
            operation=error.operation,
        )
    return {
        "method_id": method_id,
        "raw_response": raw_response,
        "rpc_code": rpc_code,
        "found_ids": normalized_found_ids,
    }


def project_backend_error(error: BackendError) -> Exception:
    """Reconstruct the exact public exception class from closed neutral evidence."""
    reason = error.reason
    if reason is None:
        raise BackendContractError(
            "backend compatibility error lacks a closed reason",
            operation=error.operation,
        )
    diagnostics = _diagnostics(error)

    if reason is BackendErrorReason.NETWORK:
        return _preserve_outcome(
            error,
            NetworkError(
                error.message,
                method_id=cast(str | None, _optional(error, diagnostics, "method_id", str)),
            ),
        )
    if reason is BackendErrorReason.TIMEOUT:
        return _preserve_outcome(
            error,
            RPCTimeoutError(
                error.message,
                method_id=cast(str | None, _optional(error, diagnostics, "method_id", str)),
                timeout_seconds=cast(
                    float | None,
                    _optional(error, diagnostics, "timeout_seconds", (float, int)),
                ),
            ),
        )
    if reason is BackendErrorReason.UNKNOWN_RPC_METHOD:
        method_id = _optional(error, diagnostics, "method_id", (str, int))
        rpc_code = _optional(error, diagnostics, "rpc_code", (str, int))
        found_ids = diagnostics.get("found_ids")
        if found_ids is not None and not (
            isinstance(found_ids, list) and all(isinstance(item, (str, int)) for item in found_ids)
        ):
            raise BackendContractError(
                "unknown-RPC found_ids must be list[str | int] or None",
                operation=error.operation,
            )
        path = diagnostics.get("path")
        if path is not None and not (
            isinstance(path, tuple)
            and all(isinstance(item, int) and not isinstance(item, bool) for item in path)
        ):
            raise BackendContractError(
                "unknown-RPC path must be tuple[int, ...] or None",
                operation=error.operation,
            )
        source = _optional(error, diagnostics, "source", str)
        return _preserve_outcome(
            error,
            UnknownRPCMethodError(
                error.message,
                method_id=cast(str | int | None, method_id),
                path=cast(tuple[int, ...] | None, path),
                source=cast(str | None, source),
                found_ids=cast(list[str | int] | None, found_ids),
                raw_response=diagnostics.get("raw_response"),
                data_at_failure=diagnostics.get("data_at_failure"),
                rpc_code=cast(str | int | None, rpc_code),
            ),
        )
    if reason is BackendErrorReason.NOTEBOOK_NOT_FOUND:
        notebook_id = _optional(error, diagnostics, "notebook_id", str)
        if notebook_id is None:
            raise BackendContractError(
                "notebook-not-found compatibility error lacks notebook_id",
                operation=error.operation,
            )
        return _preserve_outcome(
            error,
            NotebookNotFoundError(
                cast(str, notebook_id),
                method_id=cast(str | None, _optional(error, diagnostics, "method_id", str)),
            ),
        )
    if reason is BackendErrorReason.NOTEBOOK_LIMIT:
        current_count = _required_int(error, diagnostics, "current_count")
        if current_count is None:
            raise BackendContractError(
                "notebook-limit compatibility error lacks current_count",
                operation=error.operation,
            )
        original_reason = _optional(error, diagnostics, "original_reason", str)
        original_message = _optional(error, diagnostics, "original_message", str)
        original_diagnostics = diagnostics.get("original_diagnostics")
        if (
            original_reason is None
            or original_message is None
            or not isinstance(original_diagnostics, Mapping)
        ):
            raise BackendContractError(
                "notebook-limit compatibility error lacks original RPC evidence",
                operation=error.operation,
            )
        try:
            nested_reason = BackendErrorReason(original_reason)
        except ValueError as exc:
            raise BackendContractError(
                f"invalid notebook-limit original reason {original_reason!r}",
                operation=error.operation,
            ) from exc
        original = project_backend_error(
            BackendError(
                cast(str, original_message),
                operation=error.operation,
                diagnostics=original_diagnostics,
                reason=nested_reason,
            )
        )
        if not isinstance(original, RPCError):
            raise BackendContractError(
                "notebook-limit original evidence does not reconstruct RPCError",
                operation=error.operation,
            )
        return _preserve_outcome(
            error,
            NotebookLimitError(
                current_count,
                limit=_required_int(error, diagnostics, "limit"),
                original_error=original,
            ),
        )

    rpc = _rpc_diagnostics(error)
    if reason is BackendErrorReason.AUTH:
        projected = AuthError(error.message, **rpc)
        recoverable = _optional(error, diagnostics, "recoverable", bool)
        projected.recoverable = bool(recoverable)
        return _preserve_outcome(error, projected)
    if reason is BackendErrorReason.CLIENT:
        return _preserve_outcome(
            error,
            ClientError(
                error.message,
                status_code=_required_int(error, diagnostics, "status_code"),
                **rpc,
            ),
        )
    if reason is BackendErrorReason.DECODING:
        return _preserve_outcome(error, DecodingError(error.message, **rpc))
    if reason is BackendErrorReason.RATE_LIMIT:
        return _preserve_outcome(
            error,
            RateLimitError(
                error.message,
                retry_after=_required_int(error, diagnostics, "retry_after"),
                **rpc,
            ),
        )
    if reason is BackendErrorReason.RESPONSE_TOO_LARGE:
        return _preserve_outcome(
            error,
            RPCResponseTooLargeError(
                error.message,
                limit_bytes=_required_int(error, diagnostics, "limit_bytes"),
                bytes_read=_required_int(error, diagnostics, "bytes_read"),
                method_id=rpc["method_id"],
            ),
        )
    if reason is BackendErrorReason.RPC:
        return _preserve_outcome(error, RPCError(error.message, **rpc))
    if reason is BackendErrorReason.SERVER:
        return _preserve_outcome(
            error,
            ServerError(
                error.message,
                status_code=_required_int(error, diagnostics, "status_code"),
                **rpc,
            ),
        )
    raise BackendContractError(
        f"unsupported backend compatibility reason {reason.value!r}",
        operation=error.operation,
    )


def _project_source_add_record(record: SourceAddFailureRecord) -> Exception:
    original_error = (
        _project_source_add_record(record.original_error)
        if record.original_error is not None
        else None
    )
    cause = (
        original_error
        if record.cause_is_original
        else (_project_source_add_record(record.cause) if record.cause is not None else None)
    )
    context = (
        cause
        if record.context_is_cause
        else (
            original_error
            if record.context_is_original
            else (
                _project_source_add_record(record.context) if record.context is not None else None
            )
        )
    )
    rpc: dict[str, Any] = {
        "method_id": record.method_id,
        "raw_response": record.raw_response,
        "rpc_code": record.rpc_code,
        "found_ids": list(record.found_ids),
    }
    kind = record.kind
    if kind is SourceAddFailureKind.SOURCE_ADD:
        if record.url is None:
            raise BackendContractError("source-add failure lacks url")
        projected: Exception = SourceAddError(record.url, cause=cause, message=record.message)
    elif kind is SourceAddFailureKind.SOURCE_NOT_FOUND:
        if record.source_id is None:
            raise BackendContractError("source-not-found failure lacks source_id")
        projected = SourceNotFoundError(
            record.source_id,
            method_id=rpc["method_id"],
            raw_response=record.raw_response,
        )
    elif kind is SourceAddFailureKind.SOURCE_PROCESSING:
        if record.source_id is None or record.status is None:
            raise BackendContractError("source-processing failure lacks source_id/status")
        projected = SourceProcessingError(record.source_id, record.status, record.message)
    elif kind is SourceAddFailureKind.SOURCE_TIMEOUT:
        if record.source_id is None or record.timeout is None:
            raise BackendContractError("source-timeout failure lacks source_id/timeout")
        projected = SourceTimeoutError(record.source_id, record.timeout, record.last_status)
    elif kind is SourceAddFailureKind.AUTH:
        projected = AuthError(record.message, **rpc)
        projected.recoverable = bool(record.recoverable)
    elif kind is SourceAddFailureKind.CLIENT:
        projected = ClientError(record.message, status_code=record.status_code, **rpc)
    elif kind is SourceAddFailureKind.DECODING:
        projected = DecodingError(record.message, **rpc)
    elif kind is SourceAddFailureKind.NETWORK:
        projected = NetworkError(
            record.message,
            method_id=rpc["method_id"],
            original_error=original_error,
        )
    elif kind is SourceAddFailureKind.RATE_LIMIT:
        projected = RateLimitError(
            record.message,
            retry_after=record.retry_after,
            **rpc,
        )
    elif kind is SourceAddFailureKind.RESPONSE_TOO_LARGE:
        projected = RPCResponseTooLargeError(
            record.message,
            limit_bytes=record.limit_bytes,
            bytes_read=record.bytes_read,
            method_id=rpc["method_id"],
        )
    elif kind is SourceAddFailureKind.RPC:
        projected = RPCError(record.message, **rpc)
    elif kind is SourceAddFailureKind.RPC_TIMEOUT:
        projected = RPCTimeoutError(
            record.message,
            timeout_seconds=record.timeout_seconds,
            method_id=rpc["method_id"],
            original_error=original_error,
        )
    elif kind is SourceAddFailureKind.SERVER:
        projected = ServerError(record.message, status_code=record.status_code, **rpc)
    elif kind is SourceAddFailureKind.UNKNOWN_RPC_METHOD:
        projected = UnknownRPCMethodError(
            record.message,
            method_id=record.method_id,
            path=record.path,
            source=record.source,
            found_ids=list(record.found_ids) or None,
            raw_response=record.raw_response,
            data_at_failure=record.data_at_failure,
            rpc_code=record.rpc_code,
        )
    else:
        httpx_types: dict[SourceAddFailureKind, type[httpx.RequestError]] = {
            SourceAddFailureKind.HTTPX_REQUEST: httpx.RequestError,
            SourceAddFailureKind.HTTPX_TRANSPORT: httpx.TransportError,
            SourceAddFailureKind.HTTPX_TIMEOUT: httpx.TimeoutException,
            SourceAddFailureKind.HTTPX_CONNECT_TIMEOUT: httpx.ConnectTimeout,
            SourceAddFailureKind.HTTPX_READ_TIMEOUT: httpx.ReadTimeout,
            SourceAddFailureKind.HTTPX_WRITE_TIMEOUT: httpx.WriteTimeout,
            SourceAddFailureKind.HTTPX_POOL_TIMEOUT: httpx.PoolTimeout,
            SourceAddFailureKind.HTTPX_NETWORK: httpx.NetworkError,
            SourceAddFailureKind.HTTPX_CONNECT: httpx.ConnectError,
            SourceAddFailureKind.HTTPX_READ: httpx.ReadError,
            SourceAddFailureKind.HTTPX_WRITE: httpx.WriteError,
            SourceAddFailureKind.HTTPX_CLOSE: httpx.CloseError,
            SourceAddFailureKind.HTTPX_PROXY: httpx.ProxyError,
            SourceAddFailureKind.HTTPX_PROTOCOL: httpx.ProtocolError,
            SourceAddFailureKind.HTTPX_LOCAL_PROTOCOL: httpx.LocalProtocolError,
            SourceAddFailureKind.HTTPX_REMOTE_PROTOCOL: httpx.RemoteProtocolError,
            SourceAddFailureKind.HTTPX_UNSUPPORTED_PROTOCOL: httpx.UnsupportedProtocol,
            SourceAddFailureKind.HTTPX_TOO_MANY_REDIRECTS: httpx.TooManyRedirects,
            SourceAddFailureKind.HTTPX_DECODING: httpx.DecodingError,
        }
        httpx_type = httpx_types.get(kind)
        if httpx_type is not None:
            if (record.request_method is None) != (record.request_url is None):
                raise BackendContractError("httpx failure has incomplete request evidence")
            request = (
                httpx.Request(record.request_method, record.request_url)
                if record.request_method is not None and record.request_url is not None
                else None
            )
            projected = httpx_type(record.message, request=request)
        else:
            builtin_types: dict[SourceAddFailureKind, type[Exception]] = {
                SourceAddFailureKind.BUILTIN_CONNECTION: ConnectionError,
                SourceAddFailureKind.BUILTIN_BROKEN_PIPE: BrokenPipeError,
                SourceAddFailureKind.BUILTIN_CONNECTION_ABORTED: ConnectionAbortedError,
                SourceAddFailureKind.BUILTIN_CONNECTION_REFUSED: ConnectionRefusedError,
                SourceAddFailureKind.BUILTIN_CONNECTION_RESET: ConnectionResetError,
                SourceAddFailureKind.BUILTIN_OS: OSError,
                SourceAddFailureKind.BUILTIN_RUNTIME: RuntimeError,
                SourceAddFailureKind.BUILTIN_TIMEOUT: TimeoutError,
                SourceAddFailureKind.BUILTIN_VALUE: ValueError,
            }
            builtin = builtin_types.get(kind)
            if builtin is None:
                raise BackendContractError(f"unsupported source-add failure kind {kind.value!r}")
            projected = builtin(*record.args)

    if record.source_id is not None and not hasattr(projected, "source_id"):
        projected.source_id = record.source_id  # type: ignore[attr-defined]
    if record.stage is not None:
        projected.stage = record.stage  # type: ignore[attr-defined]
    if record.unconfirmed:
        projected.unconfirmed = True  # type: ignore[attr-defined]
    projected.__cause__ = cause if record.explicit_cause else None
    projected.__context__ = context
    projected.__suppress_context__ = record.suppress_context
    return projected


def project_source_add_error(error: BackendError) -> Exception:
    """Reconstruct a bounded URL-source public failure outside the catch frame."""
    diagnostics = _diagnostics(error)
    record = diagnostics.get("source_add_failure")
    if not isinstance(record, SourceAddFailureRecord):
        raise BackendContractError(
            "source.add_url backend failure lacks SourceAddFailureRecord",
            operation=error.operation,
        )
    return _project_source_add_record(record)


__all__ = ["project_backend_error", "project_source_add_error"]
