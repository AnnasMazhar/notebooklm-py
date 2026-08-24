"""Web artifact response codecs returning transport-neutral records."""

from __future__ import annotations

import logging
import reprlib
from typing import Any

from ..._records import (
    ArtifactInfographicRecord,
    ArtifactMediaRecord,
    ArtifactRecord,
    ArtifactSlideRecord,
    ArtifactUserStateRecord,
    AudioArtifactUserStateRecord,
    FlashcardArtifactUserStateRecord,
    ReportSuggestionRecord,
    UnknownArtifactUserStateRecord,
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
from ...exceptions import UnknownRPCMethodError
from ...rpc.types import ArtifactStatus, ArtifactTypeCode

logger = logging.getLogger("notebooklm._types.artifacts")


def _decode_user_state(value: _ArtifactUserStateValue) -> ArtifactUserStateRecord:
    if isinstance(value, _AudioUserStateValue):
        return AudioArtifactUserStateRecord(float(value.playback_position_seconds))
    if isinstance(value, _FlashcardUserStateValue):
        return FlashcardArtifactUserStateRecord(
            card_acquisitions=value.card_acquisitions,
            current_card_index=value.current_card_index,
            hidden_card_indices=value.hidden_card_indices,
            last_shown_order=value.last_shown_order,
            current_view=value.current_view,
        )
    assert isinstance(value, _UnknownUserStateValue)
    return UnknownArtifactUserStateRecord(value.raw)


def decode_artifact(data: list[Any]) -> ArtifactRecord:
    """Decode one ``LIST_ARTIFACTS`` row without constructing ``Artifact``."""

    row = ArtifactRow(data)
    type_code = row.type_code
    try:
        generation_prompt = row.generation_prompt
    except UnknownRPCMethodError:
        generation_prompt = None
    user_state = row.user_state_value
    return ArtifactRecord(
        id=row.id,
        title=row.title,
        artifact_type=type_code,
        status=row.status,
        created_at=row.created_at,
        url=row.artifact_url(type_code, suppress_drift=True),
        variant=row.variant,
        generation_prompt=generation_prompt,
        media_urls=tuple(
            ArtifactMediaRecord(
                url=item.url,
                kind=item.kind,
                type_code=item.type_code,
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
        artifact_type=ArtifactTypeCode.MIND_MAP.value,
        status=ArtifactStatus.COMPLETED.value,
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


__all__ = ["decode_artifact", "decode_mind_map_artifact", "decode_report_suggestion"]
