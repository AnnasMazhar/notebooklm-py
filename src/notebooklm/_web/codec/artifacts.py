"""Web artifact response codecs returning transport-neutral records."""

from __future__ import annotations

import logging
import reprlib
from typing import Any

from ..._artifact.formatters import _parse_data_table
from ..._records import (
    ArtifactInfographicRecord,
    ArtifactMediaRecord,
    ArtifactRecord,
    ArtifactRepresentationRecord,
    ArtifactSlideRecord,
    ArtifactUserStateRecord,
    GenerationStatusRecord,
    MindMapRepresentationRecord,
    ReportSuggestionRecord,
)
from ..._row_adapters.artifacts import (
    ArtifactRow,
    ReportSuggestionRow,
    _ArtifactUserStateValue,
    _AudioUserStateValue,
    _FlashcardUserStateValue,
    _UnknownUserStateValue,
)
from ..._row_adapters.notes import NoteRow
from ...exceptions import ArtifactParseError, UnknownRPCMethodError
from ...rpc import RPCMethod, safe_index
from ...rpc.types import ArtifactStatus, ArtifactTypeCode, artifact_status_to_str
from .notes import _decode_note_rows

logger = logging.getLogger("notebooklm._types.artifacts")

_ARTIFACT_FAMILIES = {
    1: "audio",
    2: "report",
    3: "video",
    5: "mind_map",
    6: "fantasy_map",
    7: "infographic",
    8: "slide_deck",
    9: "data_table",
    10: "file",
}
_ARTIFACT_VARIANTS = {1: "flashcards", 2: "quiz", 4: "interactive_mind_map"}
_MEDIA_KINDS = {1: "progressive", 2: "hls", 3: "dash", 4: "download"}


def _decode_user_state(value: _ArtifactUserStateValue) -> ArtifactUserStateRecord:
    if isinstance(value, _AudioUserStateValue):
        return ArtifactUserStateRecord(
            kind="audio",
            playback_position_seconds=float(value.playback_position_seconds),
        )
    if isinstance(value, _FlashcardUserStateValue):
        return ArtifactUserStateRecord(
            kind="flashcards",
            card_acquisitions=value.card_acquisitions,
            current_card_index=value.current_card_index,
            hidden_card_indices=value.hidden_card_indices,
            last_shown_order=value.last_shown_order,
            current_view=value.current_view,
        )
    assert isinstance(value, _UnknownUserStateValue)
    return ArtifactUserStateRecord(kind="unknown", raw=value.raw)


def _artifact_identity(
    type_code: int, variant_code: int | None
) -> tuple[str, int | str | None, str | None, int | str | None]:
    variant = None if variant_code is None else _ARTIFACT_VARIANTS.get(variant_code)
    if type_code == ArtifactTypeCode.QUIZ.value and variant is not None:
        family = "mind_map" if variant == "interactive_mind_map" else variant
        unrecognized_family: int | str | None = None
    else:
        family = _ARTIFACT_FAMILIES.get(type_code, "unknown")
        unrecognized_family = type_code if type_code not in _ARTIFACT_FAMILIES else None
    unrecognized_variant = (
        variant_code
        if type_code == ArtifactTypeCode.QUIZ.value and variant_code is not None and variant is None
        else None
    )
    return family, unrecognized_family, variant, unrecognized_variant


def decode_artifact(data: list[Any]) -> ArtifactRecord:
    """Decode one ``LIST_ARTIFACTS`` row without constructing ``Artifact``."""

    row = ArtifactRow(data)
    type_code = row.type_code
    variant_code = row.variant
    family, unrecognized_family, variant, unrecognized_variant = _artifact_identity(
        type_code, variant_code
    )
    status = artifact_status_to_str(row.status)
    try:
        generation_prompt = row.generation_prompt
    except UnknownRPCMethodError:
        generation_prompt = None
    user_state = row.user_state_value
    return ArtifactRecord(
        id=row.id,
        title=row.title,
        family=family,
        status=status,
        unrecognized_family=unrecognized_family,
        variant=variant,
        interactive_variant_pending=(
            type_code == ArtifactTypeCode.QUIZ.value and variant_code is None
        ),
        unrecognized_variant=unrecognized_variant,
        unrecognized_status=(row.status if status == "unknown" and row.status != 0 else None),
        created_at=row.created_at,
        url=row.artifact_url(type_code, suppress_drift=True),
        generation_prompt=generation_prompt,
        media_urls=tuple(
            ArtifactMediaRecord(
                url=item.url,
                kind=item.kind,
                unrecognized_kind=(
                    item.type_code
                    if item.type_code is not None and item.type_code not in _MEDIA_KINDS
                    else None
                ),
                mime_type=item.mime_type,
            )
            for item in row.media_values
        ),
        duration_seconds=row.duration_seconds,
        slides=tuple(
            ArtifactSlideRecord(
                item.image_url,
                item.width,
                item.height,
                item.alt_text,
                item.text,
            )
            for item in row.slide_values
        ),
        infographics=tuple(
            ArtifactInfographicRecord(
                item.title,
                item.image_url,
                item.width,
                item.height,
                item.alt_text,
                item.text,
            )
            for item in row.infographic_values
        ),
        report_kind=row.report_kind,
        source_ids=row.source_ids,
        last_modified_at=row.last_modified_at,
        etag=row.etag,
        user_state=_decode_user_state(user_state) if user_state is not None else None,
    )


def decode_mind_map_artifact(data: list[Any]) -> ArtifactRecord | None:
    """Decode one note-backed mind-map row, excluding delete tombstones."""

    if not isinstance(data, list) or not data:
        return None
    row = NoteRow(data)
    if row.is_deleted:
        return None
    if row.has_unrecognized_tombstone:
        logger.warning(
            "Mind-map row %s has a null content slot without the "
            "soft-delete sentinel (tombstone drift? a deleted mind map "
            "may be leaking as live): %s",
            row.id,
            reprlib.repr(data),
        )
    return ArtifactRecord(
        id=row.id,
        title=row.title,
        family="mind_map",
        status=artifact_status_to_str(ArtifactStatus.COMPLETED.value),
        created_at=row.created_at,
    )


def decode_report_suggestion(data: list[Any]) -> ReportSuggestionRecord:
    """Decode one suggested-report row."""

    row = ReportSuggestionRow(data)
    return ReportSuggestionRecord(
        title=row.title,
        description=row.description,
        prompt=row.prompt,
        audience_level=row.audience_level,
    )


def decode_artifact_representation(data: list[Any]) -> ArtifactRepresentationRecord:
    """Decode only the representation fields relevant to one artifact family."""

    row = ArtifactRow(data)
    type_code = row.type_code
    variant_code = row.variant if type_code == ArtifactTypeCode.QUIZ.value else None
    family, unrecognized_family, variant, unrecognized_variant = _artifact_identity(
        type_code, variant_code
    )
    status = artifact_status_to_str(row.status)
    artifact = ArtifactRecord(
        id=row.id,
        title=row.title,
        family=family,
        status=status,
        unrecognized_family=unrecognized_family,
        variant=variant,
        unrecognized_variant=unrecognized_variant,
        unrecognized_status=(row.status if status == "unknown" and row.status != 0 else None),
        created_at=row.created_at,
    )
    audio_url = video_url = infographic_url = None
    slide_deck_pdf_url = slide_deck_pptx_url = None
    report_markdown = None
    data_table_headers: tuple[str, ...] = ()
    data_table_rows: tuple[tuple[str, ...], ...] = ()
    data_table_error = parse_error = None
    try:
        if artifact.family == "audio":
            audio_url = row.audio_url
        elif artifact.family == "video":
            video_url = row.video_url
        elif artifact.family == "infographic":
            infographic_url = row.infographic_url
        elif artifact.family == "slide_deck":
            slide_deck_pdf_url = row.slide_deck_pdf_url
            slide_deck_pptx_url = row.slide_deck_pptx_url
        elif artifact.family == "report":
            report_markdown = row.report_markdown
        elif artifact.family == "data_table":
            try:
                headers, rows = _parse_data_table(row.data_table_raw_payload)
            except ArtifactParseError as exc:
                data_table_error = exc.details or str(exc)
            else:
                data_table_headers = tuple(headers)
                data_table_rows = tuple(tuple(item) for item in rows)
    except (IndexError, TypeError, UnknownRPCMethodError) as exc:
        parse_error = str(exc)
    return ArtifactRepresentationRecord(
        artifact=artifact,
        audio_url=audio_url,
        video_url=video_url,
        infographic_url=infographic_url,
        slide_deck_pdf_url=slide_deck_pdf_url,
        slide_deck_pptx_url=slide_deck_pptx_url,
        report_markdown=report_markdown,
        data_table_headers=data_table_headers,
        data_table_rows=data_table_rows,
        data_table_error=data_table_error,
        parse_error=parse_error,
    )


def decode_mind_map_representation(data: list[Any]) -> MindMapRepresentationRecord | None:
    """Decode one live note-backed mind map without retaining its wire row."""

    row = NoteRow(data)
    if row.is_deleted or not row.is_mind_map:
        return None
    return MindMapRepresentationRecord(
        id=row.id,
        title=row.title,
        content=row.content,
        created_at=row.created_at,
    )


def decode_mind_map_representations(result: object) -> tuple[MindMapRepresentationRecord, ...]:
    """Decode live note-backed maps from the mixed note collection."""

    return tuple(
        record
        for row in _decode_note_rows(result)
        if (record := decode_mind_map_representation(row)) is not None
    )


def decode_interactive_content(result: object, *, tree: bool) -> str | None:
    """Decode quiz/flashcard HTML or an interactive mind-map tree leaf."""

    if result is None:
        return None
    payload = safe_index(
        result,
        0,
        9,
        method_id=RPCMethod.GET_INTERACTIVE_HTML.value,
        source=(
            "_artifact_downloads._get_interactive_mind_map_tree"
            if tree
            else "_artifact_downloads._get_artifact_content"
        ),
    )
    if not isinstance(payload, list):
        return None
    index = 3 if tree else 0
    if len(payload) <= index:
        return None
    value = payload[index]
    return value if isinstance(value, str) else None


def decode_artifact_poll(
    rows: list[list[Any]],
    task_id: str,
) -> GenerationStatusRecord:
    """Decode one lifecycle observation with the legacy media-settling rule."""

    row = None
    for item in rows:
        candidate = ArtifactRow(item)
        if candidate.id == task_id:
            row = candidate
            break
    if row is None:
        return GenerationStatusRecord(task_id=task_id, status="not_found")

    status_code = row.status
    raw_status = artifact_status_to_str(status_code)
    metadata: tuple[tuple[str, object], ...] = ()
    if status_code == ArtifactStatus.COMPLETED.value and not row.is_media_ready(row.type_code):
        try:
            type_name = ArtifactTypeCode(row.type_code).name
        except ValueError:
            type_name = str(row.type_code)
        metadata = (
            ("artifact_type", type_name),
            ("artifact_type_code", row.type_code),
            ("media_ready", False),
            ("normalized_status", "in_progress"),
            ("raw_status", raw_status),
        )
        status = "in_progress"
    else:
        status = raw_status
    return GenerationStatusRecord(
        task_id=task_id,
        status=status,
        url=row.artifact_url(row.type_code, suppress_drift=True),
        metadata=metadata,
    )


__all__ = [
    "decode_artifact",
    "decode_artifact_representation",
    "decode_artifact_poll",
    "decode_interactive_content",
    "decode_mind_map_artifact",
    "decode_mind_map_representation",
    "decode_mind_map_representations",
    "decode_report_suggestion",
]
