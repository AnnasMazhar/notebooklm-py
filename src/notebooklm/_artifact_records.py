"""Small transport-neutral records for artifact parse-failure replay."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, unique

from ._logging import scrub_secrets


def sanitize_artifact_parse_text(value: object) -> str:
    """Return credential-scrubbed artifact parse evidence for public replay."""

    return scrub_secrets(value)


@unique
class ArtifactParseFailureKind(str, Enum):
    """Closed exception vocabulary emitted while decoding representation rows."""

    UNKNOWN_RPC_METHOD = "unknown_rpc_method"
    INDEX = "index"
    KEY = "key"
    TYPE = "type"
    VALUE = "value"


@dataclass(frozen=True, slots=True)
class ArtifactParseFailureRecord:
    """Sanitized evidence needed to reconstruct one public parse-error cause."""

    kind: ArtifactParseFailureKind
    message: str = field(repr=False)
    method_id: str | int | None = None
    path: tuple[int, ...] | None = None
    source: str | None = None
    found_ids: tuple[str | int, ...] = ()
    raw_response: str | None = field(default=None, repr=False)
    data_at_failure: str | None = field(default=None, repr=False)
    rpc_code: str | int | None = None


__all__ = [
    "ArtifactParseFailureKind",
    "ArtifactParseFailureRecord",
    "sanitize_artifact_parse_text",
]
