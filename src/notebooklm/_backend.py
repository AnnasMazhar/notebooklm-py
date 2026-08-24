"""Private semantic backend boundary.

The port is intentionally transport-neutral.  It accepts closed semantic
operation definitions and typed values, carries one caller-owned absolute
deadline through unchanged, and returns the operation's declared result type.
Concrete wire adapters live above this module and are not part of the protocol.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, unique
from typing import Protocol, TypeVar, runtime_checkable

from ._deadline import RuntimeDeadline
from ._operations import Operation, OperationDef

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


@unique
class BackendKind(str, Enum):
    """Protocol families understood by the private semantic boundary."""

    WEB = "web"
    MOBILE = "mobile"


@unique
class BackendErrorReason(str, Enum):
    """Closed neutral reasons emitted by reviewed semantic web bindings."""

    AUTH = "auth"
    ARTIFACT_FEATURE_UNAVAILABLE = "artifact_feature_unavailable"
    CLIENT = "client"
    DECODING = "decoding"
    IDEMPOTENCY_VARIANT = "idempotency_variant"
    LABEL_AMBIGUOUS_CREATE = "label_ambiguous_create"
    LABEL_NOT_FOUND = "label_not_found"
    NETWORK = "network"
    NOTEBOOK_LIMIT = "notebook_limit"
    NOTEBOOK_NOT_FOUND = "notebook_not_found"
    RATE_LIMIT = "rate_limit"
    RESPONSE_TOO_LARGE = "response_too_large"
    RPC = "rpc"
    SERVER = "server"
    SOURCE_ADD = "source_add"
    TIMEOUT = "timeout"
    UNKNOWN_RPC_METHOD = "unknown_rpc_method"


@dataclass(frozen=True, slots=True)
class BackendCapabilities:
    """Closed set of semantic operations implemented by one backend."""

    supported_operations: frozenset[Operation] = frozenset()

    def supports(self, operation: Operation) -> bool:
        """Return whether the backend implements ``operation``."""

        return operation in self.supported_operations


@runtime_checkable
class BackendAdapter(Protocol):
    """Neutral unary semantic backend port.

    Streaming operations may grow a separate typed protocol when migrated;
    forcing a stream through this unary method would weaken the boundary.
    """

    @property
    def kind(self) -> BackendKind:
        """Backend protocol family."""

        ...

    @property
    def capabilities(self) -> BackendCapabilities:
        """Semantic operations implemented by this backend."""

        ...

    async def invoke(
        self,
        operation: OperationDef[InputT, OutputT],
        value: InputT,
        *,
        deadline: RuntimeDeadline | None,
    ) -> OutputT:
        """Invoke one supported operation with its declared input type."""

        ...

    async def close(self) -> None:
        """Release backend-owned resources."""

        ...


@dataclass(frozen=True, slots=True)
class BackendError(Exception):
    """Smallest transport-neutral failure record returned by a backend.

    ``diagnostics`` is an opaque, already-scrubbed mapping.  The backend owns
    its contents; later compatibility projectors replay the same mapping rather
    than interpreting wire-specific fields in semantic services.
    """

    message: str
    operation: Operation | None = None
    outcome_unknown: bool = False
    diagnostics: Mapping[str, object] | None = field(default=None, repr=False, hash=False)
    reason: BackendErrorReason | None = None

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)


class BackendContractError(BackendError):
    """A backend registration, input, or result violated its typed contract."""

    __slots__ = ()


class UnsupportedOperationError(BackendContractError):
    """The selected backend does not implement a semantic operation."""

    __slots__ = ("backend_kind",)

    backend_kind: BackendKind

    def __init__(self, operation: Operation, backend_kind: BackendKind) -> None:
        BackendError.__init__(
            self,
            message=f"{backend_kind.value} backend does not support {operation.value}",
            operation=operation,
        )
        object.__setattr__(self, "backend_kind", backend_kind)


class BackendDeadlineExceededError(BackendError):
    """A semantic invocation exhausted the caller-owned absolute deadline."""

    __slots__ = ()

    def __init__(
        self,
        operation: Operation,
        *,
        outcome_unknown: bool = False,
        diagnostics: Mapping[str, object] | None = None,
    ) -> None:
        BackendError.__init__(
            self,
            message=f"{operation.value} exceeded its deadline",
            operation=operation,
            outcome_unknown=outcome_unknown,
            diagnostics=diagnostics,
            reason=BackendErrorReason.TIMEOUT,
        )


def mark_backend_outcome_unknown(error: BackendError) -> BackendError:
    """Return closed neutral evidence for a write whose outcome is unconfirmed."""
    if error.outcome_unknown:
        return error
    return BackendError(
        message=error.message,
        operation=error.operation,
        outcome_unknown=True,
        diagnostics=error.diagnostics,
        reason=error.reason,
    )


__all__ = [
    "BackendAdapter",
    "BackendCapabilities",
    "BackendContractError",
    "BackendDeadlineExceededError",
    "BackendError",
    "BackendErrorReason",
    "BackendKind",
    "mark_backend_outcome_unknown",
    "UnsupportedOperationError",
]
