"""Closed web dispositions for the semantic operation vocabulary.

P2.1 reads and the P2.2 notebook mutation core have executable bindings; P2.3
adds a URL-source binding that production dispatch explicitly rejects until its
facade delegates. Every other P0 operation has an unsupported disposition, and
the count assertions force a deliberate registry update when the closed
:class:`Operation` enum changes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

from .._operations import Operation, OperationDef
from .._records import (
    NOTEBOOK_CREATE_DEF,
    NOTEBOOK_DELETE_DEF,
    NOTEBOOK_GET_DEF,
    NOTEBOOK_LIST_DEF,
    NOTEBOOK_UPDATE_DEF,
    SOURCE_ADD_URL_DEF,
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
        has_definition = self.definition is not None
        has_handler = self.handler_name is not None
        if has_definition != has_handler:
            raise ValueError("web definitions and handler names must be present together")
        if not has_handler and self.unsupported_reason is None:
            raise ValueError("unsupported web bindings require a reason")

    @property
    def is_supported(self) -> bool:
        """Whether this binding names an executable P1 handler."""
        return self.definition is not None and self.unsupported_reason is None

    @property
    def is_staged(self) -> bool:
        """Whether a reviewed handler exists but production dispatch rejects it."""
        return self.definition is not None and self.unsupported_reason is not None


_SUPPORTED_DEFINITIONS: Final[Mapping[Operation, OperationDef[Any, Any]]] = MappingProxyType(
    {
        Operation.NOTEBOOK_LIST: NOTEBOOK_LIST_DEF,
        Operation.NOTEBOOK_GET: NOTEBOOK_GET_DEF,
        Operation.NOTEBOOK_CREATE: NOTEBOOK_CREATE_DEF,
        Operation.NOTEBOOK_UPDATE: NOTEBOOK_UPDATE_DEF,
        Operation.NOTEBOOK_DELETE: NOTEBOOK_DELETE_DEF,
        Operation.SOURCE_LIST: SOURCE_LIST_DEF,
        Operation.SOURCE_GET: SOURCE_GET_DEF,
    }
)

_HANDLER_NAMES: Final[Mapping[Operation, str]] = MappingProxyType(
    {
        Operation.NOTEBOOK_LIST: "_notebook_list",
        Operation.NOTEBOOK_GET: "_notebook_get",
        Operation.NOTEBOOK_CREATE: "_notebook_create",
        Operation.NOTEBOOK_UPDATE: "_notebook_update",
        Operation.NOTEBOOK_DELETE: "_notebook_delete",
        Operation.SOURCE_LIST: "_source_list",
        Operation.SOURCE_GET: "_source_get",
    }
)

_STAGED_DEFINITIONS: Final[Mapping[Operation, OperationDef[Any, Any]]] = MappingProxyType(
    {Operation.SOURCE_ADD_URL: SOURCE_ADD_URL_DEF}
)

_STAGED_HANDLER_NAMES: Final[Mapping[Operation, str]] = MappingProxyType(
    {Operation.SOURCE_ADD_URL: "_source_add_url"}
)

# P0 freezes 86 operations.  This local assertion is intentionally repeated at
# the runtime registry boundary: a new enum member must not silently inherit an
# unsupported disposition without a P1 registry review.
_EXPECTED_OPERATION_COUNT: Final = 86
_EXPECTED_SUPPORTED_COUNT: Final = 7
_EXPECTED_STAGED_COUNT: Final = 1


def _build_web_operation_registry() -> Mapping[Operation, WebOperationBinding]:
    if set(_SUPPORTED_DEFINITIONS) != set(_HANDLER_NAMES):
        raise RuntimeError("web definitions and handler names disagree")
    if set(_STAGED_DEFINITIONS) != set(_STAGED_HANDLER_NAMES):
        raise RuntimeError("staged web definitions and handler names disagree")
    if set(_SUPPORTED_DEFINITIONS) & set(_STAGED_DEFINITIONS):
        raise RuntimeError("a web operation cannot be active and staged")
    if len(Operation) != _EXPECTED_OPERATION_COUNT:
        raise RuntimeError(
            "the semantic operation vocabulary changed; review every web disposition "
            f"(expected {_EXPECTED_OPERATION_COUNT}, found {len(Operation)})"
        )
    if len(_SUPPORTED_DEFINITIONS) != _EXPECTED_SUPPORTED_COUNT:
        raise RuntimeError(
            "the P1 web handler set changed; update the reviewed supported-operation count"
        )
    if len(_STAGED_DEFINITIONS) != _EXPECTED_STAGED_COUNT:
        raise RuntimeError(
            "the staged web handler set changed; update the reviewed staged-operation count"
        )

    registry: dict[Operation, WebOperationBinding] = {}
    for operation in Operation:
        definition = _SUPPORTED_DEFINITIONS.get(operation)
        staged_definition = _STAGED_DEFINITIONS.get(operation)
        if definition is not None:
            registry[operation] = WebOperationBinding(
                definition=definition,
                handler_name=_HANDLER_NAMES[operation],
                unsupported_reason=None,
            )
        elif staged_definition is not None:
            registry[operation] = WebOperationBinding(
                definition=staged_definition,
                handler_name=_STAGED_HANDLER_NAMES[operation],
                unsupported_reason="handler staged until the source facade delegates",
            )
        else:
            registry[operation] = WebOperationBinding(
                definition=None,
                handler_name=None,
                unsupported_reason="not migrated to the semantic backend",
            )
    if set(registry) != set(Operation):
        raise RuntimeError("web operation registry is not closed over Operation")
    return MappingProxyType(registry)


WEB_OPERATION_REGISTRY: Final = _build_web_operation_registry()

WEB_SUPPORTED_OPERATIONS: Final = frozenset(
    operation for operation, binding in WEB_OPERATION_REGISTRY.items() if binding.is_supported
)

WEB_STAGED_OPERATIONS: Final = frozenset(
    operation for operation, binding in WEB_OPERATION_REGISTRY.items() if binding.is_staged
)


__all__ = [
    "WEB_OPERATION_REGISTRY",
    "WEB_SUPPORTED_OPERATIONS",
    "WEB_STAGED_OPERATIONS",
    "WebOperationBinding",
]
