"""Migration sentinels for the semantic URL-source mutation slice.

The selected P2.3 boundary is plan option (a): ``SourcesAPI.add_url`` migrates
as one operation, including its hidden YouTube dispatch.  A generic/YouTube
split would create two execution authorities behind one public method.
"""

from __future__ import annotations

import inspect
import logging
from unittest.mock import AsyncMock, MagicMock, call

import httpx
import pytest

from notebooklm._deadline import RuntimeDeadline
from notebooklm._records import (
    SourceAddCommitState,
    SourceAddTitleState,
    SourceAddUrlReceipt,
    SourceAddUrlResult,
    SourceRecord,
)
from notebooklm._source.add import SourceAddService
from notebooklm._source.upload_payloads import build_template_block
from notebooklm._sources import SourcesAPI
from notebooklm.exceptions import (
    NetworkError,
    RPCError,
    ServerError,
    SourceAddError,
    SourceTimeoutError,
)
from notebooklm.rpc import RPCMethod
from tests._fixtures.web_backend import build_web_backend


def _source_result(source_id: str, title: str, url: str, *, type_code: int) -> list[object]:
    metadata: list[object] = [None] * 8
    metadata[2] = [1704067200, 0]
    metadata[4] = type_code
    metadata[7] = [url]
    return [[[source_id], title, metadata, [None, 2]]]


def _sources_api() -> SourcesAPI:
    return SourcesAPI(MagicMock(), uploader=MagicMock(), _backend=MagicMock())


def test_add_url_public_signature_is_frozen() -> None:
    parameters = inspect.signature(SourcesAPI.add_url).parameters
    assert list(parameters) == ["self", "notebook_id", "url", "wait", "wait_timeout", "title"]
    assert parameters["notebook_id"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert parameters["url"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert parameters["wait"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["wait"].default is False
    assert parameters["wait_timeout"].default == 120.0
    assert parameters["title"].default is None


@pytest.mark.asyncio
async def test_generic_and_youtube_payloads_share_one_url_operation_boundary() -> None:
    """Option (a): both branches are ADD_SOURCE's same ``url`` variant."""
    rpc_call = AsyncMock(return_value=None)
    rpc = MagicMock(rpc_call=rpc_call)
    service = SourceAddService()
    regular_url = "https://example.com/article"
    youtube_url = "https://youtu.be/dQw4w9WgXcQ"

    await service.add_url_source("nb-1", regular_url, rpc=rpc)
    await service.add_youtube_source("nb-1", youtube_url, rpc=rpc)

    assert rpc_call.await_args_list == [
        call(
            RPCMethod.ADD_SOURCE,
            [
                [[None, None, [regular_url], None, None, None, None, None, None, None, 1]],
                "nb-1",
                build_template_block(),
            ],
            source_path="/notebook/nb-1",
            disable_internal_retries=True,
            operation_variant="url",
        ),
        call(
            RPCMethod.ADD_SOURCE,
            [
                [[None, None, None, None, None, None, None, [youtube_url], None, None, 1]],
                "nb-1",
                build_template_block(),
            ],
            source_path="/notebook/nb-1",
            allow_null=False,
            disable_internal_retries=True,
            operation_variant="url",
        ),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "video_id", "type_code", "expected_adder"),
    [
        ("https://example.com/article", None, 5, "regular"),
        ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ", 9, "youtube"),
    ],
)
async def test_add_url_hidden_dispatch_stays_inside_shared_reconciliation(
    url: str,
    video_id: str | None,
    type_code: int,
    expected_adder: str,
) -> None:
    service = SourceAddService()
    add_regular = AsyncMock(
        return_value=_source_result("src-1", "Upstream", url, type_code=type_code)
    )
    add_youtube = AsyncMock(
        return_value=_source_result("src-1", "Upstream", url, type_code=type_code)
    )
    list_sources = AsyncMock(return_value=[])

    source = await service.add_url(
        "nb-1",
        url,
        add_youtube_source=add_youtube,
        add_url_source=add_regular,
        list_sources=list_sources,
        wait_until_ready=AsyncMock(),
        extract_youtube_video_id=lambda _url: video_id,
        is_youtube_url=lambda _url: video_id is not None,
        logger=logging.getLogger(__name__),
    )

    assert (source.id, source.url) == ("src-1", url)
    list_sources.assert_awaited_once_with("nb-1")
    if expected_adder == "youtube":
        add_youtube.assert_awaited_once_with("nb-1", url)
        add_regular.assert_not_awaited()
    else:
        add_regular.assert_awaited_once_with("nb-1", url)
        add_youtube.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovered_url_add_uses_exact_baseline_diff_and_still_honors_title() -> None:
    url = "https://example.com/article"
    api = _sources_api()
    api._url_mutation_service = MagicMock(  # type: ignore[assignment]
        add_url=AsyncMock(
            return_value=SourceAddUrlResult(
                SourceRecord(id="src-new", title="Requested", url=url, kind="web_page"),
                SourceAddUrlReceipt(
                    SourceAddCommitState.RECONCILED,
                    SourceAddTitleState.RENAMED,
                ),
            )
        )
    )
    api._add_url_source = AsyncMock()  # type: ignore[method-assign]
    api._add_youtube_source = AsyncMock()  # type: ignore[method-assign]

    source = await api.add_url("nb-1", url, title="  Requested  ")

    assert (source.id, source.title, source.url) == ("src-new", "Requested", url)
    api._url_mutation_service.add_url.assert_awaited_once_with(
        "nb-1",
        url,
        wait=False,
        wait_timeout=120.0,
        requested_title="  Requested  ",
        deadline=None,
    )
    api._add_url_source.assert_not_awaited()
    api._add_youtube_source.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_url_facade_preserves_bounded_public_failure_chain() -> None:
    cause = RPCError(
        "request rejected",
        method_id=RPCMethod.ADD_SOURCE.value,
        raw_response="safe excerpt",
        rpc_code=3,
        found_ids=[RPCMethod.ADD_SOURCE.value],
    )
    rpc_call = AsyncMock(side_effect=[[["Notebook", [], "nb-1"]], cause])
    rpc = MagicMock(rpc_call=rpc_call)
    api = SourcesAPI(
        rpc,
        uploader=MagicMock(),
        _backend=build_web_backend(rpc),
    )

    with pytest.raises(SourceAddError) as caught:
        await api.add_url("nb-1", "https://example.com/article")

    public = caught.value
    assert public.url == "https://example.com/article"
    assert isinstance(public.cause, RPCError)
    assert public.cause.args == cause.args
    assert public.cause.method_id == cause.method_id
    assert public.cause.raw_response == cause.raw_response
    assert public.cause.rpc_code == cause.rpc_code
    assert public.cause.found_ids == cause.found_ids
    assert public.__cause__ is public.cause
    assert public.__context__ is public.cause
    assert public.__suppress_context__ is True


@pytest.mark.asyncio
async def test_live_url_facade_preserves_uncertain_leaf_fields_and_context() -> None:
    create_error = ServerError("create response lost", status_code=503)
    original_error = httpx.ConnectError(
        "connection reset",
        request=httpx.Request(
            "POST", "https://notebooklm.google.com/_/LabsTailwindUi/data/batchexecute"
        ),
    )
    probe_error = NetworkError(
        "probe disconnected",
        method_id=RPCMethod.GET_NOTEBOOK.value,
        original_error=original_error,
    )
    probe_error.source_id = "src-maybe"  # type: ignore[attr-defined]
    probe_error.stage = "url-probe"  # type: ignore[attr-defined]
    rpc_call = AsyncMock(side_effect=[[["Notebook", [], "nb-1"]], create_error, probe_error])
    rpc = MagicMock(rpc_call=rpc_call)
    api = SourcesAPI(
        rpc,
        uploader=MagicMock(),
        _backend=build_web_backend(rpc),
    )

    with pytest.raises(NetworkError) as caught:
        await api.add_url("nb-1", "https://example.com/article")

    public = caught.value
    assert public.args == probe_error.args
    assert public.method_id == probe_error.method_id
    assert type(public.original_error) is httpx.ConnectError
    assert public.original_error.args == original_error.args
    assert public.original_error.request.method == original_error.request.method
    assert public.original_error.request.url == original_error.request.url
    assert public.source_id == "src-maybe"  # type: ignore[attr-defined]
    assert public.stage == "url-probe"  # type: ignore[attr-defined]
    assert public.unconfirmed is True  # type: ignore[attr-defined]
    assert isinstance(public.__context__, ServerError)
    assert public.__context__.args == create_error.args
    assert public.__cause__ is None


@pytest.mark.asyncio
async def test_public_wait_timeout_starts_after_url_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://example.com/article"
    rpc_call = AsyncMock(
        side_effect=[
            [["Notebook", [], "nb-1"]],
            _source_result("src-new", "Upstream", url, type_code=5),
        ]
    )
    rpc = MagicMock(rpc_call=rpc_call)
    api = SourcesAPI(
        rpc,
        uploader=MagicMock(),
        _backend=build_web_backend(rpc),
    )

    starts: list[float] = []

    def start_after_creation(timeout: float, **_kwargs: object) -> RuntimeDeadline:
        assert rpc_call.await_count == 2
        starts.append(timeout)
        return RuntimeDeadline(timeout=timeout, started_at=0.0, monotonic=lambda: 0.0)

    monkeypatch.setattr(RuntimeDeadline, "start", start_after_creation)

    with pytest.raises(SourceTimeoutError) as caught:
        await api.add_url(
            "nb-1",
            url,
            wait=True,
            wait_timeout=0.0,
        )

    public = caught.value
    assert public.source_id == "src-new"
    assert public.timeout == 0.0
    assert starts == [0.0]
    assert sum(call.args[0] is RPCMethod.ADD_SOURCE for call in rpc_call.await_args_list) == 1
