"""Wire shape for ``WebSourcesAPI.delete_many`` (#1995 / PR #2262)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from notebooklm._web.sources import WebSourcesAPI
from notebooklm.rpc import RPCMethod


def _api(rpc: MagicMock) -> WebSourcesAPI:
    return WebSourcesAPI(rpc, supervisor=MagicMock(), uploader=MagicMock())


@pytest.mark.asyncio
async def test_delete_many_issues_one_rpc_with_nested_id_lists() -> None:
    rpc = MagicMock()
    rpc.rpc_call = AsyncMock(return_value=None)
    await _api(rpc).delete_many("nb_123", ["src_a", "src_b", "src_a"])
    rpc.rpc_call.assert_awaited_once_with(
        RPCMethod.DELETE_SOURCE,
        [[["src_a"], ["src_b"]]],
        source_path="/notebook/nb_123",
        allow_null=True,
    )


@pytest.mark.asyncio
async def test_delete_delegates_to_delete_many_single_id() -> None:
    rpc = MagicMock()
    rpc.rpc_call = AsyncMock(return_value=None)
    await _api(rpc).delete("nb_123", "src_a")
    rpc.rpc_call.assert_awaited_once_with(
        RPCMethod.DELETE_SOURCE,
        [[["src_a"]]],
        source_path="/notebook/nb_123",
        allow_null=True,
    )


@pytest.mark.asyncio
async def test_delete_many_empty_is_noop() -> None:
    rpc = MagicMock()
    rpc.rpc_call = AsyncMock(return_value=None)
    await _api(rpc).delete_many("nb_123", [])
    rpc.rpc_call.assert_not_called()
