"""Migration sentinels for the semantic URL-source mutation slice.

The selected P2.3 boundary is plan option (a): ``SourcesAPI.add_url`` migrates
as one operation, including its hidden YouTube dispatch.  A generic/YouTube
split would create two execution authorities behind one public method.
"""

from __future__ import annotations

import inspect
import logging
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from notebooklm._source.add import SourceAddService
from notebooklm._source.upload_payloads import build_template_block
from notebooklm._sources import SourcesAPI
from notebooklm.exceptions import ServerError
from notebooklm.rpc import RPCMethod
from notebooklm.types import Source


def _source_result(source_id: str, title: str, url: str, *, type_code: int) -> list[object]:
    metadata: list[object] = [None] * 8
    metadata[2] = [1704067200, 0]
    metadata[4] = type_code
    metadata[7] = [url]
    return [[[source_id], title, metadata, [None, 2]]]


def _sources_api() -> SourcesAPI:
    return SourcesAPI(MagicMock(), uploader=MagicMock())


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
    old = Source(id="src-old", title="Old", url=url)
    recovered = Source(id="src-new", title="Upstream", url=url)
    api = _sources_api()
    api.list = AsyncMock(side_effect=[[old], [old, recovered]])  # type: ignore[method-assign]
    api._add_url_source = AsyncMock(  # type: ignore[method-assign]
        side_effect=ServerError("bad gateway", status_code=502)
    )
    api._add_youtube_source = AsyncMock()  # type: ignore[method-assign]
    api.rename = AsyncMock(  # type: ignore[method-assign]
        return_value=Source(id="src-new", title="Requested")
    )

    source = await api.add_url("nb-1", url, title="  Requested  ")

    assert (source.id, source.title, source.url) == ("src-new", "Requested", url)
    assert api.list.await_args_list == [call("nb-1"), call("nb-1")]
    api._add_url_source.assert_awaited_once_with("nb-1", url)
    api._add_youtube_source.assert_not_awaited()
    api.rename.assert_awaited_once_with("nb-1", "src-new", "Requested")
