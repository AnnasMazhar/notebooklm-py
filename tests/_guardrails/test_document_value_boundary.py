"""Guard the structured-document graph's transport-neutral value-type exemption.

ADR-0035 deliberately lets web codecs construct this one exported graph directly:
the public constructors own UTF-16 range validation, collection freezing, layout,
rendering, and annotation ordering.  The exemption stays safe only while the types
remain dependency-bottom values and all positional/wire knowledge stays outside
``notebooklm._types.documents``.
"""

from __future__ import annotations

import ast
import pickle
from dataclasses import FrozenInstanceError, is_dataclass
from enum import Enum
from pathlib import Path

import pytest

import notebooklm
import notebooklm.types as public_types
from notebooklm._types import documents as document_types

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCUMENT_TYPES_PATH = PROJECT_ROOT / "src" / "notebooklm" / "_types" / "documents.py"

# Deliberately closed: adding a dependency requires reviewing the value-type
# exemption rather than silently letting protocol or runtime knowledge leak in.
ALLOWED_DOCUMENT_IMPORT_ROOTS = frozenset(
    {"__future__", "collections", "dataclasses", "enum", "typing"}
)

DOCUMENT_TYPE_NAMES = (
    "BlockKind",
    "BlockStyle",
    "DocumentAnnotation",
    "DocumentBlock",
    "ListInfo",
    "ListStyle",
    "StructuredDocument",
    "TableCell",
    "TextSpan",
)


def _document_dependency_violations(source: str) -> list[str]:
    """Return imports outside the document value module's closed stdlib set."""
    tree = ast.parse(source)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots = {alias.name.partition(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                violations.append(f"line {node.lineno}: {ast.unparse(node)}")
                continue
            roots = {(node.module or "").partition(".")[0]}
        else:
            continue
        if not roots <= ALLOWED_DOCUMENT_IMPORT_ROOTS:
            violations.append(f"line {node.lineno}: {ast.unparse(node)}")
    return violations


def test_document_value_module_has_no_wire_backend_or_runtime_dependencies() -> None:
    source = DOCUMENT_TYPES_PATH.read_text(encoding="utf-8")
    assert _document_dependency_violations(source) == []


@pytest.mark.parametrize(
    "planted_import",
    [
        "from .._backend import BackendAdapter",
        "from .._row_adapters.documents import DocumentBodyRow",
        "from .._web.backend import WebRpcBackend",
        "from notebooklm.rpc import RPCMethod",
        "import httpx",
    ],
)
def test_document_dependency_guard_rejects_planted_transport_imports(
    planted_import: str,
) -> None:
    source = DOCUMENT_TYPES_PATH.read_text(encoding="utf-8")
    assert _document_dependency_violations(f"{source}\n{planted_import}\n") == [
        f"line {len(source.splitlines()) + 2}: {planted_import}"
    ]


def test_document_value_exemption_is_exact_frozen_and_public() -> None:
    assert tuple(document_types.__all__) == (*DOCUMENT_TYPE_NAMES, "utf16_len")

    for name in DOCUMENT_TYPE_NAMES:
        canonical = getattr(document_types, name)
        assert getattr(public_types, name) is canonical
        assert getattr(notebooklm, name) is canonical
        if issubclass(canonical, Enum):
            continue
        assert is_dataclass(canonical)
        assert canonical.__dataclass_params__.frozen


def test_nested_document_value_round_trips_without_a_private_mirror() -> None:
    """Pin rich public behavior beyond the default-valued pickle baseline."""
    paragraph = notebooklm.DocumentBlock(
        0,
        4,
        [
            notebooklm.TextSpan(2, 4, "AB", url="https://example.test"),
            notebooklm.TextSpan(0, 2, "🔬", bold=True),
        ],
        style=notebooklm.BlockStyle.HEADING_2,
        list_info=notebooklm.ListInfo(
            style=notebooklm.ListStyle.ORDERED,
            nesting_level=1,
            glyph="1.",
            ordinal=1,
        ),
        kind=notebooklm.BlockKind.PARAGRAPH,
    )
    table = notebooklm.DocumentBlock(
        4,
        8,
        (notebooklm.TextSpan(4, 6, "CD"), notebooklm.TextSpan(6, 8, "EF")),
        kind=notebooklm.BlockKind.TABLE,
        table_rows=((notebooklm.TableCell(4, 6), notebooklm.TableCell(6, 8)),),
    )
    document = notebooklm.StructuredDocument(
        [table, paragraph],
        [
            notebooklm.DocumentAnnotation("later", 6, 8),
            notebooklm.DocumentAnnotation("answer", 2, 4),
        ],
    )

    restored = pickle.loads(pickle.dumps(document))

    assert restored == document
    assert hash(restored) == hash(document)
    assert restored.text == "🔬ABCDEF"
    assert notebooklm.utf16_len(restored.text) == restored.extent == 8
    assert restored.slice(2, 4) == "AB"
    assert restored.render() == "🔬AB\nCD\tEF"
    assert restored.annotations == (
        notebooklm.DocumentAnnotation("answer", 2, 4),
        notebooklm.DocumentAnnotation("later", 6, 8),
    )
    with pytest.raises(FrozenInstanceError):
        restored.blocks = ()
