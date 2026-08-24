"""Closed web dispositions for the semantic operation vocabulary.

Only the P2.1 read slice has handlers in P1.  Every other P0 operation is
present with an explicit unsupported disposition, and the count assertion
forces a deliberate registry update when the closed :class:`Operation` enum
changes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

from .._operations import Operation, OperationDef
from .._records import (
    NOTEBOOK_GET_DEF,
    NOTEBOOK_LIST_DEF,
    SOURCE_GET_DEF,
    SOURCE_LIST_DEF,
)


@dataclass(frozen=True, slots=True)
class WebOperationBinding:
    """One executable handler or one reviewed unsupported disposition."""

    definition: OperationDef[Any, Any] | None
    handler_name: str | None
    unsupported_reason: str | None

    def __post_init__(self) -> None:
        executable = self.definition is not None and self.handler_name is not None
        unsupported = self.definition is None and self.handler_name is None
        if executable == unsupported:
            raise ValueError(
                "a web operation binding must be exactly one of executable or unsupported"
            )
        if unsupported != (self.unsupported_reason is not None):
            raise ValueError("unsupported web bindings require a reason; executable ones forbid it")

    @property
    def is_supported(self) -> bool:
        """Whether this binding names an executable P1 handler."""
        return self.definition is not None


_SUPPORTED_DEFINITIONS: Final[Mapping[Operation, OperationDef[Any, Any]]] = MappingProxyType(
    {
        Operation.NOTEBOOK_LIST: NOTEBOOK_LIST_DEF,
        Operation.NOTEBOOK_GET: NOTEBOOK_GET_DEF,
        Operation.SOURCE_LIST: SOURCE_LIST_DEF,
        Operation.SOURCE_GET: SOURCE_GET_DEF,
    }
)

_HANDLER_NAMES: Final[Mapping[Operation, str]] = MappingProxyType(
    {
        Operation.NOTEBOOK_LIST: "_notebook_list",
        Operation.NOTEBOOK_GET: "_notebook_get",
        Operation.SOURCE_LIST: "_source_list",
        Operation.SOURCE_GET: "_source_get",
    }
)

# P0 freezes 86 operations.  This local assertion is intentionally repeated at
# the runtime registry boundary: a new enum member must not silently inherit an
# unsupported disposition without a P1 registry review.
_EXPECTED_OPERATION_COUNT: Final = 86
_EXPECTED_SUPPORTED_COUNT: Final = 4


def _build_web_operation_registry() -> Mapping[Operation, WebOperationBinding]:
    if set(_SUPPORTED_DEFINITIONS) != set(_HANDLER_NAMES):
        raise RuntimeError("web definitions and handler names disagree")
    if len(Operation) != _EXPECTED_OPERATION_COUNT:
        raise RuntimeError(
            "the semantic operation vocabulary changed; review every web disposition "
            f"(expected {_EXPECTED_OPERATION_COUNT}, found {len(Operation)})"
        )
    if len(_SUPPORTED_DEFINITIONS) != _EXPECTED_SUPPORTED_COUNT:
        raise RuntimeError(
            "the P1 web handler set changed; update the reviewed supported-operation count"
        )

    registry: dict[Operation, WebOperationBinding] = {}
    for operation in Operation:
        definition = _SUPPORTED_DEFINITIONS.get(operation)
        if definition is None:
            registry[operation] = WebOperationBinding(
                definition=None,
                handler_name=None,
                unsupported_reason="not migrated to the semantic backend",
            )
        else:
            registry[operation] = WebOperationBinding(
                definition=definition,
                handler_name=_HANDLER_NAMES[operation],
                unsupported_reason=None,
            )
    if set(registry) != set(Operation):
        raise RuntimeError("web operation registry is not closed over Operation")
    return MappingProxyType(registry)


WEB_OPERATION_REGISTRY: Final = _build_web_operation_registry()

WEB_SUPPORTED_OPERATIONS: Final = frozenset(
    operation for operation, binding in WEB_OPERATION_REGISTRY.items() if binding.is_supported
)


__all__ = [
    "WEB_OPERATION_REGISTRY",
    "WEB_SUPPORTED_OPERATIONS",
    "WebOperationBinding",
]
