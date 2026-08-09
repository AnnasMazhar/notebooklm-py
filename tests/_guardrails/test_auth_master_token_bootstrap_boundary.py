"""Fail-closed composition and capability boundary for master-token bootstrap."""

from __future__ import annotations

import ast
import hashlib
import inspect
from pathlib import Path

import pytest

import notebooklm.auth as auth_facade
from notebooklm._auth import master_token, master_token_bootstrap

pytestmark = pytest.mark.repo_lint

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "notebooklm"
AUTH_ROOT = SRC_ROOT / "_auth"
MODULE_PATH = AUTH_ROOT / "master_token_bootstrap.py"
ADAPTER_PATH = AUTH_ROOT / "master_token.py"

ImportRecord = tuple[str, int, str, str, str | None]

_EXPECTED_IMPORTS: list[ImportRecord] = [
    ("module", 0, "__future__", "annotations", None),
    ("module", 0, "asyncio", "", None),
    ("module", 0, "enum", "", None),
    ("module", 0, "json", "", None),
    ("module", 0, "logging", "", None),
    ("module", 0, "sys", "", None),
    ("module", 0, "collections.abc", "Awaitable", None),
    ("module", 0, "collections.abc", "Callable", None),
    ("module", 0, "pathlib", "Path", None),
    ("module", 0, "typing", "cast", None),
    ("module", 0, "httpx", "", None),
    ("module", 0, "filelock", "FileLock", None),
    ("module", 0, "filelock", "Timeout", None),
    ("module", 2, "paths", "master_token_path_for", None),
    ("module", 1, "cookie_semantics", "cookie_is_http_only", None),
    ("module", 1, "cookie_types", "Cookie", None),
    ("module", 1, "cookie_types", "CookieJar", None),
    ("module", 1, "master_token_types", "MasterToken", None),
    ("module", 1, "master_token_types", "_MasterTokenRecordError", None),
    ("module", 1, "mint_service", "MintService", None),
    ("module", 1, "mint_service", "_MintError", None),
    ("module", 1, "profile_store", "MintedSessionWriteRequest", None),
    ("module", 1, "profile_store", "ProfileStore", None),
    ("module", 1, "profile_store", "_MintedSessionOwnershipRefused", None),
]

_METHOD_HASHES = {
    "__init__": "0eefd43a7e671e5b8eb2e0e8ff49c13668d92b643905f5174c97f2c1f3135863",
    "_read_master_token": "9680f4656c89db11629ea81fcee66f4a5b4bbb4019a943a71410bb59c55dd91c",
    "assert_account_writable": "3356c0e0ea003526f8ce9017aad76a342f90e8faf1c4920d55e69d09c426c3b9",
    "_resolve_android_id": "dc25c110dc729395610ee66adca9a2c36647fdbbc43695704912232962fbb927",
    "_exchange": "f2418ce9e52b931bbd46f18bb82b3abef7f16374fe72980847a0eafbd7dba79a",
    "_mint": "f72ae62a2db8fa6e817a55129f3c13408873cb63a18bdd989291b3dc985cc37e",
    "_minted_session_request": ("c94e58f823dad46aa3e9a39561c87c8c5ec1597c6a8f987c4214102e1ab8b21a"),
    "_replace_minted_session": ("aee40beafe61d1f19e0d4be5c3b9e33915b60a36ea337f7426cfa6c5c7af0516"),
    "bootstrap_from_oauth_token": (
        "033363fc9ecbc5b0af33574a21eca75bcbddca09cf68577c52efb4f15c7ebed6"
    ),
    "remint_from_stored_token": (
        "a224d0e76eba28312d1693be018fd3e79e19ae340ee3d84f5959694045168224"
    ),
    "_acquire_bootstrap_lock": ("59d49c676dd41acbe6b1fc3bc741993528f756403160dec565862c9c62cb8798"),
    "_run_remint_to_settlement": (
        "4d74087648db70fbd0d45414eb47600a7281fac9639bc3f4b10f6acc39886cc7"
    ),
    "bootstrap_storage": "a66b4166a902428975a90043867c63fa84b37daac81772b50595211ab3d1dd11",
}

_ADAPTER_HASHES = {
    "assert_account_writable": ("c7e0451c1ec7e6ae37c4bef3c6b9d98932bfe3e7fe4b5f8e0122fb92563b603b"),
    "bootstrap_from_oauth_token": (
        "d7cdd3b6e28a7adddbc30f668af2e653dc8b6b851879921519d3819f2a243275"
    ),
    "remint_from_stored_token": (
        "2a7623979038fae29a3ee5a9663cb31b2614a8dbaa45fa5467c9365821780bcf"
    ),
    "bootstrap_storage_from_master_token": (
        "9cd8f4d1776ac6352e3e8d9ba3a729369e46d83a2f00da25ea2f096d855ea96a"
    ),
    "bootstrap_missing_storage_from_master_token": (
        "75e60a24e3d8889547381125196fd628c2c90dd22e6355edd47166db227e9ad1"
    ),
    "_session_owner_reader": ("5f69fcb81ff610e45cffd418387471a53c8dd71585eee1068d901d69ec5722e5"),
    "_android_id_generator": ("5ca6d2beba8150d7e6239958c4363a462194e468148a195f8f17a23218497044"),
    "_strict_loader": "93d263d64cd3225051c00cb69a9560dfd31f0183827446c816428f92a05ca478",
    "_verifier": "09876ad43f4403b5145739d385d1dd4f561bd4b6f2243bb6762309185c870191",
    "_bootstrapper": "af1366949d35d0b2c49f8251b39795096f1e8325e0ca3261e0e5a883fb29861e",
}

_FORBIDDEN_IMPORT_FRAGMENTS = {
    "notebooklm.auth",
    "notebooklm.client",
    "notebooklm._runtime",
    "notebooklm._auth.cookies",
    "notebooklm._auth.keepalive",
    "notebooklm._auth.master_token",
    "notebooklm._auth.master_token_file",
    "notebooklm._auth.profile_migration",
    "notebooklm._auth.recovery",
    "notebooklm._auth.storage",
    "notebooklm.cli",
}
_FORBIDDEN_NAMES = {
    "MasterTokenFile",
    "NotebookLMClient",
    "Protocol",
    "StorageLockManager",
    "__import__",
    "eval",
    "exec",
    "getattr",
    "globals",
    "import_module",
    "locals",
    "open",
    "setattr",
}
_FIELDS = {"_mint_service", "_store", "_bootstrap_lock", "_verifier"}


def _tree(path: Path = MODULE_PATH) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _class(tree: ast.Module) -> ast.ClassDef:
    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "MasterTokenBootstrapper"
    ]
    assert len(matches) == 1
    return matches[0]


def _methods(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        node.name: node
        for node in _class(tree).body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _functions(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _semantic_hash(node: ast.AST) -> str:
    payload = ast.dump(node, include_attributes=False).encode()
    return hashlib.sha256(payload).hexdigest()


class _ImportCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.scope = "module"
        self.records: list[ImportRecord] = []

    def _deferred(self, node: ast.AST, scope: str) -> None:
        prior = self.scope
        self.scope = scope
        self.generic_visit(node)
        self.scope = prior

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._deferred(node, "function")

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._deferred(node, "class")

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._deferred(node, "lambda")

    def visit_Import(self, node: ast.Import) -> None:
        self.records.extend((self.scope, 0, item.name, "", item.asname) for item in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.records.extend(
            (self.scope, node.level, node.module or "", item.name, item.asname)
            for item in node.names
        )


def _imports(tree: ast.AST) -> list[ImportRecord]:
    collector = _ImportCollector()
    collector.visit(tree)
    return collector.records


def _boundary_violations(source: str) -> set[str]:
    tree = ast.parse(source)
    parents: dict[ast.AST, ast.AST] = {
        child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
    }
    violations: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                if any(part in item.name for part in _FORBIDDEN_IMPORT_FRAGMENTS):
                    violations.add(f"import:{item.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            rendered = "." * node.level + module
            if any(
                part.rsplit(".", 1)[-1] in module.split(".") for part in _FORBIDDEN_IMPORT_FRAGMENTS
            ):
                violations.add(f"import:{rendered}")
            if any(item.name in _FORBIDDEN_NAMES for item in node.names):
                violations.add(f"import-name:{rendered}")
        elif isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            violations.add(f"name:{node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_NAMES:
            violations.add(f"attribute:{node.attr}")
        elif isinstance(node, ast.Global | ast.Nonlocal) and set(node.names) & _FIELDS:
            violations.add("scope-rebinding")
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Store | ast.Del)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        ):
            owner = parents.get(node)
            while owner is not None and not isinstance(
                owner, ast.FunctionDef | ast.AsyncFunctionDef
            ):
                owner = parents.get(owner)
            if not (
                isinstance(owner, ast.FunctionDef | ast.AsyncFunctionDef)
                and owner.name == "__init__"
                and node.attr in _FIELDS
                and isinstance(node.ctx, ast.Store)
            ):
                violations.add(f"self-state:{node.attr}")
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Load)
            and isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "self"
            and node.value.attr in {"_store", "_mint_service"}
            and node.attr != "path"
        ):
            parent = parents.get(node)
            direct = isinstance(parent, ast.Call) and parent.func is node
            offload = (
                isinstance(parent, ast.Call)
                and bool(parent.args)
                and parent.args[0] is node
                and ast.unparse(parent.func) == "asyncio.to_thread"
                and node.value.attr == "_store"
                and node.attr == "write_master_token"
            )
            if not direct and not offload:
                violations.add(f"bound-capability:{node.value.attr}.{node.attr}")
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Load)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
            and node.attr == "_verifier"
        ):
            parent = parents.get(node)
            if not (isinstance(parent, ast.Call) and parent.func is node):
                violations.add("callback-capability:_verifier")
    return violations


def _composition_reference_violations(source: str) -> set[str]:
    tree = ast.parse(source)
    parents: dict[ast.AST, ast.AST] = {
        child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
    }
    violations: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for item in node.names:
                if item.name == "MasterTokenBootstrapper" and item.asname is not None:
                    violations.add("aliased-import")
        elif isinstance(node, ast.Attribute) and node.attr == "MasterTokenBootstrapper":
            violations.add("qualified-reference")
        elif isinstance(node, ast.Constant) and node.value == "MasterTokenBootstrapper":
            violations.add("dynamic-name")
        elif isinstance(node, ast.Name) and node.id == "MasterTokenBootstrapper":
            parent = parents.get(node)
            direct_constructor = (
                isinstance(parent, ast.Call)
                and parent.func is node
                and isinstance(node.ctx, ast.Load)
            )
            return_annotation = (
                isinstance(parent, ast.FunctionDef | ast.AsyncFunctionDef)
                and parent.returns is node
                and parent.name == "_bootstrapper"
            )
            if not direct_constructor and not return_annotation:
                violations.add(f"capability-reference:{type(node.ctx).__name__}")
    return violations


def test_imports_internal_values_api_and_four_field_state_are_exact() -> None:
    tree = _tree()
    assert _imports(tree) == _EXPECTED_IMPORTS
    top_names = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert top_names == {"_BootstrapError", "BootstrapOutcome", "MasterTokenBootstrapper"}
    assert not any(
        isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
        for node in tree.body
    )
    assert tuple(
        (member.name, member.value) for member in master_token_bootstrap.BootstrapOutcome
    ) == (
        ("MINTED", "minted"),
        ("PRESENT_AFTER_WAIT", "present_after_wait"),
        ("PRESENT_ON_ENTRY", "present_on_entry"),
        ("NO_TOKEN", "no_token"),
    )
    cls = master_token_bootstrap.MasterTokenBootstrapper
    assert str(inspect.signature(cls)) == (
        "(*, mint_service: 'MintService', store: 'ProfileStore', bootstrap_lock: 'FileLock', "
        "verifier: 'Callable[[Path], Awaitable[int]]') -> 'None'"
    )
    assert str(inspect.signature(cls.assert_account_writable)) == (
        "(self, *, email: 'str', force: 'bool' = False, "
        "session_owner_reader: 'Callable[[Path], str | None]') -> 'None'"
    )
    assert str(inspect.signature(cls.bootstrap_from_oauth_token)) == (
        "(self, *, email: 'str', oauth_token: 'str', android_id: 'str | None' = None, "
        "verify: 'bool' = True, force: 'bool' = False, session_owner_reader: "
        "'Callable[[Path], str | None]', android_id_generator: 'Callable[[], str]') -> 'int'"
    )
    assert str(inspect.signature(cls.remint_from_stored_token)) == (
        "(self, *, strict_loader: 'Callable[[Path], httpx.Cookies]') -> 'httpx.Cookies'"
    )
    assert str(inspect.signature(cls.bootstrap_storage)) == (
        "(self, *, strict_loader: 'Callable[[Path], httpx.Cookies]') -> 'BootstrapOutcome'"
    )
    value = cls(
        mint_service=object(),  # type: ignore[arg-type]
        store=object(),  # type: ignore[arg-type]
        bootstrap_lock=object(),  # type: ignore[arg-type]
        verifier=object(),  # type: ignore[arg-type]
    )
    assert set(value.__dict__) == _FIELDS
    assert _class(tree).bases == []
    assert not any(
        isinstance(node, ast.ClassDef)
        and any(ast.unparse(base).endswith("Protocol") for base in node.bases)
        for node in tree.body
    )


def test_semantic_method_and_adapter_shapes_are_exact() -> None:
    methods = _methods(_tree())
    assert set(methods) == set(_METHOD_HASHES)
    assert {name: _semantic_hash(node) for name, node in methods.items()} == _METHOD_HASHES
    functions = _functions(_tree(ADAPTER_PATH))
    assert {name: _semantic_hash(functions[name]) for name in _ADAPTER_HASHES} == _ADAPTER_HASHES


def test_composition_owner_provenance_and_exports_are_exact() -> None:
    owners: set[str] = set()
    for path in SRC_ROOT.rglob("*.py"):
        tree = _tree(path)

        class Collector(ast.NodeVisitor):
            def __init__(self, label: str) -> None:
                self.label = label
                self.stack: list[str] = []

            def _scope(self, node: ast.AST, name: str) -> None:
                self.stack.append(name)
                self.generic_visit(node)
                self.stack.pop()

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self._scope(node, node.name)

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                self._scope(node, node.name)

            def visit_Call(self, node: ast.Call) -> None:
                if isinstance(node.func, ast.Name) and node.func.id == "MasterTokenBootstrapper":
                    owners.add(f"{self.label}:{'.'.join(self.stack)}")
                self.generic_visit(node)

        Collector(path.relative_to(SRC_ROOT).as_posix()).visit(tree)
    assert owners == {"_auth/master_token.py:_bootstrapper"}
    import_records: list[tuple[str, int, str, str, str | None]] = []
    reference_violations: dict[str, set[str]] = {}
    for path in SRC_ROOT.rglob("*.py"):
        relative = path.relative_to(SRC_ROOT).as_posix()
        tree = _tree(path)
        import_records.extend(
            (relative, node.level, node.module or "", item.name, item.asname)
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for item in node.names
            if item.name == "MasterTokenBootstrapper"
        )
        violations = _composition_reference_violations(path.read_text(encoding="utf-8"))
        if violations:
            reference_violations[relative] = violations
    assert import_records == [
        ("_auth/master_token.py", 1, "master_token_bootstrap", "MasterTokenBootstrapper", None)
    ]
    assert reference_violations == {}
    adapter = _functions(_tree(ADAPTER_PATH))["_bootstrapper"]
    returns = [node for node in ast.walk(adapter) if isinstance(node, ast.Return)]
    assert len(returns) == 1
    assert ast.unparse(returns[0].value) == (
        "MasterTokenBootstrapper(mint_service=MintService(), store=ProfileStore(storage_path), "
        "bootstrap_lock=FileLock(str(_bootstrap_lock_path(storage_path))), verifier=_verifier)"
    )
    for path in (SRC_ROOT / "auth.py", SRC_ROOT / "__init__.py", AUTH_ROOT / "__init__.py"):
        tree = _tree(path)
        imported = {
            item.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for item in node.names
        }
        exported = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        assert imported.isdisjoint({"MasterTokenBootstrapper", "BootstrapOutcome"})
        assert exported.isdisjoint({"MasterTokenBootstrapper", "BootstrapOutcome"})
    assert master_token.BootstrapOutcome is master_token_bootstrap.BootstrapOutcome
    assert not hasattr(auth_facade, "MasterTokenBootstrapper")
    assert not hasattr(auth_facade, "BootstrapOutcome")


def test_order_cancellation_lock_and_ephemeral_callable_contract_is_exact() -> None:
    methods = _methods(_tree())
    oauth = ast.unparse(methods["bootstrap_from_oauth_token"])
    ordered = (
        "asyncio.to_thread(self._resolve_android_id",
        "asyncio.to_thread(self.assert_account_writable",
        "asyncio.to_thread(self._exchange",
        "await self._mint(token)",
        "asyncio.to_thread(self._replace_minted_session, request)",
        "asyncio.to_thread(self._store.write_master_token, token)",
        "await self._verifier(self._store.path)",
    )
    positions = [oauth.index(fragment) for fragment in ordered]
    assert positions == sorted(positions)
    assert "asyncio.shield" not in oauth
    assert oauth.count("asyncio.to_thread") == 5
    assert "del oauth_token, session_owner_reader, android_id_generator" in oauth

    remint = ast.unparse(methods["remint_from_stored_token"])
    assert remint.count("asyncio.to_thread") == 3
    assert (
        remint.index("asyncio.to_thread(self._read_master_token)")
        < remint.index("await self._mint(token)")
        < remint.index("asyncio.to_thread(self._replace_minted_session, request)")
        < remint.index("asyncio.to_thread(strict_loader, self._store.path)")
    )
    assert "del strict_loader" in remint

    acquire = ast.unparse(methods["_acquire_bootstrap_lock"])
    assert "self._bootstrap_lock.acquire(blocking=False)" in acquire
    assert "await asyncio.sleep(0.05)" in acquire
    settlement = ast.unparse(methods["_run_remint_to_settlement"])
    assert settlement.count("asyncio.shield(task)") == 2
    assert "while not task.done()" in settlement
    assert "task.exception()" in settlement
    storage = ast.unparse(methods["bootstrap_storage"])
    assert storage.index("storage_path.exists()") < storage.index("token_path.exists()")
    assert storage.count("storage_path.exists()") == 2
    assert storage.count("token_path.exists()") == 2
    assert storage.index("self._bootstrap_lock.release()") < storage.index("logger.debug")
    assert (
        "logger.debug('bootstrap_storage_from_master_token(%s) -> %s', storage_path, outcome)"
        in storage
    )


def test_cookie_projection_store_calls_and_error_boundary_are_exact() -> None:
    methods = _methods(_tree())
    projection = methods["_minted_session_request"]
    cookie_calls = [
        node
        for node in ast.walk(projection)
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "Cookie"
    ]
    assert len(cookie_calls) == 1
    assert [(item.arg, ast.unparse(item.value)) for item in cookie_calls[0].keywords] == [
        ("name", "cookie.name"),
        ("value", "cast(str, cookie.value)"),
        ("domain", "cookie.domain"),
        ("path", "cookie.path or '/'"),
        ("expires", "cookie.expires"),
        ("http_only", "cookie_is_http_only(cookie)"),
        ("secure", "cookie.secure"),
        ("same_site", "'None'"),
    ]
    request_calls = [
        node
        for node in ast.walk(projection)
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "MintedSessionWriteRequest"
    ]
    assert len(request_calls) == 1
    assert [ast.unparse(arg) for arg in request_calls[0].args] == [
        "cookies",
        "email",
        "force",
        "refuse_unknown_owner",
    ]
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert _boundary_violations(source) == set()
    assert source.count("self._store = store") == 1
    assert source.count("self._mint_service = mint_service") == 1
    assert "MasterTokenFile" not in source
    assert "storage_state_from_jar" not in source
    assert "persist_minted_jar" not in source
    assert "CookieJar.from_httpx" not in source
    assert "logger.info" not in source
    assert "logger.warning" not in source
    assert "logger.error" not in source


@pytest.mark.parametrize(
    "source",
    [
        "from .master_token_file import MasterTokenFile as TokenFile\n",
        "def later():\n    from .storage import write_master_token\n",
        "import importlib\nmodule = importlib.import_module('notebooklm._auth.' + 'storage')\n",
        "callback = getattr(store, 'write_' + 'master_token')\n",
        "class X:\n    def f(self):\n        callback = self._store.write_master_token\n",
        "class X:\n    def f(self, consume):\n        consume(self._verifier)\n",
        "def deco(value): return value\n@deco(MasterTokenFile)\ndef later(): pass\n",
        "def later(value=MasterTokenFile): pass\n",
        "class Later(MasterTokenFile): pass\n",
        "items = [getattr(value, 'write_master_token') for value in values]\n",
        "run = lambda value: getattr(value, 'read_master_token')\n",
        "class X:\n    def f(self, values):\n        for self._store in values:\n            pass\n",
        "class X:\n    def f(self, value):\n        self._token = value\n",
        "class X:\n    def f(self):\n        global _store\n        _store = object()\n",
    ],
)
def test_boundary_detector_bites_alias_dynamic_deferred_and_escape_shapes(source: str) -> None:
    assert _boundary_violations(source)


@pytest.mark.parametrize(
    "source",
    [
        "from .master_token_bootstrap import MasterTokenBootstrapper as Bootstrap\n",
        "Alias = MasterTokenBootstrapper\n",
        "factory = provider.MasterTokenBootstrapper\n",
        "factory = getattr(provider, 'MasterTokenBootstrapper')\n",
        "@decorate(MasterTokenBootstrapper)\ndef later(): pass\n",
        "def later(value=MasterTokenBootstrapper): pass\n",
        "class Later(MasterTokenBootstrapper): pass\n",
        "values = [MasterTokenBootstrapper for _ in items]\n",
        "later = lambda: MasterTokenBootstrapper\n",
        "for MasterTokenBootstrapper in values:\n    pass\n",
        "def later():\n    global MasterTokenBootstrapper\n    MasterTokenBootstrapper = object\n",
    ],
)
def test_composition_reference_guard_bites_every_deferred_and_rebinding_shape(
    source: str,
) -> None:
    assert _composition_reference_violations(source)


def test_only_logger_is_module_state_and_instance_state_never_grows() -> None:
    tree = _tree()
    assignments = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert assignments == {"logger"}
    methods = _methods(tree)
    for name, method in methods.items():
        assigned = {
            node.attr
            for node in ast.walk(method)
            if isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Store | ast.Del)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        }
        assert assigned == (_FIELDS if name == "__init__" else set())
