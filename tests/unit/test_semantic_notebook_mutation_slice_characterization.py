"""Migration sentinels for the semantic notebook mutation slice.

P2 may replace the execution authority, but create reconciliation, mutation
payloads, read-back behavior, and public signatures remain facade contracts.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from notebooklm._notebook_payloads import (
    build_create_notebook_params,
    build_get_notebook_params,
    build_update_notebook_params,
)
from notebooklm._notebooks import NotebooksAPI
from notebooklm.exceptions import ServerError, ValidationError
from notebooklm.rpc import RPCMethod
from notebooklm.types import Notebook


def _api(rpc_call: AsyncMock) -> NotebooksAPI:
    return NotebooksAPI(MagicMock(rpc_call=rpc_call), sources_api=MagicMock())


def test_notebook_mutation_public_signatures_are_frozen() -> None:
    assert list(inspect.signature(NotebooksAPI.create).parameters) == ["self", "title"]
    assert list(inspect.signature(NotebooksAPI.rename).parameters) == [
        "self",
        "notebook_id",
        "new_title",
    ]
    update = inspect.signature(NotebooksAPI.update).parameters
    assert list(update) == ["self", "notebook_id", "title", "emoji"]
    assert update["notebook_id"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert update["title"].kind is inspect.Parameter.KEYWORD_ONLY
    assert update["title"].default is None
    assert update["emoji"].kind is inspect.Parameter.KEYWORD_ONLY
    assert update["emoji"].default is None
    assert list(inspect.signature(NotebooksAPI.delete).parameters) == ["self", "notebook_id"]


@pytest.mark.asyncio
async def test_create_pins_baseline_payload_projection_and_retry_ownership() -> None:
    rpc_call = AsyncMock(
        return_value=[
            "Daily News",
            None,
            "nb-new",
            None,
            None,
            [None, False, None, None, None, [1704067200, 0]],
        ]
    )
    api = _api(rpc_call)
    api.list = AsyncMock(return_value=[])  # type: ignore[method-assign]

    notebook = await api.create("Daily News")

    assert (notebook.id, notebook.title) == ("nb-new", "Daily News")
    api.list.assert_awaited_once_with()
    rpc_call.assert_awaited_once_with(
        RPCMethod.CREATE_NOTEBOOK,
        build_create_notebook_params("Daily News"),
        disable_internal_retries=True,
    )


@pytest.mark.asyncio
async def test_create_transport_failure_adopts_one_new_baseline_diff_without_repost() -> None:
    rpc_call = AsyncMock(side_effect=ServerError("bad gateway", status_code=502))
    api = _api(rpc_call)
    recovered = Notebook(id="nb-landed", title="Daily News")
    api.list = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            [Notebook(id="nb-old", title="Daily News")],
            [Notebook(id="nb-old", title="Daily News"), recovered],
        ]
    )

    assert await api.create("Daily News") is recovered
    assert api.list.await_args_list == [call(), call()]
    rpc_call.assert_awaited_once_with(
        RPCMethod.CREATE_NOTEBOOK,
        build_create_notebook_params("Daily News"),
        disable_internal_retries=True,
    )


@pytest.mark.asyncio
async def test_title_update_pins_mutation_then_get_readback() -> None:
    rpc_call = AsyncMock(
        side_effect=[
            None,
            [["Renamed", [], "nb-1"]],
        ]
    )
    api = _api(rpc_call)

    notebook = await api.rename("nb-1", "Renamed")

    assert (notebook.id, notebook.title) == ("nb-1", "Renamed")
    assert rpc_call.await_args_list == [
        call(
            RPCMethod.RENAME_NOTEBOOK,
            build_update_notebook_params("nb-1", title="Renamed"),
            source_path="/",
            allow_null=True,
        ),
        call(
            RPCMethod.GET_NOTEBOOK,
            build_get_notebook_params("nb-1"),
            source_path="/notebook/nb-1",
        ),
    ]


@pytest.mark.asyncio
async def test_update_rejects_empty_change_and_delete_stays_single_id_set_operation() -> None:
    rpc_call = AsyncMock(return_value=None)
    api = _api(rpc_call)

    with pytest.raises(ValidationError, match="At least one"):
        await api.update("nb-1")
    rpc_call.assert_not_awaited()

    assert await api.delete("nb-1") is None
    rpc_call.assert_awaited_once_with(
        RPCMethod.DELETE_NOTEBOOK,
        [["nb-1"], [2]],
    )
