"""Transport-neutral ``source list`` fetch business logic.

This is the Click-free core of ``cli/services/source_listing.py``: it owns the
shared business logic in the source-list path: **which sources to fetch** given
an optional label filter, plus the notebook-wide quota snapshot used by MCP and
REST list responses. JSON-envelope assembly and Rich-table rendering stay in
their adapters.

The label ``<id|name>`` resolver is **injected** as a callable
(``label_resolver``) so this module never imports the Click-coupled
``cli.services.label_listing.resolve_label_id`` (which reaches into
``cli.resolve``); the CLI adapter supplies the live resolver and keeps its
resolution behaviour + error contract.

This module is transport-neutral — no ``click`` / ``rich`` / ``cli`` /
``fastmcp`` imports (enforced by ``tests/_guardrails/test_app_boundary.py``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..types import Source
from .source_capacity import (
    SourceCapacity,
    get_source_capacity,
    get_source_capacity_with_sources,
)

if TYPE_CHECKING:
    from ..client import NotebookLMClient

#: Resolves a label ``<id|name>`` token to a full label id. The CLI adapter
#: supplies its ``cli.services.label_listing.resolve_label_id``-backed
#: implementation (which raises its own typed ``LabelResolutionError``).
LabelResolver = Callable[..., Awaitable[str]]


@dataclass(frozen=True)
class SourceListSnapshot:
    """Returned sources plus notebook-wide quota accounting."""

    sources: list[Source]
    capacity: SourceCapacity


async def fetch_sources(
    client: NotebookLMClient,
    notebook_id: str,
    *,
    label_filter: str | None,
    label_resolver: LabelResolver,
    json_output: bool = False,
) -> list[Source]:
    """Fetch the notebook's sources, optionally restricted to one label's members.

    When ``label_filter`` is set, the injected ``label_resolver`` resolves the
    ``<id|name>`` token and the group's members are fetched via
    ``client.labels.sources()`` (a single ``sources.list()`` join) — so the
    filtered set is fetched once and the caller's ``count``/rows match the
    filtered set with no post-filter desync. Otherwise the full notebook source
    list is returned.

    ``json_output`` is forwarded to the resolver only (it tunes the resolver's
    own diagnostic routing); this function performs no rendering.
    """
    if label_filter is not None:
        label_id = await label_resolver(client, notebook_id, label_filter, json_output=json_output)
        # ``labels.sources()`` returns the group's members (joined from a single
        # ``sources.list()``), so the filtered set is fetched once.
        return await client.labels.sources(notebook_id, label_id)
    return await client.sources.list(notebook_id)


async def fetch_sources_with_capacity(
    client: NotebookLMClient,
    notebook_id: str,
    *,
    label_filter: str | None = None,
    label_resolver: LabelResolver | None = None,
    json_output: bool = False,
) -> SourceListSnapshot:
    """Fetch a roster and the unfiltered notebook's live capacity in parallel."""
    if label_filter is None:
        sources, capacity = await get_source_capacity_with_sources(client, notebook_id)
        return SourceListSnapshot(sources=sources, capacity=capacity)
    if label_resolver is None:
        raise ValueError("label_resolver is required when label_filter is set")
    sources, capacity = await asyncio.gather(
        fetch_sources(
            client,
            notebook_id,
            label_filter=label_filter,
            label_resolver=label_resolver,
            json_output=json_output,
        ),
        get_source_capacity(client, notebook_id),
    )
    return SourceListSnapshot(sources=sources, capacity=capacity)


__all__ = [
    "LabelResolver",
    "SourceListSnapshot",
    "fetch_sources",
    "fetch_sources_with_capacity",
]
