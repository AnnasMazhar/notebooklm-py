"""Discover fail-closed adapter JSON result sites.

This module deliberately inventories *terminal sites*, not public model names.
The compatibility contract can then require every discovered site to have a
reviewed disposition (projection, delegated projection, or non-model result).
That closes a different gap from projection-shape checks: a newly added result
site cannot stay invisible merely because nobody added a projection row for it.

The inventory is source-derived and contains no line numbers.  A site's stable
identity combines its channel, module path, qualified handler, sink kind, and a
semantic hash of the emitted expression.  Duplicate expressions are retained
with an occurrence number, so copying an existing return still changes the
closed-world set.
"""

from __future__ import annotations

import ast
import hashlib
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path, PurePath
from typing import Literal, cast

Channel = Literal["cli --json", "mcp tool result", "mcp auxiliary response", "rest response"]
SiteRole = Literal["projection", "error-projection", "forwarding-infrastructure"]

_REST_DECORATORS = frozenset({"delete", "get", "patch", "post", "put"})
_CLI_ERROR_CALLS = frozenset(
    {"_output_error", "emit_cancelled_and_exit", "json_error_response", "output_error"}
)
_CLI_FORWARDING_OWNERS = frozenset(
    {
        ("notebooklm/cli/helpers.py", "json_error_response"),
        ("notebooklm/cli/helpers.py", "json_output_response"),
        ("notebooklm/cli/rendering.py", "json_error_response"),
    }
)
_CLI_DIRECT_JSON_OWNERS = frozenset(
    {
        ("notebooklm/cli/error_handler.py", "_output_error"),
        ("notebooklm/cli/error_handler.py", "emit_cancelled_and_exit"),
        ("notebooklm/cli/rendering.py", "json_output_response"),
    }
)


@dataclass(frozen=True)
class AdapterJsonSink:
    """One source-level adapter result site."""

    id: str
    channel: Channel
    path: str
    owner: str
    kind: str
    site_role: SiteRole
    expression_fingerprint: str
    owner_fingerprint: str

    def to_dict(self) -> dict[str, str]:
        """Return the deterministic baseline representation."""
        return asdict(self)


@dataclass(frozen=True)
class _Candidate:
    channel: Channel
    path: str
    owner: str
    kind: str
    site_role: SiteRole
    expression: ast.AST | None
    owner_node: ast.FunctionDef | ast.AsyncFunctionDef
    lineno: int
    col_offset: int


def _stable_ast_shape(node: ast.AST | None) -> tuple[object, ...]:
    """Return a semantic AST tuple without locations or interpreter-only fields."""
    if node is None:
        return ("None",)

    ignored_fields = {"ctx", "kind", "type_comment", "type_params"}
    fields: list[tuple[str, object]] = []
    for field_name, value in ast.iter_fields(node):
        if field_name in ignored_fields:
            continue
        if field_name == "body" and isinstance(
            node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
        ):
            body = list(value)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body = body[1:]
            value = body
        if isinstance(value, ast.AST):
            normalized: object = _stable_ast_shape(value)
        elif isinstance(value, list):
            normalized = tuple(
                _stable_ast_shape(item) if isinstance(item, ast.AST) else item for item in value
            )
        else:
            normalized = value
        fields.append((field_name, normalized))
    return (type(node).__name__, tuple(fields))


def _fingerprint(node: ast.AST | None) -> str:
    payload = repr(_stable_ast_shape(node)).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _call_owner_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
        return node.func.value.id
    return None


def _json_dumps_argument(
    node: ast.Call,
    *,
    module_aliases: frozenset[str],
    dumps_aliases: frozenset[str],
) -> ast.expr | None:
    qualified = _call_name(node) == "dumps" and _call_owner_name(node) in module_aliases
    direct = isinstance(node.func, ast.Name) and node.func.id in dumps_aliases
    if not qualified and not direct:
        return None
    return node.args[0] if node.args else None


def _direct_json_argument(
    node: ast.Call,
    *,
    module_aliases: frozenset[str],
    dumps_aliases: frozenset[str],
) -> ast.expr | None:
    if _call_name(node) not in {"echo", "print", "write"}:
        return None
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            argument = _json_dumps_argument(
                child,
                module_aliases=module_aliases,
                dumps_aliases=dumps_aliases,
            )
            if argument is not None:
                return argument
    return None


def _decorator_name(node: ast.expr) -> str | None:
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Attribute):
        return target.attr
    if isinstance(target, ast.Name):
        return target.id
    return None


def _decorator_owner(node: ast.expr) -> str | None:
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
        return target.value.id
    return None


def _is_mcp_tool(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        _decorator_owner(decorator) == "mcp" and _decorator_name(decorator) == "tool"
        for decorator in node.decorator_list
    )


def _is_mcp_custom_route(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        _decorator_owner(decorator) == "mcp" and _decorator_name(decorator) == "custom_route"
        for decorator in node.decorator_list
    )


def _is_rest_handler(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        _decorator_owner(decorator) in {"app", "router"}
        and _decorator_name(decorator) in _REST_DECORATORS
        for decorator in node.decorator_list
    )


def _is_rest_exception_handler(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        _decorator_owner(decorator) == "app" and _decorator_name(decorator) == "exception_handler"
        for decorator in node.decorator_list
    )


def _is_rest_http_middleware(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        _decorator_owner(decorator) == "app" and _decorator_name(decorator) == "middleware"
        for decorator in node.decorator_list
    )


class _FunctionCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.rows: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
        self._scope: list[str] = []

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._scope.append(node.name)
        self.rows.append((".".join(self._scope), node))
        self.generic_visit(node)
        self._scope.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()


class _OwnedNodeVisitor(ast.NodeVisitor):
    """Visit one function body without entering nested function/class scopes."""

    def __init__(self, owner: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.owner = owner

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node is self.owner:
            self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if node is self.owner:
            self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return


class _CliSinkVisitor(_OwnedNodeVisitor):
    def __init__(
        self,
        owner: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        path: str,
        qualname: str,
        json_module_aliases: frozenset[str],
        json_dumps_aliases: frozenset[str],
    ) -> None:
        super().__init__(owner)
        self.path = path
        self.qualname = qualname
        self.json_module_aliases = json_module_aliases
        self.json_dumps_aliases = json_dumps_aliases
        self.rows: list[_Candidate] = []

    def visit_Call(self, node: ast.Call) -> None:
        call_name = _call_name(node)
        if call_name == "json_output_response" or call_name in _CLI_ERROR_CALLS:
            forwarding = (self.path, self.qualname) in _CLI_FORWARDING_OWNERS
            if call_name == "json_output_response":
                expression = node.args[0] if node.args else None
                site_role: SiteRole = "forwarding-infrastructure" if forwarding else "projection"
            else:
                expression = _error_extra_expression(node, cast(str, call_name))
                site_role = "forwarding-infrastructure" if forwarding else "error-projection"
            self.rows.append(
                _Candidate(
                    channel="cli --json",
                    path=self.path,
                    owner=self.qualname,
                    kind=cast(str, call_name),
                    site_role=site_role,
                    expression=expression,
                    owner_node=self.owner,
                    lineno=node.lineno,
                    col_offset=node.col_offset,
                )
            )
        direct_expression = _direct_json_argument(
            node,
            module_aliases=self.json_module_aliases,
            dumps_aliases=self.json_dumps_aliases,
        )
        if direct_expression is not None:
            direct_forwarding = (self.path, self.qualname) in _CLI_DIRECT_JSON_OWNERS
            self.rows.append(
                _Candidate(
                    channel="cli --json",
                    path=self.path,
                    owner=self.qualname,
                    kind="direct-json-emission",
                    site_role=("forwarding-infrastructure" if direct_forwarding else "projection"),
                    expression=direct_expression,
                    owner_node=self.owner,
                    lineno=node.lineno,
                    col_offset=node.col_offset,
                )
            )
        self.generic_visit(node)


class _ReturnSinkVisitor(_OwnedNodeVisitor):
    def __init__(
        self,
        owner: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        channel: Channel,
        path: str,
        qualname: str,
    ) -> None:
        super().__init__(owner)
        self.channel = channel
        self.path = path
        self.qualname = qualname
        self.rows: list[_Candidate] = []

    def visit_Return(self, node: ast.Return) -> None:
        self.rows.append(
            _Candidate(
                channel=self.channel,
                path=self.path,
                owner=self.qualname,
                kind="return",
                site_role="projection",
                expression=node.value,
                owner_node=self.owner,
                lineno=node.lineno,
                col_offset=node.col_offset,
            )
        )
        self.generic_visit(node)


class _McpErrorFunnelVisitor(_OwnedNodeVisitor):
    """Record each registered tool's central ``mcp_errors`` serialization boundary."""

    def __init__(
        self,
        owner: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        path: str,
        qualname: str,
    ) -> None:
        super().__init__(owner)
        self.path = path
        self.qualname = qualname
        self.rows: list[_Candidate] = []

    def _visit_with(self, node: ast.With | ast.AsyncWith) -> None:
        for item in node.items:
            expression = item.context_expr
            if isinstance(expression, ast.Call) and _call_name(expression) == "mcp_errors":
                self.rows.append(
                    _Candidate(
                        channel="mcp tool result",
                        path=self.path,
                        owner=self.qualname,
                        kind="tool-error-funnel",
                        site_role="error-projection",
                        expression=expression,
                        owner_node=self.owner,
                        lineno=node.lineno,
                        col_offset=node.col_offset,
                    )
                )
        self.generic_visit(node)

    visit_With = _visit_with
    visit_AsyncWith = _visit_with


class _JsonResponseSinkVisitor(_ReturnSinkVisitor):
    """Collect only JSONResponse terminals inside an MCP auxiliary HTTP route."""

    def __init__(
        self,
        owner: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        channel: Channel,
        path: str,
        qualname: str,
        site_role: SiteRole = "projection",
    ) -> None:
        super().__init__(owner, channel=channel, path=path, qualname=qualname)
        self._site_role = site_role

    def visit_Return(self, node: ast.Return) -> None:
        value = node.value
        if isinstance(value, ast.Call) and _call_name(value) == "JSONResponse":
            expression = value.args[0] if value.args else _keyword_value(value, "content")
            if expression is None:
                self.generic_visit(node)
                return
            self.rows.append(
                _Candidate(
                    channel=self.channel,
                    path=self.path,
                    owner=self.qualname,
                    kind="json-response-return",
                    site_role=self._site_role,
                    expression=expression,
                    owner_node=self.owner,
                    lineno=node.lineno,
                    col_offset=node.col_offset,
                )
            )
        self.generic_visit(node)


class _NamedResponseSinkVisitor(_ReturnSinkVisitor):
    """Collect returns routed through one of the named JSON response helpers."""

    def visit_Return(self, node: ast.Return) -> None:
        value = node.value
        if isinstance(value, ast.Call) and _call_name(value) in {
            "error_response",
            "http_error_response",
        }:
            self.rows.append(
                _Candidate(
                    channel=self.channel,
                    path=self.path,
                    owner=self.qualname,
                    kind="json-helper-return",
                    site_role="projection",
                    expression=value,
                    owner_node=self.owner,
                    lineno=node.lineno,
                    col_offset=node.col_offset,
                )
            )
        self.generic_visit(node)


def _python_files(path: Path) -> Iterator[Path]:
    yield from sorted(path.rglob("*.py"))


def _relative_source_path(path: PurePath, source_root: PurePath) -> str:
    return path.relative_to(source_root).as_posix()


def _keyword_value(node: ast.Call, name: str) -> ast.expr | None:
    return next((keyword.value for keyword in node.keywords if keyword.arg == name), None)


def _error_extra_expression(node: ast.Call, call_name: str) -> ast.expr | None:
    """Return only the dynamically merged error projection, not prose/code fields."""
    keyword = _keyword_value(node, "extra")
    if keyword is not None:
        return keyword
    positional_index = 2 if call_name == "json_error_response" else 4
    if len(node.args) > positional_index:
        return node.args[positional_index]
    return None


def _json_import_aliases(tree: ast.Module) -> tuple[frozenset[str], frozenset[str]]:
    modules: set[str] = set()
    dumps: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "json":
                    modules.add(alias.asname or "json")
        elif isinstance(node, ast.ImportFrom) and node.module == "json":
            for alias in node.names:
                if alias.name in {"dumps", "*"}:
                    dumps.add(alias.asname or "dumps")
    return frozenset(modules), frozenset(dumps)


def _discover_candidates(source_root: Path) -> list[_Candidate]:
    package_root = source_root / "notebooklm"
    candidates: list[_Candidate] = []

    channel_roots: tuple[tuple[Channel, Path], ...] = (
        ("cli --json", package_root / "cli"),
        ("mcp tool result", package_root / "mcp"),
        ("mcp auxiliary response", package_root / "mcp"),
        ("rest response", package_root / "server"),
    )
    for channel, root in channel_roots:
        for path in _python_files(root):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            json_module_aliases, json_dumps_aliases = _json_import_aliases(tree)
            functions = _FunctionCollector()
            functions.visit(tree)
            relative_path = _relative_source_path(path, source_root)
            for qualname, function in functions.rows:
                visitor: _CliSinkVisitor | _ReturnSinkVisitor
                if channel == "cli --json":
                    visitor = _CliSinkVisitor(
                        function,
                        path=relative_path,
                        qualname=qualname,
                        json_module_aliases=json_module_aliases,
                        json_dumps_aliases=json_dumps_aliases,
                    )
                elif channel == "mcp tool result":
                    if not _is_mcp_tool(function):
                        continue
                    visitor = _ReturnSinkVisitor(
                        function,
                        channel=channel,
                        path=relative_path,
                        qualname=qualname,
                    )
                    errors = _McpErrorFunnelVisitor(
                        function,
                        path=relative_path,
                        qualname=qualname,
                    )
                    errors.visit(function)
                    candidates.extend(errors.rows)
                elif channel == "mcp auxiliary response":
                    if not _is_mcp_custom_route(function):
                        continue
                    visitor = _ReturnSinkVisitor(
                        function,
                        channel=channel,
                        path=relative_path,
                        qualname=qualname,
                    )
                else:
                    if _is_rest_handler(function) or _is_rest_exception_handler(function):
                        visitor = _ReturnSinkVisitor(
                            function,
                            channel=channel,
                            path=relative_path,
                            qualname=qualname,
                        )
                    elif _is_rest_http_middleware(function):
                        visitor = _NamedResponseSinkVisitor(
                            function,
                            channel=channel,
                            path=relative_path,
                            qualname=qualname,
                        )
                    elif relative_path == "notebooklm/server/_errors.py" and qualname in {
                        "error_response",
                        "http_error_response",
                    }:
                        visitor = _JsonResponseSinkVisitor(
                            function,
                            channel=channel,
                            path=relative_path,
                            qualname=qualname,
                            site_role="forwarding-infrastructure",
                        )
                    else:
                        continue
                visitor.visit(function)
                candidates.extend(visitor.rows)
    return candidates


def discover_adapter_json_sinks(source_root: Path) -> list[AdapterJsonSink]:
    """Return every discovered terminal adapter result site in stable order."""
    candidates = _discover_candidates(source_root)
    candidates.sort(
        key=lambda row: (
            row.channel,
            row.path,
            row.owner,
            row.kind,
            row.lineno,
            row.col_offset,
        )
    )

    duplicate_counts: Counter[tuple[str, ...]] = Counter()
    rows: list[AdapterJsonSink] = []
    for candidate in candidates:
        expression_fingerprint = _fingerprint(candidate.expression)
        identity = (
            candidate.channel,
            candidate.path,
            candidate.owner,
            candidate.kind,
            candidate.site_role,
            expression_fingerprint,
        )
        duplicate_counts[identity] += 1
        occurrence = duplicate_counts[identity]
        sink_id = ":".join((*identity, str(occurrence)))
        rows.append(
            AdapterJsonSink(
                id=sink_id,
                channel=candidate.channel,
                path=candidate.path,
                owner=candidate.owner,
                kind=candidate.kind,
                site_role=candidate.site_role,
                expression_fingerprint=expression_fingerprint,
                owner_fingerprint=_fingerprint(candidate.owner_node),
            )
        )
    return rows


def fingerprint_adapter_helpers(source_root: Path, helper_symbols: Iterable[str]) -> dict[str, str]:
    """Fingerprint explicitly delegated helper functions by qualified symbol."""
    requested = set(helper_symbols)
    fingerprints: dict[str, str] = {}
    for path in _python_files(source_root / "notebooklm"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        functions = _FunctionCollector()
        functions.visit(tree)
        module_path = path.relative_to(source_root).with_suffix("")
        module_parts = list(module_path.parts)
        if module_parts[-1] == "__init__":
            module_parts.pop()
        module = ".".join(module_parts)
        for qualname, function in functions.rows:
            symbol = f"{module}.{qualname}"
            if symbol in requested:
                fingerprints[symbol] = _fingerprint(function)
    missing = sorted(requested - set(fingerprints))
    if missing:
        raise ValueError(f"unresolved delegated adapter projection helpers: {missing}")
    return {symbol: fingerprints[symbol] for symbol in sorted(fingerprints)}


def fingerprint_adapter_evidence(
    source_root: Path,
    required_ast_fragments: Mapping[str, Iterable[str]],
) -> dict[str, str]:
    """Fingerprint named function scopes and require normalized AST evidence fragments."""
    requirements = {
        symbol: tuple(fragments) for symbol, fragments in required_ast_fragments.items()
    }
    if any(
        not symbol
        or not fragments
        or any(not isinstance(fragment, str) or not fragment for fragment in fragments)
        for symbol, fragments in requirements.items()
    ):
        raise ValueError("adapter evidence requires named scopes and non-empty AST fragments")

    fingerprints = fingerprint_adapter_helpers(source_root, requirements)
    normalized_scopes: dict[str, str] = {}
    for path in _python_files(source_root / "notebooklm"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        functions = _FunctionCollector()
        functions.visit(tree)
        module_path = path.relative_to(source_root).with_suffix("")
        module_parts = list(module_path.parts)
        if module_parts[-1] == "__init__":
            module_parts.pop()
        module = ".".join(module_parts)
        for qualname, function in functions.rows:
            symbol = f"{module}.{qualname}"
            if symbol in requirements:
                normalized_scopes[symbol] = ast.unparse(function)

    missing_fragments = {
        symbol: [fragment for fragment in fragments if fragment not in normalized_scopes[symbol]]
        for symbol, fragments in requirements.items()
    }
    missing_fragments = {
        symbol: fragments for symbol, fragments in missing_fragments.items() if fragments
    }
    if missing_fragments:
        raise ValueError(f"missing required adapter evidence AST fragments: {missing_fragments}")
    return fingerprints


def assert_no_unreviewed_direct_json_emissions(sinks: Iterable[AdapterJsonSink]) -> None:
    """Reject CLI stdout JSON that bypasses the reviewed rendering funnels."""
    violations = sorted(
        sink.id
        for sink in sinks
        if sink.kind == "direct-json-emission" and sink.site_role != "forwarding-infrastructure"
    )
    if violations:
        raise ValueError(f"unreviewed direct CLI JSON emissions: {violations}")


def _is_reviewed_oauth_login_route(relative: str, node: ast.Call) -> bool:
    if relative != "notebooklm/mcp/_oauth.py" or len(node.args) < 2:
        return False
    path_arg, handler_arg = node.args[:2]
    methods = _keyword_value(node, "methods")
    return (
        isinstance(path_arg, ast.Constant)
        and path_arg.value == "/login"
        and isinstance(handler_arg, ast.Attribute)
        and isinstance(handler_arg.value, ast.Name)
        and handler_arg.value.id == "self"
        and handler_arg.attr == "_login"
        and isinstance(methods, ast.List)
        and [item.value for item in methods.elts if isinstance(item, ast.Constant)]
        == ["GET", "POST"]
        and len(methods.elts) == 2
    )


def assert_supported_adapter_registrations(source_root: Path) -> None:
    """Reject tool/route registration styles the terminal scanner cannot follow."""
    violations: list[str] = []
    roots = (
        (
            source_root / "notebooklm" / "mcp",
            "mcp",
            {"add_tool", "tool", "add_custom_route", "custom_route"},
        ),
        (
            source_root / "notebooklm" / "server",
            "router",
            {
                "add_api_route",
                "api_route",
                "add_route",
                "delete",
                "get",
                "patch",
                "post",
                "put",
                "route",
            },
        ),
        (
            source_root / "notebooklm" / "server",
            "app",
            {
                "add_api_route",
                "add_exception_handler",
                "api_route",
                "delete",
                "exception_handler",
                "get",
                "middleware",
                "patch",
                "post",
                "put",
                "route",
            },
        ),
    )
    for root, owner_name, rejected_names in roots:
        for path in _python_files(root):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            decorator_calls = {
                id(decorator)
                for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                for decorator in node.decorator_list
                if isinstance(decorator, ast.Call)
            }
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if _call_owner_name(node) != owner_name or _call_name(node) not in rejected_names:
                    continue
                if (
                    owner_name == "mcp"
                    and _call_name(node) in {"custom_route", "tool"}
                    and id(node) in decorator_calls
                ):
                    continue
                if owner_name == "app" and id(node) in decorator_calls:
                    continue
                if owner_name == "router" and id(node) in decorator_calls:
                    continue
                relative = _relative_source_path(path, source_root)
                violations.append(f"{relative}:{node.lineno}:{owner_name}.{_call_name(node)}")
    # A Starlette Route(...) constructor is another externally reachable route
    # registration style.  The OAuth login route is the one reviewed non-JSON
    # exception; any new constructor would otherwise bypass decorator discovery.
    for path in _python_files(source_root / "notebooklm" / "mcp"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "Route":
                continue
            relative = _relative_source_path(path, source_root)
            reviewed_oauth_login = _is_reviewed_oauth_login_route(relative, node)
            if not reviewed_oauth_login:
                violations.append(f"{relative}:{node.lineno}:Route")
    if violations:
        raise ValueError(f"unsupported dynamic adapter registrations: {sorted(violations)}")


def assert_exact_sink_dispositions(
    sinks: Iterable[AdapterJsonSink],
    dispositions: dict[str, object],
    *,
    known_projection_ids: Iterable[str] = (),
    known_helper_symbols: Iterable[str] = (),
) -> None:
    """Require one well-formed reviewed disposition for every discovered sink.

    The accepted dispositions are intentionally discriminated.  A row must
    reference one or more compatibility projection specs, delegate to one or
    more separately fingerprinted helpers, or explain why no public model can
    reach it.  Forwarding infrastructure is the only fourth case.
    """
    sink_rows = {sink.id: sink for sink in sinks}
    discovered = set(sink_rows)
    reviewed = set(dispositions)
    missing = sorted(discovered - reviewed)
    stale = sorted(reviewed - discovered)
    if missing or stale:
        raise ValueError(
            f"adapter JSON sink dispositions are not exact: missing={missing}, stale={stale}"
        )

    known = set(known_projection_ids)
    known_helpers = set(known_helper_symbols)
    discriminator_keys = {
        "projection_ids",
        "delegated_helpers",
        "non_public_model_reason",
        "infrastructure_reason",
    }
    for sink_id in sorted(discovered):
        disposition = dispositions[sink_id]
        if not isinstance(disposition, dict):
            raise ValueError(f"invalid adapter JSON sink disposition for {sink_id}: not a mapping")
        selected = discriminator_keys & set(disposition)
        if len(selected) != 1:
            raise ValueError(
                f"invalid adapter JSON sink disposition for {sink_id}: "
                f"expected one discriminator, found {sorted(selected)}"
            )
        discriminator = next(iter(selected))
        if sink_rows[sink_id].site_role == "forwarding-infrastructure":
            if discriminator != "infrastructure_reason":
                raise ValueError(
                    f"forwarding adapter JSON sink requires infrastructure_reason: {sink_id}"
                )
        elif discriminator == "infrastructure_reason":
            raise ValueError(f"terminal adapter JSON sink cannot be infrastructure-only: {sink_id}")

        value = disposition[discriminator]
        if discriminator in {"projection_ids", "delegated_helpers"}:
            if (
                not isinstance(value, list)
                or not value
                or not all(isinstance(item, str) and item for item in value)
            ):
                raise ValueError(
                    f"invalid adapter JSON sink disposition for {sink_id}: "
                    f"{discriminator} must be a non-empty string list"
                )
            if discriminator == "projection_ids" and known:
                unknown = sorted(set(value) - known)
                if unknown:
                    raise ValueError(
                        f"unknown compatibility projection ids for {sink_id}: {unknown}"
                    )
            if discriminator == "delegated_helpers" and known_helpers:
                unknown = sorted(set(value) - known_helpers)
                if unknown:
                    raise ValueError(f"unknown delegated helpers for {sink_id}: {unknown}")
        elif not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"invalid adapter JSON sink disposition for {sink_id}: "
                f"{discriminator} must be a non-empty reason"
            )


__all__ = [
    "AdapterJsonSink",
    "assert_exact_sink_dispositions",
    "assert_no_unreviewed_direct_json_emissions",
    "assert_supported_adapter_registrations",
    "discover_adapter_json_sinks",
    "fingerprint_adapter_evidence",
    "fingerprint_adapter_helpers",
]
