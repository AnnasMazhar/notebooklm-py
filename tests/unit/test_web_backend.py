"""P1 web semantic-backend dispatch and registry tests."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from notebooklm._backend import (
    BackendContractError,
    BackendDeadlineExceededError,
    BackendError,
    BackendErrorReason,
    BackendKind,
    UnsupportedOperationError,
)
from notebooklm._deadline import RuntimeDeadline
from notebooklm._operations import CallPolicy, Operation, OperationDef
from notebooklm._records import (
    NOTEBOOK_GET_DEF,
    NOTEBOOK_LIST_DEF,
    SOURCE_GET_DEF,
    SOURCE_LIST_DEF,
    NotebookGetInput,
    NotebookListInput,
    NotebookListResult,
    SourceGetInput,
    SourceListInput,
)
from notebooklm._web.backend import WebRpcBackend
from notebooklm._web.registry import WEB_OPERATION_REGISTRY, WEB_SUPPORTED_OPERATIONS
from notebooklm.exceptions import (
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
from notebooklm.rpc import RPCMethod


@dataclass(frozen=True)
class _Call:
    method: RPCMethod
    params: list[Any]
    kwargs: dict[str, Any]


class _RecordingExecutor:
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[_Call] = []

    async def rpc_call(self, method: RPCMethod, params: list[Any], **kwargs: Any) -> Any:
        self.calls.append(_Call(method=method, params=params, kwargs=kwargs))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _transport_factory(**_kwargs: object) -> object:
    return object()


def _backend(executor: _RecordingExecutor) -> WebRpcBackend:
    return WebRpcBackend(executor, transport_factory=_transport_factory)  # type: ignore[arg-type]


def test_registry_is_closed_and_only_exposes_the_p2_1_read_slice() -> None:
    assert set(WEB_OPERATION_REGISTRY) == set(Operation)
    assert {
        Operation.NOTEBOOK_LIST,
        Operation.NOTEBOOK_GET,
        Operation.SOURCE_LIST,
        Operation.SOURCE_GET,
    } == WEB_SUPPORTED_OPERATIONS
    assert {
        operation: binding.definition
        for operation, binding in WEB_OPERATION_REGISTRY.items()
        if binding.is_supported
    } == {
        Operation.NOTEBOOK_LIST: NOTEBOOK_LIST_DEF,
        Operation.NOTEBOOK_GET: NOTEBOOK_GET_DEF,
        Operation.SOURCE_LIST: SOURCE_LIST_DEF,
        Operation.SOURCE_GET: SOURCE_GET_DEF,
    }
    assert all(
        binding.unsupported_reason
        for binding in WEB_OPERATION_REGISTRY.values()
        if not binding.is_supported
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    [operation for operation in Operation if operation not in WEB_SUPPORTED_OPERATIONS],
)
async def test_every_unsupported_operation_fails_before_executor(operation: Operation) -> None:
    executor = _RecordingExecutor([])
    backend = _backend(executor)
    definition = OperationDef(
        key=operation,
        policy=CallPolicy.READ,
        input_type=NotebookListInput,
        output_type=NotebookListResult,
    )

    with pytest.raises(UnsupportedOperationError) as caught:
        await backend.invoke(definition, NotebookListInput(), deadline=None)

    assert caught.value.operation is operation
    assert caught.value.backend_kind is BackendKind.WEB
    assert executor.calls == []


@pytest.mark.asyncio
async def test_noncanonical_definition_and_wrong_input_fail_before_executor() -> None:
    executor = _RecordingExecutor([])
    backend = _backend(executor)
    noncanonical = OperationDef(
        key=Operation.NOTEBOOK_LIST,
        policy=CallPolicy.MUTATION,
        input_type=NotebookListInput,
        output_type=NotebookListResult,
    )

    with pytest.raises(BackendContractError, match="non-canonical"):
        await backend.invoke(noncanonical, NotebookListInput(), deadline=None)
    with pytest.raises(BackendContractError, match="requires NotebookListInput"):
        await backend.invoke(NOTEBOOK_LIST_DEF, NotebookGetInput("nb"), deadline=None)

    assert executor.calls == []


@pytest.mark.asyncio
async def test_notebook_handlers_reuse_current_payloads_and_return_neutral_records() -> None:
    list_row = ["Listed", [], "nb-list", "📚"]
    get_row = [
        "Fetched",
        [],
        "nb-get",
        "🧬",
        None,
        [2, False, True, None, None, [1700000000, 0], 1, False, [1690000000, 0]],
        None,
        [[2, "Tutor"], [4]],
        None,
        [True, False, True],
        None,
        [["chat-session"]],
    ]
    executor = _RecordingExecutor([[list_row]], [get_row])
    backend = _backend(executor)

    listed = await backend.invoke(NOTEBOOK_LIST_DEF, NotebookListInput(), deadline=None)
    fetched = await backend.invoke(
        NOTEBOOK_GET_DEF,
        NotebookGetInput("nb-get"),
        deadline=None,
    )

    assert [(item.id, item.title, item.emoji) for item in listed.notebooks] == [
        ("nb-list", "Listed", "📚")
    ]
    assert listed.notebooks[0].chat_settings is None
    assert fetched.notebook is not None
    assert fetched.notebook.id == "nb-get"
    assert fetched.notebook.role == "editor"
    assert fetched.notebook.chat_settings is not None
    assert fetched.notebook.chat_settings.goal == "custom"
    assert fetched.notebook.chat_settings.response_length == "longer"
    assert fetched.notebook.chat_sessions[0].id == "chat-session"
    assert executor.calls[0].method is RPCMethod.LIST_NOTEBOOKS
    assert executor.calls[0].params == [None, 1, None, [2]]
    assert executor.calls[1].method is RPCMethod.GET_NOTEBOOK
    assert executor.calls[1].params[0] == "nb-get"
    assert executor.calls[1].kwargs["source_path"] == "/notebook/nb-get"


@pytest.mark.asyncio
async def test_notebook_get_empty_payload_is_typed_not_found_state() -> None:
    executor = _RecordingExecutor([[]])
    result = await _backend(executor).invoke(
        NOTEBOOK_GET_DEF,
        NotebookGetInput("missing"),
        deadline=None,
    )
    assert result.notebook is None


def _source_entry(source_id: str, *, status: int = 1, kind: int = 5) -> list[Any]:
    return [
        [source_id],
        f"Source {source_id}",
        [None, 11, [1704067200, 0], None, kind, None, None, ["https://example.com"]],
        [None, status],
    ]


@pytest.mark.asyncio
async def test_source_handlers_reuse_source_lister_and_apply_semantic_filters() -> None:
    source_rows = [_source_entry("src-web"), _source_entry("src-pdf", status=2, kind=3)]
    executor = _RecordingExecutor(
        [["Notebook", source_rows, "nb"]],
        [["Notebook", source_rows, "nb"]],
    )
    backend = _backend(executor)

    listed = await backend.invoke(
        SOURCE_LIST_DEF,
        SourceListInput(
            notebook_id="nb",
            statuses=frozenset({"processing"}),
            kinds=frozenset({"web_page"}),
        ),
        deadline=None,
    )
    fetched = await backend.invoke(
        SOURCE_GET_DEF,
        SourceGetInput(notebook_id="nb", source_id="src-pdf"),
        deadline=None,
    )

    assert [(item.id, item.status, item.kind) for item in listed.sources] == [
        ("src-web", "processing", "web_page")
    ]
    assert fetched.source is not None
    assert fetched.source.id == "src-pdf"
    assert fetched.source.kind == "pdf"
    assert all(call.method is RPCMethod.GET_NOTEBOOK for call in executor.calls)
    assert all(call.params[0] == "nb" for call in executor.calls)


@pytest.mark.asyncio
async def test_absolute_deadline_is_forwarded_unchanged_with_remaining_read_timeout() -> None:
    executor = _RecordingExecutor([[]])
    deadline = RuntimeDeadline(timeout=5.0, started_at=10.0, monotonic=lambda: 12.0)

    await _backend(executor).invoke(NOTEBOOK_LIST_DEF, NotebookListInput(), deadline=deadline)

    assert executor.calls[0].kwargs["read_timeout"] == 3.0
    assert executor.calls[0].kwargs["_retry_deadline"] is deadline


@pytest.mark.asyncio
async def test_expired_deadline_fails_before_executor() -> None:
    executor = _RecordingExecutor([])
    deadline = RuntimeDeadline(timeout=2.0, started_at=10.0, monotonic=lambda: 12.0)

    with pytest.raises(BackendDeadlineExceededError) as caught:
        await _backend(executor).invoke(
            NOTEBOOK_LIST_DEF,
            NotebookListInput(),
            deadline=deadline,
        )

    assert caught.value.operation is Operation.NOTEBOOK_LIST
    assert caught.value.reason is BackendErrorReason.TIMEOUT
    assert caught.value.diagnostics == {"timeout": 2.0, "remaining": 0.0}
    assert executor.calls == []


@pytest.mark.asyncio
async def test_rpc_error_is_translated_with_scrubbed_diagnostics() -> None:
    error = RPCError(
        "decode failed",
        method_id=RPCMethod.LIST_NOTEBOOKS.value,
        rpc_code=13,
        found_ids=["other"],
        raw_response="already-scrubbed",
    )
    executor = _RecordingExecutor(error)

    with pytest.raises(BackendError) as caught:
        await _backend(executor).invoke(
            NOTEBOOK_LIST_DEF,
            NotebookListInput(),
            deadline=None,
        )

    assert caught.value.message == "decode failed"
    assert caught.value.operation is Operation.NOTEBOOK_LIST
    assert caught.value.outcome_unknown is False
    assert caught.value.reason is BackendErrorReason.RPC
    assert caught.value.diagnostics == {
        "method_id": RPCMethod.LIST_NOTEBOOKS.value,
        "rpc_code": 13,
        "found_ids": ["other"],
        "raw_response": "already-scrubbed",
    }
    assert caught.value.diagnostics["found_ids"] is error.found_ids
    assert isinstance(caught.value.diagnostics["found_ids"], list)


@pytest.mark.parametrize(
    ("error", "reason", "specific_diagnostics"),
    [
        (AuthError("auth"), BackendErrorReason.AUTH, {"recoverable": False}),
        (
            ClientError("client", status_code=404, rpc_code=5),
            BackendErrorReason.CLIENT,
            {"status_code": 404},
        ),
        (DecodingError("decode"), BackendErrorReason.DECODING, {}),
        (NetworkError("network"), BackendErrorReason.NETWORK, {}),
        (
            RateLimitError("rate", retry_after=7),
            BackendErrorReason.RATE_LIMIT,
            {"retry_after": 7},
        ),
        (
            RPCResponseTooLargeError("large", limit_bytes=10, bytes_read=11),
            BackendErrorReason.RESPONSE_TOO_LARGE,
            {"limit_bytes": 10, "bytes_read": 11},
        ),
        (RPCError("rpc"), BackendErrorReason.RPC, {}),
        (
            ServerError("server", status_code=503),
            BackendErrorReason.SERVER,
            {"status_code": 503},
        ),
        (
            RPCTimeoutError("timeout", timeout_seconds=3.0),
            BackendErrorReason.TIMEOUT,
            {"timeout_seconds": 3.0},
        ),
        (
            UnknownRPCMethodError(
                "unknown",
                path=(0, 2),
                source="test",
                data_at_failure="scrubbed",
            ),
            BackendErrorReason.UNKNOWN_RPC_METHOD,
            {"path": (0, 2), "source": "test", "data_at_failure": "scrubbed"},
        ),
    ],
)
def test_web_error_reasons_are_closed_and_preserve_reconstruction_evidence(
    error: RPCError | NetworkError,
    reason: BackendErrorReason,
    specific_diagnostics: dict[str, object],
) -> None:
    translated = WebRpcBackend._translate_error(Operation.NOTEBOOK_LIST, error)

    assert translated.reason is reason
    assert (
        type(error)
        is {
            BackendErrorReason.AUTH: AuthError,
            BackendErrorReason.CLIENT: ClientError,
            BackendErrorReason.DECODING: DecodingError,
            BackendErrorReason.NETWORK: NetworkError,
            BackendErrorReason.RATE_LIMIT: RateLimitError,
            BackendErrorReason.RESPONSE_TOO_LARGE: RPCResponseTooLargeError,
            BackendErrorReason.RPC: RPCError,
            BackendErrorReason.SERVER: ServerError,
            BackendErrorReason.TIMEOUT: RPCTimeoutError,
            BackendErrorReason.UNKNOWN_RPC_METHOD: UnknownRPCMethodError,
        }[translated.reason]
    )
    assert set(BackendErrorReason) == {
        BackendErrorReason.AUTH,
        BackendErrorReason.CLIENT,
        BackendErrorReason.DECODING,
        BackendErrorReason.NETWORK,
        BackendErrorReason.RATE_LIMIT,
        BackendErrorReason.RESPONSE_TOO_LARGE,
        BackendErrorReason.RPC,
        BackendErrorReason.SERVER,
        BackendErrorReason.TIMEOUT,
        BackendErrorReason.UNKNOWN_RPC_METHOD,
    }
    assert translated.diagnostics is not None
    assert {
        name: translated.diagnostics[name] for name in specific_diagnostics
    } == specific_diagnostics


def test_unreviewed_rpc_error_subclass_fails_closed() -> None:
    class _UnreviewedRPCError(RPCError):
        pass

    with pytest.raises(BackendContractError, match="unclassified web error type"):
        WebRpcBackend._translate_error(Operation.NOTEBOOK_LIST, _UnreviewedRPCError("new"))


@pytest.mark.asyncio
async def test_nonexpired_transport_timeout_remains_a_typed_backend_timeout() -> None:
    timeout = RPCTimeoutError(
        "request timed out",
        method_id=RPCMethod.LIST_NOTEBOOKS.value,
        timeout_seconds=3.0,
    )
    executor = _RecordingExecutor(timeout)
    deadline = RuntimeDeadline(timeout=5.0, started_at=10.0, monotonic=lambda: 12.0)

    with pytest.raises(BackendError) as caught:
        await _backend(executor).invoke(
            NOTEBOOK_LIST_DEF,
            NotebookListInput(),
            deadline=deadline,
        )

    assert caught.value.operation is Operation.NOTEBOOK_LIST
    assert type(caught.value) is BackendError
    assert caught.value.reason is BackendErrorReason.TIMEOUT
    assert caught.value.__cause__ is timeout
    assert caught.value.diagnostics == {
        "method_id": RPCMethod.LIST_NOTEBOOKS.value,
        "rpc_code": None,
        "found_ids": None,
        "raw_response": None,
        "timeout_seconds": 3.0,
    }


@pytest.mark.asyncio
async def test_expired_midflight_transport_timeout_maps_to_semantic_deadline_error() -> None:
    timeout = RPCTimeoutError(
        "request timed out",
        method_id=RPCMethod.LIST_NOTEBOOKS.value,
        timeout_seconds=3.0,
    )
    executor = _RecordingExecutor(timeout)
    times = iter((12.0, 12.0, 16.0, 16.0))
    deadline = RuntimeDeadline(timeout=5.0, started_at=10.0, monotonic=lambda: next(times))

    with pytest.raises(BackendDeadlineExceededError) as caught:
        await _backend(executor).invoke(
            NOTEBOOK_LIST_DEF,
            NotebookListInput(),
            deadline=deadline,
        )

    assert caught.value.reason is BackendErrorReason.TIMEOUT
    assert caught.value.outcome_unknown is False
    assert caught.value.__cause__ is timeout
    assert caught.value.diagnostics == {
        "method_id": RPCMethod.LIST_NOTEBOOKS.value,
        "rpc_code": None,
        "found_ids": None,
        "raw_response": None,
        "timeout_seconds": 3.0,
        "timeout": 5.0,
        "remaining": 0.0,
    }


@pytest.mark.asyncio
async def test_close_does_not_close_client_owned_executor() -> None:
    executor = _RecordingExecutor([])
    backend = _backend(executor)

    await backend.close()

    assert not hasattr(executor, "close")
    with pytest.raises(BackendContractError, match="closed"):
        await backend.invoke(NOTEBOOK_LIST_DEF, NotebookListInput(), deadline=None)
    assert executor.calls == []


def test_no_feature_runtime_reads_private_backend_before_p2() -> None:
    """P1 keeps the port inert; P2 removes this pin as each facade delegates."""
    package = Path(__file__).resolve().parents[2] / "src" / "notebooklm"
    allowed = {
        package / "_client_assembly.py",
        package / "client.py",  # annotation-only declaration
    }
    allowed.update((package / "_web").rglob("*.py"))
    violations: list[str] = []
    for path in package.rglob("*.py"):
        if path in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "_backend":
                violations.append(f"{path.relative_to(package)}:{node.lineno}")
    assert violations == []
