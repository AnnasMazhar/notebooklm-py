"""Transport-neutral source-capacity accounting and pre-add guard.

The backend can persist an ``ERROR`` source row even when an add is rejected at
the notebook quota boundary. Adapters therefore read the advertised account
limit and the notebook's current source records before issuing a create RPC.

This remains a client-side preflight rather than an atomic backend reservation:
independent processes can race between the read and create. Sequential batch
adapters carry one local snapshot forward after each successful create, which
both avoids redundant reads and closes that stale-list window within a batch.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..exceptions import SourceAddError
from ..types import Source, SourceCounts

if TYPE_CHECKING:
    from ..client import NotebookLMClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceCapacity:
    """One notebook's explicit source counts paired with its account limit."""

    counts: SourceCounts
    source_limit: int | None
    source_ids: frozenset[str] = frozenset()

    @classmethod
    def from_sources(cls, sources: Iterable[Source], source_limit: int | None) -> SourceCapacity:
        """Build one snapshot from the same materialized source roster."""
        roster = list(sources)
        return cls(
            counts=SourceCounts.from_sources(roster),
            source_limit=source_limit,
            source_ids=frozenset(source.id for source in roster if source.id),
        )

    @property
    def remaining_capacity(self) -> int | None:
        """Number of additional active sources allowed, when the limit is known."""
        return self.counts.remaining_capacity(self.source_limit)

    def to_dict(self) -> dict[str, object]:
        """Return the stable capacity fields shared by list adapters."""
        return {
            "source_counts": self.counts.to_dict(),
            "source_limit": self.source_limit,
            "remaining_capacity": self.remaining_capacity,
        }

    def require_available(self, notebook_id: str) -> None:
        """Raise when this snapshot has no quota-counted slot available."""
        counts = self.counts
        limit = self.source_limit
        if limit is not None and counts.quota_counted >= limit:
            raise SourceAddError(
                notebook_id,
                message=(
                    f"Cannot add source to notebook {notebook_id}: source limit reached "
                    f"(limit={limit}, current_active={counts.active}, "
                    f"quota_counted={counts.quota_counted}, "
                    f"total_records={counts.total_records}, failed={counts.failed}). "
                    "No source row was created. Delete an active source and retry."
                ),
            )

    def after_add(self, source: Source) -> SourceCapacity:
        """Return a snapshot advanced by one successfully returned source."""
        if source.id and source.id in self.source_ids:
            return self
        # An id-less create response is malformed, but the backend may still
        # have consumed a slot. Advance pessimistically by status so a batch
        # cannot over-admit merely because it cannot deduplicate that row.
        delta = SourceCounts.from_statuses([source.status])
        counts = self.counts
        return SourceCapacity(
            counts=SourceCounts(
                active=counts.active + delta.active,
                ready=counts.ready + delta.ready,
                processing=counts.processing + delta.processing,
                preparing=counts.preparing + delta.preparing,
                failed=counts.failed + delta.failed,
                quota_counted=counts.quota_counted + delta.quota_counted,
                total_records=counts.total_records + delta.total_records,
            ),
            source_limit=self.source_limit,
            source_ids=(self.source_ids | {source.id}) if source.id else self.source_ids,
        )


async def get_source_limit_or_none(client: NotebookLMClient) -> int | None:
    """Read the live limit without making list/add availability depend on it."""
    try:
        limits = await client.settings.get_account_limits()
    except Exception as exc:  # noqa: BLE001 - optional enrichment must degrade safely
        logger.warning(
            "Could not read source_limit; continuing without capacity enforcement (%s)",
            type(exc).__name__,
        )
        return None
    return limits.source_limit


async def get_source_capacity_with_sources(
    client: NotebookLMClient, notebook_id: str
) -> tuple[list[Source], SourceCapacity]:
    """Fetch one source roster and its live-limit capacity snapshot."""
    sources, limits = await asyncio.gather(
        client.sources.list(notebook_id),
        get_source_limit_or_none(client),
    )
    return sources, SourceCapacity.from_sources(sources, limits)


async def get_source_capacity(client: NotebookLMClient, notebook_id: str) -> SourceCapacity:
    """Fetch current source records and the live account limit concurrently."""
    _, capacity = await get_source_capacity_with_sources(client, notebook_id)
    return capacity


async def ensure_source_capacity(client: NotebookLMClient, notebook_id: str) -> SourceCapacity:
    """Raise before a create RPC when the notebook has no remaining capacity."""
    capacity = await get_source_capacity(client, notebook_id)
    capacity.require_available(notebook_id)
    return capacity


__all__ = [
    "SourceCapacity",
    "ensure_source_capacity",
    "get_source_capacity",
    "get_source_capacity_with_sources",
    "get_source_limit_or_none",
]
