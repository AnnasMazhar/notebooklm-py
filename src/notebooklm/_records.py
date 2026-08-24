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
class NotebookTitleUpdateInput:
    """Notebook identity and replacement title."""

    notebook_id: str
    title: str


@dataclass(frozen=True, slots=True)
class NotebookTitleUpdateResult:
    """Notebook read back after its title mutation."""

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
NOTEBOOK_TITLE_UPDATE_DEF: OperationDef[NotebookTitleUpdateInput, NotebookTitleUpdateResult] = (
    OperationDef(
        Operation.NOTEBOOK_UPDATE,
        CallPolicy.MUTATION,
        NotebookTitleUpdateInput,
        NotebookTitleUpdateResult,
    )
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
    "NOTEBOOK_GET_DEF",
    "NOTEBOOK_LIST_DEF",
    "NOTEBOOK_CREATE_DEF",
    "NOTEBOOK_DELETE_DEF",
    "NOTEBOOK_TITLE_UPDATE_DEF",
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
    "NotebookTitleUpdateInput",
    "NotebookTitleUpdateResult",
    "SourceGetInput",
    "SourceGetResult",
    "SourceAddCommitState",
    "SourceAddTitleState",
    "SourceAddUrlInput",
    "SourceAddUrlReceipt",
    "SourceAddUrlResult",
    "SourceListInput",
    "SourceListResult",
    "SourceRecord",
]
