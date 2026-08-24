"""Web source response codecs returning transport-neutral records."""

from __future__ import annotations

import builtins
import logging
import re
import reprlib
from typing import Any

from ..._records import (
    SourceFileRegistrationRecord,
    SourceFulltextRecord,
    SourceGuideRecord,
    SourceRecord,
)
from ..._row_adapters.sources import (
    SourceFulltextRow,
    SourceGuideRow,
    SourceRow,
    unwrap_add_source_rows,
)
from ..._url_utils import pdf_url_display_title
from ...rpc import RPCError, RPCMethod, safe_index
from ...rpc.types import drive_source_status_to_str, source_status_to_str
from .documents import decode_structured_document

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

_SOURCE_ID_UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_SOURCE_ID_FIELD_NAMES = frozenset({"SOURCE_ID", "source_id", "sourceId"})
_CONTEXTUAL_SOURCE_ID_FIELD_NAMES = frozenset({"id"})
_SOURCE_NAME_FIELD_NAMES = frozenset(
    {"SOURCE_NAME", "source_name", "sourceName", "filename", "fileName", "name", "title"}
)
_SOURCE_ID_ENVELOPE_MAX_DEPTH = 8


def _unwrap_singleton_envelope(value: Any) -> tuple[Any, int]:
    depth = 0
    while isinstance(value, list) and len(value) == 1 and depth < _SOURCE_ID_ENVELOPE_MAX_DEPTH:
        (value,) = value
        depth += 1
    return value, depth


def _coerce_filename_candidate(value: Any) -> str | None:
    value, _depth = _unwrap_singleton_envelope(value)
    return value.strip() if isinstance(value, str) else None


def _looks_like_id_string(candidate: str) -> bool:
    return (
        len(candidate) >= 4
        and not any(character in candidate for character in " \t/")
        and any(character.isdigit() or character in "-_" for character in candidate)
    )


def _coerce_source_id_candidate(value: Any, filename: str) -> str | None:
    value, _depth = _unwrap_singleton_envelope(value)
    if not isinstance(value, str) or len(value) > 1000:
        return None
    candidate = value.strip()
    if not candidate or candidate == filename:
        return None
    if _SOURCE_ID_UUID_PATTERN.match(candidate) or _looks_like_id_string(candidate):
        return candidate
    return None


def _source_context_names(node: dict[Any, Any]) -> list[Any]:
    return [
        value
        for key, value in node.items()
        if isinstance(key, str) and key in _SOURCE_NAME_FIELD_NAMES
    ]


def _extract_source_id_field_candidates(result: Any, filename: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def add_candidate(value: Any) -> None:
        candidate = _coerce_source_id_candidate(value, filename)
        if candidate is not None and candidate not in seen:
            candidates.append(candidate)
            seen.add(candidate)

    def walk(node: Any, depth: int) -> None:
        if depth > _SOURCE_ID_ENVELOPE_MAX_DEPTH:
            return
        if isinstance(node, dict):
            names = _source_context_names(node)
            matched_context = bool(names) and any(
                _coerce_filename_candidate(name) == filename for name in names
            )
            mismatched_context = bool(names) and not matched_context
            for key, value in node.items():
                if not isinstance(key, str):
                    continue
                if (
                    key in _SOURCE_ID_FIELD_NAMES
                    and not mismatched_context
                    and (depth == 0 or matched_context)
                ) or (key in _CONTEXTUAL_SOURCE_ID_FIELD_NAMES and matched_context):
                    add_candidate(value)
            for value in node.values():
                walk(value, depth + 1)
        elif isinstance(node, list):
            for child in node:
                walk(child, depth + 1)

    walk(result, 0)
    return candidates


def _extract_contextual_source_id_row_candidates(result: Any, filename: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def add_candidate(value: Any) -> None:
        candidate = _coerce_source_id_candidate(value, filename)
        if candidate is not None and candidate not in seen:
            candidates.append(candidate)
            seen.add(candidate)

    def walk(node: Any, depth: int) -> None:
        if depth > _SOURCE_ID_ENVELOPE_MAX_DEPTH:
            return
        if isinstance(node, list):
            if len(node) >= 2:
                first, second, *_rest = node
                if _coerce_filename_candidate(second) == filename:
                    add_candidate(first)
                if _coerce_filename_candidate(first) == filename:
                    add_candidate(second)
            for child in node:
                walk(child, depth + 1)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value, depth + 1)

    walk(result, 0)
    return candidates


def _extract_singleton_source_id_envelope(result: Any, filename: str) -> str | None:
    node, depth = _unwrap_singleton_envelope(result)
    return None if depth == 0 else _coerce_source_id_candidate(node, filename)


def _extract_prefixed_singleton_source_id_envelope(result: Any, filename: str) -> str | None:
    if not isinstance(result, list) or len(result) != 2:
        return None
    prefix, inner = result
    return _extract_singleton_source_id_envelope(inner, filename) if prefix is None else None


def _extract_register_file_source_id(result: Any, filename: str) -> str | None:
    field_candidates = _extract_source_id_field_candidates(result, filename)
    if len(field_candidates) == 1:
        (candidate,) = field_candidates
        return candidate
    if len(field_candidates) > 1:
        return None

    row_candidates = _extract_contextual_source_id_row_candidates(result, filename)
    if len(row_candidates) == 1:
        (candidate,) = row_candidates
        return candidate
    if len(row_candidates) > 1:
        return None

    prefixed = _extract_prefixed_singleton_source_id_envelope(result, filename)
    if prefixed is not None:
        return prefixed
    return _extract_singleton_source_id_envelope(result, filename)


def _register_response_shape_label(result: Any) -> str:
    if isinstance(result, dict):
        return "object"
    if isinstance(result, list):
        return "array"
    if isinstance(result, str):
        return "string"
    if result is None:
        return "null"
    return type(result).__name__


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


def decode_source_snapshot(
    notebook_id: str,
    payload: Any,
    *,
    strict: bool = False,
    logger: logging.Logger,
) -> tuple[SourceRecord, ...]:
    """Decode one ``GET_NOTEBOOK`` envelope into unique source records."""

    if not payload or not isinstance(payload, builtins.list):
        logger.warning(
            "SourcesAPI.list: Empty or invalid notebook response when listing sources for %s "
            "(API response structure may have changed)",
            notebook_id,
        )
        raise RPCError(f"Could not list sources for {notebook_id}: API response structure changed")

    notebook_row = safe_index(
        payload,
        0,
        method_id=RPCMethod.GET_NOTEBOOK.value,
        source="decode_source_snapshot",
    )
    if not isinstance(notebook_row, builtins.list) or len(notebook_row) <= 1:
        logger.warning(
            "SourcesAPI.list: Unexpected notebook structure for %s: expected list with "
            "sources at index 1 (API structure may have changed)",
            notebook_id,
        )
        raise RPCError(f"Could not list sources for {notebook_id}: API response structure changed")

    source_rows = safe_index(
        notebook_row,
        1,
        method_id=RPCMethod.GET_NOTEBOOK.value,
        source="decode_source_snapshot",
    )
    if source_rows is None:
        return ()
    if not isinstance(source_rows, builtins.list):
        logger.warning(
            "SourcesAPI.list: Sources data for %s is not a list (type=%s), returning empty "
            "list (API structure may have changed)",
            notebook_id,
            type(source_rows).__name__,
        )
        raise RPCError(
            f"Could not list sources for {notebook_id}: "
            f"sources data is {type(source_rows).__name__}, not list"
        )

    seen: dict[str, SourceRecord] = {}
    sources: list[SourceRecord] = []
    for index, raw_row in enumerate(source_rows):
        if not isinstance(raw_row, builtins.list) or not raw_row:
            if strict:
                raise RPCError(
                    f"Could not list sources for {notebook_id}: "
                    f"malformed source row at index {index}"
                )
            continue
        row = SourceRow.from_entry(raw_row, method_id=RPCMethod.GET_NOTEBOOK.value)
        if not row.has_id:
            logger.warning(
                "SourcesAPI.list: Skipping source with unexpected id shape: %s",
                repr(raw_row)[:500],
            )
            if strict:
                raise RPCError(
                    f"Could not list sources for {notebook_id}: "
                    f"source row at index {index} has no usable id"
                )
            continue
        if strict and (shape_error := row.listing_shape_error()) is not None:
            raise RPCError(
                f"Could not list sources for {notebook_id}: "
                f"incomplete source row at index {index} ({shape_error})"
            )
        source = decode_source_row(row)
        previous = seen.get(source.id)
        if previous is not None:
            if strict and source != previous:
                raise RPCError(
                    f"Could not list sources for {notebook_id}: "
                    f"conflicting duplicate source row at index {index}"
                )
            logger.debug("SourcesAPI.list: Skipping duplicate source id %s", source.id)
            continue
        seen[source.id] = source
        sources.append(source)
    return tuple(sources)


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


def encode_source_snapshot(notebook_id: str) -> list[Any]:
    """Encode the recency-writing ``GET_NOTEBOOK`` source snapshot request."""

    return [notebook_id, None, _template_block(), None, 0]


def encode_delete(source_id: str) -> list[Any]:
    """Encode one source id for the batch-capable delete method."""
    return [[[source_id]]]


def encode_register_file_source(filename: str, notebook_id: str) -> list[Any]:
    """Encode the file-source registration mutation."""

    return [[[filename]], notebook_id, _template_block()]


def encode_update_source(source_id: str, new_title: str) -> list[Any]:
    """Encode one source-title set operation."""

    return [None, [source_id], [[[new_title]]]]


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


def decode_file_registration(payload: Any, *, filename: str) -> SourceFileRegistrationRecord:
    """Decode a file registration without exposing the raw envelope upstream."""

    return SourceFileRegistrationRecord(
        source_id=_extract_register_file_source_id(payload, filename),
        response_shape=_register_response_shape_label(payload),
    )


def decode_source_guide(payload: Any) -> SourceGuideRecord:
    """Soft-decode the optional source guide fields."""
    row = SourceGuideRow(payload)
    return SourceGuideRecord(summary=row.summary, keywords=tuple(row.keywords))


def _extract_all_text(
    data: builtins.list[Any],
    *,
    logger: logging.Logger,
    max_depth: int = 100,
) -> builtins.list[str]:
    """Preserve the historical flat fulltext traversal exactly."""

    if max_depth <= 0:
        logger.warning("Max recursion depth reached in text extraction")
        return []
    texts: builtins.list[str] = []
    for item in data:
        if isinstance(item, str) and item:
            texts.append(item)
        elif isinstance(item, builtins.list):
            texts.extend(_extract_all_text(item, logger=logger, max_depth=max_depth - 1))
    return texts


def decode_source_fulltext(
    payload: Any,
    *,
    source_id: str,
    output_format: str,
    logger: logging.Logger,
) -> SourceFulltextRecord | None:
    """Decode the optional ``GET_SOURCE`` envelope into a neutral fulltext record."""

    if not payload or not isinstance(payload, list):
        return None

    source_type: int | None = None
    url: str | None = None
    content = ""
    fulltext_row = SourceFulltextRow(payload)
    title = fulltext_row.title
    metadata = fulltext_row.metadata
    if metadata is not None:
        source_row = fulltext_row.source_row
        source_type = source_row.type_code if source_row is not None else None
        if source_type == 14 and source_row is not None and source_row.mime == "application/pdf":
            source_type = 3
        type_slot = fulltext_row.raw_metadata_type_slot
        if source_type is None and type_slot is not None:
            logger.warning(
                "Source %s metadata type-code slot malformed (expected int at "
                "metadata[4], got %s); treating type as unknown: %s",
                source_id,
                type(type_slot).__name__,
                reprlib.repr(metadata),
            )
        url = SourceRow.url_from_metadata(metadata, allow_bare_http=False)

    content_blocks = fulltext_row.text_content_blocks
    document = (
        decode_structured_document(content_blocks)
        if content_blocks is not None
        else decode_structured_document([])
    )
    if output_format == "markdown":
        html_content = fulltext_row.html_content
        if html_content is not None:
            from ..._source.markdown import html_to_markdown

            content = html_to_markdown(html_content, source_type=source_type)
        else:
            logger.warning(
                "Source %s (type=%s) has no HTML rendition for output_format='markdown'; "
                "returning empty content. Retry with output_format='text'.",
                source_id,
                source_type,
            )
    elif content_blocks is not None:
        content = "\n".join(_extract_all_text(content_blocks, logger=logger))

    if not content:
        logger.warning(
            "Source %s returned empty content (type=%s, title=%s)",
            source_id,
            source_type,
            title,
        )

    kind = _SOURCE_KINDS.get(source_type, "unknown") if source_type is not None else "unknown"
    if title is not None and title == url and source_type == 3:
        title = pdf_url_display_title(title) or title
    return SourceFulltextRecord(
        source_id=source_id,
        title=title,
        content=content,
        kind=kind,
        unrecognized_kind=(
            source_type if source_type is not None and source_type not in _SOURCE_KINDS else None
        ),
        kind_present=source_type is not None,
        url=url,
        char_count=len(content),
        document=document,
    )


__all__ = [
    "decode_add_source_records",
    "decode_file_registration",
    "decode_source",
    "decode_source_fulltext",
    "decode_source_guide",
    "decode_source_record",
    "decode_source_row",
    "decode_source_snapshot",
    "encode_add_drive",
    "encode_add_text",
    "encode_add_url_batch",
    "encode_delete",
    "encode_get_fulltext",
    "encode_get_guide",
    "encode_refresh_or_freshness",
    "encode_register_file_source",
    "encode_source_snapshot",
    "encode_update_source",
]
