"""Decode the mixed web note-row collection into neutral note records."""

from __future__ import annotations

from typing import Any

from ..._records import MindMapRecord, NoteRecord
from ..._row_adapters.notes import NoteRow
from ...exceptions import DecodingError, RPCError
from ...rpc import RPCMethod, safe_index


def _is_note_row_like(item: Any) -> bool:
    if not isinstance(item, list) or not item:
        return False
    method_id = RPCMethod.GET_NOTES_AND_MIND_MAPS.value
    head = safe_index(item, 0, method_id=method_id, source="notes codec row detection")
    if isinstance(head, str):
        return True
    if head is not None or len(item) <= 1:
        return False
    nested = safe_index(item, 1, method_id=method_id, source="notes codec row detection")
    if not isinstance(nested, list) or not nested:
        return False
    nested_head = safe_index(
        nested,
        0,
        method_id=method_id,
        source="notes codec row detection",
    )
    return isinstance(nested_head, str)


def _normalize_note_row(item: Any) -> list[Any] | None:
    if not _is_note_row_like(item):
        return None
    method_id = RPCMethod.GET_NOTES_AND_MIND_MAPS.value
    head = safe_index(item, 0, method_id=method_id, source="notes codec row normalization")
    if isinstance(head, str):
        return item
    nested = safe_index(item, 1, method_id=method_id, source="notes codec row normalization")
    nested_head = safe_index(
        nested,
        0,
        method_id=method_id,
        source="notes codec row normalization",
    )
    return [nested_head, nested, *item[2:]]


def _decode_note_rows(result: Any) -> tuple[list[Any], ...]:
    """Normalize the known nested/flat GET_NOTES_AND_MIND_MAPS envelopes."""

    if not result:
        return ()
    if not isinstance(result, list):
        raise DecodingError(
            "Unrecognized GET_NOTES_AND_MIND_MAPS payload shape",
            raw_response=repr(result),
            method_id=RPCMethod.GET_NOTES_AND_MIND_MAPS.value,
        )
    first = safe_index(
        result,
        0,
        method_id=RPCMethod.GET_NOTES_AND_MIND_MAPS.value,
        source="notes codec response envelope",
    )
    if _is_note_row_like(first):
        rows = result
    elif isinstance(first, list):
        rows = first
    else:
        return ()
    return tuple(row for item in rows if (row := _normalize_note_row(item)) is not None)


def _record(row: list[Any], notebook_id: str) -> NoteRecord:
    note = NoteRow(row)
    return NoteRecord(
        id=note.id,
        notebook_id=notebook_id,
        title=note.title,
        content=note.content or "",
        created_at=note.created_at,
    )


def decode_notes(result: Any, notebook_id: str) -> tuple[NoteRecord, ...]:
    """Decode active non-mind-map rows, preserving backend order."""

    records: list[NoteRecord] = []
    for row in _decode_note_rows(result):
        note = NoteRow(row)
        if note.is_deleted or NoteRow.is_mind_map_content(note.content):
            continue
        records.append(_record(row, notebook_id))
    return tuple(records)


def decode_note(result: Any, notebook_id: str, note_id: str) -> NoteRecord | None:
    """Select the first exact row id, matching the legacy optional lookup."""

    for row in _decode_note_rows(result):
        if NoteRow(row).id == note_id:
            return _record(row, notebook_id)
    return None


def decode_note_backed_mind_maps(result: Any, notebook_id: str) -> tuple[MindMapRecord, ...]:
    """Decode active note-backed rows without exposing the mixed wire collection."""

    records: list[MindMapRecord] = []
    for row in _decode_note_rows(result):
        note = NoteRow(row)
        if note.is_deleted or not NoteRow.is_mind_map_content(note.content):
            continue
        records.append(
            MindMapRecord(
                id=note.id,
                notebook_id=notebook_id,
                title=note.title,
                kind="note_backed",
                created_at=note.created_at,
                tree_json=note.content,
            )
        )
    return tuple(records)


def decode_created_note(result: Any, notebook_id: str, title: str, content: str) -> NoteRecord:
    """Decode CREATE_NOTE's nested or flat identity/timestamp envelope."""

    note_id: str | None = None
    created_inner_row: list[Any] | None = None
    if result and isinstance(result, list):
        first = safe_index(
            result,
            0,
            method_id=RPCMethod.CREATE_NOTE.value,
            source="notes codec create response",
        )
        if isinstance(first, list) and first:
            candidate = safe_index(
                first,
                0,
                method_id=RPCMethod.CREATE_NOTE.value,
                source="notes codec create response",
            )
            note_id = candidate if isinstance(candidate, str) else None
            created_inner_row = first
        elif isinstance(first, str):
            note_id = first
            created_inner_row = result
    if not note_id:
        raise RPCError(
            "CREATE_NOTE returned no usable note id; the note was not created",
            method_id=RPCMethod.CREATE_NOTE.value,
        )
    created_at = (
        NoteRow([note_id, created_inner_row]).created_at if created_inner_row is not None else None
    )
    return NoteRecord(note_id, notebook_id, title, content, created_at)


__all__ = [
    "decode_created_note",
    "decode_note",
    "decode_note_backed_mind_maps",
    "decode_notes",
]
