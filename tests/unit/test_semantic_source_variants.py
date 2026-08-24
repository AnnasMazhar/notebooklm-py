"""P6.7 semantic Source service, facade, and web-binding characterization."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from notebooklm._operations import Operation
from notebooklm._records import (
    SOURCE_ADD_FILE_DEF,
    SOURCE_ADD_TEXT_DEF,
    SOURCE_ADD_URL_BATCH_DEF,
    SOURCE_CHECK_FRESHNESS_DEF,
    SOURCE_DELETE_DEF,
    SOURCE_GET_GUIDE_DEF,
    SOURCE_REFRESH_DEF,
    SOURCE_UPDATE_DEF,
    SourceAddFailureKind,
    SourceAddFailureRecord,
    SourceAddFileInput,
    SourceAddTextInput,
    SourceAddTextResult,
    SourceAddUrlBatchInput,
    SourceAddUrlBatchResult,
    SourceDeleteInput,
    SourceFileInputKind,
    SourceFreshnessInput,
    SourceGuideInput,
    SourceRecord,
    SourceRefreshInput,
    SourceUpdateInput,
    SourceUrlBatchItemRecord,
)
from notebooklm._source_service import SourceService
from notebooklm._sources import SourcesAPI
from notebooklm._web.backend import WebRpcBackend
from notebooklm._web.codec.sources import (
    decode_source_guide,
    encode_add_drive,
    encode_add_text,
    encode_add_url_batch,
    encode_delete,
    encode_get_fulltext,
    encode_get_guide,
    encode_refresh_or_freshness,
)
from notebooklm.exceptions import SourceAddError
from notebooklm.rpc import RPCMethod
from notebooklm.types import Source, SourceStatus
from tests._fixtures.recording_backend import RecordingBackend


class _RecordingExecutor:
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[RPCMethod, list[Any], dict[str, Any]]] = []

    async def rpc_call(self, method: RPCMethod, params: list[Any], **kwargs: Any) -> Any:
        self.calls.append((method, params, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _source_entry(
    source_id: str,
    *,
    title: str,
    url: str | None = None,
    status: int = 2,
    kind: int = 5,
) -> list[Any]:
    metadata = [None, 11, [1704067200, 0], None, kind, None, None, [url] if url else None]
    return [[source_id], title, metadata, [None, status]]


def _add_result(source_id: str, *, title: str, url: str | None = None) -> list[Any]:
    return [[_source_entry(source_id, title=title, url=url)]]


def _web_backend(executor: _RecordingExecutor, *, uploader: object | None = None) -> WebRpcBackend:
    return WebRpcBackend(  # type: ignore[arg-type]
        executor,
        transport_factory=lambda **_kwargs: object(),
        source_uploader=uploader,
    )


def test_source_codec_goldens_pin_every_remaining_positional_request() -> None:
    template_tail = [
        2,
        None,
        None,
        [1, None, None, None, None, None, None, None, None, None, [1]],
    ]
    assert encode_add_text("nb", "Title", "body") == [
        [[None, ["Title", "body"], None, 2, None, None, None, None, None, None, 1]],
        "nb",
        template_tail,
    ]
    assert encode_add_drive("nb", "drive-id", "Drive", "application/pdf") == [
        [[["drive-id", "application/pdf", 1, "Drive"], *([None] * 9), 1]],
        "nb",
        [2],
        [1, None, None, None, None, None, None, None, None, None, [1]],
    ]
    assert encode_add_url_batch(
        "nb",
        ["https://example.com", "https://youtu.be/abcdefghijk"],
        youtube_flags=[False, True],
    ) == [
        [
            [None, None, ["https://example.com"], *([None] * 7), 1],
            [
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                ["https://youtu.be/abcdefghijk"],
                None,
                None,
                1,
            ],
        ],
        "nb",
        template_tail,
    ]
    assert encode_delete("src") == [[["src"]]]
    assert encode_refresh_or_freshness("src") == [None, ["src"], [2]]
    assert encode_get_guide("src") == [[[["src"]]]]
    assert encode_get_fulltext("src", markdown=False) == [["src"], [2], [2]]
    assert encode_get_fulltext("src", markdown=True) == [["src"], [3], [3]]
    assert decode_source_guide([[[None, ["Summary"], [["one", "two"]], []]]]).keywords == (
        "one",
        "two",
    )


@pytest.mark.asyncio
async def test_neutral_service_materializes_batch_and_hides_sensitive_inputs() -> None:
    backend = RecordingBackend()
    source = SourceRecord("src", "Title", status="ready")
    backend.set_result(SOURCE_ADD_TEXT_DEF, SourceAddTextResult(source))
    backend.set_result(
        SOURCE_ADD_URL_BATCH_DEF,
        SourceAddUrlBatchResult(
            (
                SourceUrlBatchItemRecord("https://ok.example", source=source),
                SourceUrlBatchItemRecord(
                    "https://bad.example",
                    error=SourceAddFailureRecord(
                        SourceAddFailureKind.SOURCE_ADD,
                        "Failed to add URL source",
                        url="https://bad.example",
                    ),
                ),
            )
        ),
    )
    service = SourceService(backend)

    text_result = await service.add_text(
        "nb",
        "Secret title",
        "secret body",
        wait=False,
        wait_timeout=120.0,
        idempotent=False,
    )
    batch_result = await service.add_urls_batch(
        "nb",
        ("https://ok.example", "https://bad.example"),
    )

    assert text_result.source == source
    assert len(batch_result.items) == 2
    assert [call.operation for call in backend.invocations] == [
        Operation.SOURCE_ADD_TEXT,
        Operation.SOURCE_ADD_URL_BATCH,
    ]
    text_input = backend.invocations[0].value
    batch_input = backend.invocations[1].value
    assert isinstance(text_input, SourceAddTextInput)
    assert "Secret title" not in repr(text_input) and "secret body" not in repr(text_input)
    assert isinstance(batch_input, SourceAddUrlBatchInput)
    assert "ok.example" not in repr(batch_input)


@pytest.mark.asyncio
async def test_facade_projects_positional_batch_records_without_rpc_vocabulary() -> None:
    backend = RecordingBackend()
    backend.set_result(
        SOURCE_ADD_URL_BATCH_DEF,
        SourceAddUrlBatchResult(
            (
                SourceUrlBatchItemRecord(
                    "https://ok.example",
                    source=SourceRecord("src", "Ready", status="ready"),
                ),
                SourceUrlBatchItemRecord(
                    "https://bad.example",
                    error=SourceAddFailureRecord(
                        SourceAddFailureKind.SOURCE_ADD,
                        "bad URL",
                        url="https://bad.example",
                    ),
                ),
            )
        ),
    )
    uploader = MagicMock()
    api = SourcesAPI(MagicMock(), uploader=uploader, _backend=backend)

    outcomes = await api._add_urls_batch(
        "nb",
        ["https://ok.example", "https://bad.example"],
    )

    assert outcomes[0].source is not None and outcomes[0].source.id == "src"
    assert isinstance(outcomes[1].error, SourceAddError)
    assert outcomes[1].error.url == "https://bad.example"
    uploader.configure_source_lifecycle.assert_called_once()


@pytest.mark.asyncio
async def test_batch_web_binding_is_one_shot_and_reconciles_omissions_once() -> None:
    good = "https://good.example/"
    missing = "https://missing.example/"
    executor = _RecordingExecutor(
        [_source_entry("good", title="Good", url=good)],
        [["Notebook", [_source_entry("ghost", title="Ghost", url=missing, status=3)], "nb"]],
    )

    result = await _web_backend(executor).invoke(
        SOURCE_ADD_URL_BATCH_DEF,
        SourceAddUrlBatchInput("nb", (good, missing)),
        deadline=None,
    )

    assert [call[0] for call in executor.calls] == [RPCMethod.ADD_SOURCE, RPCMethod.GET_NOTEBOOK]
    assert executor.calls[0][2]["disable_internal_retries"] is True
    assert executor.calls[0][2]["operation_variant"] == "url"
    assert result.items[0].source is not None and result.items[0].source.id == "good"
    assert result.items[1].error is not None
    assert result.items[1].error.source_id is None


@pytest.mark.asyncio
async def test_simple_web_bindings_preserve_shapes_and_null_echo_recency() -> None:
    hydrated = _source_entry("src", title="Renamed", url="https://example.com")
    executor = _RecordingExecutor(
        None,
        None,
        [],
        [[[None, ["Summary"], [["alpha"]], []]]],
        None,
        [["Notebook", [hydrated], "nb"]],
    )
    backend = _web_backend(executor)

    await backend.invoke(SOURCE_DELETE_DEF, SourceDeleteInput("nb", "src"), deadline=None)
    await backend.invoke(SOURCE_REFRESH_DEF, SourceRefreshInput("nb", "src"), deadline=None)
    freshness = await backend.invoke(
        SOURCE_CHECK_FRESHNESS_DEF,
        SourceFreshnessInput("nb", "src"),
        deadline=None,
    )
    guide = await backend.invoke(
        SOURCE_GET_GUIDE_DEF,
        SourceGuideInput("nb", "src"),
        deadline=None,
    )
    renamed = await backend.invoke(
        SOURCE_UPDATE_DEF,
        SourceUpdateInput("nb", "src", "Renamed", return_object=True),
        deadline=None,
    )

    assert freshness.fresh is True
    assert (guide.guide.summary, guide.guide.keywords) == ("Summary", ("alpha",))
    assert renamed.source is not None and renamed.source.id == "src"
    assert [call[0] for call in executor.calls] == [
        RPCMethod.DELETE_SOURCE,
        RPCMethod.REFRESH_SOURCE,
        RPCMethod.CHECK_SOURCE_FRESHNESS,
        RPCMethod.GET_SOURCE_GUIDE,
        RPCMethod.UPDATE_SOURCE,
        RPCMethod.GET_NOTEBOOK,
    ]
    assert executor.calls[0][1] == [[["src"]]]
    assert executor.calls[1][1] == executor.calls[2][1] == [None, ["src"], [2]]
    assert executor.calls[3][1] == [[[["src"]]]]


class _Uploader:
    def __init__(self) -> None:
        self.limit_lookup: object | None = None
        self.calls: list[tuple[object, ...]] = []

    def configure_source_limit_lookup(self, callback: object) -> None:
        self.limit_lookup = callback

    async def add_file(self, notebook_id: str, file_path: str | Path, **kwargs: Any) -> Source:
        self.calls.append((notebook_id, file_path, kwargs))
        return Source(id="uploaded", title="file.txt", status=SourceStatus.PROCESSING)


@pytest.mark.asyncio
async def test_file_binding_preserves_path_callback_and_existing_upload_authority(
    tmp_path: Path,
) -> None:
    uploader = _Uploader()
    callback = MagicMock()
    backend = _web_backend(_RecordingExecutor(), uploader=uploader)
    path = tmp_path / "file.txt"

    result = await backend.invoke(
        SOURCE_ADD_FILE_DEF,
        SourceAddFileInput(
            "nb",
            SourceFileInputKind.LOCAL,
            file_path=path,
            mime_type="text/plain",
            on_progress=callback,
        ),
        deadline=None,
    )

    assert result.source.id == "uploaded"
    assert uploader.limit_lookup is not None
    assert uploader.calls == [
        (
            "nb",
            path,
            {
                "mime_type": "text/plain",
                "wait": False,
                "wait_timeout": 120.0,
                "title": None,
                "on_progress": callback,
            },
        )
    ]


@pytest.mark.asyncio
async def test_drive_download_variant_uses_dedicated_gate_and_upload_callback(monkeypatch) -> None:
    uploader = _Uploader()
    events: list[str] = []

    uploader.live_cookies = lambda: None  # type: ignore[attr-defined]
    uploader.authuser_value = lambda: "0"  # type: ignore[attr-defined]

    @asynccontextmanager
    async def gate():
        events.append("enter")
        yield
        events.append("exit")

    uploader.get_download_semaphore = gate  # type: ignore[attr-defined]

    class _DriveService:
        def __init__(self, *, fetch: object, add_file: object) -> None:
            assert fetch is not None
            assert add_file == uploader.add_file

        async def add_drive_file(self, notebook_id: str, document_id: str, **kwargs: Any) -> Source:
            events.append(f"route:{notebook_id}:{document_id}:{kwargs['title']}")
            return Source(id="drive-upload", title="Drive file")

    monkeypatch.setattr("notebooklm._web.source_variants.DriveImportService", _DriveService)
    result = await _web_backend(_RecordingExecutor(), uploader=uploader).invoke(
        SOURCE_ADD_FILE_DEF,
        SourceAddFileInput(
            "nb",
            SourceFileInputKind.DRIVE_DOWNLOAD,
            document_id="drive-id",
            title="Chosen",
        ),
        deadline=None,
    )

    assert result.source.id == "drive-upload"
    assert events == ["enter", "route:nb:drive-id:Chosen", "exit"]
