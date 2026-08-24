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
from ...exceptions import UnknownRPCMethodError
from ...rpc.types import ArtifactStatus, ArtifactTypeCode, artifact_status_to_str

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


__all__ = ["decode_artifact", "decode_mind_map_artifact", "decode_report_suggestion"]
