"""P1 web semantic-backend dispatch and registry tests."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from notebooklm._artifact.payloads import build_audio_artifact_params
from notebooklm._backend import (
    BackendContractError,
    BackendDeadlineExceededError,
    BackendError,
    BackendErrorReason,
    BackendKind,
    UnsupportedOperationError,
)
from notebooklm._backend_compat import project_backend_error
from notebooklm._deadline import RuntimeDeadline
from notebooklm._notebook_payloads import (
    build_create_notebook_params,
    build_get_notebook_params,
    build_update_notebook_params,
)
from notebooklm._operations import CallPolicy, Operation, OperationDef
from notebooklm._records import (
    ARTIFACT_GENERATE_AUDIO_DEF,
    ARTIFACT_GET_DEF,
    ARTIFACT_LIST_DEF,
    NOTE_CREATE_DEF,
    NOTE_DELETE_DEF,
    NOTE_GET_DEF,
    NOTE_LIST_DEF,
    NOTE_UPDATE_DEF,
    NOTEBOOK_CREATE_DEF,
    NOTEBOOK_DELETE_DEF,
    NOTEBOOK_GET_DEF,
    NOTEBOOK_LIST_DEF,
    NOTEBOOK_UPDATE_DEF,
    SOURCE_ADD_URL_DEF,
    SOURCE_GET_DEF,
    SOURCE_LIST_DEF,
    AudioGenerateInput,
    NotebookCreateInput,
    NotebookDeleteInput,
    NotebookDeleteResult,
    NotebookGetInput,
    NotebookListInput,
    NotebookListResult,
    NotebookUpdateInput,
    NoteCreateInput,
    NoteDeleteInput,
    NoteGetInput,
    NoteListInput,
    NoteUpdateInput,
    SourceAddCommitState,
    SourceAddFailureKind,
    SourceAddFailureRecord,
    SourceAddTitleState,
    SourceAddUrlInput,
    SourceAddUrlReceipt,
    SourceGetInput,
    SourceListInput,
)
from notebooklm._source.upload_payloads import build_template_block
from notebooklm._web.backend import WebRpcBackend
from notebooklm._web.registry import (
    WEB_OPERATION_REGISTRY,
    WEB_STAGED_OPERATIONS,
    WEB_SUPPORTED_OPERATIONS,
)
from notebooklm.exceptions import (
    ArtifactFeatureUnavailableError,
    AuthError,
    ClientError,
    DecodingError,
    IdempotencyVariantError,
    NetworkError,
    NotebookLMError,
    NotebookNotFoundError,
    RateLimitError,
    RPCError,
    RPCResponseTooLargeError,
    RPCTimeoutError,
    ServerError,
    UnknownRPCMethodError,
)
from notebooklm.rpc import AudioFormat, AudioLength, RPCMethod


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


def test_registry_is_closed_and_exposes_only_reviewed_live_handlers() -> None:
    assert set(WEB_OPERATION_REGISTRY) == set(Operation)
    assert {
        Operation.NOTEBOOK_LIST,
        Operation.NOTEBOOK_GET,
        Operation.NOTEBOOK_CREATE,
        Operation.NOTEBOOK_UPDATE,
        Operation.NOTEBOOK_DELETE,
        Operation.SOURCE_LIST,
        Operation.SOURCE_GET,
        Operation.SOURCE_ADD_URL,
        Operation.NOTE_LIST,
        Operation.NOTE_GET,
        Operation.NOTE_CREATE,
        Operation.NOTE_UPDATE,
        Operation.NOTE_DELETE,
        Operation.ARTIFACT_LIST,
        Operation.ARTIFACT_GET,
        Operation.ARTIFACT_GENERATE_AUDIO,
    } == WEB_SUPPORTED_OPERATIONS
    assert {
        operation: binding.definition
        for operation, binding in WEB_OPERATION_REGISTRY.items()
        if binding.is_supported
    } == {
        Operation.NOTEBOOK_LIST: NOTEBOOK_LIST_DEF,
        Operation.NOTEBOOK_GET: NOTEBOOK_GET_DEF,
        Operation.NOTEBOOK_CREATE: NOTEBOOK_CREATE_DEF,
        Operation.NOTEBOOK_UPDATE: NOTEBOOK_UPDATE_DEF,
        Operation.NOTEBOOK_DELETE: NOTEBOOK_DELETE_DEF,
        Operation.SOURCE_LIST: SOURCE_LIST_DEF,
        Operation.SOURCE_GET: SOURCE_GET_DEF,
        Operation.SOURCE_ADD_URL: SOURCE_ADD_URL_DEF,
        Operation.NOTE_LIST: NOTE_LIST_DEF,
        Operation.NOTE_GET: NOTE_GET_DEF,
        Operation.NOTE_CREATE: NOTE_CREATE_DEF,
        Operation.NOTE_UPDATE: NOTE_UPDATE_DEF,
        Operation.NOTE_DELETE: NOTE_DELETE_DEF,
        Operation.ARTIFACT_LIST: ARTIFACT_LIST_DEF,
        Operation.ARTIFACT_GET: ARTIFACT_GET_DEF,
        Operation.ARTIFACT_GENERATE_AUDIO: ARTIFACT_GENERATE_AUDIO_DEF,
    }
    assert all(
        binding.unsupported_reason
        for binding in WEB_OPERATION_REGISTRY.values()
        if not binding.is_supported
    )
    assert not WEB_STAGED_OPERATIONS
    assert WEB_OPERATION_REGISTRY[Operation.SOURCE_ADD_URL].definition is SOURCE_ADD_URL_DEF


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
async def test_note_handlers_preserve_classification_exact_id_and_wire_shapes() -> None:
    rows = [
        [
            ["note-123", ["note-123", "Body", None, None, "Title"]],
            ["note-12", ["note-12", "Prefix", None, None, "Other"]],
            ["mind-map", '{"name":"Map","children":[]}'],
            ["deleted", None, 2],
        ]
    ]
    executor = _RecordingExecutor(
        rows,
        rows,
        [["created", "", [1, "user", [1_700_000_000, 0]], None, "Ignored"]],
        None,
        None,
    )
    backend = _backend(executor)
    deadline = RuntimeDeadline(timeout=5.0, started_at=10.0, monotonic=lambda: 11.0)

    listed = await backend.invoke(NOTE_LIST_DEF, NoteListInput("nb"), deadline=deadline)
    selected = await backend.invoke(
        NOTE_GET_DEF,
        NoteGetInput("nb", "note-123"),
        deadline=deadline,
    )
    created = await backend.invoke(
        NOTE_CREATE_DEF,
        NoteCreateInput("nb", "Title", "Body"),
        deadline=deadline,
    )
    await backend.invoke(
        NOTE_UPDATE_DEF,
        NoteUpdateInput("nb", "note-123", "New body", "New title"),
        deadline=deadline,
    )
    await backend.invoke(
        NOTE_DELETE_DEF,
        NoteDeleteInput("nb", "note-123"),
        deadline=deadline,
    )

    assert [note.id for note in listed.notes] == ["note-123", "note-12"]
    assert selected.note is not None and selected.note.id == "note-123"
    assert (created.note.id, created.note.title, created.note.content) == (
        "created",
        "Title",
        "Body",
    )
    assert [call.method for call in executor.calls] == [
        RPCMethod.GET_NOTES_AND_MIND_MAPS,
        RPCMethod.GET_NOTES_AND_MIND_MAPS,
        RPCMethod.CREATE_NOTE,
        RPCMethod.UPDATE_NOTE,
        RPCMethod.DELETE_NOTE,
    ]
    assert all(call.kwargs["_retry_deadline"] is deadline for call in executor.calls)
    assert executor.calls[2].params == ["nb", "", [1], None, "Title"]
    assert executor.calls[3].params == ["nb", "note-123", [[["New body", "New title", [], 0]]]]
    assert executor.calls[4].params == ["nb", None, ["note-123"]]


@pytest.mark.asyncio
async def test_audio_generate_reuses_payload_builder_and_one_absolute_deadline() -> None:
    executor = _RecordingExecutor([["audio-id", "Audio", 1, None, 1]])
    deadline = RuntimeDeadline(timeout=5.0, started_at=10.0, monotonic=lambda: 12.0)
    value = AudioGenerateInput(
        notebook_id="nb-audio",
        source_ids=("src-a", "src-b"),
        language="fr",
        instructions="Compare the sources",
        audio_format="debate",
        audio_length="long",
    )

    result = await _backend(executor).invoke(
        ARTIFACT_GENERATE_AUDIO_DEF,
        value,
        deadline=deadline,
    )

    assert (result.status.task_id, result.status.status) == ("audio-id", "pending")
    assert [call.method for call in executor.calls] == [RPCMethod.CREATE_ARTIFACT]
    assert executor.calls[0].params == build_audio_artifact_params(
        "nb-audio",
        ["src-a", "src-b"],
        language="fr",
        instructions="Compare the sources",
        audio_format=AudioFormat.DEBATE,
        audio_length=AudioLength.LONG,
    )
    assert executor.calls[0].kwargs["read_timeout"] == 3.0
    assert executor.calls[0].kwargs["disable_internal_retries"] is False
    assert executor.calls[0].kwargs["operation_variant"] is None


@pytest.mark.asyncio
async def test_audio_generate_none_language_uses_current_profile_default(monkeypatch) -> None:
    monkeypatch.setattr("notebooklm._web.backend.get_default_language", lambda: "ja")
    executor = _RecordingExecutor([["audio-id", "Audio", 1, None, 1]])

    await _backend(executor).invoke(
        ARTIFACT_GENERATE_AUDIO_DEF,
        AudioGenerateInput("nb", (), language=None),
        deadline=None,
    )

    assert executor.calls[0].params[2][6][1][4] == "ja"


@pytest.mark.asyncio
async def test_audio_generate_resolves_all_sources_once_inside_backend() -> None:
    executor = _RecordingExecutor(
        [["Notebook", [[["src-a"], "A"], [["src-b"], "B"]], "nb-audio"]],
        [["audio-id", "Audio", 1, None, 1]],
    )

    await _backend(executor).invoke(
        ARTIFACT_GENERATE_AUDIO_DEF,
        AudioGenerateInput("nb-audio", source_ids=None),
        deadline=None,
    )

    assert [call.method for call in executor.calls] == [
        RPCMethod.GET_NOTEBOOK,
        RPCMethod.CREATE_ARTIFACT,
    ]
    assert executor.calls[0].params == build_get_notebook_params("nb-audio")
    assert executor.calls[1].params[2][3] == [[["src-a"]], [["src-b"]]]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [
        AudioGenerateInput("nb", (), audio_format="future_format"),
        AudioGenerateInput("nb", (), audio_length="future_length"),
    ],
)
async def test_audio_generate_rejects_unreviewed_options_before_executor(
    value: AudioGenerateInput,
) -> None:
    executor = _RecordingExecutor([])

    with pytest.raises(BackendContractError, match="unrecognized audio"):
        await _backend(executor).invoke(ARTIFACT_GENERATE_AUDIO_DEF, value, deadline=None)

    assert executor.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "artifact_type"),
    [
        (None, "audio"),
        ([[None, "Audio", 1, None, 1]], "artifact"),
    ],
)
async def test_audio_generate_feature_unavailable_reconstructs_public_error(
    response: object,
    artifact_type: str,
) -> None:
    executor = _RecordingExecutor(response)

    with pytest.raises(BackendError) as caught:
        await _backend(executor).invoke(
            ARTIFACT_GENERATE_AUDIO_DEF,
            AudioGenerateInput("nb", ()),
            deadline=None,
        )

    assert caught.value.reason is BackendErrorReason.ARTIFACT_FEATURE_UNAVAILABLE
    projected = project_backend_error(caught.value)
    assert isinstance(projected, ArtifactFeatureUnavailableError)
    assert projected.artifact_type == artifact_type
    assert projected.method_id == RPCMethod.CREATE_ARTIFACT.value


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


@pytest.mark.asyncio
async def test_notebook_create_uses_baseline_and_disables_executor_retries() -> None:
    created_row = [
        "Daily News",
        None,
        "nb-new",
        None,
        None,
        [None, False, None, None, None, [1704067200, 0]],
    ]
    executor = _RecordingExecutor([], created_row)

    result = await _backend(executor).invoke(
        NOTEBOOK_CREATE_DEF,
        NotebookCreateInput("Daily News"),
        deadline=None,
    )

    assert (result.notebook.id, result.notebook.title) == ("nb-new", "Daily News")
    assert [call.method for call in executor.calls] == [
        RPCMethod.LIST_NOTEBOOKS,
        RPCMethod.CREATE_NOTEBOOK,
    ]
    assert executor.calls[1].params == build_create_notebook_params("Daily News")
    assert executor.calls[1].kwargs["disable_internal_retries"] is True


@pytest.mark.asyncio
async def test_notebook_create_adopts_unique_baseline_diff_after_transport_loss() -> None:
    old_row = ["Daily News", [], "nb-old"]
    new_row = ["Daily News", [], "nb-landed"]
    executor = _RecordingExecutor(
        [[old_row]],
        ServerError("bad gateway", status_code=502),
        [[old_row, new_row]],
    )

    result = await _backend(executor).invoke(
        NOTEBOOK_CREATE_DEF,
        NotebookCreateInput("Daily News"),
        deadline=None,
    )

    assert result.notebook.id == "nb-landed"
    assert [call.method for call in executor.calls] == [
        RPCMethod.LIST_NOTEBOOKS,
        RPCMethod.CREATE_NOTEBOOK,
        RPCMethod.LIST_NOTEBOOKS,
    ]
    assert sum(call.method is RPCMethod.CREATE_NOTEBOOK for call in executor.calls) == 1


@pytest.mark.asyncio
async def test_notebook_title_update_mutates_then_reads_back() -> None:
    executor = _RecordingExecutor(None, [["Renamed", [], "nb-1"]])

    result = await _backend(executor).invoke(
        NOTEBOOK_UPDATE_DEF,
        NotebookUpdateInput("nb-1", title="Renamed"),
        deadline=None,
    )

    assert (result.notebook.id, result.notebook.title) == ("nb-1", "Renamed")
    assert [call.method for call in executor.calls] == [
        RPCMethod.RENAME_NOTEBOOK,
        RPCMethod.GET_NOTEBOOK,
    ]
    assert executor.calls[0].params == build_update_notebook_params("nb-1", title="Renamed")
    assert executor.calls[1].params == build_get_notebook_params("nb-1")
    assert executor.calls[0].kwargs["source_path"] == "/"
    assert executor.calls[0].kwargs["allow_null"] is True
    assert executor.calls[1].kwargs["source_path"] == "/notebook/nb-1"


@pytest.mark.asyncio
async def test_notebook_update_readback_not_found_preserves_public_error_context() -> None:
    original = ClientError(
        "not found",
        status_code=404,
        method_id=RPCMethod.GET_NOTEBOOK.value,
        raw_response="scrubbed response",
        rpc_code=5,
    )
    executor = _RecordingExecutor(None, original)

    with pytest.raises(BackendError) as exc_info:
        await _backend(executor).invoke(
            NOTEBOOK_UPDATE_DEF,
            NotebookUpdateInput("nb-missing", title="Renamed"),
            deadline=None,
        )

    projected = project_backend_error(exc_info.value)

    assert isinstance(projected, NotebookNotFoundError)
    assert projected.notebook_id == "nb-missing"
    assert projected.method_id == RPCMethod.GET_NOTEBOOK.value
    assert isinstance(projected.__cause__, ClientError)
    assert projected.__cause__.status_code == 404
    assert projected.__cause__.rpc_code == 5
    assert projected.__cause__.raw_response == "scrubbed response"


@pytest.mark.asyncio
async def test_notebook_delete_is_one_id_and_returns_empty_result() -> None:
    executor = _RecordingExecutor(None)

    result = await _backend(executor).invoke(
        NOTEBOOK_DELETE_DEF,
        NotebookDeleteInput("nb-1"),
        deadline=None,
    )

    assert result == NotebookDeleteResult()
    assert executor.calls[0].method is RPCMethod.DELETE_NOTEBOOK
    assert executor.calls[0].params == [["nb-1"], [2]]


def _source_entry(
    source_id: str,
    *,
    title: str | None = None,
    url: str = "https://example.com",
    status: int = 1,
    kind: int = 5,
) -> list[Any]:
    return [
        [source_id],
        title or f"Source {source_id}",
        [None, 11, [1704067200, 0], None, kind, None, None, [url]],
        [None, status],
    ]


def _source_result(
    source_id: str,
    *,
    title: str,
    url: str,
    kind: int = 5,
) -> list[Any]:
    return [[_source_entry(source_id, title=title, url=url, status=2, kind=kind)]]


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
@pytest.mark.parametrize(
    ("url", "kind", "source_spec"),
    [
        (
            "https://example.com/article",
            5,
            [
                None,
                None,
                ["https://example.com/article"],
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                1,
            ],
        ),
        (
            "https://youtu.be/dQw4w9WgXcQ",
            9,
            [
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                ["https://youtu.be/dQw4w9WgXcQ"],
                None,
                None,
                1,
            ],
        ),
    ],
)
async def test_live_url_handler_preserves_regular_and_hidden_youtube_payloads(
    url: str,
    kind: int,
    source_spec: list[object],
) -> None:
    executor = _RecordingExecutor(
        [["Notebook", [], "nb"]],
        _source_result("src-new", title="Upstream", url=url, kind=kind),
    )

    result = await _backend(executor).invoke(
        SOURCE_ADD_URL_DEF,
        SourceAddUrlInput("nb", url),
        deadline=None,
    )

    assert (result.source.id, result.source.url) == ("src-new", url)
    assert result.receipt == SourceAddUrlReceipt(
        SourceAddCommitState.CREATED,
        SourceAddTitleState.NOT_REQUESTED,
    )
    assert [call.method for call in executor.calls] == [
        RPCMethod.GET_NOTEBOOK,
        RPCMethod.ADD_SOURCE,
    ]
    assert executor.calls[1].params == [[source_spec], "nb", build_template_block()]
    assert executor.calls[1].kwargs["disable_internal_retries"] is True
    assert executor.calls[1].kwargs["operation_variant"] == "url"


@pytest.mark.asyncio
async def test_url_handler_reconciles_only_one_new_exact_url_and_renames_without_repost() -> None:
    url = "https://example.com/article"
    old = _source_entry("src-old", title="Old", url=url, status=2)
    recovered = _source_entry("src-new", title="Upstream", url=url, status=2)
    executor = _RecordingExecutor(
        [["Notebook", [old], "nb"]],
        ServerError("lost response", status_code=502),
        [["Notebook", [old, recovered], "nb"]],
        [["src-new"], "Requested"],
    )

    result = await _backend(executor).invoke(
        SOURCE_ADD_URL_DEF,
        SourceAddUrlInput("nb", url, requested_title="  Requested  "),
        deadline=None,
    )

    assert (result.source.id, result.source.title, result.source.url) == (
        "src-new",
        "Requested",
        url,
    )
    assert result.receipt == SourceAddUrlReceipt(
        SourceAddCommitState.RECONCILED,
        SourceAddTitleState.RENAMED,
    )
    assert [call.method for call in executor.calls] == [
        RPCMethod.GET_NOTEBOOK,
        RPCMethod.ADD_SOURCE,
        RPCMethod.GET_NOTEBOOK,
        RPCMethod.UPDATE_SOURCE,
    ]
    assert sum(call.method is RPCMethod.ADD_SOURCE for call in executor.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "probe_response",
    [
        [
            "Notebook",
            [
                _source_entry("src-one", url="https://example.com/article"),
                _source_entry("src-two", url="https://example.com/article"),
            ],
            "nb",
        ],
        DecodingError("probe could not decode"),
    ],
)
async def test_url_handler_fails_closed_when_reconciliation_is_ambiguous_or_unanswered(
    probe_response: object,
) -> None:
    executor = _RecordingExecutor(
        [["Notebook", [], "nb"]],
        ServerError("lost response", status_code=502),
        [probe_response] if isinstance(probe_response, list) else probe_response,
    )

    with pytest.raises(BackendError) as caught:
        await _backend(executor).invoke(
            SOURCE_ADD_URL_DEF,
            SourceAddUrlInput("nb", "https://example.com/article"),
            deadline=None,
        )

    assert caught.value.outcome_unknown is True
    assert caught.value.reason is BackendErrorReason.SOURCE_ADD
    assert caught.value.diagnostics is not None
    assert caught.value.diagnostics["receipt"] == SourceAddUrlReceipt(
        SourceAddCommitState.UNKNOWN,
        SourceAddTitleState.NOT_ATTEMPTED,
        outcome_unknown=True,
    )
    failure = caught.value.diagnostics["source_add_failure"]
    assert failure.message == str(caught.value)
    assert failure.unconfirmed is True
    assert sum(call.method is RPCMethod.ADD_SOURCE for call in executor.calls) == 1


@pytest.mark.asyncio
async def test_url_handler_title_failure_is_best_effort_and_never_reposts() -> None:
    url = "https://example.com/article"
    executor = _RecordingExecutor(
        [["Notebook", [], "nb"]],
        _source_result("src-new", title="Upstream", url=url),
        ServerError("rename failed", status_code=503),
    )

    result = await _backend(executor).invoke(
        SOURCE_ADD_URL_DEF,
        SourceAddUrlInput("nb", url, requested_title="Requested"),
        deadline=None,
    )

    assert result.source.title == "Upstream"
    assert result.receipt.title_state is SourceAddTitleState.RENAME_FAILED
    assert [call.method for call in executor.calls] == [
        RPCMethod.GET_NOTEBOOK,
        RPCMethod.ADD_SOURCE,
        RPCMethod.UPDATE_SOURCE,
    ]


@pytest.mark.asyncio
async def test_notebook_create_marks_neutral_probe_failure_unconfirmed() -> None:
    probe_error = BackendError(
        "probe deadline",
        operation=Operation.NOTEBOOK_CREATE,
        reason=BackendErrorReason.TIMEOUT,
        diagnostics={"timeout_seconds": 3.0},
    )
    executor = _RecordingExecutor(
        [],
        ServerError("create response lost", status_code=502),
        probe_error,
    )

    with pytest.raises(BackendError) as caught:
        await _backend(executor).invoke(
            NOTEBOOK_CREATE_DEF,
            NotebookCreateInput("Daily News"),
            deadline=None,
        )

    assert caught.value is not probe_error
    assert caught.value.reason is BackendErrorReason.TIMEOUT
    assert caught.value.outcome_unknown is True


@pytest.mark.asyncio
async def test_url_composite_forwards_one_absolute_deadline_to_baseline_add_and_wait() -> None:
    url = "https://example.com/article"
    ready = _source_entry("src-new", title="Upstream", url=url, status=2)
    executor = _RecordingExecutor(
        [["Notebook", [], "nb"]],
        _source_result("src-new", title="Upstream", url=url),
        [["Notebook", [ready], "nb"]],
    )
    deadline = RuntimeDeadline(timeout=30.0, started_at=10.0, monotonic=lambda: 12.0)

    await _backend(executor).invoke(
        SOURCE_ADD_URL_DEF,
        SourceAddUrlInput("nb", url, wait=True, wait_timeout=17.0),
        deadline=deadline,
    )

    assert [call.method for call in executor.calls] == [
        RPCMethod.GET_NOTEBOOK,
        RPCMethod.ADD_SOURCE,
        RPCMethod.GET_NOTEBOOK,
    ]
    assert all(call.kwargs["_retry_deadline"] is deadline for call in executor.calls)
    assert all(call.kwargs["read_timeout"] == 28.0 for call in executor.calls)


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
    assert caught.value.diagnostics == {
        "timeout": 2.0,
        "remaining": 0.0,
        "timeout_seconds": 2.0,
    }
    assert executor.calls == []


@pytest.mark.asyncio
async def test_mutation_expiring_before_dispatch_is_not_marked_unconfirmed() -> None:
    executor = _RecordingExecutor(None)
    times = iter((11.0, 12.0))
    deadline = RuntimeDeadline(timeout=1.5, started_at=10.0, monotonic=lambda: next(times))

    with pytest.raises(BackendDeadlineExceededError) as caught:
        await _backend(executor).invoke(
            NOTEBOOK_DELETE_DEF,
            NotebookDeleteInput("nb-1"),
            deadline=deadline,
        )

    assert executor.calls == []
    assert caught.value.outcome_unknown is False
    projected = project_backend_error(caught.value)
    assert isinstance(projected, RPCTimeoutError)
    assert getattr(projected, "unconfirmed", False) is False


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
    assert caught.value.diagnostics is not None
    assert {
        name: caught.value.diagnostics[name]
        for name in ("method_id", "rpc_code", "found_ids", "raw_response")
    } == {
        "method_id": RPCMethod.LIST_NOTEBOOKS.value,
        "rpc_code": 13,
        "found_ids": ["other"],
        "raw_response": "already-scrubbed",
    }
    assert caught.value.diagnostics["public_error_failure"].kind is SourceAddFailureKind.RPC
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
    assert translated.message == str(error.args[0])
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
        BackendErrorReason.ARTIFACT_FEATURE_UNAVAILABLE,
        BackendErrorReason.CLIENT,
        BackendErrorReason.DECODING,
        BackendErrorReason.IDEMPOTENCY_VARIANT,
        BackendErrorReason.NETWORK,
        BackendErrorReason.NOTEBOOK_LIMIT,
        BackendErrorReason.NOTEBOOK_NOT_FOUND,
        BackendErrorReason.RATE_LIMIT,
        BackendErrorReason.RESPONSE_TOO_LARGE,
        BackendErrorReason.RPC,
        BackendErrorReason.SERVER,
        BackendErrorReason.SOURCE_ADD,
        BackendErrorReason.TIMEOUT,
        BackendErrorReason.UNKNOWN_RPC_METHOD,
    }
    assert translated.diagnostics is not None
    assert {
        name: translated.diagnostics[name] for name in specific_diagnostics
    } == specific_diagnostics
    assert isinstance(translated.diagnostics["public_error_failure"], SourceAddFailureRecord)


@pytest.mark.asyncio
async def test_reviewed_idempotency_variant_error_round_trips_as_typed_caller_error() -> None:
    executor = _RecordingExecutor(IdempotencyVariantError("unknown variant"))

    with pytest.raises(BackendError) as caught:
        await _backend(executor).invoke(NOTEBOOK_LIST_DEF, NotebookListInput(), deadline=None)

    assert caught.value.reason is BackendErrorReason.IDEMPOTENCY_VARIANT
    projected = project_backend_error(caught.value)
    assert type(projected) is IdempotencyVariantError
    assert str(projected) == "unknown variant"


@pytest.mark.asyncio
async def test_unreviewed_non_rpc_library_error_remains_a_closed_contract_failure() -> None:
    executor = _RecordingExecutor(NotebookLMError("unreviewed semantic error"))

    with pytest.raises(BackendContractError, match="unclassified web error type"):
        await _backend(executor).invoke(NOTEBOOK_LIST_DEF, NotebookListInput(), deadline=None)


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
    assert caught.value.diagnostics is not None
    assert {
        name: caught.value.diagnostics[name]
        for name in ("method_id", "rpc_code", "found_ids", "raw_response", "timeout_seconds")
    } == {
        "method_id": RPCMethod.LIST_NOTEBOOKS.value,
        "rpc_code": None,
        "found_ids": None,
        "raw_response": None,
        "timeout_seconds": 3.0,
    }
    assert caught.value.diagnostics["public_error_failure"].kind is (
        SourceAddFailureKind.RPC_TIMEOUT
    )


@pytest.mark.asyncio
async def test_expired_midflight_transport_timeout_maps_to_semantic_deadline_error() -> None:
    leaf = httpx.ReadTimeout(
        "socket stalled",
        request=httpx.Request(
            "POST", "https://notebooklm.google.com/_/LabsTailwindUi/data/batchexecute"
        ),
    )
    timeout = RPCTimeoutError(
        "request timed out",
        method_id=RPCMethod.LIST_NOTEBOOKS.value,
        timeout_seconds=3.0,
        original_error=leaf,
    )
    timeout.__cause__ = leaf
    timeout.__context__ = leaf
    timeout.__suppress_context__ = True
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
    assert caught.value.diagnostics is not None
    failure = caught.value.diagnostics["public_error_failure"]
    assert isinstance(failure, SourceAddFailureRecord)
    assert failure.kind is SourceAddFailureKind.RPC_TIMEOUT
    assert {
        key: value
        for key, value in caught.value.diagnostics.items()
        if key != "public_error_failure"
    } == {
        "method_id": RPCMethod.LIST_NOTEBOOKS.value,
        "rpc_code": None,
        "found_ids": None,
        "raw_response": None,
        "timeout_seconds": 3.0,
        "timeout": 5.0,
        "remaining": 0.0,
    }
    projected = project_backend_error(caught.value)
    assert isinstance(projected, RPCTimeoutError)
    assert isinstance(projected.original_error, httpx.ReadTimeout)
    assert projected.original_error.args == ("socket stalled",)
    assert projected.__cause__ is projected.original_error
    assert projected.__context__ is projected.original_error
    assert projected.__suppress_context__ is True


@pytest.mark.asyncio
async def test_close_does_not_close_client_owned_executor() -> None:
    executor = _RecordingExecutor([])
    backend = _backend(executor)

    await backend.close()

    assert not hasattr(executor, "close")
    with pytest.raises(BackendContractError, match="closed"):
        await backend.invoke(NOTEBOOK_LIST_DEF, NotebookListInput(), deadline=None)
    assert executor.calls == []


def test_only_migrated_feature_runtime_reads_private_backend() -> None:
    """Only composition plus the migrated semantic slices may use the port."""
    package = Path(__file__).resolve().parents[2] / "src" / "notebooklm"
    allowed = {
        package / "_client_assembly.py",
        package / "_artifacts.py",
        package / "client.py",  # annotation-only declaration
        package / "_notebooks.py",
        package / "_notebook_mutation_service.py",
        package / "_mutation_services.py",
        package / "_note_service.py",
        package / "_read_services.py",
        package / "_sources.py",
    }
    allowed.update((package / "_studio").rglob("*.py"))
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
