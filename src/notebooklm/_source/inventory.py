"""Parse and select one raw notebook source inventory."""

from __future__ import annotations

import builtins
import logging
from typing import Any

from .._row_adapters.sources import SourceRow
from ..rpc import RPCMethod
from ..rpc.types import SourceStatus, source_status_to_str
from ..types import Source, SourceCounts, SourceInventory, SourceType

logger = logging.getLogger("notebooklm").getChild("_sources")

_STATUS_LABELS = tuple(source_status_to_str(status) for status in SourceStatus)
_TYPE_LABELS = tuple(source_type.value for source_type in SourceType)


def build_source_inventory(raw_sources: builtins.list[Any]) -> SourceInventory:
    """Parse every raw record once, retaining phantom and duplicate accounting."""
    seen_ids: set[str] = set()
    sources: builtins.list[Source] = []
    raw_source_ids: builtins.list[str] = []
    idless_records = 0
    duplicate_id_records = 0
    by_status = dict.fromkeys(_STATUS_LABELS, 0)
    by_type = dict.fromkeys(_TYPE_LABELS, 0)

    for raw_source in raw_sources:
        if not isinstance(raw_source, builtins.list) or not raw_source:
            idless_records += 1
            continue
        row = SourceRow.from_entry(raw_source, method_id=RPCMethod.GET_NOTEBOOK.value)
        if not row.has_id:
            idless_records += 1
            logger.warning(
                "SourcesAPI.list: Skipping source with unexpected id shape: %s",
                repr(raw_source)[:500],
            )
            continue

        raw_source_ids.append(row.id)
        if row.id in seen_ids:
            duplicate_id_records += 1
            logger.debug("SourcesAPI.list: Skipping duplicate source id %s", row.id)
            continue

        seen_ids.add(row.id)
        source = Source.from_row(row)
        sources.append(source)
        by_status[source_status_to_str(source.status)] += 1
        by_type[source.kind.value] += 1

    id_bearing_records = len(raw_source_ids)
    counts = SourceCounts(
        records_total=len(raw_sources),
        id_bearing_records=id_bearing_records,
        unique_sources=len(sources),
        idless_records=idless_records,
        duplicate_id_records=duplicate_id_records,
        by_status=by_status,
        by_type=by_type,
    )
    return SourceInventory(
        sources=tuple(sources),
        raw_source_ids=tuple(raw_source_ids),
        counts=counts,
    )


__all__ = ["SourceInventory", "build_source_inventory"]
