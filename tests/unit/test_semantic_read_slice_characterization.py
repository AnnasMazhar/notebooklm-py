"""Migration sentinels for the first semantic notebook/source read slice.

These tests intentionally characterize the legacy facade contract before P1's
backend types are available.  P2 may replace the execution authority, but it
must preserve these request, projection, filtering, and miss semantics.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from notebooklm._notebook_payloads import build_get_notebook_params
from notebooklm._notebooks import NotebooksAPI
from notebooklm._sources import SourcesAPI
from notebooklm.exceptions import SourceNotFoundError
from notebooklm.rpc import RPCMethod
from notebooklm.rpc.types import SourceStatus
from notebooklm.types import Source, SourceType


def _source_entry(
    source_id: str,
    *,
    title: str,
    type_code: int = 3,
    status: SourceStatus = SourceStatus.READY,
) -> list[object]:
    return [
        [source_id],
        title,
        [None, 10, [1704067200, 0], None, type_code],
        [None, status],
    ]


def _sources_api(result: object) -> tuple[SourcesAPI, AsyncMock]:
    rpc_call = AsyncMock(return_value=result)
    rpc = MagicMock(rpc_call=rpc_call)
    return SourcesAPI(rpc, uploader=MagicMock()), rpc_call


def test_read_slice_public_signatures_are_frozen() -> None:
    """P2 keeps positional IDs and the source-list keyword-only controls."""
    assert list(inspect.signature(NotebooksAPI.list).parameters) == ["self"]
    assert list(inspect.signature(NotebooksAPI.get).parameters) == ["self", "notebook_id"]
    assert list(inspect.signature(NotebooksAPI.get_or_none).parameters) == [
        "self",
        "notebook_id",
    ]

    source_list = inspect.signature(SourcesAPI.list).parameters
    assert list(source_list) == ["self", "notebook_id", "strict", "statuses", "types"]
    assert source_list["notebook_id"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert source_list["strict"].kind is inspect.Parameter.KEYWORD_ONLY
    assert source_list["strict"].default is False
    assert source_list["statuses"].default is None
    assert source_list["types"].default is None
    assert list(inspect.signature(SourcesAPI.get).parameters) == [
        "self",
        "notebook_id",
        "source_id",
    ]
    assert list(inspect.signature(SourcesAPI.get_or_none).parameters) == [
        "self",
        "notebook_id",
        "source_id",
    ]


@pytest.mark.asyncio
async def test_notebook_reads_pin_requests_projection_and_backend_order() -> None:
    rpc_call = AsyncMock(
        side_effect=[
            [
                [
                    [
                        "Second",
                        [],
                        "nb-2",
                    ],
                    ["First", [], "nb-1"],
                ]
            ],
            [["Details", [], "nb-1"]],
        ]
    )
    api = NotebooksAPI(MagicMock(rpc_call=rpc_call), sources_api=MagicMock())

    listed = await api.list()
    fetched = await api.get("nb-1")

    assert [(notebook.id, notebook.title) for notebook in listed] == [
        ("nb-2", "Second"),
        ("nb-1", "First"),
    ]
    assert (fetched.id, fetched.title) == ("nb-1", "Details")
    assert rpc_call.await_args_list[0].args == (
        RPCMethod.LIST_NOTEBOOKS,
        [None, 1, None, [2]],
    )
    assert rpc_call.await_args_list[0].kwargs == {}
    assert rpc_call.await_args_list[1].args == (
        RPCMethod.GET_NOTEBOOK,
        build_get_notebook_params("nb-1"),
    )
    assert rpc_call.await_args_list[1].kwargs == {"source_path": "/notebook/nb-1"}


@pytest.mark.asyncio
async def test_source_list_pins_request_normalization_filters_and_strict_count() -> None:
    duplicate = _source_entry("src-pdf", title="PDF", status=SourceStatus.READY)
    payload = [
        [
            "Notebook",
            [
                duplicate,
                duplicate,
                _source_entry(
                    "src-web",
                    title="Web",
                    type_code=5,
                    status=SourceStatus.ERROR,
                ),
            ],
            "nb-1",
        ]
    ]
    api, rpc_call = _sources_api(payload)

    sources = await api.list(
        "nb-1",
        strict=True,
        statuses={SourceStatus.READY, SourceStatus.ERROR},
        types={SourceType.PDF, SourceType.WEB_PAGE},
    )

    assert [(source.id, source.title, source.kind, source.status) for source in sources] == [
        ("src-pdf", "PDF", SourceType.PDF, SourceStatus.READY),
        ("src-web", "Web", SourceType.WEB_PAGE, SourceStatus.ERROR),
    ]
    rpc_call.assert_awaited_once_with(
        RPCMethod.GET_NOTEBOOK,
        build_get_notebook_params("nb-1"),
        source_path="/notebook/nb-1",
    )


@pytest.mark.asyncio
async def test_source_get_preserves_late_bound_list_and_miss_contracts() -> None:
    api, rpc_call = _sources_api(None)
    expected = Source(id="src-2", title="Two")
    api.list = AsyncMock(return_value=[Source(id="src-1"), expected])  # type: ignore[method-assign]

    assert await api.get_or_none("nb-1", "src-2") is expected
    assert await api.get_or_none("nb-1", "missing") is None
    with pytest.raises(SourceNotFoundError) as exc_info:
        await api.get("nb-1", "missing")

    assert exc_info.value.source_id == "missing"
    assert api.list.await_args_list == [
        (("nb-1",), {}),
        (("nb-1",), {}),
        (("nb-1",), {}),
    ]
    rpc_call.assert_not_awaited()
