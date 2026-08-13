"""Shared JSON serializers for source CLI output."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..._app.serialize import source_drive_fields, source_summary
from ...types import source_status_to_str

if TYPE_CHECKING:
    from ...types import Source, SourceFulltext, SourceType


def source_kind_value(kind: SourceType | None) -> str | None:
    """Return the public JSON value for a source kind."""
    return kind.value if kind is not None else None


def source_summary_payload(src: Source) -> dict[str, Any]:
    """Return the stable public JSON shape for source summaries.

    Thin re-export of the neutral :func:`notebooklm._app.serialize.source_summary`
    — the single source of truth for the ``{"id", "title", "type", "url"}``
    shape shared by the CLI and the ``_app`` add/add-drive envelopes (§11). Kept
    as a named wrapper so the historical
    ``source_serializers.source_summary_payload`` import/patch surface resolves.
    """
    return source_summary(src)


def source_row_payload(src: Source) -> dict[str, Any]:
    """Return the CLI's full source row: summary + status axis + Drive axis.

    The single source of truth for the row shape emitted by BOTH
    ``source list --json`` and ``source get --json``. Those two paths built the
    same dict independently until #2113/#2211, which is exactly how the Drive
    fields could land on one surface and not the other; sharing the builder
    makes list/get drift a compile-time impossibility rather than a review
    catch.

    Composed, not widened: :func:`~notebooklm._app.serialize.source_summary`
    stays the narrow shape the add envelopes publish, and the Drive axis comes
    from :func:`~notebooklm._app.serialize.source_drive_fields` — so
    ``source add`` / ``source add-drive`` output is unchanged by this row.

    ``status`` is the label string and ``status_id`` the raw code; the Drive
    axis mirrors that pairing (``drive_status`` / ``drive_status_id``).
    """
    return {
        **source_summary(src),
        "status": source_status_to_str(src.status),
        "status_id": src.status,
        "created_at": src.created_at.isoformat() if src.created_at else None,
        **source_drive_fields(src),
    }


def source_fulltext_payload(fulltext: SourceFulltext) -> dict[str, Any]:
    """Return the stable public JSON shape for source fulltext."""
    return {
        "source_id": fulltext.source_id,
        "title": fulltext.title,
        "kind": source_kind_value(fulltext.kind),
        "content": fulltext.content,
        "url": fulltext.url,
        "char_count": fulltext.char_count,
    }
