"""Regression coverage for the source multi-wait orchestration boundary.

``SourcesAPI.wait_for_sources`` once spawned one waiter task per source. The
semantic Source migration removes that fan-out: one facade-owned poller invokes
one ``source.wait`` snapshot read per tick and resolves every ID from that
shared notebook snapshot. With no sibling tasks, a terminal outcome cannot
leave an orphan poller behind.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

from notebooklm import NotebookLMClient
from notebooklm._records import SourceRecord
from notebooklm._web.source_variants import SourceVariantWebHandlers
from notebooklm.types import SourceProcessingError

pytestmark = pytest.mark.allow_no_vcr


@pytest.mark.asyncio
async def test_wait_for_sources_has_one_shared_wait_and_no_sibling_tasks(auth_tokens) -> None:
    """One terminal row fails promptly without polling its PROCESSING sibling."""
    snapshot = AsyncMock(
        return_value=(
            SourceRecord("bad-id", "Bad", kind="pdf", status="error"),
            SourceRecord("slow-id", "Slow", kind="pdf", status="processing"),
        )
    )

    async with NotebookLMClient(auth_tokens) as client:
        single_wait = AsyncMock(side_effect=AssertionError("single-source fan-out is forbidden"))
        with (
            patch.object(SourceVariantWebHandlers, "_source_snapshot_records", snapshot),
            patch.object(client.sources, "wait_until_ready", single_wait),
        ):
            started = time.monotonic()
            with pytest.raises(SourceProcessingError):
                await client.sources.wait_for_sources("nb_123", ["bad-id", "slow-id"])
            elapsed = time.monotonic() - started

    snapshot.assert_awaited_once()
    single_wait.assert_not_awaited()
    assert elapsed < 1.0
