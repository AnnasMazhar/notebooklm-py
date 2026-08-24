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
from notebooklm.exceptions import RPCError
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
async def test_absolute_deadline_is_forwarded_as_executor_read_timeout() -> None:
    executor = _RecordingExecutor([[]])
    deadline = RuntimeDeadline(timeout=5.0, started_at=10.0, monotonic=lambda: 12.0)

    await _backend(executor).invoke(NOTEBOOK_LIST_DEF, NotebookListInput(), deadline=deadline)

    assert executor.calls[0].kwargs["read_timeout"] == 3.0


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
    assert executor.calls == []


@pytest.mark.asyncio
async def test_rpc_error_is_translated_with_scrubbed_diagnostics() -> None:
    executor = _RecordingExecutor(
        RPCError(
            "decode failed",
            method_id=RPCMethod.LIST_NOTEBOOKS.value,
            rpc_code=13,
            found_ids=["other"],
            raw_response="already-scrubbed",
        )
    )

    with pytest.raises(BackendError) as caught:
        await _backend(executor).invoke(
            NOTEBOOK_LIST_DEF,
            NotebookListInput(),
            deadline=None,
        )

    assert caught.value.message == "decode failed"
    assert caught.value.operation is Operation.NOTEBOOK_LIST
    assert caught.value.outcome_unknown is False
    assert caught.value.diagnostics == {
        "method_id": RPCMethod.LIST_NOTEBOOKS.value,
        "rpc_code": 13,
        "found_ids": ("other",),
        "raw_response": "already-scrubbed",
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
