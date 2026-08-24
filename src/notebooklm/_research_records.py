"""Transport-neutral records and operation definitions for Research."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum, unique

from ._operations import CallPolicy, Operation, OperationDef


@dataclass(frozen=True, slots=True)
class ResearchSourceRecord:
    """One neutral research result row (web hit, drive file, or report entry)."""

    url: str
    title: str
    result_type: int | str
    research_task_id: str | None = None
    report_markdown: str = ""
    source_ordinal: int | None = None
    hint: str = ""


@dataclass(frozen=True, slots=True)
class ResearchTaskRecord:
    """Neutral research task observed by one poll."""

    task_id: str
    status: str
    query: str = ""
    sources: tuple[ResearchSourceRecord, ...] = ()
    summary: str = ""
    report: str = ""
    status_code: int | None = None
    source_type: int | None = None
    discovery_mode: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    account_id: str | None = None


@unique
class ResearchSearchSource(str, Enum):
    """Corpus a research run searches."""

    WEB = "web"
    DRIVE = "drive"


@unique
class ResearchMode(str, Enum):
    """Discovery depth a research run executes under."""

    FAST = "fast"
    DEEP = "deep"


@dataclass(frozen=True, slots=True)
class ResearchStartInput:
    """Validated request for one research run."""

    notebook_id: str
    query: str
    search_source: ResearchSearchSource
    mode: ResearchMode


@dataclass(frozen=True, slots=True)
class ResearchStartResult:
    """Identifiers a started run volunteered."""

    task_id: str
    report_id: str | None


@dataclass(frozen=True, slots=True)
class ResearchPollInput:
    """Notebook whose in-flight research tasks are being listed."""

    notebook_id: str


@dataclass(frozen=True, slots=True)
class ResearchPollResult:
    """Every research task visible at one poll, in backend order."""

    tasks: tuple[ResearchTaskRecord, ...]


@dataclass(frozen=True, slots=True)
class ResearchCancelInput:
    """Run to cancel plus the notebook used purely for request routing."""

    notebook_id: str
    run_id: str


@dataclass(frozen=True, slots=True)
class ResearchCancelResult:
    """Fire-and-forget cancel acknowledgement; it carries no success signal."""


@unique
class ResearchImportEntryKind(str, Enum):
    """How one requested import entry is carried to the backend."""

    WEB = "web"
    REPORT = "report"


@dataclass(frozen=True, slots=True)
class ResearchImportEntry:
    """One neutral entry in an import batch, in the order it is sent."""

    kind: ResearchImportEntryKind
    title: str
    url: str = ""
    report_markdown: str = ""


@dataclass(frozen=True, slots=True)
class ResearchImportInput:
    """One import attempt for an already-filtered, already-ordered batch."""

    notebook_id: str
    task_id: str
    entries: tuple[ResearchImportEntry, ...]
    attempt_timeout: float | None = None


@dataclass(frozen=True, slots=True)
class ResearchImportedSourceRecord:
    """One source the import response confirmed by id."""

    id: str
    title: str


@dataclass(frozen=True, slots=True)
class ResearchImportResult:
    """Sources the import response acknowledged; may under-report commits."""

    imported: tuple[ResearchImportedSourceRecord, ...]


RESEARCH_START_DEF: OperationDef[ResearchStartInput, ResearchStartResult] = OperationDef(
    Operation.RESEARCH_START,
    CallPolicy.STATEFUL_START,
    ResearchStartInput,
    ResearchStartResult,
)
RESEARCH_POLL_DEF: OperationDef[ResearchPollInput, ResearchPollResult] = OperationDef(
    Operation.RESEARCH_POLL,
    CallPolicy.READ,
    ResearchPollInput,
    ResearchPollResult,
)
RESEARCH_CANCEL_DEF: OperationDef[ResearchCancelInput, ResearchCancelResult] = OperationDef(
    Operation.RESEARCH_CANCEL,
    CallPolicy.MUTATION,
    ResearchCancelInput,
    ResearchCancelResult,
)
RESEARCH_IMPORT_DEF: OperationDef[ResearchImportInput, ResearchImportResult] = OperationDef(
    Operation.RESEARCH_IMPORT,
    CallPolicy.MUTATION,
    ResearchImportInput,
    ResearchImportResult,
)

__all__ = [
    "RESEARCH_CANCEL_DEF",
    "RESEARCH_IMPORT_DEF",
    "RESEARCH_POLL_DEF",
    "RESEARCH_START_DEF",
    "ResearchCancelInput",
    "ResearchCancelResult",
    "ResearchImportEntry",
    "ResearchImportEntryKind",
    "ResearchImportInput",
    "ResearchImportResult",
    "ResearchImportedSourceRecord",
    "ResearchMode",
    "ResearchPollInput",
    "ResearchPollResult",
    "ResearchSearchSource",
    "ResearchSourceRecord",
    "ResearchStartInput",
    "ResearchStartResult",
    "ResearchTaskRecord",
]
