"""Project neutral semantic records onto the existing public value models."""

from __future__ import annotations

from typing import cast

from ._records import NotebookRecord, SourceRecord
from .types import (
    ChatGoal,
    ChatResponseLength,
    ChatSession,
    ChatSettings,
    DriveSourceStatus,
    Notebook,
    PremiumFeatureInfo,
    SharePermission,
    Source,
    SourceStatus,
)

_NOTEBOOK_ROLES = {
    "owner": SharePermission.OWNER,
    "editor": SharePermission.EDITOR,
    "viewer": SharePermission.VIEWER,
}
_CHAT_GOALS = {
    "default": ChatGoal.DEFAULT,
    "custom": ChatGoal.CUSTOM,
    "learning_guide": ChatGoal.LEARNING_GUIDE,
}
_CHAT_RESPONSE_LENGTHS = {
    "default": ChatResponseLength.DEFAULT,
    "long": ChatResponseLength.LONGER,
    "longer": ChatResponseLength.LONGER,
    "short": ChatResponseLength.SHORTER,
    "shorter": ChatResponseLength.SHORTER,
}

# ``Source`` still exposes its public ``kind`` through the legacy private integer
# constructor field. Keep this reverse table beside the compatibility projector,
# not in a semantic service: records and services remain free of wire codes.
_SOURCE_KIND_CODES = {
    "unknown": 0,
    "google_docs": 1,
    "google_slides": 2,
    "pdf": 3,
    "pasted_text": 4,
    "web_page": 5,
    "powerpoint": 6,
    "markdown": 8,
    "youtube": 9,
    "media": 10,
    "docx": 11,
    "image": 13,
    "google_spreadsheet": 14,
    "csv": 16,
    "epub": 17,
}
_SOURCE_STATUSES = {
    "unknown": SourceStatus.UNKNOWN,
    "processing": SourceStatus.PROCESSING,
    "ready": SourceStatus.READY,
    "error": SourceStatus.ERROR,
    "preparing": SourceStatus.PREPARING,
}
_DRIVE_STATUSES = {
    "unknown": DriveSourceStatus.UNKNOWN,
    "inaccessible": DriveSourceStatus.INACCESSIBLE,
    "syncing": DriveSourceStatus.SYNCING,
    "active": DriveSourceStatus.ACTIVE,
    "deleted": DriveSourceStatus.DELETED,
    "gen_ai_access_denied": DriveSourceStatus.GEN_AI_ACCESS_DENIED,
}


def _project_chat_settings(record: NotebookRecord) -> ChatSettings | None:
    settings = record.chat_settings
    if settings is None:
        return None
    goal = _CHAT_GOALS.get(settings.goal)
    response_length = _CHAT_RESPONSE_LENGTHS.get(settings.response_length)
    if goal is None or response_length is None:
        # The legacy whole-notebook mapper soft-degrades a malformed optional
        # settings block instead of discarding the notebook.
        return None
    return ChatSettings(goal, response_length, settings.custom_prompt)


def project_notebook(record: NotebookRecord) -> Notebook:
    """Construct one public :class:`Notebook` from a neutral record."""

    premium = record.premium_features
    premium_features = (
        PremiumFeatureInfo(
            premium.can_edit_advanced_settings,
            premium.can_edit_guidebook_config,
            premium.can_view_analytics,
        )
        if premium is not None
        else None
    )
    return Notebook(
        id=record.id,
        title=record.title,
        created_at=record.created_at,
        sources_count=record.sources_count,
        is_owner=record.is_owner,
        role=_NOTEBOOK_ROLES.get(record.role or ""),
        last_viewed_at=record.last_viewed_at,
        emoji=record.emoji,
        premium_features=premium_features,
        chat_sessions=[ChatSession(session.id) for session in record.chat_sessions],
        chat_settings=_project_chat_settings(record),
    )


def _source_type_code(record: SourceRecord) -> int | None:
    if record.unrecognized_kind is not None:
        # An adapter has already identified this as an unrecognized backend
        # discriminator. Preserve it even though the normalized semantic kind
        # is necessarily ``"unknown"``.
        return cast(int, record.unrecognized_kind)
    known = _SOURCE_KIND_CODES.get(record.kind)
    if known is not None:
        return known
    # A future backend may identify an unknown kind with a string. The legacy
    # public model stores only its opaque private discriminator, but its normal
    # constructor and ``kind`` property safely preserve that value and project
    # ``SourceType.UNKNOWN``. The cast documents that compatibility impedance.
    return cast(int, record.kind)


def project_source(record: SourceRecord) -> Source:
    """Construct one public :class:`Source` from a neutral record."""

    drive_status = None if record.drive_status is None else _DRIVE_STATUSES.get(record.drive_status)
    if record.drive_status is not None and drive_status is None:
        drive_status = DriveSourceStatus.UNKNOWN
    return Source(
        id=record.id,
        title=record.title,
        url=record.url,
        _type_code=_source_type_code(record),
        created_at=record.created_at,
        status=_SOURCE_STATUSES.get(record.status, SourceStatus.UNKNOWN),
        drive_document_id=record.drive_document_id,
        drive_status=drive_status,
        download_url=record.download_url,
        viewer_url=record.viewer_url,
        content_mime=record.content_mime,
        word_count=record.word_count,
        revision_id=record.revision_id,
        revision_timestamp=record.revision_timestamp,
        last_modified_at=record.last_modified_at,
    )


__all__ = ["project_notebook", "project_source"]
