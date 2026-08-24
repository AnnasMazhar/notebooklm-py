"""Private transport-neutral records for migrated semantic slices."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, unique

from ._operations import CallPolicy, Operation, OperationDef
from ._sharing_records import (
    SHARING_GET_DEF,
    SHARING_SET_PUBLIC_DEF,
    SHARING_SET_VIEW_LEVEL_DEF,
    SHARING_UPDATE_USERS_DEF,
    ShareAccessLevel,
    SharedUserRecord,
    SharePermissionLevel,
    ShareStatusRecord,
    ShareViewScope,
    SharingGetInput,
    SharingGetResult,
    SharingSetPublicInput,
    SharingSetPublicResult,
    SharingSetViewLevelInput,
    SharingSetViewLevelResult,
    SharingUpdateUsersInput,
    SharingUpdateUsersResult,
    SharingUserGrant,
)


@dataclass(frozen=True, slots=True)
class NotebookPremiumFeaturesRecord:
    """Tier-dependent notebook feature verdicts, independent of public models."""

    can_edit_advanced_settings: bool | None = None
    can_edit_guidebook_config: bool | None = None
    can_view_analytics: bool | None = None


@dataclass(frozen=True, slots=True)
class NotebookChatSessionRecord:
    """One chat-session identity volunteered by a notebook read."""

    id: str


@dataclass(frozen=True, slots=True)
class NotebookChatSettingsRecord:
    """Semantic notebook chat configuration without RPC enum types."""

    goal: str
    response_length: str
    custom_prompt: str | None = None


@dataclass(frozen=True, slots=True)
class NotebookRecord:
    """Neutral notebook value returned by list/get backends."""

    id: str
    title: str
    created_at: datetime | None = None
    sources_count: int = 0
    is_owner: bool = True
    role: str | None = None
    last_viewed_at: datetime | None = None
    emoji: str | None = None
    premium_features: NotebookPremiumFeaturesRecord | None = None
    chat_sessions: tuple[NotebookChatSessionRecord, ...] = ()
    chat_settings: NotebookChatSettingsRecord | None = None


@dataclass(frozen=True, slots=True)
class SuggestedTopicRecord:
    """One transport-neutral notebook guide topic."""

    question: str
    prompt: str


@dataclass(frozen=True, slots=True)
class NotebookDescriptionRecord:
    """Decoded notebook guide without exported model dependencies."""

    summary: str
    suggested_topics: tuple[SuggestedTopicRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class NotebookListInput:
    """Input for the parameter-free notebook listing operation."""


@dataclass(frozen=True, slots=True)
class NotebookListResult:
    """Notebook listing in backend order."""

    notebooks: tuple[NotebookRecord, ...]


@dataclass(frozen=True, slots=True)
class NotebookGetInput:
    """Identity requested by the notebook get operation."""

    notebook_id: str


@dataclass(frozen=True, slots=True)
class NotebookGetResult:
    """Notebook get result; ``None`` is the semantic not-found state."""

    notebook: NotebookRecord | None


@dataclass(frozen=True, slots=True)
class NotebookCreateInput:
    """Requested notebook title."""

    title: str


@dataclass(frozen=True, slots=True)
class NotebookCreateResult:
    """Created or uniquely reconciled notebook."""

    notebook: NotebookRecord


@dataclass(frozen=True, slots=True)
class NotebookUpdateInput:
    """Notebook identity and optional title/emoji replacements."""

    notebook_id: str
    title: str | None = None
    emoji: str | None = None


@dataclass(frozen=True, slots=True)
class NotebookUpdateResult:
    """Notebook read back after its property mutation."""

    notebook: NotebookRecord


@dataclass(frozen=True, slots=True)
class NotebookDeleteInput:
    """Single notebook identity to delete idempotently."""

    notebook_id: str


@dataclass(frozen=True, slots=True)
class NotebookDeleteResult:
    """Successful idempotent notebook deletion."""


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """Neutral source value returned by list/get backends."""

    id: str
    title: str | None = None
    url: str | None = None
    kind: str = "unknown"
    unrecognized_kind: int | str | None = None
    kind_present: bool = True
    created_at: datetime | None = None
    status: str = "unknown"
    drive_document_id: str | None = None
    drive_status: str | None = None
    download_url: str | None = None
    viewer_url: str | None = None
    content_mime: str | None = None
    word_count: int | None = None
    revision_id: str | None = None
    revision_timestamp: datetime | None = None
    last_modified_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ReportSuggestionRecord:
    """Neutral suggested-report row."""

    title: str
    description: str
    prompt: str
    audience_level: int = 2


@dataclass(frozen=True, slots=True)
class CollectionRecord:
    """Neutral account-level notebook collection."""

    id: str
    name: str
    emoji: str | None = None
    notebook_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceListInput:
    """Validated semantic source-list request."""

    notebook_id: str
    strict: bool = False
    statuses: frozenset[str] | None = None
    kinds: frozenset[str] | None = None


@dataclass(frozen=True, slots=True)
class SourceListResult:
    """Source listing in backend order after semantic filtering."""

    sources: tuple[SourceRecord, ...]


@dataclass(frozen=True, slots=True)
class SourceGetInput:
    """Notebook and source identities requested by source get."""

    notebook_id: str
    source_id: str


@dataclass(frozen=True, slots=True)
class SourceGetResult:
    """Source get result; ``None`` is the semantic not-found state."""

    source: SourceRecord | None


@dataclass(frozen=True, slots=True)
class SourceAddUrlInput:
    """One URL-source request, including the hidden YouTube variant."""

    notebook_id: str
    url: str
    wait: bool = False
    wait_timeout: float = 120.0
    requested_title: str | None = None


@unique
class SourceAddCommitState(str, Enum):
    """How confidently a URL-source write is attributed to this call."""

    CREATED = "created"
    RECONCILED = "reconciled"
    FAILED = "failed"
    UNKNOWN = "unknown"


@unique
class SourceAddTitleState(str, Enum):
    """Best-effort requested-title outcome after URL registration."""

    NOT_REQUESTED = "not_requested"
    UNCHANGED = "unchanged"
    RENAMED = "renamed"
    RENAME_FAILED = "rename_failed"
    NOT_ATTEMPTED = "not_attempted"


@dataclass(frozen=True, slots=True)
class SourceAddUrlReceipt:
    """Safe internal evidence for commit and title uncertainty."""

    commit_state: SourceAddCommitState
    title_state: SourceAddTitleState
    outcome_unknown: bool = False


@dataclass(frozen=True, slots=True)
class SourceAddUrlResult:
    """Neutral URL-source result plus its reconciliation receipt."""

    source: SourceRecord
    receipt: SourceAddUrlReceipt


@unique
class SourceAddFailureKind(str, Enum):
    """Closed public failure vocabulary for URL-source compatibility replay."""

    SOURCE_ADD = "source_add"
    SOURCE_NOT_FOUND = "source_not_found"
    SOURCE_PROCESSING = "source_processing"
    SOURCE_TIMEOUT = "source_timeout"
    AUTH = "auth"
    CLIENT = "client"
    DECODING = "decoding"
    NETWORK = "network"
    RATE_LIMIT = "rate_limit"
    RESPONSE_TOO_LARGE = "response_too_large"
    RPC = "rpc"
    RPC_TIMEOUT = "rpc_timeout"
    SERVER = "server"
    UNKNOWN_RPC_METHOD = "unknown_rpc_method"
    BUILTIN_CONNECTION = "builtin_connection"
    BUILTIN_BROKEN_PIPE = "builtin_broken_pipe"
    BUILTIN_CONNECTION_ABORTED = "builtin_connection_aborted"
    BUILTIN_CONNECTION_REFUSED = "builtin_connection_refused"
    BUILTIN_CONNECTION_RESET = "builtin_connection_reset"
    BUILTIN_OS = "builtin_os"
    BUILTIN_INDEX = "builtin_index"
    BUILTIN_KEY = "builtin_key"
    BUILTIN_RUNTIME = "builtin_runtime"
    BUILTIN_TIMEOUT = "builtin_timeout"
    BUILTIN_TYPE = "builtin_type"
    BUILTIN_VALUE = "builtin_value"
    HTTPX_STATUS = "httpx_status"
    HTTPX_REQUEST = "httpx_request"
    HTTPX_TRANSPORT = "httpx_transport"
    HTTPX_TIMEOUT = "httpx_timeout"
    HTTPX_CONNECT_TIMEOUT = "httpx_connect_timeout"
    HTTPX_READ_TIMEOUT = "httpx_read_timeout"
    HTTPX_WRITE_TIMEOUT = "httpx_write_timeout"
    HTTPX_POOL_TIMEOUT = "httpx_pool_timeout"
    HTTPX_NETWORK = "httpx_network"
    HTTPX_CONNECT = "httpx_connect"
    HTTPX_READ = "httpx_read"
    HTTPX_WRITE = "httpx_write"
    HTTPX_CLOSE = "httpx_close"
    HTTPX_PROXY = "httpx_proxy"
    HTTPX_PROTOCOL = "httpx_protocol"
    HTTPX_LOCAL_PROTOCOL = "httpx_local_protocol"
    HTTPX_REMOTE_PROTOCOL = "httpx_remote_protocol"
    HTTPX_UNSUPPORTED_PROTOCOL = "httpx_unsupported_protocol"
    HTTPX_TOO_MANY_REDIRECTS = "httpx_too_many_redirects"
    HTTPX_DECODING = "httpx_decoding"


ScalarExceptionArg = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class SourceAddFailureRecord:
    """Serializable evidence needed to reconstruct one bounded public error graph."""

    kind: SourceAddFailureKind
    message: str
    args: tuple[ScalarExceptionArg, ...] = ()
    url: str | None = None
    unconfirmed: bool = False
    source_id: str | None = None
    stage: str | None = None
    method_id: str | int | None = None
    raw_response: str | None = None
    rpc_code: str | int | None = None
    found_ids: tuple[str | int, ...] = ()
    recoverable: bool | None = None
    retry_after: int | None = None
    status_code: int | None = None
    timeout_seconds: float | None = None
    limit_bytes: int | None = None
    bytes_read: int | None = None
    status: int | None = None
    timeout: float | None = None
    last_status: int | None = None
    path: tuple[int, ...] | None = None
    source: str | None = None
    data_at_failure: str | None = None
    request_method: str | None = None
    request_url: str | None = None
    original_error: SourceAddFailureRecord | None = None
    cause: SourceAddFailureRecord | None = None
    context: SourceAddFailureRecord | None = None
    cause_is_original: bool = False
    context_is_cause: bool = False
    context_is_original: bool = False
    explicit_cause: bool = False
    suppress_context: bool = False


@dataclass(frozen=True, slots=True)
class NoteRecord:
    """Neutral note value returned by note semantic operations."""

    id: str
    notebook_id: str
    title: str
    content: str
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class NoteListInput:
    """Notebook whose active plain notes should be listed."""

    notebook_id: str


@dataclass(frozen=True, slots=True)
class NoteListResult:
    """Active plain notes in backend order."""

    notes: tuple[NoteRecord, ...]


@dataclass(frozen=True, slots=True)
class NoteGetInput:
    """Notebook and exact note identity requested by note get."""

    notebook_id: str
    note_id: str


@dataclass(frozen=True, slots=True)
class NoteGetResult:
    """Exact note lookup result; ``None`` is a genuine miss."""

    note: NoteRecord | None


@dataclass(frozen=True, slots=True)
class NoteCreateInput:
    """Requested plain-note value."""

    notebook_id: str
    title: str
    content: str


@dataclass(frozen=True, slots=True)
class NoteCreateResult:
    """Created note identity and creation metadata before finalization."""

    note: NoteRecord


@dataclass(frozen=True, slots=True)
class NoteUpdateInput:
    """Exact note identity and replacement content/title."""

    notebook_id: str
    note_id: str
    content: str
    title: str


@dataclass(frozen=True, slots=True)
class NoteUpdateResult:
    """Successful in-place note update."""


@dataclass(frozen=True, slots=True)
class NoteDeleteInput:
    """Exact note identity to soft-delete idempotently."""

    notebook_id: str
    note_id: str


@dataclass(frozen=True, slots=True)
class NoteDeleteResult:
    """Successful idempotent note deletion."""


@dataclass(frozen=True, slots=True)
class MindMapRecord:
    """One backend-neutral mind map with its optional JSON tree payload."""

    id: str
    notebook_id: str
    title: str
    kind: str
    created_at: datetime | None = None
    tree_json: str | None = None


@dataclass(frozen=True, slots=True)
class MindMapListInput:
    """Notebook whose active note-backed mind maps are requested."""

    notebook_id: str


@dataclass(frozen=True, slots=True)
class MindMapListResult:
    """Active note-backed mind maps in backend order."""

    mind_maps: tuple[MindMapRecord, ...]


@dataclass(frozen=True, slots=True)
class MindMapGetInput:
    """Interactive mind-map identity whose tree is requested."""

    notebook_id: str
    mind_map_id: str


@dataclass(frozen=True, slots=True)
class MindMapGetResult:
    """Interactive tree JSON, or ``None`` while absent/not populated."""

    tree_json: str | None


@dataclass(frozen=True, slots=True)
class MindMapGenerateNoteInput:
    """Note-backed mind-map generation options."""

    notebook_id: str
    source_ids: tuple[str, ...] | None = None
    language: str | None = "en"
    instructions: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class MindMapGenerateNoteResult:
    """Generated note-backed tree before semantic note persistence."""

    tree_json: str | None


@dataclass(frozen=True, slots=True)
class MindMapGenerateInteractiveInput:
    """Interactive Studio mind-map generation options."""

    notebook_id: str
    source_ids: tuple[str, ...] | None = None
    instructions: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class MindMapGenerateInteractiveResult:
    """Allocated interactive Studio mind-map identity."""

    mind_map_id: str


@dataclass(frozen=True, slots=True)
class MindMapUpdateInput:
    """Interactive mind-map title replacement."""

    notebook_id: str
    mind_map_id: str
    title: str


@dataclass(frozen=True, slots=True)
class MindMapUpdateResult:
    """Successful interactive mind-map rename."""


@dataclass(frozen=True, slots=True)
class MindMapDeleteInput:
    """Interactive mind-map identity to delete idempotently."""

    notebook_id: str
    mind_map_id: str


@dataclass(frozen=True, slots=True)
class MindMapDeleteResult:
    """Successful idempotent interactive mind-map deletion."""


@dataclass(frozen=True, slots=True)
class ArtifactMediaRecord:
    """One transport-neutral artifact media rendition."""

    url: str = field(repr=False)
    kind: str = "unknown"
    unrecognized_kind: int | str | None = None
    mime_type: str | None = None


@dataclass(frozen=True, slots=True)
class ArtifactSlideRecord:
    """One rendered slide without exposing asset contents in representations."""

    image_url: str | None = field(default=None, repr=False)
    width: int | None = None
    height: int | None = None
    alt_text: str | None = field(default=None, repr=False)
    text: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class ArtifactInfographicRecord:
    """One rendered infographic."""

    title: str | None = None
    image_url: str | None = field(default=None, repr=False)
    width: int | None = None
    height: int | None = None
    alt_text: str | None = field(default=None, repr=False)
    text: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class ArtifactUserStateRecord:
    """Closed known user-state summary with opaque forward-compatible payload."""

    kind: str
    playback_position_seconds: float | None = None
    card_acquisitions: tuple[tuple[str, str], ...] = ()
    current_card_index: int | None = None
    hidden_card_indices: tuple[int, ...] = ()
    last_shown_order: tuple[int, ...] = ()
    current_view: str | None = None
    raw: object | None = field(default=None, repr=False, compare=True)


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    """Complete neutral Studio catalog entry from one catalog snapshot."""

    id: str
    title: str
    family: str
    status: str
    unrecognized_family: int | str | None = None
    variant: str | None = None
    interactive_variant_pending: bool = False
    unrecognized_variant: int | str | None = None
    unrecognized_status: int | str | None = None
    created_at: datetime | None = None
    url: str | None = field(default=None, repr=False)
    generation_prompt: str | None = field(default=None, repr=False)
    media_urls: tuple[ArtifactMediaRecord, ...] = field(default=(), repr=False)
    duration_seconds: float | None = None
    slides: tuple[ArtifactSlideRecord, ...] = field(default=(), repr=False)
    infographics: tuple[ArtifactInfographicRecord, ...] = field(default=(), repr=False)
    report_kind: str | None = None
    source_ids: tuple[str, ...] = ()
    last_modified_at: datetime | None = None
    etag: str | None = field(default=None, repr=False)
    user_state: ArtifactUserStateRecord | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class ArtifactListInput:
    """Notebook whose complete Studio catalog is requested."""

    notebook_id: str
    family: str | None = None


@dataclass(frozen=True, slots=True)
class ArtifactListResult:
    """Complete heterogeneous Studio catalog in backend order."""

    artifacts: tuple[ArtifactRecord, ...]


@dataclass(frozen=True, slots=True)
class ArtifactGetInput:
    """Notebook and artifact identities requested from one catalog snapshot."""

    notebook_id: str
    artifact_id: str


@dataclass(frozen=True, slots=True)
class ArtifactGetResult:
    """Artifact get result; ``None`` is the semantic not-found state."""

    artifact: ArtifactRecord | None


@dataclass(frozen=True, slots=True)
class GenerationStatusRecord:
    """Transport-neutral artifact generation task state."""

    task_id: str
    status: str
    url: str | None = field(default=None, repr=False)
    error: str | None = field(default=None, repr=False)
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class AudioGenerateInput:
    """Audio generation options without web enum or payload vocabulary."""

    notebook_id: str
    source_ids: tuple[str, ...] | None = None
    language: str | None = "en"
    instructions: str | None = field(default=None, repr=False)
    audio_format: str | None = None
    audio_length: str | None = None


@dataclass(frozen=True, slots=True)
class AudioGenerateResult:
    """Audio generation kickoff result."""

    status: GenerationStatusRecord


@dataclass(frozen=True, slots=True)
class VideoGenerateInput:
    """Video generation options without web enum or payload vocabulary."""

    notebook_id: str
    source_ids: tuple[str, ...] | None = None
    language: str | None = "en"
    instructions: str | None = field(default=None, repr=False)
    video_format: str | None = None
    video_style: str | None = None
    style_prompt: str | None = field(default=None, repr=False)
    cinematic_route: bool = False


@dataclass(frozen=True, slots=True)
class VideoGenerateResult:
    """Video generation kickoff result."""

    status: GenerationStatusRecord


@dataclass(frozen=True, slots=True)
class InteractiveGenerateInput:
    """Quiz or flashcard generation options without web enum vocabulary."""

    notebook_id: str
    source_ids: tuple[str, ...] | None = None
    instructions: str | None = field(default=None, repr=False)
    quantity: str | None = None
    difficulty: str | None = None


@dataclass(frozen=True, slots=True)
class InteractiveGenerateResult:
    """Quiz or flashcard generation kickoff result."""

    status: GenerationStatusRecord


@dataclass(frozen=True, slots=True)
class ReportGenerateInput:
    """Report generation options without web enum or payload vocabulary."""

    notebook_id: str
    report_format: str = "briefing_doc"
    source_ids: tuple[str, ...] | None = None
    language: str | None = "en"
    custom_prompt: str | None = field(default=None, repr=False)
    extra_instructions: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class ReportGenerateResult:
    """Report generation kickoff result."""

    status: GenerationStatusRecord


@dataclass(frozen=True, slots=True)
class AudioMetadataRecord:
    """Audio readiness and representation metadata derived from one catalog row."""

    artifact_id: str
    lifecycle_status: str
    usable: bool
    preferred_url: str | None = field(default=None, repr=False)
    media_urls: tuple[ArtifactMediaRecord, ...] = field(default=(), repr=False)
    duration_seconds: float | None = None
    generation_prompt: str | None = field(default=None, repr=False)
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class VideoMetadataRecord:
    """Video readiness and representation metadata derived from one catalog row."""

    artifact_id: str
    lifecycle_status: str
    usable: bool
    preferred_url: str | None = field(default=None, repr=False)
    media_urls: tuple[ArtifactMediaRecord, ...] = field(default=(), repr=False)
    duration_seconds: float | None = None
    generation_prompt: str | None = field(default=None, repr=False)
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class InteractiveMetadataRecord:
    """Interactive-family readiness and per-user study metadata."""

    artifact_id: str
    family: str
    lifecycle_status: str
    usable: bool
    generation_prompt: str | None = field(default=None, repr=False)
    user_state: ArtifactUserStateRecord | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class ReportMetadataRecord:
    """Report readiness and format metadata derived from one catalog row."""

    artifact_id: str
    lifecycle_status: str
    usable: bool
    report_kind: str | None = None
    report_format: str | None = None
    generation_prompt: str | None = field(default=None, repr=False)
    source_ids: tuple[str, ...] = ()
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class DataTableGenerateInput:
    """Data-table generation options without web payload vocabulary."""

    notebook_id: str
    source_ids: tuple[str, ...] | None = None
    language: str | None = "en"
    instructions: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class DataTableGenerateResult:
    """Data-table generation kickoff result."""

    status: GenerationStatusRecord


@dataclass(frozen=True, slots=True)
class MindMapGenerateInput:
    """Note-backed mind-map generation options."""

    notebook_id: str
    source_ids: tuple[str, ...] | None = None
    language: str | None = "en"
    instructions: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class MindMapGenerateResult:
    """Generated mind-map tree plus its persisted note identity."""

    mind_map: object | None = field(default=None, repr=False)
    note_id: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class DriveExportInput:
    """One explicit web companion export to Google Drive."""

    notebook_id: str
    artifact_id: str | None = None
    content: str | None = field(default=None, repr=False)
    title: str = "Export"
    destination: str = "docs"


@dataclass(frozen=True, slots=True)
class DriveExportResult:
    """Opaque decoded export response preserved for facade compatibility."""

    value: object = field(repr=False)


@dataclass(frozen=True, slots=True)
class InfographicGenerateInput:
    """Infographic options without web enum or payload vocabulary."""

    notebook_id: str
    source_ids: tuple[str, ...] | None = None
    language: str | None = "en"
    instructions: str | None = field(default=None, repr=False)
    orientation: str | None = None
    detail_level: str | None = None
    style: str | None = None


@dataclass(frozen=True, slots=True)
class SlideDeckGenerateInput:
    """Slide-deck options without web enum or payload vocabulary."""

    notebook_id: str
    source_ids: tuple[str, ...] | None = None
    language: str | None = "en"
    instructions: str | None = field(default=None, repr=False)
    slide_format: str | None = None
    slide_length: str | None = None


@dataclass(frozen=True, slots=True)
class VisualGenerateResult:
    """Visual generation kickoff result."""

    status: GenerationStatusRecord


@dataclass(frozen=True, slots=True)
class VisualMetadataRecord:
    """Visual readiness and accessibility metadata from one catalog row."""

    artifact_id: str
    family: str
    lifecycle_status: str
    usable: bool
    slides: tuple[ArtifactSlideRecord, ...] = field(default=(), repr=False)
    infographics: tuple[ArtifactInfographicRecord, ...] = field(default=(), repr=False)
    preferred_url: str | None = field(default=None, repr=False)
    generation_prompt: str | None = field(default=None, repr=False)
    created_at: datetime | None = None


@unique
class LabelKind(str, Enum):
    """Closed discriminator over the one shared label/collection wire surface.

    A collection is a label with a distinct type discriminator and no notebook
    parent, so both facades share four RPC ids verbatim.  Every neutral label
    value carries this discriminator explicitly instead of relying on a null
    ``notebook_id`` to imply it.
    """

    SOURCE_LABEL = "source_label"
    COLLECTION = "collection"


@dataclass(frozen=True, slots=True)
class LabelRecord:
    """Neutral member-grouping value shared by source labels and collections.

    ``member_ids`` is the group's membership in backend order: source ids for a
    :attr:`LabelKind.SOURCE_LABEL`, notebook ids for a
    :attr:`LabelKind.COLLECTION`.  ``notebook_id`` is the notebook scope and is
    ``None`` for account-level collections.
    """

    id: str
    name: str
    kind: LabelKind
    notebook_id: str | None = None
    emoji: str | None = None
    member_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LabelListInput:
    """Kind-discriminated request for one label set."""

    kind: LabelKind
    notebook_id: str | None = None


@dataclass(frozen=True, slots=True)
class LabelListResult:
    """Label set in backend order."""

    labels: tuple[LabelRecord, ...]


@dataclass(frozen=True, slots=True)
class LabelGetInput:
    """Exact-id selection request within one label set."""

    kind: LabelKind
    label_id: str
    notebook_id: str | None = None


@dataclass(frozen=True, slots=True)
class LabelGetResult:
    """Label get result; ``None`` is the semantic not-found state."""

    label: LabelRecord | None


@dataclass(frozen=True, slots=True)
class LabelGenerateInput:
    """Auto-grouping request; only source labels have a generation mode.

    ``replace_existing`` is the destructive mode: every existing label is wiped
    and regenerated with new ids.  The safe default groups only the sources that
    are not labelled yet.
    """

    notebook_id: str
    replace_existing: bool = False


@dataclass(frozen=True, slots=True)
class LabelGenerateResult:
    """Full post-generation label set echoed by the backend."""

    labels: tuple[LabelRecord, ...]


@dataclass(frozen=True, slots=True)
class LabelCreateInput:
    """Manual creation request for one empty, named group."""

    kind: LabelKind
    name: str
    notebook_id: str | None = None
    emoji: str = ""


@dataclass(frozen=True, slots=True)
class LabelCreateResult:
    """The single group this call is proven to have created."""

    label: LabelRecord


@dataclass(frozen=True, slots=True)
class LabelUpdateInput:
    """Field and/or membership mutation for one group.

    A field mutation (``name`` and/or ``emoji``) and a membership mutation
    (``add_member_ids`` / ``remove_member_ids``) are separate wire field masks
    with different reconciliation duties, so exactly one form is requested per
    call.  ``return_object`` selects whether the caller wants the group read
    back; the not-found contract holds in both modes.
    """

    kind: LabelKind
    label_id: str
    notebook_id: str | None = None
    name: str | None = None
    emoji: str | None = None
    add_member_ids: tuple[str, ...] = ()
    remove_member_ids: tuple[str, ...] = ()
    return_object: bool = True


@dataclass(frozen=True, slots=True)
class LabelUpdateResult:
    """Group read back after its mutation, or ``None`` when not requested."""

    label: LabelRecord | None


@dataclass(frozen=True, slots=True)
class LabelDeleteInput:
    """Batch deletion request; an absent id is an idempotent no-op."""

    kind: LabelKind
    label_ids: tuple[str, ...]
    notebook_id: str | None = None


@dataclass(frozen=True, slots=True)
class LabelDeleteResult:
    """Successful idempotent group deletion."""


NOTEBOOK_LIST_DEF: OperationDef[NotebookListInput, NotebookListResult] = OperationDef(
    Operation.NOTEBOOK_LIST,
    CallPolicy.READ,
    NotebookListInput,
    NotebookListResult,
)
NOTEBOOK_GET_DEF: OperationDef[NotebookGetInput, NotebookGetResult] = OperationDef(
    Operation.NOTEBOOK_GET,
    # GET_NOTEBOOK updates lastViewedTime even though its result is read-shaped.
    CallPolicy.MUTATION,
    NotebookGetInput,
    NotebookGetResult,
)
NOTEBOOK_CREATE_DEF: OperationDef[NotebookCreateInput, NotebookCreateResult] = OperationDef(
    Operation.NOTEBOOK_CREATE,
    CallPolicy.MUTATION,
    NotebookCreateInput,
    NotebookCreateResult,
)
NOTEBOOK_UPDATE_DEF: OperationDef[NotebookUpdateInput, NotebookUpdateResult] = OperationDef(
    Operation.NOTEBOOK_UPDATE,
    CallPolicy.MUTATION,
    NotebookUpdateInput,
    NotebookUpdateResult,
)
NOTEBOOK_DELETE_DEF: OperationDef[NotebookDeleteInput, NotebookDeleteResult] = OperationDef(
    Operation.NOTEBOOK_DELETE,
    CallPolicy.MUTATION,
    NotebookDeleteInput,
    NotebookDeleteResult,
)
SOURCE_LIST_DEF: OperationDef[SourceListInput, SourceListResult] = OperationDef(
    Operation.SOURCE_LIST,
    # Both source reads use GET_NOTEBOOK and therefore update notebook recency.
    CallPolicy.MUTATION,
    SourceListInput,
    SourceListResult,
)
ARTIFACT_LIST_DEF: OperationDef[ArtifactListInput, ArtifactListResult] = OperationDef(
    Operation.ARTIFACT_LIST,
    CallPolicy.READ,
    ArtifactListInput,
    ArtifactListResult,
)
ARTIFACT_GET_DEF: OperationDef[ArtifactGetInput, ArtifactGetResult] = OperationDef(
    Operation.ARTIFACT_GET,
    CallPolicy.READ,
    ArtifactGetInput,
    ArtifactGetResult,
)
ARTIFACT_GENERATE_DATA_TABLE_DEF: OperationDef[DataTableGenerateInput, DataTableGenerateResult] = (
    OperationDef(
        Operation.ARTIFACT_GENERATE_DATA_TABLE,
        CallPolicy.STATEFUL_START,
        DataTableGenerateInput,
        DataTableGenerateResult,
    )
)
ARTIFACT_GENERATE_MIND_MAP_DEF: OperationDef[MindMapGenerateInput, MindMapGenerateResult] = (
    OperationDef(
        Operation.ARTIFACT_GENERATE_MIND_MAP,
        CallPolicy.STATEFUL_START,
        MindMapGenerateInput,
        MindMapGenerateResult,
    )
)
ARTIFACT_EXPORT_DEF: OperationDef[DriveExportInput, DriveExportResult] = OperationDef(
    Operation.ARTIFACT_EXPORT,
    CallPolicy.MUTATION,
    DriveExportInput,
    DriveExportResult,
)
ARTIFACT_GENERATE_AUDIO_DEF: OperationDef[AudioGenerateInput, AudioGenerateResult] = OperationDef(
    Operation.ARTIFACT_GENERATE_AUDIO,
    CallPolicy.STATEFUL_START,
    AudioGenerateInput,
    AudioGenerateResult,
)
ARTIFACT_GENERATE_QUIZ_DEF: OperationDef[InteractiveGenerateInput, InteractiveGenerateResult] = (
    OperationDef(
        Operation.ARTIFACT_GENERATE_QUIZ,
        CallPolicy.STATEFUL_START,
        InteractiveGenerateInput,
        InteractiveGenerateResult,
    )
)
ARTIFACT_GENERATE_FLASHCARDS_DEF: OperationDef[
    InteractiveGenerateInput, InteractiveGenerateResult
] = OperationDef(
    Operation.ARTIFACT_GENERATE_FLASHCARDS,
    CallPolicy.STATEFUL_START,
    InteractiveGenerateInput,
    InteractiveGenerateResult,
)
ARTIFACT_GENERATE_VIDEO_DEF: OperationDef[VideoGenerateInput, VideoGenerateResult] = OperationDef(
    Operation.ARTIFACT_GENERATE_VIDEO,
    CallPolicy.STATEFUL_START,
    VideoGenerateInput,
    VideoGenerateResult,
)
ARTIFACT_GENERATE_REPORT_DEF: OperationDef[ReportGenerateInput, ReportGenerateResult] = (
    OperationDef(
        Operation.ARTIFACT_GENERATE_REPORT,
        CallPolicy.STATEFUL_START,
        ReportGenerateInput,
        ReportGenerateResult,
    )
)
ARTIFACT_GENERATE_INFOGRAPHIC_DEF: OperationDef[InfographicGenerateInput, VisualGenerateResult] = (
    OperationDef(
        Operation.ARTIFACT_GENERATE_INFOGRAPHIC,
        CallPolicy.STATEFUL_START,
        InfographicGenerateInput,
        VisualGenerateResult,
    )
)
ARTIFACT_GENERATE_SLIDE_DECK_DEF: OperationDef[SlideDeckGenerateInput, VisualGenerateResult] = (
    OperationDef(
        Operation.ARTIFACT_GENERATE_SLIDE_DECK,
        CallPolicy.STATEFUL_START,
        SlideDeckGenerateInput,
        VisualGenerateResult,
    )
)
SOURCE_GET_DEF: OperationDef[SourceGetInput, SourceGetResult] = OperationDef(
    Operation.SOURCE_GET,
    CallPolicy.MUTATION,
    SourceGetInput,
    SourceGetResult,
)
SOURCE_ADD_URL_DEF: OperationDef[SourceAddUrlInput, SourceAddUrlResult] = OperationDef(
    Operation.SOURCE_ADD_URL,
    CallPolicy.MUTATION,
    SourceAddUrlInput,
    SourceAddUrlResult,
)
NOTE_LIST_DEF: OperationDef[NoteListInput, NoteListResult] = OperationDef(
    Operation.NOTE_LIST,
    CallPolicy.READ,
    NoteListInput,
    NoteListResult,
)
NOTE_GET_DEF: OperationDef[NoteGetInput, NoteGetResult] = OperationDef(
    Operation.NOTE_GET,
    CallPolicy.READ,
    NoteGetInput,
    NoteGetResult,
)
NOTE_CREATE_DEF: OperationDef[NoteCreateInput, NoteCreateResult] = OperationDef(
    Operation.NOTE_CREATE,
    CallPolicy.MUTATION,
    NoteCreateInput,
    NoteCreateResult,
)
NOTE_UPDATE_DEF: OperationDef[NoteUpdateInput, NoteUpdateResult] = OperationDef(
    Operation.NOTE_UPDATE,
    CallPolicy.MUTATION,
    NoteUpdateInput,
    NoteUpdateResult,
)
NOTE_DELETE_DEF: OperationDef[NoteDeleteInput, NoteDeleteResult] = OperationDef(
    Operation.NOTE_DELETE,
    CallPolicy.MUTATION,
    NoteDeleteInput,
    NoteDeleteResult,
)
MIND_MAP_LIST_DEF: OperationDef[MindMapListInput, MindMapListResult] = OperationDef(
    Operation.MIND_MAP_LIST,
    CallPolicy.READ,
    MindMapListInput,
    MindMapListResult,
)
MIND_MAP_GET_DEF: OperationDef[MindMapGetInput, MindMapGetResult] = OperationDef(
    Operation.MIND_MAP_GET,
    CallPolicy.READ,
    MindMapGetInput,
    MindMapGetResult,
)
MIND_MAP_GENERATE_NOTE_DEF: OperationDef[MindMapGenerateNoteInput, MindMapGenerateNoteResult] = (
    OperationDef(
        Operation.MIND_MAP_GENERATE_NOTE,
        CallPolicy.STATEFUL_START,
        MindMapGenerateNoteInput,
        MindMapGenerateNoteResult,
    )
)
MIND_MAP_GENERATE_INTERACTIVE_DEF: OperationDef[
    MindMapGenerateInteractiveInput, MindMapGenerateInteractiveResult
] = OperationDef(
    Operation.MIND_MAP_GENERATE_INTERACTIVE,
    CallPolicy.STATEFUL_START,
    MindMapGenerateInteractiveInput,
    MindMapGenerateInteractiveResult,
)
MIND_MAP_UPDATE_DEF: OperationDef[MindMapUpdateInput, MindMapUpdateResult] = OperationDef(
    Operation.MIND_MAP_UPDATE,
    CallPolicy.MUTATION,
    MindMapUpdateInput,
    MindMapUpdateResult,
)
MIND_MAP_DELETE_DEF: OperationDef[MindMapDeleteInput, MindMapDeleteResult] = OperationDef(
    Operation.MIND_MAP_DELETE,
    CallPolicy.MUTATION,
    MindMapDeleteInput,
    MindMapDeleteResult,
)


# One neutral operation family, two discriminated public facades.  Both keys of
# each pair share the input/output record types and the web codec beneath them;
# only ``LabelKind`` selects the wire dialect.
LABEL_LIST_DEF: OperationDef[LabelListInput, LabelListResult] = OperationDef(
    Operation.LABEL_LIST,
    CallPolicy.READ,
    LabelListInput,
    LabelListResult,
)
LABEL_GET_DEF: OperationDef[LabelGetInput, LabelGetResult] = OperationDef(
    Operation.LABEL_GET,
    CallPolicy.READ,
    LabelGetInput,
    LabelGetResult,
)
LABEL_GENERATE_DEF: OperationDef[LabelGenerateInput, LabelGenerateResult] = OperationDef(
    Operation.LABEL_GENERATE,
    CallPolicy.STATEFUL_START,
    LabelGenerateInput,
    LabelGenerateResult,
)
LABEL_CREATE_DEF: OperationDef[LabelCreateInput, LabelCreateResult] = OperationDef(
    Operation.LABEL_CREATE,
    CallPolicy.MUTATION,
    LabelCreateInput,
    LabelCreateResult,
)
LABEL_UPDATE_DEF: OperationDef[LabelUpdateInput, LabelUpdateResult] = OperationDef(
    Operation.LABEL_UPDATE,
    CallPolicy.MUTATION,
    LabelUpdateInput,
    LabelUpdateResult,
)
LABEL_DELETE_DEF: OperationDef[LabelDeleteInput, LabelDeleteResult] = OperationDef(
    Operation.LABEL_DELETE,
    CallPolicy.MUTATION,
    LabelDeleteInput,
    LabelDeleteResult,
)
COLLECTION_LIST_DEF: OperationDef[LabelListInput, LabelListResult] = OperationDef(
    Operation.COLLECTION_LIST,
    CallPolicy.READ,
    LabelListInput,
    LabelListResult,
)
COLLECTION_GET_DEF: OperationDef[LabelGetInput, LabelGetResult] = OperationDef(
    Operation.COLLECTION_GET,
    CallPolicy.READ,
    LabelGetInput,
    LabelGetResult,
)
COLLECTION_CREATE_DEF: OperationDef[LabelCreateInput, LabelCreateResult] = OperationDef(
    Operation.COLLECTION_CREATE,
    CallPolicy.MUTATION,
    LabelCreateInput,
    LabelCreateResult,
)
COLLECTION_UPDATE_DEF: OperationDef[LabelUpdateInput, LabelUpdateResult] = OperationDef(
    Operation.COLLECTION_UPDATE,
    CallPolicy.MUTATION,
    LabelUpdateInput,
    LabelUpdateResult,
)
COLLECTION_DELETE_DEF: OperationDef[LabelDeleteInput, LabelDeleteResult] = OperationDef(
    Operation.COLLECTION_DELETE,
    CallPolicy.MUTATION,
    LabelDeleteInput,
    LabelDeleteResult,
)


__all__ = [
    "ARTIFACT_EXPORT_DEF",
    "ARTIFACT_GET_DEF",
    "ARTIFACT_GENERATE_DATA_TABLE_DEF",
    "ARTIFACT_GENERATE_AUDIO_DEF",
    "ARTIFACT_GENERATE_FLASHCARDS_DEF",
    "ARTIFACT_GENERATE_INFOGRAPHIC_DEF",
    "ARTIFACT_GENERATE_MIND_MAP_DEF",
    "ARTIFACT_GENERATE_QUIZ_DEF",
    "ARTIFACT_GENERATE_REPORT_DEF",
    "ARTIFACT_GENERATE_SLIDE_DECK_DEF",
    "ARTIFACT_GENERATE_VIDEO_DEF",
    "ARTIFACT_LIST_DEF",
    "COLLECTION_CREATE_DEF",
    "COLLECTION_DELETE_DEF",
    "COLLECTION_GET_DEF",
    "COLLECTION_LIST_DEF",
    "COLLECTION_UPDATE_DEF",
    "LABEL_CREATE_DEF",
    "LABEL_DELETE_DEF",
    "LABEL_GENERATE_DEF",
    "LABEL_GET_DEF",
    "LABEL_LIST_DEF",
    "LABEL_UPDATE_DEF",
    "NOTEBOOK_GET_DEF",
    "NOTEBOOK_LIST_DEF",
    "NOTEBOOK_CREATE_DEF",
    "NOTEBOOK_DELETE_DEF",
    "NOTEBOOK_UPDATE_DEF",
    "SOURCE_GET_DEF",
    "SOURCE_LIST_DEF",
    "SOURCE_ADD_URL_DEF",
    "SHARING_GET_DEF",
    "SHARING_SET_PUBLIC_DEF",
    "SHARING_SET_VIEW_LEVEL_DEF",
    "SHARING_UPDATE_USERS_DEF",
    "NOTE_CREATE_DEF",
    "NOTE_DELETE_DEF",
    "NOTE_GET_DEF",
    "NOTE_LIST_DEF",
    "NOTE_UPDATE_DEF",
    "MIND_MAP_DELETE_DEF",
    "MIND_MAP_GENERATE_INTERACTIVE_DEF",
    "MIND_MAP_GENERATE_NOTE_DEF",
    "MIND_MAP_GET_DEF",
    "MIND_MAP_LIST_DEF",
    "MIND_MAP_UPDATE_DEF",
    "ArtifactGetInput",
    "ArtifactGetResult",
    "ArtifactInfographicRecord",
    "ArtifactListInput",
    "ArtifactListResult",
    "ArtifactMediaRecord",
    "ArtifactRecord",
    "ArtifactSlideRecord",
    "ArtifactUserStateRecord",
    "DataTableGenerateInput",
    "DataTableGenerateResult",
    "DriveExportInput",
    "DriveExportResult",
    "AudioGenerateInput",
    "AudioGenerateResult",
    "AudioMetadataRecord",
    "CollectionRecord",
    "GenerationStatusRecord",
    "InteractiveGenerateInput",
    "InteractiveGenerateResult",
    "InteractiveMetadataRecord",
    "InfographicGenerateInput",
    "LabelCreateInput",
    "LabelCreateResult",
    "LabelDeleteInput",
    "LabelDeleteResult",
    "LabelGenerateInput",
    "LabelGenerateResult",
    "LabelGetInput",
    "LabelGetResult",
    "LabelKind",
    "LabelListInput",
    "LabelListResult",
    "LabelRecord",
    "LabelUpdateInput",
    "LabelUpdateResult",
    "NotebookChatSessionRecord",
    "NotebookChatSettingsRecord",
    "NotebookCreateInput",
    "NotebookCreateResult",
    "NotebookDeleteInput",
    "NotebookDeleteResult",
    "NotebookGetInput",
    "NotebookGetResult",
    "NotebookListInput",
    "NotebookListResult",
    "NotebookPremiumFeaturesRecord",
    "NotebookRecord",
    "NotebookDescriptionRecord",
    "NotebookUpdateInput",
    "NotebookUpdateResult",
    "MindMapGenerateInput",
    "MindMapGenerateResult",
    "MindMapDeleteInput",
    "MindMapDeleteResult",
    "MindMapGenerateInteractiveInput",
    "MindMapGenerateInteractiveResult",
    "MindMapGenerateNoteInput",
    "MindMapGenerateNoteResult",
    "MindMapGetInput",
    "MindMapGetResult",
    "MindMapListInput",
    "MindMapListResult",
    "MindMapRecord",
    "MindMapUpdateInput",
    "MindMapUpdateResult",
    "NoteCreateInput",
    "NoteCreateResult",
    "NoteDeleteInput",
    "NoteDeleteResult",
    "NoteGetInput",
    "NoteGetResult",
    "NoteListInput",
    "NoteListResult",
    "NoteRecord",
    "NoteUpdateInput",
    "NoteUpdateResult",
    "ReportGenerateInput",
    "ReportGenerateResult",
    "ReportMetadataRecord",
    "SourceGetInput",
    "SourceGetResult",
    "SourceAddCommitState",
    "SourceAddFailureKind",
    "SourceAddFailureRecord",
    "SourceAddTitleState",
    "SourceAddUrlInput",
    "SourceAddUrlReceipt",
    "SourceAddUrlResult",
    "SourceListInput",
    "SourceListResult",
    "SourceRecord",
    "SlideDeckGenerateInput",
    "ReportSuggestionRecord",
    "ShareAccessLevel",
    "SharePermissionLevel",
    "ShareStatusRecord",
    "ShareViewScope",
    "SharedUserRecord",
    "SharingGetInput",
    "SharingGetResult",
    "SharingSetPublicInput",
    "SharingSetPublicResult",
    "SharingSetViewLevelInput",
    "SharingSetViewLevelResult",
    "SharingUpdateUsersInput",
    "SharingUpdateUsersResult",
    "SharingUserGrant",
    "SuggestedTopicRecord",
    "VideoGenerateInput",
    "VideoGenerateResult",
    "VideoMetadataRecord",
    "VisualGenerateResult",
    "VisualMetadataRecord",
]
