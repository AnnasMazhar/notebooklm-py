"""Source-count definitions and the pre-add quota guard (#1962)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from notebooklm._app.source_add import (
    SourceAddExecutionPlan,
    SourceAddPlan,
    execute_source_add,
)
from notebooklm._app.source_capacity import SourceCapacity, get_source_capacity
from notebooklm.exceptions import SourceAddError
from notebooklm.types import AccountLimits, Source, SourceCounts, SourceStatus


def test_source_counts_separate_failed_records_from_quota_count() -> None:
    sources = [
        Source(id="ready", status=SourceStatus.READY),
        Source(id="processing", status=SourceStatus.PROCESSING),
        Source(id="preparing", status=SourceStatus.PREPARING),
        Source(id="failed", status=SourceStatus.ERROR),
    ]

    counts = SourceCounts.from_sources(sources)

    assert counts == SourceCounts(
        active=3,
        ready=1,
        processing=1,
        preparing=1,
        failed=1,
        quota_counted=3,
        total_records=4,
    )
    assert counts.remaining_capacity(5) == 2


def test_source_counts_ignore_idless_and_duplicate_records() -> None:
    counts = SourceCounts.from_sources(
        [
            Source(id="ready", status=SourceStatus.READY),
            Source(id="ready", status=SourceStatus.ERROR),
            Source(id="", status=SourceStatus.ERROR),
        ]
    )

    assert counts == SourceCounts(
        active=1,
        ready=1,
        quota_counted=1,
        total_records=1,
    )


@pytest.mark.asyncio
async def test_source_capacity_pairs_counts_with_live_limit() -> None:
    client = MagicMock()
    client.sources.list = AsyncMock(
        return_value=[
            Source(id="active", status=SourceStatus.READY),
            Source(id="failed", status=SourceStatus.ERROR),
        ]
    )
    client.settings.get_account_limits = AsyncMock(return_value=AccountLimits(source_limit=50))

    capacity = await get_source_capacity(client, "nb")

    assert capacity.source_limit == 50
    assert capacity.counts.active == 1
    assert capacity.counts.failed == 1
    assert capacity.source_ids == frozenset({"active", "failed"})
    assert capacity.remaining_capacity == 49


@pytest.mark.asyncio
async def test_capacity_degrades_to_unknown_limit_when_settings_fail() -> None:
    client = MagicMock()
    client.sources.list = AsyncMock(return_value=[Source(id="active", status=SourceStatus.READY)])
    client.settings.get_account_limits = AsyncMock(side_effect=RuntimeError("settings down"))

    capacity = await get_source_capacity(client, "nb")

    assert capacity.counts.active == 1
    assert capacity.source_limit is None
    assert capacity.remaining_capacity is None


def test_capacity_snapshot_advances_without_recounting_duplicate_ids() -> None:
    capacity = SourceCapacity(
        counts=SourceCounts(active=1, ready=1, quota_counted=1, total_records=1),
        source_limit=2,
        source_ids=frozenset({"existing"}),
    )

    unchanged = capacity.after_add(Source(id="existing", status=SourceStatus.PROCESSING))
    advanced = capacity.after_add(Source(id="new", status=SourceStatus.PROCESSING))
    idless = capacity.after_add(Source(id="", status=SourceStatus.PROCESSING))

    assert unchanged is capacity
    assert advanced.counts == SourceCounts(
        active=2,
        ready=1,
        processing=1,
        quota_counted=2,
        total_records=2,
    )
    assert advanced.remaining_capacity == 0
    assert idless.counts == advanced.counts
    assert idless.remaining_capacity == 0


@pytest.mark.asyncio
async def test_quota_rejected_add_never_calls_create() -> None:
    client = MagicMock()
    client.sources.list = AsyncMock(
        return_value=[
            *[Source(id=f"src-{index}", status=SourceStatus.READY) for index in range(50)],
            Source(id="failed-stub", status=SourceStatus.ERROR),
        ]
    )
    client.settings.get_account_limits = AsyncMock(return_value=AccountLimits(source_limit=50))
    client.sources.add_url = AsyncMock()
    plan = SourceAddExecutionPlan(
        notebook_id="nb",
        plan=SourceAddPlan(
            content="https://example.com",
            detected_type="url",
            title=None,
            upload_path=None,
        ),
    )

    with pytest.raises(SourceAddError) as exc_info:
        await execute_source_add(client, plan)

    message = str(exc_info.value)
    assert "limit=50" in message
    assert "current_active=50" in message
    assert "total_records=51" in message
    assert "failed=1" in message
    assert "No source row was created" in message
    client.sources.add_url.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_continues_when_live_limit_is_unavailable() -> None:
    client = MagicMock()
    client.sources.list = AsyncMock(return_value=[])
    client.settings.get_account_limits = AsyncMock(side_effect=RuntimeError("settings down"))
    client.sources.add_url = AsyncMock(
        return_value=Source(id="created", status=SourceStatus.PROCESSING)
    )
    plan = SourceAddExecutionPlan(
        notebook_id="nb",
        plan=SourceAddPlan(
            content="https://example.com",
            detected_type="url",
            title=None,
            upload_path=None,
        ),
    )

    result = await execute_source_add(client, plan)

    assert result.source.id == "created"
    client.sources.add_url.assert_awaited_once_with("nb", "https://example.com")
