"""Recording semantic backend for service-level tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar, cast

from notebooklm._backend import (
    BackendCapabilities,
    BackendContractError,
    BackendKind,
    UnsupportedOperationError,
)
from notebooklm._deadline import RuntimeDeadline
from notebooklm._operations import Operation, OperationDef

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


@dataclass(frozen=True, slots=True)
class BackendInvocation:
    """One validated fake-backend invocation."""

    operation: Operation
    value: object
    deadline: RuntimeDeadline | None


class RecordingBackend:
    """Typed fake that records calls and returns explicitly registered results."""

    def __init__(self, *, kind: BackendKind = BackendKind.WEB) -> None:
        self.kind = kind
        self.capabilities = BackendCapabilities()
        self.invocations: list[BackendInvocation] = []
        self.closed = False
        self._definitions: dict[Operation, OperationDef[object, object]] = {}
        self._results: dict[Operation, object] = {}

    def set_result(
        self,
        operation: OperationDef[InputT, OutputT],
        result: OutputT,
    ) -> None:
        """Register one result after validating its declared output type."""

        if not isinstance(result, operation.output_type):
            raise TypeError(
                f"{operation.key.value} result must be {operation.output_type.__name__}, "
                f"got {type(result).__name__}"
            )
        self._definitions[operation.key] = cast(OperationDef[object, object], operation)
        self._results[operation.key] = result
        self.capabilities = BackendCapabilities(frozenset(self._definitions))

    async def invoke(
        self,
        operation: OperationDef[InputT, OutputT],
        value: InputT,
        *,
        deadline: RuntimeDeadline | None,
    ) -> OutputT:
        """Validate and record one call before returning its registered result."""

        if not self.capabilities.supports(operation.key):
            raise UnsupportedOperationError(operation.key, self.kind)

        registered = self._definitions[operation.key]
        if registered != operation:
            raise BackendContractError(
                f"{operation.key.value} was invoked with an unregistered operation definition",
                operation=operation.key,
            )
        if not isinstance(value, operation.input_type):
            raise BackendContractError(
                f"{operation.key.value} input must be {operation.input_type.__name__}, "
                f"got {type(value).__name__}",
                operation=operation.key,
            )

        self.invocations.append(BackendInvocation(operation.key, value, deadline))
        result = self._results[operation.key]
        if not isinstance(result, operation.output_type):
            raise BackendContractError(
                f"{operation.key.value} registered result no longer matches "
                f"{operation.output_type.__name__}",
                operation=operation.key,
            )
        return cast(OutputT, result)

    async def close(self) -> None:
        """Record lifecycle closure without constructing runtime resources."""

        self.closed = True


__all__ = ["BackendInvocation", "RecordingBackend"]
