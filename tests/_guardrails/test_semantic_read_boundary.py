"""Dependency-direction gates for semantic read services and projectors."""

from __future__ import annotations

import ast
from pathlib import Path

import notebooklm
import notebooklm._projectors as projector_module
import notebooklm._read_services as service_module

_ROOT = Path(__file__).resolve().parents[2]
_PROJECTORS = _ROOT / "src" / "notebooklm" / "_projectors.py"
_SERVICES = _ROOT / "src" / "notebooklm" / "_read_services.py"
_MUTATION_SERVICES = _ROOT / "src" / "notebooklm" / "_mutation_services.py"

_FORBIDDEN_MODULE_PARTS = frozenset(
    {
        "_row_adapters",
        "_web",
        "cli",
        "httpx",
        "mcp",
        "rpc",
        "server",
    }
)
_FORBIDDEN_IDENTIFIERS = frozenset(
    {
        "NotebookLMClient",
        "RPCMethod",
        "RpcCaller",
        "SourceRow",
        "ProjectRow",
    }
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


def _identifiers(path: Path) -> set[str]:
    return {node.id for node in ast.walk(_tree(path)) if isinstance(node, ast.Name)}


def test_read_services_depend_only_on_semantic_port_records_deadline_and_projectors() -> None:
    assert _imported_modules(_SERVICES) <= {
        "__future__",
        "typing",
        "_backend",
        "_deadline",
        "_projectors",
        "_records",
        "types",
    }
    assert not (_identifiers(_SERVICES) & _FORBIDDEN_IDENTIFIERS)


def test_read_core_has_no_transport_wire_or_adapter_dependencies() -> None:
    for path in (_MUTATION_SERVICES, _PROJECTORS, _SERVICES):
        assert not {
            module
            for module in _imported_modules(path)
            if any(part in _FORBIDDEN_MODULE_PARTS for part in module.split("."))
        }
        assert not (_identifiers(path) & _FORBIDDEN_IDENTIFIERS)


def test_url_mutation_service_depends_only_on_semantic_port_deadline_and_records() -> None:
    assert _imported_modules(_MUTATION_SERVICES) <= {
        "__future__",
        "_backend",
        "_deadline",
        "_records",
    }
    assert not any(isinstance(node, ast.Subscript) for node in ast.walk(_tree(_MUTATION_SERVICES)))


def test_projectors_use_normal_public_constructors_without_wire_factories() -> None:
    tree = _tree(_PROJECTORS)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]

    assert {node.func.id for node in calls if isinstance(node.func, ast.Name)} >= {
        "Notebook",
        "Source",
    }
    assert not {
        node.func.attr
        for node in calls
        if isinstance(node.func, ast.Attribute)
        and node.func.attr in {"from_api_response", "from_row"}
    }


def test_read_core_remains_private_and_does_not_expand_public_package_exports() -> None:
    assert projector_module.__name__ == "notebooklm._projectors"
    assert service_module.__name__ == "notebooklm._read_services"
    assert not (set(projector_module.__all__) | set(service_module.__all__)) & set(
        notebooklm.__all__
    )
