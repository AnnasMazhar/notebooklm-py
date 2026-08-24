"""Closed compatibility projection for neutral semantic-backend failures."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from notebooklm._backend import (
    BackendContractError,
    BackendError,
    BackendErrorReason,
)
from notebooklm._backend_compat import project_backend_error
from notebooklm._operations import Operation
from notebooklm._web.backend import WebRpcBackend
from notebooklm.exceptions import (
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
    UnknownRPCMethodError,
)


def _round_trip(error: RPCError | NetworkError) -> Exception:
    neutral = WebRpcBackend._translate_error(Operation.NOTEBOOK_GET, error)
    return project_backend_error(neutral)


@pytest.mark.parametrize(
    ("original", "expected_type", "assert_diagnostics"),
    [
        (
            AuthError("authenticate", method_id="rpc", rpc_code=16),
            AuthError,
            lambda error: (
                error.method_id == "rpc" and error.rpc_code == 16 and error.recoverable is True
            ),
        ),
        (
            ClientError("client", status_code=404, method_id="rpc", rpc_code=5),
            ClientError,
            lambda error: error.status_code == 404 and error.rpc_code == 5,
        ),
        (
            DecodingError("decode", method_id="rpc", found_ids=["other"]),
            DecodingError,
            lambda error: error.method_id == "rpc" and error.found_ids == ["other"],
        ),
        (
            NetworkError("network", method_id="rpc"),
            NetworkError,
            lambda error: error.method_id == "rpc" and error.original_error is None,
        ),
        (
            RateLimitError("rate", retry_after=7, method_id="rpc"),
            RateLimitError,
            lambda error: error.retry_after == 7 and error.method_id == "rpc",
        ),
        (
            RPCResponseTooLargeError(
                "large",
                limit_bytes=10,
                bytes_read=11,
                method_id="rpc",
            ),
            RPCResponseTooLargeError,
            lambda error: (
                error.limit_bytes == 10 and error.bytes_read == 11 and error.method_id == "rpc"
            ),
        ),
        (
            RPCError(
                "rpc",
                method_id="rpc",
                raw_response="scrubbed",
                rpc_code=13,
                found_ids=["other"],
            ),
            RPCError,
            lambda error: (
                error.method_id == "rpc"
                and error.raw_response == "scrubbed"
                and error.rpc_code == 13
                and error.found_ids == ["other"]
            ),
        ),
        (
            ServerError("server", status_code=503, method_id="rpc"),
            ServerError,
            lambda error: error.status_code == 503 and error.method_id == "rpc",
        ),
        (
            RPCTimeoutError("timeout", timeout_seconds=3.0, method_id="rpc"),
            RPCTimeoutError,
            lambda error: (
                error.timeout_seconds == 3.0
                and error.method_id == "rpc"
                and error.original_error is None
            ),
        ),
        (
            UnknownRPCMethodError(
                "unknown shape",
                method_id=123,
                path=(0, 2),
                source="decoder",
                found_ids=[456, "other"],
                raw_response={"safe": "preview"},
                data_at_failure="safe data",
                rpc_code=13,
            ),
            UnknownRPCMethodError,
            lambda error: (
                error.method_id == 123
                and error.path == (0, 2)
                and error.source == "decoder"
                and error.found_ids == [456, "other"]
                and error.raw_response == {"safe": "preview"}
                and error.data_at_failure == "safe data"
                and error.rpc_code == 13
            ),
        ),
    ],
)
def test_closed_backend_reasons_reconstruct_public_exception_contract(
    original: RPCError | NetworkError,
    expected_type: type[Exception],
    assert_diagnostics: Callable[[object], bool],
) -> None:
    if isinstance(original, AuthError):
        original.recoverable = True

    projected = _round_trip(original)

    assert type(projected) is expected_type
    assert projected.args == original.args
    assert str(projected) == str(original)
    assert assert_diagnostics(projected)
    assert projected is not original


def test_unknown_rpc_base_message_is_not_rendered_with_diagnostics_twice() -> None:
    original = UnknownRPCMethodError(
        "unknown shape",
        method_id="rpc",
        path=(0, 2),
        source="decoder",
    )

    neutral = WebRpcBackend._translate_error(Operation.NOTEBOOK_GET, original)
    projected = project_backend_error(neutral)

    assert neutral.message == "unknown shape"
    assert str(projected) == str(original)
    assert str(projected).count("method_id='rpc'") == 1
    assert str(projected).count("path=(0, 2)") == 1


def test_notebook_mutation_specific_errors_reconstruct_from_neutral_evidence() -> None:
    not_found = project_backend_error(
        BackendError(
            "Notebook not found: missing",
            operation=Operation.NOTEBOOK_UPDATE,
            reason=BackendErrorReason.NOTEBOOK_NOT_FOUND,
            diagnostics={"notebook_id": "missing", "method_id": "rpc-get"},
        )
    )
    assert isinstance(not_found, NotebookNotFoundError)
    assert not_found.notebook_id == "missing"
    assert not_found.method_id == "rpc-get"

    limit = project_backend_error(
        BackendError(
            "notebook limit reached",
            operation=Operation.NOTEBOOK_CREATE,
            reason=BackendErrorReason.NOTEBOOK_LIMIT,
            diagnostics={
                "current_count": 499,
                "limit": 500,
                "original_message": "invalid argument",
                "original_reason": BackendErrorReason.RPC.value,
                "original_diagnostics": {
                    "method_id": "rpc-create",
                    "rpc_code": 3,
                    "found_ids": [],
                },
            },
        )
    )
    assert isinstance(limit, NotebookLimitError)
    assert (limit.current_count, limit.limit) == (499, 500)
    assert isinstance(limit.original_error, RPCError)
    assert limit.original_error.method_id == "rpc-create"
    assert limit.original_error.rpc_code == 3


def test_unknown_mutation_outcome_marker_survives_public_reconstruction() -> None:
    projected = project_backend_error(
        BackendError(
            "network",
            operation=Operation.NOTEBOOK_CREATE,
            reason=BackendErrorReason.NETWORK,
            diagnostics={},
            outcome_unknown=True,
        )
    )

    assert isinstance(projected, NetworkError)
    assert getattr(projected, "unconfirmed", False) is True


@pytest.mark.parametrize(
    "mutation",
    [
        BackendError("no reason", operation=Operation.NOTEBOOK_GET, diagnostics={}),
        BackendError(
            "no evidence",
            operation=Operation.NOTEBOOK_GET,
            reason=BackendErrorReason.RPC,
        ),
        BackendError(
            "bad evidence",
            operation=Operation.NOTEBOOK_GET,
            reason=BackendErrorReason.CLIENT,
            diagnostics={"status_code": "404"},
        ),
    ],
)
def test_incomplete_or_invalid_compatibility_evidence_fails_closed(
    mutation: BackendError,
) -> None:
    with pytest.raises(BackendContractError):
        project_backend_error(mutation)
