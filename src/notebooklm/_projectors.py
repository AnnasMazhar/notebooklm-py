"""Project neutral semantic records onto the existing public value models."""

from __future__ import annotations

import json
from typing import cast
from urllib.parse import quote

from ._env import get_base_url
from ._records import (
    AccountLimitsRecord,
    ArtifactRecord,
    ArtifactUserStateRecord,
    CollectionRecord,
    GenerationStatusRecord,
    LabelRecord,
    MindMapRecord,
    NotebookDescriptionRecord,
    NotebookRecord,
    NoteRecord,
    PromptSuggestionRecord,
    ReportSuggestionRecord,
    ShareStatusRecord,
    SourceRecord,
    UserSettingsRecord,
)
from .types import (
    AccountLimits,
    Artifact,
    ArtifactInfographic,
    ArtifactMedia,
    ArtifactMediaType,
    ArtifactSlide,
    AudioArtifactUserState,
    ChatGoal,
    ChatResponseLength,
    ChatSession,
    ChatSettings,
    Collection,
    DriveSourceStatus,
    FlashcardArtifactUserState,
    GenerationState,
    GenerationStatus,
    Label,
    MindMap,
    MindMapKind,
    Note,
    Notebook,
    NotebookDescription,
    PremiumFeatureInfo,
    PromptSuggestion,
    ReportSuggestion,
    ShareAccess,
    SharedUser,
    SharePermission,
    ShareStatus,
    ShareViewLevel,
    Source,
    SourceStatus,
    SuggestedTopic,
    UnknownArtifactUserState,
    UserSettings,
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

_ARTIFACT_FAMILY_CODES = {
    "unknown": 0,
    "audio": 1,
    "report": 2,
    "video": 3,
    "mind_map": 5,
    "fantasy_map": 6,
    "infographic": 7,
    "slide_deck": 8,
    "data_table": 9,
    "file": 10,
}
_ARTIFACT_VARIANT_CODES = {
    "flashcards": 1,
    "quiz": 2,
    "interactive_mind_map": 4,
}
_ARTIFACT_STATUS_CODES = {
    "unknown": 0,
    "pending": 1,
    "in_progress": 2,
    "completed": 3,
    "failed": 4,
    "suggested": 5,
    "pending_review": 6,
}
_ARTIFACT_MEDIA_TYPES = {
    "progressive": ArtifactMediaType.PROGRESSIVE,
    "hls": ArtifactMediaType.HLS,
    "dash": ArtifactMediaType.DASH,
    "download": ArtifactMediaType.DOWNLOAD,
    "unknown": ArtifactMediaType.UNKNOWN,
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


def project_account_limits(record: AccountLimitsRecord) -> AccountLimits:
    """Construct the existing public account-limit model from neutral facts."""
    return AccountLimits(
        notebook_limit=record.notebook_limit,
        source_limit=record.source_limit,
        raw_limits=record.raw_limits,
        tier=record.tier,
    )


def project_user_settings(record: UserSettingsRecord) -> UserSettings:
    """Construct the combined public settings model from one neutral row."""
    return UserSettings(
        limits=project_account_limits(record.limits),
        output_language=cast(str | None, record.output_language),
    )


def project_prompt_suggestions(
    records: tuple[PromptSuggestionRecord, ...],
) -> list[PromptSuggestion]:
    """Construct immutable public prompt suggestions in backend order."""
    return [PromptSuggestion(record.title, record.prompt) for record in records]


def project_report_suggestions(
    records: tuple[ReportSuggestionRecord, ...],
) -> list[ReportSuggestion]:
    """Construct legacy mutable report suggestions in backend order."""
    return [project_report_suggestion(record) for record in records]


def _source_type_code(record: SourceRecord) -> int | None:
    if not record.kind_present:
        return None
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


def project_notebook_description(record: NotebookDescriptionRecord) -> NotebookDescription:
    """Construct a public notebook guide from its neutral decode."""

    return NotebookDescription(
        summary=record.summary,
        suggested_topics=[
            SuggestedTopic(question=topic.question, prompt=topic.prompt)
            for topic in record.suggested_topics
        ],
    )


def project_note(record: NoteRecord) -> Note:
    """Construct one public :class:`Note` from a neutral record."""

    return Note(
        id=record.id,
        notebook_id=record.notebook_id,
        title=record.title,
        content=record.content,
        created_at=record.created_at,
    )


def project_generation_status(record: GenerationStatusRecord) -> GenerationStatus:
    """Construct one public generation kickoff state from a neutral record."""

    try:
        status = GenerationState(record.status)
    except ValueError:
        status = GenerationState.UNKNOWN
    return GenerationStatus(
        task_id=record.task_id,
        status=status,
        url=record.url,
        error=record.error,
        error_code=record.error_code,
    )


def project_mind_map(record: MindMapRecord) -> MindMap:
    """Construct a public mind-map value without leaking backend row shapes."""

    tree = None
    if record.tree_json:
        try:
            parsed = json.loads(record.tree_json)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            tree = parsed
    return MindMap(
        id=record.id,
        notebook_id=record.notebook_id,
        title=record.title,
        kind=(
            MindMapKind.INTERACTIVE
            if record.kind == MindMapKind.INTERACTIVE.value
            else MindMapKind.NOTE_BACKED
        ),
        created_at=record.created_at,
        tree=tree,
    )


def _project_artifact_user_state(
    record: ArtifactUserStateRecord | None,
) -> AudioArtifactUserState | FlashcardArtifactUserState | UnknownArtifactUserState | None:
    if record is None:
        return None
    if record.kind == "audio":
        return AudioArtifactUserState(record.playback_position_seconds or 0.0)
    if record.kind == "flashcards":
        return FlashcardArtifactUserState(
            card_acquisitions=dict(record.card_acquisitions),
            current_card_index=record.current_card_index,
            hidden_card_indices=record.hidden_card_indices,
            last_shown_order=record.last_shown_order,
            current_view=record.current_view,
        )
    return UnknownArtifactUserState(raw=record.raw)


def _artifact_type_code(record: ArtifactRecord) -> int:
    if record.unrecognized_family is not None:
        return cast(int, record.unrecognized_family)
    if record.family in {"quiz", "flashcards"} or record.variant == "interactive_mind_map":
        return 4
    return _ARTIFACT_FAMILY_CODES.get(record.family, 0)


def project_artifact(record: ArtifactRecord) -> Artifact:
    """Construct a public :class:`Artifact` without losing catalog fields."""

    variant = (
        cast(int | None, record.unrecognized_variant)
        if record.unrecognized_variant is not None
        else _ARTIFACT_VARIANT_CODES.get(record.variant or "")
    )
    status = (
        cast(int, record.unrecognized_status)
        if record.unrecognized_status is not None
        else _ARTIFACT_STATUS_CODES.get(record.status, 0)
    )
    return Artifact(
        id=record.id,
        title=record.title,
        _artifact_type=_artifact_type_code(record),
        status=status,
        created_at=record.created_at,
        url=record.url,
        _variant=variant,
        generation_prompt=record.generation_prompt,
        media_urls=tuple(
            ArtifactMedia(
                url=media.url,
                kind=_ARTIFACT_MEDIA_TYPES.get(media.kind, ArtifactMediaType.UNKNOWN),
                type_code=(
                    cast(int, media.unrecognized_kind)
                    if media.unrecognized_kind is not None
                    else {"progressive": 1, "hls": 2, "dash": 3, "download": 4}.get(media.kind)
                ),
                mime_type=media.mime_type,
            )
            for media in record.media_urls
        ),
        duration_seconds=record.duration_seconds,
        slides=tuple(
            ArtifactSlide(
                slide.image_url,
                slide.width,
                slide.height,
                slide.alt_text,
                slide.text,
            )
            for slide in record.slides
        ),
        infographics=tuple(
            ArtifactInfographic(
                infographic.title,
                infographic.image_url,
                infographic.width,
                infographic.height,
                infographic.alt_text,
                infographic.text,
            )
            for infographic in record.infographics
        ),
        report_kind=record.report_kind,
        source_ids=record.source_ids,
        last_modified_at=record.last_modified_at,
        etag=record.etag,
        user_state=_project_artifact_user_state(record.user_state),
    )


def project_report_suggestion(record: ReportSuggestionRecord) -> ReportSuggestion:
    """Construct one public suggested-report value."""

    return ReportSuggestion(
        title=record.title,
        description=record.description,
        prompt=record.prompt,
        audience_level=cast(int, record.audience_level),
    )


def project_label(record: LabelRecord) -> Label:
    """Construct one public source label."""

    return Label(
        id=record.id,
        name=record.name,
        notebook_id=record.notebook_id,
        emoji=record.emoji,
        source_ids=list(record.source_ids),
    )


def project_collection(record: CollectionRecord) -> Collection:
    """Construct one public notebook collection."""

    return Collection(
        id=record.id,
        name=record.name,
        emoji=record.emoji,
        notebook_ids=list(record.notebook_ids),
    )


_SHARE_PERMISSIONS = {
    "owner": SharePermission.OWNER,
    "editor": SharePermission.EDITOR,
    "viewer": SharePermission.VIEWER,
}


def project_share_status(record: ShareStatusRecord) -> ShareStatus:
    """Construct one public sharing status and its collaborator values."""

    return ShareStatus(
        notebook_id=record.notebook_id,
        is_public=record.is_public,
        access=(ShareAccess.ANYONE_WITH_LINK if record.is_public else ShareAccess.RESTRICTED),
        view_level=ShareViewLevel.FULL_NOTEBOOK,
        shared_users=[
            SharedUser(
                email=user.email,
                permission=_SHARE_PERMISSIONS.get(user.permission, SharePermission.VIEWER),
                display_name=user.display_name,
                avatar_url=user.avatar_url,
            )
            for user in record.shared_users
        ],
        share_url=(
            f"{get_base_url()}/notebook/{quote(record.notebook_id, safe='')}"
            if record.is_public
            else None
        ),
        max_individuals_share_limit=record.max_individuals_share_limit,
        is_public_sharing_allowed=record.is_public_sharing_allowed,
    )


__all__ = [
    "project_account_limits",
    "project_artifact",
    "project_collection",
    "project_generation_status",
    "project_label",
    "project_mind_map",
    "project_note",
    "project_notebook",
    "project_notebook_description",
    "project_prompt_suggestions",
    "project_report_suggestion",
    "project_report_suggestions",
    "project_share_status",
    "project_source",
    "project_user_settings",
]
