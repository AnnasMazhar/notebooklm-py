"""Web source-label codecs."""

from __future__ import annotations

from typing import Any

from ..._records import LabelRecord
from ..._row_adapters.labels import LabelRow


def decode_label(
    data: list[Any],
    *,
    notebook_id: str | None = None,
    method_id: str | None = None,
) -> LabelRecord:
    """Decode one strict four-slot source-label tuple."""

    row = LabelRow.from_label_tuple(data, method_id=method_id)
    return LabelRecord(
        id=row.id,
        name=row.name,
        notebook_id=notebook_id,
        emoji=row.emoji or None,
        source_ids=tuple(row.source_ids),
    )


__all__ = ["decode_label"]
