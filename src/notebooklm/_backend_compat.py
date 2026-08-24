"""Project neutral backend failures onto the legacy public exception contract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from ._backend import BackendContractError, BackendError, BackendErrorReason
from .exceptions import (
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
        return NetworkError(
            error.message,
            method_id=cast(str | None, _optional(error, diagnostics, "method_id", str)),
        )
    if reason is BackendErrorReason.TIMEOUT:
        return RPCTimeoutError(
            error.message,
            method_id=cast(str | None, _optional(error, diagnostics, "method_id", str)),
            timeout_seconds=cast(
                float | None,
                _optional(error, diagnostics, "timeout_seconds", (float, int)),
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
        return UnknownRPCMethodError(
            error.message,
            method_id=cast(str | int | None, method_id),
            path=cast(tuple[int, ...] | None, path),
            source=cast(str | None, source),
            found_ids=cast(list[str | int] | None, found_ids),
            raw_response=diagnostics.get("raw_response"),
            data_at_failure=diagnostics.get("data_at_failure"),
            rpc_code=cast(str | int | None, rpc_code),
        )

    rpc = _rpc_diagnostics(error)
    if reason is BackendErrorReason.AUTH:
        projected = AuthError(error.message, **rpc)
        recoverable = _optional(error, diagnostics, "recoverable", bool)
        projected.recoverable = bool(recoverable)
        return projected
    if reason is BackendErrorReason.CLIENT:
        return ClientError(
            error.message,
            status_code=_required_int(error, diagnostics, "status_code"),
            **rpc,
        )
    if reason is BackendErrorReason.DECODING:
        return DecodingError(error.message, **rpc)
    if reason is BackendErrorReason.RATE_LIMIT:
        return RateLimitError(
            error.message,
            retry_after=_required_int(error, diagnostics, "retry_after"),
            **rpc,
        )
    if reason is BackendErrorReason.RESPONSE_TOO_LARGE:
        return RPCResponseTooLargeError(
            error.message,
            limit_bytes=_required_int(error, diagnostics, "limit_bytes"),
            bytes_read=_required_int(error, diagnostics, "bytes_read"),
            method_id=rpc["method_id"],
        )
    if reason is BackendErrorReason.RPC:
        return RPCError(error.message, **rpc)
    if reason is BackendErrorReason.SERVER:
        return ServerError(
            error.message,
            status_code=_required_int(error, diagnostics, "status_code"),
            **rpc,
        )
    raise BackendContractError(
        f"unsupported backend compatibility reason {reason.value!r}",
        operation=error.operation,
    )


__all__ = ["project_backend_error"]
