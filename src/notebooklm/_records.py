"""Private transport-neutral records for migrated semantic slices."""

from __future__ import annotations

from dataclasses import dataclass
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
    kind_present: bool = True


@dataclass(frozen=True, slots=True)
class ArtifactMediaRecord:
    """One neutral audio/video media location."""

    url: str
    kind: str
    type_code: int | None = None
    mime_type: str | None = None


@dataclass(frozen=True, slots=True)
class ArtifactSlideRecord:
    """One neutral rendered slide."""

    image_url: str | None
    width: int | None
    height: int | None
    alt_text: str | None
    text: str | None


@dataclass(frozen=True, slots=True)
class ArtifactInfographicRecord:
    """One neutral rendered infographic."""

    title: str | None
    image_url: str | None
    width: int | None
    height: int | None
    alt_text: str | None
    text: str | None


@dataclass(frozen=True, slots=True)
class AudioArtifactUserStateRecord:
    """Neutral audio playback state."""

    playback_position_seconds: float


@dataclass(frozen=True, slots=True)
class FlashcardArtifactUserStateRecord:
    """Neutral flashcard study state."""

    card_acquisitions: tuple[tuple[str, str], ...]
    current_card_index: int | None = None
    hidden_card_indices: tuple[int, ...] = ()
    last_shown_order: tuple[int, ...] = ()
    current_view: str | None = None


@dataclass(frozen=True, slots=True)
class UnknownArtifactUserStateRecord:
    """Lossless forward-compatible artifact state payload."""

    raw: object


ArtifactUserStateRecord = (
    AudioArtifactUserStateRecord | FlashcardArtifactUserStateRecord | UnknownArtifactUserStateRecord
)


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    """Neutral studio artifact decoded from a web listing row."""

    id: str
    title: str
    artifact_type: int
    status: int
    created_at: datetime | None = None
    url: str | None = None
    variant: int | None = None
    generation_prompt: str | None = None
    media_urls: tuple[ArtifactMediaRecord, ...] = ()
    duration_seconds: float | None = None
    slides: tuple[ArtifactSlideRecord, ...] = ()
    infographics: tuple[ArtifactInfographicRecord, ...] = ()
    report_kind: str | None = None
    source_ids: tuple[str, ...] = ()
    last_modified_at: datetime | None = None
    etag: str | None = None
    user_state: ArtifactUserStateRecord | None = None


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


NOTEBOOK_LIST_DEF: OperationDef[NotebookListInput, NotebookListResult] = OperationDef(
    Operation.NOTEBOOK_LIST,
    CallPolicy.READ,
    NotebookListInput,
    NotebookListResult,
)
NOTEBOOK_GET_DEF: OperationDef[NotebookGetInput, NotebookGetResult] = OperationDef(
    Operation.NOTEBOOK_GET,
    CallPolicy.READ,
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
    CallPolicy.READ,
    SourceListInput,
    SourceListResult,
)
SOURCE_GET_DEF: OperationDef[SourceGetInput, SourceGetResult] = OperationDef(
    Operation.SOURCE_GET,
    CallPolicy.READ,
    SourceGetInput,
    SourceGetResult,
)
SOURCE_ADD_URL_DEF: OperationDef[SourceAddUrlInput, SourceAddUrlResult] = OperationDef(
    Operation.SOURCE_ADD_URL,
    CallPolicy.MUTATION,
    SourceAddUrlInput,
    SourceAddUrlResult,
)


__all__ = [
    "ArtifactInfographicRecord",
    "ArtifactMediaRecord",
    "ArtifactRecord",
    "ArtifactSlideRecord",
    "ArtifactUserStateRecord",
    "AudioArtifactUserStateRecord",
    "CollectionRecord",
    "FlashcardArtifactUserStateRecord",
    "LabelRecord",
    "NOTEBOOK_GET_DEF",
    "NOTEBOOK_LIST_DEF",
    "NOTEBOOK_CREATE_DEF",
    "NOTEBOOK_DELETE_DEF",
    "NOTEBOOK_UPDATE_DEF",
    "SOURCE_GET_DEF",
    "SOURCE_LIST_DEF",
    "SOURCE_ADD_URL_DEF",
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
    "UnknownArtifactUserStateRecord",
]
