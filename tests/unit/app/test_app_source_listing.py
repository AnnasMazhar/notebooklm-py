"""Unit tests for the transport-neutral ``notebooklm._app.source_listing`` core.

These pin ``source list`` business logic at the ``_app`` boundary (independent
of adapters): :func:`fetch_sources`, its label-filter branch, and the
notebook-wide capacity snapshot returned alongside a filtered roster.

* No filter → ``client.sources.list(notebook_id)``.
* ``label_filter`` set → injected ``label_resolver`` resolves the ``<id|name>``
  token, then ``client.labels.sources(notebook_id, label_id)`` returns the
  group's members (a single join, no post-filter desync).
* ``json_output`` is forwarded to the resolver only (it never reaches the list).

Pure-service tests (no Click / CliRunner): the ``ListSpec`` / ``prepare_list``
presentation half stays in the CLI adapter and is exercised in
``tests/unit/cli/test_source.py::TestSourceList``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from notebooklm._app.source_listing import fetch_sources, fetch_sources_with_capacity
from notebooklm.types import AccountLimits, Source, SourceStatus


def _client() -> MagicMock:
    client = MagicMock()
    client.sources = MagicMock()
    client.labels = MagicMock()
    client.settings = MagicMock()
    return client


@pytest.mark.asyncio
async def test_no_filter_lists_all_sources() -> None:
    client = _client()
    all_sources = [Source(id="s1", title="One"), Source(id="s2", title="Two")]
    client.sources.list = AsyncMock(return_value=all_sources)
    resolver = AsyncMock()

    result = await fetch_sources(client, "nb_1", label_filter=None, label_resolver=resolver)

    assert result == all_sources
    client.sources.list.assert_awaited_once_with("nb_1")
    # The resolver and the label-members join are never touched without a filter.
    resolver.assert_not_called()
    client.labels.sources.assert_not_called()


@pytest.mark.asyncio
async def test_label_filter_resolves_then_fetches_members() -> None:
    client = _client()
    members = [Source(id="s1", title="One")]
    client.labels.sources = AsyncMock(return_value=members)
    client.sources.list = AsyncMock()
    resolver = AsyncMock(return_value="lbl_full_id")

    result = await fetch_sources(client, "nb_1", label_filter="Papers", label_resolver=resolver)

    assert result == members
    resolver.assert_awaited_once_with(client, "nb_1", "Papers", json_output=False)
    client.labels.sources.assert_awaited_once_with("nb_1", "lbl_full_id")
    # The full-list path is NOT taken when a filter is supplied.
    client.sources.list.assert_not_called()


@pytest.mark.asyncio
async def test_json_output_forwarded_to_resolver_only() -> None:
    client = _client()
    client.labels.sources = AsyncMock(return_value=[])
    resolver = AsyncMock(return_value="lbl_id")

    await fetch_sources(
        client, "nb_1", label_filter="Topics", label_resolver=resolver, json_output=True
    )

    resolver.assert_awaited_once_with(client, "nb_1", "Topics", json_output=True)


@pytest.mark.asyncio
async def test_label_resolver_receives_client_first() -> None:
    # The resolver is called as ``resolver(client, notebook_id, token, ...)``.
    client = _client()
    client.labels.sources = AsyncMock(return_value=[])
    captured: list[object] = []

    async def resolver(*args: object, **kwargs: object) -> str:
        captured.extend(args)
        return "lbl_id"

    await fetch_sources(client, "nb_1", label_filter="x", label_resolver=resolver)

    assert captured[0] is client
    assert captured[1] == "nb_1"
    assert captured[2] == "x"


@pytest.mark.asyncio
async def test_capacity_snapshot_counts_full_unfiltered_roster() -> None:
    client = _client()
    sources = [
        Source(id="ready", status=SourceStatus.READY),
        Source(id="failed", status=SourceStatus.ERROR),
    ]
    client.sources.list = AsyncMock(return_value=sources)
    client.settings.get_account_limits = AsyncMock(return_value=AccountLimits(source_limit=50))

    snapshot = await fetch_sources_with_capacity(client, "nb_1")

    assert snapshot.sources == sources
    client.sources.list.assert_awaited_once_with("nb_1")
    assert snapshot.capacity.counts.active == 1
    assert snapshot.capacity.counts.failed == 1
    assert snapshot.capacity.remaining_capacity == 49


@pytest.mark.asyncio
async def test_capacity_snapshot_keeps_roster_when_settings_fail() -> None:
    client = _client()
    sources = [Source(id="ready", status=SourceStatus.READY)]
    client.sources.list = AsyncMock(return_value=sources)
    client.settings.get_account_limits = AsyncMock(side_effect=RuntimeError("settings down"))

    snapshot = await fetch_sources_with_capacity(client, "nb_1")

    assert snapshot.sources == sources
    assert snapshot.capacity.counts.active == 1
    assert snapshot.capacity.source_limit is None
    assert snapshot.capacity.remaining_capacity is None


@pytest.mark.asyncio
async def test_label_capacity_snapshot_keeps_counts_notebook_wide() -> None:
    client = _client()
    member = Source(id="member", status=SourceStatus.READY)
    client.labels.sources = AsyncMock(return_value=[member])
    client.sources.list = AsyncMock(
        return_value=[member, Source(id="other", status=SourceStatus.PROCESSING)]
    )
    client.settings.get_account_limits = AsyncMock(return_value=AccountLimits(source_limit=50))
    resolver = AsyncMock(return_value="label-1")

    snapshot = await fetch_sources_with_capacity(
        client,
        "nb_1",
        label_filter="Papers",
        label_resolver=resolver,
    )

    assert snapshot.sources == [member]
    assert snapshot.capacity.counts.active == 2
    assert snapshot.capacity.remaining_capacity == 48
