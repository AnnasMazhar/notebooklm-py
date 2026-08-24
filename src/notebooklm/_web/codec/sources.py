"""Web source response codecs returning transport-neutral records."""

from __future__ import annotations

from ..._records import SourceRecord
from ..._row_adapters.sources import SourceRow
from ..._url_utils import pdf_url_display_title
from ...rpc.types import drive_source_status_to_str, source_status_to_str

_SOURCE_KINDS = {
    0: "unknown",
    1: "google_docs",
    2: "google_slides",
    3: "pdf",
    4: "pasted_text",
    5: "web_page",
    6: "powerpoint",
    8: "markdown",
    9: "youtube",
    10: "media",
    11: "docx",
    13: "image",
    14: "google_spreadsheet",
    16: "csv",
    17: "epub",
}


def _effective_type_code(row: SourceRow) -> int | None:
    type_code = row.type_code
    for mime in (row.content_mime, row.mime):
        if type_code == 14 and mime == "application/pdf":
            type_code = 3
    return type_code


def decode_source_row(row: SourceRow) -> SourceRecord:
    """Decode one normalized source row without constructing ``Source``."""

    type_code = _effective_type_code(row)
    title = row.title
    if title is not None and title == row.url and type_code == 3:
        title = pdf_url_display_title(title) or title
    return SourceRecord(
        id=row.id,
        title=title,
        url=row.url,
        kind=_SOURCE_KINDS.get(type_code, "unknown") if type_code is not None else "unknown",
        unrecognized_kind=(
            type_code if type_code is not None and type_code not in _SOURCE_KINDS else None
        ),
        created_at=row.created_at,
        status=source_status_to_str(row.status),
        drive_document_id=row.drive_document_id,
        drive_status=(
            drive_source_status_to_str(row.drive_status) if row.drive_status is not None else None
        ),
        download_url=row.download_url,
        viewer_url=row.viewer_url,
        content_mime=row.content_mime,
        word_count=row.word_count,
        revision_id=row.revision_id,
        revision_timestamp=row.revision_timestamp,
        last_modified_at=row.last_modified_at,
        kind_present=type_code is not None,
    )


def decode_source(data: list[object], *, method_id: str | None = None) -> SourceRecord:
    """Decode one flat/medium/deep source response."""

    return decode_source_row(SourceRow.from_unknown_shape(data, method_id=method_id))


__all__ = ["decode_source", "decode_source_row"]
