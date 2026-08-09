"""Dependency, API, caller, and cycle guards for the path-owned profile store."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.repo_lint

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "notebooklm"
AUTH_ROOT = SRC_ROOT / "_auth"
MODULE_PATH = AUTH_ROOT / "profile_store.py"
FACADE_PATH = SRC_ROOT / "auth.py"
AUTH_INIT_PATH = AUTH_ROOT / "__init__.py"
ImportRecord = tuple[str, int, str, str, str | None]
Caller = tuple[str, str, str]

_EXPECTED_IMPORTS: list[ImportRecord] = [
    ("module", 0, "__future__", "annotations", None),
    ("module", 0, "contextlib", "", None),
    ("module", 0, "json", "", None),
    ("module", 0, "logging", "", None),
    ("module", 0, "os", "", None),
    ("module", 0, "sys", "", None),
    ("module", 0, "collections.abc", "Callable", None),
    ("module", 0, "dataclasses", "dataclass", None),
    ("module", 0, "dataclasses", "field", None),
    ("module", 0, "enum", "Enum", None),
    ("module", 0, "pathlib", "Path", None),
    ("module", 0, "typing", "Any", None),
    ("module", 0, "typing", "Protocol", None),
    ("module", 0, "typing", "TypeVar", None),
    ("module", 2, "exceptions", "LockUnavailableError", None),
    ("module", 1, "", "cookie_merge", "_cookie_merge"),
    ("module", 1, "cookie_merge", "RecoveryObservation", None),
    ("module", 1, "cookie_types", "CookieIdentity", None),
    ("module", 1, "cookie_types", "CookieJar", None),
    ("module", 1, "credential_io", "_commit_profile_json", None),
    ("module", 1, "paths", "_storage_state_lock_path", None),
    ("module", 1, "paths", "canonical_storage_key", None),
    ("module", 1, "profile_account", "AccountView", None),
    ("module", 1, "profile_account", "DomainSelection", None),
    ("module", 1, "profile_account", "StoredSession", None),
    ("module", 1, "profile_document", "ProfileDocument", None),
    ("module", 1, "profile_document", "_ProfileDocumentStructureError", None),
    ("module", 1, "storage_lock", "_LOCK_ACQUIRE_DEADLINE_SECONDS", None),
    ("module", 1, "storage_lock", "LockRequest", None),
    ("module", 1, "storage_lock", "LockState", None),
    ("module", 1, "storage_lock", "StorageLockManager", None),
]


class _ImportCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.function_depth = 0
        self.class_depth = 0
        self.records: list[ImportRecord] = []

    @property
    def scope(self) -> str:
        if self.function_depth:
            return "function"
        if self.class_depth:
            return "class"
        return "module"

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.function_depth += 1
        self.generic_visit(node)
        self.function_depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_depth += 1
        self.generic_visit(node)
        self.class_depth -= 1

    def visit_Import(self, node: ast.Import) -> None:
        self.records.extend((self.scope, 0, item.name, "", item.asname) for item in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.records.extend(
            (self.scope, node.level, node.module or "", item.name, item.asname)
            for item in node.names
        )


def _collect_imports(tree: ast.AST) -> list[ImportRecord]:
    collector = _ImportCollector()
    collector.visit(tree)
    return collector.records


def _dependency_violations(tree: ast.AST) -> list[ImportRecord]:
    return [record for record in _collect_imports(tree) if record not in _EXPECTED_IMPORTS]


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("import httpx\n", id="httpx"),
        pytest.param("from .storage import CookieSaveResult\n", id="storage"),
        pytest.param("class Lazy:\n    from .cookies import load_cookies\n", id="cookies"),
        pytest.param("def lazy():\n    from .account import enumerate_accounts\n", id="account"),
        pytest.param("import notebooklm.auth\n", id="facade"),
        pytest.param("def lazy():\n    from .._runtime import lifecycle\n", id="runtime"),
        pytest.param("class Lazy:\n    from ..cli import main\n", id="cli"),
        pytest.param("from .master_token import read_master_token\n", id="master-token"),
        pytest.param(
            "def lazy():\n    from .profile_document import ProfileDocument\n",
            id="lazy-approved-leaf",
        ),
    ],
)
def test_dependency_detector_bites_at_module_function_and_class_scope(source: str) -> None:
    assert _dependency_violations(ast.parse(source))


def test_profile_store_imports_are_exact_and_module_level() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))
    assert _collect_imports(tree) == _EXPECTED_IMPORTS
    assert _dependency_violations(tree) == []


def _resolved_module(path: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    relative = path.relative_to(REPO_ROOT / "src").with_suffix("")
    package = list(relative.parts[:-1])
    keep = len(package) - (node.level - 1)
    base = package[:keep]
    if node.module:
        base.extend(node.module.split("."))
    return ".".join(base)


def _imports_profile_store(path: Path, tree: ast.AST) -> bool:
    target = "notebooklm._auth.profile_store"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(item.name == target for item in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolved_module(path, node)
            if resolved == target or any(
                f"{resolved}.{item.name}" == target for item in node.names
            ):
                return True
    return False


@pytest.mark.parametrize(
    "source",
    [
        "from .profile_store import ProfileStore\n",
        "def lazy():\n    from . import profile_store\n",
        "class Lazy:\n    import notebooklm._auth.profile_store\n",
    ],
)
def test_importer_detector_bites_at_every_scope(source: str) -> None:
    assert _imports_profile_store(AUTH_ROOT / "synthetic.py", ast.parse(source))


def test_production_importer_is_exactly_storage() -> None:
    actual = {
        path.relative_to(AUTH_ROOT).as_posix()
        for path in AUTH_ROOT.rglob("*.py")
        if path != MODULE_PATH
        and _imports_profile_store(
            path,
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path)),
        )
    }
    assert actual == {"storage.py"}


def _profile_store_class(tree: ast.Module) -> ast.ClassDef:
    matches = [
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ProfileStore"
    ]
    assert len(matches) == 1
    return matches[0]


def test_profile_store_public_method_set_is_minimal_and_exact() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))
    cls = _profile_store_class(tree)
    methods = {
        node.name
        for node in cls.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and not node.name.startswith("_")
    }
    assert methods == {
        "path",
        "ordering_key",
        "read_document",
        "read_session",
        "merge_cookie_observation",
        "merge_legacy_cookie_observation",
    }
    forbidden_future = {
        "read_account",
        "update_account",
        "clear_account",
        "replace_from_remint",
        "replace_from_login",
        "persist_minted_session",
        "read_master_token",
        "write_master_token",
        "mutate",
    }
    assert methods.isdisjoint(forbidden_future)


def _function_owner(stack: list[str]) -> str:
    return ".".join(stack) if stack else "<module>"


class _StoreCallCollector(ast.NodeVisitor):
    def __init__(self, module: str) -> None:
        self.module = module
        self.class_stack: list[str] = []
        self.function_stack: list[str] = []
        self.calls: set[Caller] = set()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.function_stack.append(node.name)
        self.generic_visit(node)
        self.function_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node: ast.Call) -> None:
        target: str | None = None
        if isinstance(node.func, ast.Name) and node.func.id == "ProfileStore":
            target = "ProfileStore"
        elif isinstance(node.func, ast.Attribute) and node.func.attr in {
            "merge_cookie_observation",
            "merge_legacy_cookie_observation",
        }:
            target = node.func.attr
        if target is not None:
            owner = _function_owner([*self.class_stack, *self.function_stack[:1]])
            self.calls.add((self.module, owner, target))
        self.generic_visit(node)


def _store_calls(path: Path, tree: ast.AST) -> set[Caller]:
    collector = _StoreCallCollector(path.relative_to(AUTH_ROOT).as_posix())
    collector.visit(tree)
    return collector.calls


def test_direct_production_store_callers_are_exact_and_function_granular() -> None:
    actual: set[Caller] = set()
    for path in sorted(AUTH_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        actual.update(_store_calls(path, tree))
    assert actual == {
        ("profile_store.py", "in_storage_transaction", "ProfileStore"),
        ("storage.py", "merge_cookie_delta", "ProfileStore"),
        ("storage.py", "merge_cookie_delta", "merge_cookie_observation"),
        ("storage.py", "merge_cookie_delta", "merge_legacy_cookie_observation"),
    }


def test_synthetic_extra_store_caller_is_rejected() -> None:
    path = AUTH_ROOT / "storage.py"
    tree = ast.parse(
        "from .profile_store import ProfileStore\n"
        "def forbidden(path):\n"
        "    return ProfileStore(path).read_document()\n"
    )
    assert _imports_profile_store(path, tree)
    actual = _store_calls(path, tree)
    assert actual == {("storage.py", "forbidden", "ProfileStore")}


def _auth_edges() -> dict[str, set[str]]:
    modules = {path.stem for path in AUTH_ROOT.glob("*.py")}
    graph = {module: set() for module in modules}
    prefix = "notebooklm._auth."
    for path in AUTH_ROOT.glob("*.py"):
        source = path.stem
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                resolved = _resolved_module(path, node)
                targets = [resolved, *(f"{resolved}.{item.name}" for item in node.names)]
            for target in targets:
                if target.startswith(prefix):
                    candidate = target.removeprefix(prefix).split(".", 1)[0]
                    if candidate in modules and candidate != source:
                        graph[source].add(candidate)
    return graph


def _reachable(graph: dict[str, set[str]], source: str, target: str) -> bool:
    pending = list(graph.get(source, set()))
    seen: set[str] = set()
    while pending:
        node = pending.pop()
        if node == target:
            return True
        if node not in seen:
            seen.add(node)
            pending.extend(graph.get(node, set()))
    return False


def test_profile_store_participates_in_no_strongly_connected_component() -> None:
    graph = _auth_edges()
    assert not any(
        _reachable(graph, "profile_store", module) and _reachable(graph, module, "profile_store")
        for module in graph
        if module != "profile_store"
    )


def test_cycle_detector_bites_on_synthetic_two_node_cycle() -> None:
    graph = {"profile_store": {"storage_lock"}, "storage_lock": {"profile_store"}}
    assert _reachable(graph, "profile_store", "storage_lock")
    assert _reachable(graph, "storage_lock", "profile_store")


def test_profile_store_is_not_exposed_by_facade_or_auth_package() -> None:
    for path in (FACADE_PATH, AUTH_INIT_PATH):
        source = path.read_text(encoding="utf-8")
        assert "ProfileStore" not in source
        assert "profile_store" not in source
