"""Private transport-neutral records for migrated semantic slices."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, unique

from ._operations import CallPolicy, Operation, OperationDef


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
class LabelRecord:
    """Neutral notebook-scoped source label."""

    id: str
    name: str
    notebook_id: str | None = None
    emoji: str | None = None
    source_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CollectionRecord:
    """Neutral account-level notebook collection."""

    id: str
    name: str
    emoji: str | None = None
    notebook_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SharedUserRecord:
    """Neutral collaborator row."""

    email: str
    permission: str
    display_name: str | None = None
    avatar_url: str | None = None


@dataclass(frozen=True, slots=True)
class ShareStatusRecord:
    """Neutral decoded sharing configuration."""

    notebook_id: str
    is_public: bool
    shared_users: tuple[SharedUserRecord, ...] = ()
    max_individuals_share_limit: int | None = None
    is_public_sharing_allowed: bool | None = None


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
    BUILTIN_RUNTIME = "builtin_runtime"
    BUILTIN_TIMEOUT = "builtin_timeout"
    BUILTIN_VALUE = "builtin_value"
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
ARTIFACT_GENERATE_AUDIO_DEF: OperationDef[AudioGenerateInput, AudioGenerateResult] = OperationDef(
    Operation.ARTIFACT_GENERATE_AUDIO,
    CallPolicy.STATEFUL_START,
    AudioGenerateInput,
    AudioGenerateResult,
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


__all__ = [
    "ARTIFACT_GET_DEF",
    "ARTIFACT_GENERATE_AUDIO_DEF",
    "ARTIFACT_LIST_DEF",
    "NOTEBOOK_GET_DEF",
    "NOTEBOOK_LIST_DEF",
    "NOTEBOOK_CREATE_DEF",
    "NOTEBOOK_DELETE_DEF",
    "NOTEBOOK_UPDATE_DEF",
    "SOURCE_GET_DEF",
    "SOURCE_LIST_DEF",
    "SOURCE_ADD_URL_DEF",
    "NOTE_CREATE_DEF",
    "NOTE_DELETE_DEF",
    "NOTE_GET_DEF",
    "NOTE_LIST_DEF",
    "NOTE_UPDATE_DEF",
    "ArtifactGetInput",
    "ArtifactGetResult",
    "ArtifactInfographicRecord",
    "ArtifactListInput",
    "ArtifactListResult",
    "ArtifactMediaRecord",
    "ArtifactRecord",
    "ArtifactSlideRecord",
    "ArtifactUserStateRecord",
    "AudioGenerateInput",
    "AudioGenerateResult",
    "AudioMetadataRecord",
    "CollectionRecord",
    "GenerationStatusRecord",
    "LabelRecord",
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
    "ReportSuggestionRecord",
    "ShareStatusRecord",
    "SharedUserRecord",
    "SuggestedTopicRecord",
]
