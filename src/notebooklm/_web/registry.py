"""Closed web dispositions for the semantic operation vocabulary.

P2 notebook/source operations, P5 Studio family operations,
and P6.3 plain-note CRUD have executable bindings. Every other P0 operation has
an unsupported disposition, and
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
    ARTIFACT_EXPORT_DEF,
    ARTIFACT_GENERATE_AUDIO_DEF,
    ARTIFACT_GENERATE_DATA_TABLE_DEF,
    ARTIFACT_GENERATE_FLASHCARDS_DEF,
    ARTIFACT_GENERATE_INFOGRAPHIC_DEF,
    ARTIFACT_GENERATE_MIND_MAP_DEF,
    ARTIFACT_GENERATE_QUIZ_DEF,
    ARTIFACT_GENERATE_REPORT_DEF,
    ARTIFACT_GENERATE_SLIDE_DECK_DEF,
    ARTIFACT_GENERATE_VIDEO_DEF,
    ARTIFACT_GET_DEF,
    ARTIFACT_LIST_DEF,
    NOTE_CREATE_DEF,
    NOTE_DELETE_DEF,
    NOTE_GET_DEF,
    NOTE_LIST_DEF,
    NOTE_UPDATE_DEF,
    NOTEBOOK_CREATE_DEF,
    NOTEBOOK_DELETE_DEF,
    NOTEBOOK_GET_DEF,
    NOTEBOOK_LIST_DEF,
    NOTEBOOK_UPDATE_DEF,
    SOURCE_ADD_URL_DEF,
    SOURCE_GET_DEF,
    SOURCE_LIST_DEF,
)
from .policy import audit_web_call_policy_bindings


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
        Operation.SOURCE_ADD_URL: SOURCE_ADD_URL_DEF,
        Operation.SOURCE_LIST: SOURCE_LIST_DEF,
        Operation.SOURCE_GET: SOURCE_GET_DEF,
        Operation.NOTE_LIST: NOTE_LIST_DEF,
        Operation.NOTE_GET: NOTE_GET_DEF,
        Operation.NOTE_CREATE: NOTE_CREATE_DEF,
        Operation.NOTE_UPDATE: NOTE_UPDATE_DEF,
        Operation.NOTE_DELETE: NOTE_DELETE_DEF,
        Operation.ARTIFACT_LIST: ARTIFACT_LIST_DEF,
        Operation.ARTIFACT_GET: ARTIFACT_GET_DEF,
        Operation.ARTIFACT_GENERATE_AUDIO: ARTIFACT_GENERATE_AUDIO_DEF,
        Operation.ARTIFACT_GENERATE_QUIZ: ARTIFACT_GENERATE_QUIZ_DEF,
        Operation.ARTIFACT_GENERATE_FLASHCARDS: ARTIFACT_GENERATE_FLASHCARDS_DEF,
        Operation.ARTIFACT_GENERATE_REPORT: ARTIFACT_GENERATE_REPORT_DEF,
        Operation.ARTIFACT_GENERATE_VIDEO: ARTIFACT_GENERATE_VIDEO_DEF,
        Operation.ARTIFACT_GENERATE_INFOGRAPHIC: ARTIFACT_GENERATE_INFOGRAPHIC_DEF,
        Operation.ARTIFACT_GENERATE_SLIDE_DECK: ARTIFACT_GENERATE_SLIDE_DECK_DEF,
        Operation.ARTIFACT_GENERATE_DATA_TABLE: ARTIFACT_GENERATE_DATA_TABLE_DEF,
        Operation.ARTIFACT_GENERATE_MIND_MAP: ARTIFACT_GENERATE_MIND_MAP_DEF,
        Operation.ARTIFACT_EXPORT: ARTIFACT_EXPORT_DEF,
    }
)

_HANDLER_NAMES: Final[Mapping[Operation, str]] = MappingProxyType(
    {
        Operation.NOTEBOOK_LIST: "_notebook_list",
        Operation.NOTEBOOK_GET: "_notebook_get",
        Operation.NOTEBOOK_CREATE: "_notebook_create",
        Operation.NOTEBOOK_UPDATE: "_notebook_update",
        Operation.NOTEBOOK_DELETE: "_notebook_delete",
        Operation.SOURCE_ADD_URL: "_source_add_url",
        Operation.SOURCE_LIST: "_source_list",
        Operation.SOURCE_GET: "_source_get",
        Operation.NOTE_LIST: "_note_list",
        Operation.NOTE_GET: "_note_get",
        Operation.NOTE_CREATE: "_note_create",
        Operation.NOTE_UPDATE: "_note_update",
        Operation.NOTE_DELETE: "_note_delete",
        Operation.ARTIFACT_LIST: "_artifact_list",
        Operation.ARTIFACT_GET: "_artifact_get",
        Operation.ARTIFACT_GENERATE_AUDIO: "_audio_generate",
        Operation.ARTIFACT_GENERATE_QUIZ: "_quiz_generate",
        Operation.ARTIFACT_GENERATE_FLASHCARDS: "_flashcards_generate",
        Operation.ARTIFACT_GENERATE_REPORT: "_report_generate",
        Operation.ARTIFACT_GENERATE_VIDEO: "_video_generate",
        Operation.ARTIFACT_GENERATE_INFOGRAPHIC: "_infographic_generate",
        Operation.ARTIFACT_GENERATE_SLIDE_DECK: "_slide_deck_generate",
        Operation.ARTIFACT_GENERATE_DATA_TABLE: "_data_table_generate",
        Operation.ARTIFACT_GENERATE_MIND_MAP: "_mind_map_generate",
        Operation.ARTIFACT_EXPORT: "_artifact_export",
    }
)

_STAGED_DEFINITIONS: Final[Mapping[Operation, OperationDef[Any, Any]]] = MappingProxyType({})

_STAGED_HANDLER_NAMES: Final[Mapping[Operation, str]] = MappingProxyType({})

# P0 freezes 86 operations.  This local assertion is intentionally repeated at
# the runtime registry boundary: a new enum member must not silently inherit an
# unsupported disposition without a P1 registry review.
_EXPECTED_OPERATION_COUNT: Final = 86
_EXPECTED_SUPPORTED_COUNT: Final = 25
_EXPECTED_STAGED_COUNT: Final = 0


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
    if policy_errors := audit_web_call_policy_bindings(_SUPPORTED_DEFINITIONS):
        raise RuntimeError("web call-policy binding drift:\n- " + "\n- ".join(policy_errors))

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
