"""Web source response codecs returning transport-neutral records."""

from __future__ import annotations

from typing import Any

from ..._records import SourceGuideRecord, SourceRecord
from ..._row_adapters.sources import SourceGuideRow, SourceRow, unwrap_add_source_rows
from ..._url_utils import pdf_url_display_title
from ...rpc import RPCMethod
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


def _template_block() -> list[Any]:
    """Return the captured create/source request-options wrapper."""
    return [2, None, None, [1, None, None, None, None, None, None, None, None, None, [1]]]


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


def encode_add_text(notebook_id: str, title: str, content: str) -> list[Any]:
    """Encode the pasted-text ``ADD_SOURCE`` variant."""
    return [
        [[None, [title, content], None, 2, None, None, None, None, None, None, 1]],
        notebook_id,
        _template_block(),
    ]


def encode_add_drive(
    notebook_id: str,
    file_id: str,
    title: str,
    mime_type: str,
) -> list[Any]:
    """Encode the live-pinned native Drive ``ADD_SOURCE`` variant."""
    source_data = [
        [file_id, mime_type, 1, title],
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        1,
    ]
    return [
        [source_data],
        notebook_id,
        [2],
        [1, None, None, None, None, None, None, None, None, None, [1]],
    ]


def _url_spec(url: str, *, youtube: bool) -> list[Any]:
    if youtube:
        return [None, None, None, None, None, None, None, [url], None, None, 1]
    return [None, None, [url], None, None, None, None, None, None, None, 1]


def encode_add_url_batch(
    notebook_id: str,
    urls: tuple[str, ...] | list[str],
    *,
    youtube_flags: tuple[bool, ...] | list[bool],
) -> list[Any]:
    """Encode one true-batch URL/YouTube ``ADD_SOURCE`` request."""
    if len(urls) != len(youtube_flags):
        raise ValueError("URL batch and YouTube discriminator counts differ")
    return [
        [_url_spec(url, youtube=youtube) for url, youtube in zip(urls, youtube_flags, strict=True)],
        notebook_id,
        _template_block(),
    ]


def encode_delete(source_id: str) -> list[Any]:
    """Encode one source id for the batch-capable delete method."""
    return [[[source_id]]]


def encode_refresh_or_freshness(source_id: str) -> list[Any]:
    """Encode the shared refresh/freshness identity envelope."""
    return [None, [source_id], [2]]


def encode_get_guide(source_id: str) -> list[Any]:
    """Encode a source-guide request."""
    return [[[[source_id]]]]


def encode_get_fulltext(source_id: str, *, markdown: bool) -> list[Any]:
    """Encode a fulltext request for the text or HTML rendition."""
    return [[source_id], [3], [3]] if markdown else [[source_id], [2], [2]]


def decode_source_record(payload: list[Any], *, method: RPCMethod) -> SourceRecord:
    """Decode one source response through the aggregate source projector grammar."""
    return decode_source(payload, method_id=method.value)


def decode_add_source_records(payload: Any) -> tuple[SourceRecord, ...]:
    """Strictly decode every identified row in an ``ADD_SOURCE`` response."""
    return tuple(
        decode_source_record(row, method=RPCMethod.ADD_SOURCE)
        for row in unwrap_add_source_rows(payload)
    )


def decode_source_guide(payload: Any) -> SourceGuideRecord:
    """Soft-decode the optional source guide fields."""
    row = SourceGuideRow(payload)
    return SourceGuideRecord(summary=row.summary, keywords=tuple(row.keywords))


__all__ = [
    "decode_add_source_records",
    "decode_source",
    "decode_source_guide",
    "decode_source_record",
    "decode_source_row",
    "encode_add_drive",
    "encode_add_text",
    "encode_add_url_batch",
    "encode_delete",
    "encode_get_fulltext",
    "encode_get_guide",
    "encode_refresh_or_freshness",
]
