"""JSON-envelope compatibility derivation and adapter evidence checks."""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import importlib
import typing
from pathlib import Path, PurePath
from typing import Any

from .json_envelope_specs import _CHANNEL_PROJECTION_SPECS


def _public_model_exports() -> dict[type[Any], list[str]]:
    # Import lazily so compatibility_contracts can re-export this module's gates
    # without a module-initialization cycle.
    from .compatibility_contracts import _public_model_exports as implementation

    return implementation()


def _model_key(cls: type[Any]) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def _valid_dataclass_sample(cls: type[Any]) -> Any:
    from .compatibility_contracts import _valid_dataclass_sample as implementation

    return implementation(cls)


_SECRET_BEARING_PUBLIC_MODELS = frozenset({"notebooklm.auth.AuthTokens"})
_AUTH_TOKENS_ALLOWED_PROJECTIONS = (
    (
        "mcp tool result",
        "mcp.AuthTokens.redacted-server-info-account-identity-contribution",
    ),
    (
        "rest response",
        "rest.AuthTokens.redacted-server-info-account-identity-contribution",
    ),
)
_AUTH_TOKENS_ALLOWED_PROJECTION_IDS = frozenset(
    projection_id for _channel, projection_id in _AUTH_TOKENS_ALLOWED_PROJECTIONS
)
_AUTH_TOKENS_SAFE_CONTRIBUTION_FIELDS = frozenset(
    {"authuser", "account_email", "storage_path", "_profile_session_generation"}
)
_AUTH_TOKENS_SAFE_EMITTED_VALUE_FIELDS = frozenset({"authuser", "account_email"})
_AUTH_TOKENS_CONTROL_CONTRIBUTION_FIELDS = frozenset(
    {"storage_path", "_profile_session_generation"}
)
_REVIEWED_MODEL_CONTRIBUTION_PROPERTIES: dict[str, frozenset[str]] = {}
_AUTH_TOKENS_CREDENTIAL_OUTPUT_FIELDS = frozenset(
    {
        "cookies",
        "csrf_token",
        "session_id",
        "storage_path",
        "cookie_jar",
        "cookie_snapshot",
        "_profile_session_generation",
        "headers",
        "authorization_header",
        "bearer_token",
    }
)
_CHANNEL_ROOTS = {
    "cli --json": "cli",
    "mcp tool result": "mcp",
    "rest response": "server",
}


def _models_in_annotation(
    annotation: object,
    public_models: set[type[Any]],
    *,
    seen: set[type[Any]] | None = None,
) -> set[type[Any]]:
    """Return public dataclasses nested in a return/field annotation."""
    found: set[type[Any]] = set()
    origin = typing.get_origin(annotation)
    if origin is not None:
        for argument in typing.get_args(annotation):
            found.update(_models_in_annotation(argument, public_models, seen=seen))
        return found
    if not isinstance(annotation, type) or not dataclasses.is_dataclass(annotation):
        return found
    seen = set() if seen is None else set(seen)
    if annotation in seen:
        return found
    seen.add(annotation)
    if annotation in public_models:
        found.add(annotation)
    try:
        hints = typing.get_type_hints(annotation.__init__)
    except (NameError, TypeError):
        hints = {field.name: field.type for field in dataclasses.fields(annotation)}
    hints.pop("return", None)
    for field_annotation in hints.values():
        found.update(_models_in_annotation(field_annotation, public_models, seen=seen))
    return found


def _source_module_name(path: Path, source_root: Path) -> tuple[str, str]:
    relative = path.relative_to(source_root).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
        module_name = ".".join(parts)
        return module_name, module_name
    module_name = ".".join(parts)
    return module_name, module_name.rpartition(".")[0]


def _relative_source_path(path: PurePath, source_root: PurePath) -> str:
    """Return a platform-independent repository-relative source path."""
    return path.relative_to(source_root).as_posix()


def _resolved_import_module(node: ast.ImportFrom, package: str) -> str | None:
    if node.level == 0:
        return node.module
    relative = "." * node.level + (node.module or "")
    try:
        return importlib.util.resolve_name(relative, package)
    except (ImportError, ValueError):
        return None


def _serializer_name(call: ast.Call) -> str | None:
    target = call.func
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _annotation_is_secret_value(annotation: ast.expr | None, aliases: set[str]) -> bool:
    if annotation is None:
        return False
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            return _annotation_is_secret_value(
                ast.parse(annotation.value, mode="eval").body, aliases
            )
        except SyntaxError:
            return False
    if isinstance(annotation, ast.Name):
        return annotation.id in aliases
    if isinstance(annotation, ast.Attribute):
        return annotation.attr in aliases
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        return _annotation_is_secret_value(annotation.left, aliases) or _annotation_is_secret_value(
            annotation.right, aliases
        )
    if isinstance(annotation, ast.Subscript):
        wrapper = (
            annotation.value.id
            if isinstance(annotation.value, ast.Name)
            else annotation.value.attr
            if isinstance(annotation.value, ast.Attribute)
            else None
        )
        if wrapper not in {"Annotated", "Optional", "Union"}:
            return False
        elements = (
            annotation.slice.elts if isinstance(annotation.slice, ast.Tuple) else [annotation.slice]
        )
        return any(_annotation_is_secret_value(element, aliases) for element in elements)
    return False


def _secret_serialization_violations(source: str, *, filename: str) -> list[str]:
    """Find unsafe AuthTokens-derived values flowing into adapter serialization sinks."""
    tree = ast.parse(source, filename=filename)
    secret_aliases = {"AuthTokens"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            if alias.name == "AuthTokens":
                secret_aliases.add(alias.asname or alias.name)

    class _ScopeNodes(ast.NodeVisitor):
        def __init__(self, root: ast.AST) -> None:
            self.root = root
            self.rows: list[ast.AST] = []

        def generic_visit(self, node: ast.AST) -> None:
            self.rows.append(node)
            super().generic_visit(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            if node is self.root:
                self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            if node is self.root:
                self.generic_visit(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            if node is self.root:
                self.generic_visit(node)

        def visit_Lambda(self, node: ast.Lambda) -> None:
            if node is self.root:
                self.generic_visit(node)

    scopes: list[ast.AST] = [
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]
    violations: list[str] = []
    for scope in scopes:
        collector = _ScopeNodes(scope)
        collector.visit(scope)
        nodes = collector.rows
        secret_origins: dict[str, set[str]] = {}
        for node in nodes:
            if isinstance(node, ast.arg) and _annotation_is_secret_value(
                node.annotation, secret_aliases
            ):
                secret_origins[node.arg] = {"*"}
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if _annotation_is_secret_value(node.annotation, secret_aliases):
                    secret_origins[node.target.id] = {"*"}

        def origin_fields(expression: ast.expr) -> set[str]:
            if isinstance(expression, ast.Name):
                return set(secret_origins.get(expression.id, ()))  # noqa: B023
            if isinstance(expression, ast.Attribute):
                if expression.attr == "auth":
                    return {"*"}
                base = origin_fields(expression.value)
                if "*" in base:
                    return {expression.attr}
                return base
            if isinstance(expression, ast.Subscript):
                base = origin_fields(expression.value)
                if not base:
                    return set()
                if "*" in base and isinstance(expression.slice, ast.Constant):
                    if isinstance(expression.slice.value, str):
                        return {expression.slice.value}
                return base
            if isinstance(expression, ast.Call):
                target = expression.func
                target_origins: set[str] = set()
                if isinstance(target, ast.Name):
                    if target.id in secret_aliases:
                        return {"*"}
                elif isinstance(target, ast.Attribute) and target.attr == "AuthTokens":
                    return {"*"}
                elif isinstance(target, ast.Attribute):
                    target_origins = origin_fields(target.value)
                return {
                    field
                    for origins in [
                        target_origins,
                        *(origin_fields(argument) for argument in expression.args),
                        *(origin_fields(keyword.value) for keyword in expression.keywords),
                    ]
                    for field in origins
                }
            if isinstance(expression, ast.Dict):
                return {
                    field
                    for item in [
                        *(key for key in expression.keys if key is not None),
                        *expression.values,
                    ]
                    for field in origin_fields(item)
                }
            if isinstance(expression, ast.List | ast.Tuple | ast.Set):
                return {field for element in expression.elts for field in origin_fields(element)}
            return {
                field
                for child in ast.iter_child_nodes(expression)
                if isinstance(child, ast.expr)
                for field in origin_fields(child)
            }

        def merge_target(target: ast.expr, origins: set[str]) -> bool:
            if not origins:
                return False
            names: set[str] = set()

            def collect_names(candidate: ast.expr) -> None:
                if isinstance(candidate, ast.Name):
                    names.add(candidate.id)
                elif isinstance(candidate, ast.Attribute | ast.Subscript):
                    collect_names(candidate.value)
                elif isinstance(candidate, ast.List | ast.Tuple):
                    for element in candidate.elts:
                        collect_names(element)

            collect_names(target)
            if not names:
                return False
            target_changed = False
            for name in names:
                before = set(secret_origins.get(name, ()))  # noqa: B023
                after = before | origins
                if before == after:
                    continue
                secret_origins[name] = after  # noqa: B023
                target_changed = True
            return target_changed

        changed = True
        while changed:
            changed = False
            for node in nodes:
                if isinstance(node, ast.Assign):
                    origins = origin_fields(node.value)
                    for target in node.targets:
                        changed = merge_target(target, origins) or changed
                if isinstance(node, ast.AnnAssign) and node.value is not None:
                    changed = merge_target(node.target, origin_fields(node.value)) or changed
                if isinstance(node, ast.AugAssign):
                    changed = merge_target(node.target, origin_fields(node.value)) or changed
                if isinstance(node, ast.NamedExpr):
                    changed = merge_target(node.target, origin_fields(node.value)) or changed
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"add", "append", "extend", "insert", "update"}
                ):
                    mutation_origins = {
                        field
                        for value in [
                            *node.args,
                            *(keyword.value for keyword in node.keywords),
                        ]
                        for field in origin_fields(value)
                    }
                    changed = merge_target(node.func.value, mutation_origins) or changed

        for node in nodes:
            emitted_expressions: list[ast.expr]
            if isinstance(node, ast.Call):
                if _serializer_name(node) not in {
                    "JSONResponse",
                    "ToolError",
                    "asdict",
                    "dump",
                    "dumps",
                    "json_error_response",
                    "json_output_response",
                    "output_error",
                    "to_jsonable",
                }:
                    continue
                emitted_expressions = [
                    *node.args,
                    *(keyword.value for keyword in node.keywords),
                ]
            elif isinstance(node, ast.Return | ast.Yield | ast.YieldFrom):
                if node.value is None:
                    continue
                if isinstance(node.value, ast.Call) and _serializer_name(node.value) not in {
                    "JSONResponse",
                    "ToolError",
                    "dict",
                    "list",
                    "set",
                    "tuple",
                    "to_jsonable",
                }:
                    # A generic call return is an internal delegation, not proof that its
                    # AuthTokens-valued argument is itself emitted. Reviewed serializers and
                    # container constructors remain recursively checked above and here.
                    continue
                emitted_expressions = [node.value]
            else:
                continue
            origins = {
                field for expression in emitted_expressions for field in origin_fields(expression)
            }
            if "*" in origins or origins - _AUTH_TOKENS_SAFE_EMITTED_VALUE_FIELDS:
                violations.append(f"{filename}:{node.lineno}")
    return sorted(set(violations))


def _supplemental_channel_import_references() -> dict[str, dict[str, list[str]]]:
    """Collect conservative type references without promoting them to reachability."""
    import notebooklm

    public_dataclasses = {cls for cls in _public_model_exports() if dataclasses.is_dataclass(cls)}
    model_source_modules = {cls.__module__ for cls in public_dataclasses} | {
        "notebooklm.types",
        "notebooklm.artifacts",
    }
    source_root = Path(notebooklm.__file__).resolve().parents[1]
    package_root = source_root / "notebooklm"
    channels: dict[str, dict[str, list[str]]] = {}
    for channel, relative_root in _CHANNEL_ROOTS.items():
        references: dict[str, set[str]] = {}
        secret_violations: list[str] = []
        for path in sorted((package_root / relative_root).rglob("*.py")):
            relative_path = _relative_source_path(path, source_root)
            source = path.read_text(encoding="utf-8")
            secret_violations.extend(
                _secret_serialization_violations(source, filename=relative_path)
            )
            tree = ast.parse(source, filename=str(path))
            _module_name, package = _source_module_name(path, source_root)
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                imported_module = _resolved_import_module(node, package)
                if imported_module is None or not (
                    imported_module in model_source_modules
                    or imported_module.startswith("notebooklm._types.")
                ):
                    continue
                try:
                    module = importlib.import_module(imported_module)
                except ImportError:
                    continue
                for alias in node.names:
                    value = getattr(module, alias.name, None)
                    if value not in public_dataclasses:
                        continue
                    key = _model_key(value)
                    if key in _SECRET_BEARING_PUBLIC_MODELS:
                        continue
                    references.setdefault(key, set()).add(
                        f"model-import:{relative_path}:{alias.name}"
                    )
        if secret_violations:
            raise ValueError(
                f"secret-bearing public model reached {channel} serialization: "
                f"{sorted(secret_violations)}"
            )
        channels[channel] = {model: sorted(paths) for model, paths in sorted(references.items())}
    return channels


def _projection_keys(
    spec: dict[str, object],
    cls: type[Any],
    exported_inventory: dict[str, object],
) -> list[str]:
    mode = str(spec["mode"])
    if mode == "dataclass-full":
        return list(exported_inventory[_model_key(cls)]["to_jsonable_keys"])
    if mode == "app-view:ask_result_view":
        from notebooklm._app.views import ask_result_view

        return list(ask_result_view(_valid_dataclass_sample(cls)))
    if mode == "app-view:notebook_view":
        from notebooklm._app.views import notebook_view

        return list(notebook_view(_valid_dataclass_sample(cls)))
    if mode == "app-view:source_view":
        from notebooklm._app.views import source_view

        return list(source_view(_valid_dataclass_sample(cls)))
    if mode.startswith("app-view:share_status_view"):
        from notebooklm._app.views import share_status_view

        include_view_level = mode.endswith("+view_level")
        return list(
            share_status_view(_valid_dataclass_sample(cls), include_view_level=include_view_level)
        )
    return list(typing.cast(tuple[str, ...], spec["keys"]))


def _stable_ast_shape(node: ast.AST) -> tuple[object, ...]:
    """Return a cross-version-stable semantic AST tuple for one evidence scope."""

    ignored_fields = {"ctx", "kind", "type_comment", "type_params"}
    fields: list[tuple[str, object]] = []
    for field_name, value in ast.iter_fields(node):
        if field_name in ignored_fields:
            continue
        if field_name == "body" and isinstance(
            node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
        ):
            body = list(typing.cast(list[ast.stmt], value))
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


def _evidence_ast_fingerprint(source: str, token: str) -> str:
    """Hash the smallest semantic scopes containing every occurrence of ``token``."""

    tree = ast.parse(source)
    scope_types = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Assign | ast.AnnAssign
    scopes = [node for node in ast.walk(tree) if isinstance(node, scope_types)]
    offsets: list[int] = []
    cursor = 0
    while True:
        offset = source.find(token, cursor)
        if offset < 0:
            break
        offsets.append(offset)
        cursor = offset + max(len(token), 1)
    if not offsets:
        raise ValueError(f"projection evidence token not found: {token}")

    selected: dict[tuple[object, ...], None] = {}
    for offset in offsets:
        start_line = source.count("\n", 0, offset) + 1
        end_line = start_line + token.count("\n")
        containing = [
            node
            for node in scopes
            if node.lineno <= start_line and (node.end_lineno or node.lineno) >= end_line
        ]
        if not containing:
            continue
        smallest = min(
            containing,
            key=lambda node: (
                (node.end_lineno or node.lineno) - node.lineno,
                len(repr(_stable_ast_shape(node))),
            ),
        )
        selected[_stable_ast_shape(smallest)] = None
    if not selected:
        raise ValueError(f"projection evidence is outside a semantic scope: {token}")
    payload = repr(tuple(sorted(selected, key=repr))).encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _validate_projection_evidence(evidence: tuple[str, ...], source_root: Path) -> dict[str, str]:
    fingerprints: dict[str, str] = {}
    for item in evidence:
        relative_path, separator, token = item.partition(":")
        path = source_root / relative_path
        if not separator or not path.is_file():
            raise ValueError(f"stale adapter projection evidence: {item}")
        source = path.read_text(encoding="utf-8")
        if token not in source:
            raise ValueError(f"stale adapter projection evidence: {item}")
        fingerprints[item] = _evidence_ast_fingerprint(source, token)
    return fingerprints


def _literal_dict_keys(node: ast.Dict) -> list[str]:
    return [
        key.value
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    ]


def _named_function(tree: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name
    ]
    if len(matches) != 1:
        raise ValueError(f"expected one function named {name!r}, found {len(matches)}")
    return matches[0]


def _spread_projection_keys(name: str, cls: type[Any]) -> list[str]:
    if name == "notebook-viewed-keys":
        from notebooklm._app.views import notebook_viewed_keys

        return list(notebook_viewed_keys(_valid_dataclass_sample(cls)))
    raise ValueError(f"unknown projection spread derivation: {name}")


def _ast_projection_shape(
    source_root: Path, config: dict[str, object], cls: type[Any]
) -> dict[str, object]:
    path = source_root / str(config["path"])
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    kind = str(config["kind"])

    if kind == "ast-assigned-sequence":
        variable = str(config["variable"])
        matches: list[ast.Tuple | ast.List] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not any(
                isinstance(target, ast.Name) and target.id == variable for target in node.targets
            ):
                continue
            if isinstance(node.value, ast.Tuple | ast.List):
                matches.append(node.value)
        if len(matches) != 1:
            raise ValueError(f"expected one assigned sequence {variable!r}, found {len(matches)}")
        keys = [
            item.value
            for item in matches[0].elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        ]
        return {"keys": keys}

    if kind == "ast-compact-list-wrapper":
        variable = str(config["variable"])
        sequence = next(
            (
                node.value
                for node in ast.walk(tree)
                if isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == variable
                    for target in node.targets
                )
                and isinstance(node.value, ast.Tuple | ast.List)
            ),
            None,
        )
        if sequence is None:
            raise ValueError(f"missing assigned sequence {variable!r}")
        nested_keys = [
            item.value
            for item in sequence.elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        ]
        function = _named_function(tree, str(config["function"]))
        envelope = next(
            (
                node.value
                for node in ast.walk(function)
                if isinstance(node, ast.Return)
                and isinstance(node.value, ast.Dict)
                and {"notebook_id", "sources"} <= set(_literal_dict_keys(node.value))
            ),
            None,
        )
        if envelope is None:
            raise ValueError("missing source-list return envelope")
        meta_path = source_root / str(config["meta_path"])
        meta_tree = ast.parse(meta_path.read_text(encoding="utf-8"), filename=str(meta_path))
        meta_function = _named_function(meta_tree, str(config["meta_function"]))
        meta_mapping = next(
            (
                element
                for node in ast.walk(meta_function)
                if isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple)
                for element in node.value.elts
                if isinstance(element, ast.Dict)
            ),
            None,
        )
        if meta_mapping is None:
            raise ValueError("missing pagination metadata mapping")
        return {
            "keys": [*_literal_dict_keys(envelope), *_literal_dict_keys(meta_mapping)],
            "nested_keys": {"sources": nested_keys},
        }

    function = _named_function(tree, str(config["function"]))
    contains = set(typing.cast(tuple[str, ...], config.get("contains", ())))

    if kind == "ast-dict":
        candidates = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Dict) and contains <= set(_literal_dict_keys(node))
        ]
        if len(candidates) != 1:
            raise ValueError(
                f"expected one {config['function']} dict containing {sorted(contains)}, "
                f"found {len(candidates)}"
            )
        selected = candidates[0]
        shape: dict[str, object] = {"keys": _literal_dict_keys(selected)}
        nested_names = typing.cast(tuple[str, ...], config.get("nested", ()))
        nested_spreads = typing.cast(dict[str, str], config.get("nested_spreads", {}))
        if nested_names:
            nested_shapes: dict[str, list[str]] = {}
            for nested_name in nested_names:
                nested_node = next(
                    (
                        value
                        for key, value in zip(selected.keys, selected.values, strict=True)
                        if isinstance(key, ast.Constant)
                        and key.value == nested_name
                        and isinstance(value, ast.Dict)
                    ),
                    None,
                )
                if nested_node is None:
                    raise ValueError(f"missing nested dict {nested_name!r} in {config['function']}")
                nested_keys = _literal_dict_keys(nested_node)
                spread_name = nested_spreads.get(nested_name)
                if spread_name is not None:
                    nested_keys.extend(_spread_projection_keys(spread_name, cls))
                nested_shapes[nested_name] = nested_keys
            shape["nested_keys"] = nested_shapes
        return shape

    if kind == "ast-mapping-variable":
        variable = str(config["variable"])
        initial: ast.Dict | None = None
        for node in ast.walk(function):
            value: ast.expr | None = None
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                value = node.value
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                value = node.value
                targets = [node.target]
            if not isinstance(value, ast.Dict):
                continue
            if any(isinstance(target, ast.Name) and target.id == variable for target in targets):
                if contains <= set(_literal_dict_keys(value)):
                    if initial is not None:
                        raise ValueError(f"ambiguous mapping variable {variable!r}")
                    initial = value
        if initial is None:
            raise ValueError(f"missing mapping variable {variable!r}")
        optional: list[tuple[int, str]] = []
        for node in ast.walk(function):
            targets = node.targets if isinstance(node, ast.Assign) else ()
            if isinstance(node, ast.AnnAssign):
                targets = (node.target,)
            for target in targets:
                if not (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == variable
                ):
                    continue
                slice_node = target.slice
                if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
                    optional.append((node.lineno, slice_node.value))
        shape = {"keys": _literal_dict_keys(initial)}
        if optional:
            shape["optional_keys"] = [key for _, key in sorted(optional)]
        return shape

    raise ValueError(f"unknown AST projection derivation: {kind}")


def _runtime_projection_shape(name: str, cls: type[Any]) -> dict[str, object]:
    if name.startswith("ask-result-"):
        from notebooklm._app.views import ask_result_view

        payload = ask_result_view(_valid_dataclass_sample(cls))
        if name == "ask-result-cli-final":
            payload["note"] = {"id": "contract-note", "title": "Contract"}
            payload["note_save_error"] = "Contract error"
            return {
                "keys": [key for key in payload if key not in {"note", "note_save_error"}],
                "optional_keys": ["note", "note_save_error"],
                "nested_keys": {"note": ["id", "title"]},
            }
        if name in {"ask-result-mcp-lite", "ask-result-mcp-full"}:
            from notebooklm._app.serialize import to_jsonable
            from notebooklm.types import ChatReference

            base_keys = ["notebook_id", *payload]
            optional = ["history", "suggested_prompts", "source_ids"]
            is_lite = name.endswith("lite")
            reference_keys = (
                []
                if is_lite
                else list(
                    typing.cast(
                        dict[str, object], to_jsonable(_valid_dataclass_sample(ChatReference))
                    )
                )
            )
            shape: dict[str, object] = {
                "keys": base_keys,
                "optional_keys": optional,
                "nested_keys": {
                    "references": reference_keys,
                    "suggested_prompts": ["title", "prompt"],
                },
            }
            if is_lite:
                shape["nested_optional_keys"] = {
                    "references": ["source_id", "citation_number", "cited_text"]
                }
            return shape
        return {"keys": list(payload)}
    if name.startswith("prompt-suggestion-"):
        suggestion = _valid_dataclass_sample(cls)
        row = {"title": suggestion.title, "prompt": suggestion.prompt}
        channel = name.removeprefix("prompt-suggestion-")
        if channel == "cli":
            payload = {"notebook_id": "contract-notebook", "suggestions": [row], "count": 1}
        elif channel == "mcp":
            payload = {"notebook_id": "contract-notebook", "suggestions": [row]}
            return {
                "keys": list(payload),
                "optional_keys": ["source_ids"],
                "nested_keys": {"suggestions": list(row)},
            }
        elif channel == "rest":
            payload = {"notebook_id": "contract-notebook", "suggestions": [row]}
        elif channel == "mcp-chat-inline":
            return {"keys": list(row)}
        else:
            raise ValueError(f"unknown prompt suggestion projection: {channel}")
        return {"keys": list(payload), "nested_keys": {"suggestions": list(row)}}
    if name.startswith("cli-collection-"):
        from notebooklm.cli.collection_cmd import _collection_payload

        collection = _valid_dataclass_sample(cls)
        base = _collection_payload(collection)
        variant = name.removeprefix("cli-collection-")
        if variant == "mutation":
            return {"keys": list(base)}
        if variant == "add":
            return {"keys": ["added_notebook_ids", *base]}
        if variant == "remove":
            return {"keys": ["removed_notebook_ids", *base]}
        raise ValueError(f"unknown collection projection: {variant}")
    if name == "cli-note-chat-save":
        return {"keys": ["id", "title"]}
    if name == "cli-note-history-save":
        return {
            "keys": ["notebook_id", "conversation_id", "count", "qa_pairs", "note"],
            "nested_keys": {
                "qa_pairs": ["turn", "question", "answer"],
                "note": ["id", "title"],
            },
        }
    if name == "mcp-note-create":
        return {"keys": ["status", "notebook_id", "note_id", "title", "created"]}
    if name.startswith("research-task-public-dict"):
        from notebooklm.types import ResearchSource, ResearchStatus

        minimal = cls.empty().to_public_dict()
        if name.endswith("empty"):
            return {"keys": list(minimal)}
        task = cls(
            task_id="contract-task",
            status=ResearchStatus.COMPLETED,
            query="Contract",
            sources=[ResearchSource(url="https://contract.invalid", title="Contract")],
        )
        payload = task.to_public_dict()
        task_row = task._to_task_dict()
        return {
            "keys": list(payload),
            "nested_keys": {
                "sources": list(payload["sources"][0]),
                "tasks": list(task_row),
                "tasks[].sources": list(task_row["sources"][0]),
            },
        }
    if name.startswith("mind-map-"):
        if name == "mind-map-cli-final":
            return {"keys": ["mind_map", "note_id", "kind"]}
        if name in {"mind-map-mcp-final", "mind-map-rest-final"}:
            if name == "mind-map-mcp-final":
                return {"keys": ["notebook_id", "kind", "mind_map", "mind_map_id"]}
            return {
                "keys": ["notebook_id", "kind", "mind_map"],
                "nested_keys": {"mind_map": [field.name for field in dataclasses.fields(cls)]},
            }
        raise ValueError(f"unknown mind-map projection: {name}")
    if name.startswith("artifact-download-"):
        nested = {
            "selected": ["id", "title", "selection_reason"],
            "error": ["id", "title", "created_at"],
            "dry-all": ["id", "title", "filename"],
            "executed": ["id", "title", "filename", "status"],
        }
        variant = name.removeprefix("artifact-download-")
        if variant == "selected":
            return {"keys": nested[variant]}
        if variant == "error":
            return {"keys": nested[variant]}
        if variant == "dry-all":
            return {"keys": nested[variant]}
        if variant == "executed":
            return {
                "keys": nested[variant],
                "optional_keys": ["path", "reason", "error"],
            }
        if variant.startswith("mcp-root-"):
            nested_variant = variant.removeprefix("mcp-root-")
            nested_field = "artifact" if nested_variant in {"selected", "error"} else "artifacts"
            shape: dict[str, object] = {
                "keys": [
                    "outcome",
                    "error",
                    "suggestion",
                    "artifact",
                    "output_path",
                    "size_bytes",
                    "output_dir",
                    "count",
                    "total",
                    "succeeded_count",
                    "failed_count",
                    "skipped_count",
                    "is_failure",
                    "artifacts",
                    "notebook_id",
                ],
                "nested_keys": {nested_field: nested[nested_variant]},
            }
            if nested_variant == "executed":
                shape["nested_optional_keys"] = {"artifacts": ["path", "reason", "error"]}
            return shape
        raise ValueError(f"unknown artifact download projection: {variant}")
    if name.startswith("research-source-public-dict"):
        minimal = cls(url="https://contract.invalid", title="Contract").to_public_dict()
        complete = cls(
            url="https://contract.invalid",
            title="Contract",
            research_task_id="contract-task",
            report_markdown="Contract report",
            source_ordinal=1,
            hint="Contract hint",
        ).to_public_dict()
        optional_keys = [key for key in complete if key not in minimal]
        if name.endswith("-without-report"):
            optional_keys.remove("report_markdown")
        return {
            "keys": list(minimal),
            "optional_keys": optional_keys,
        }
    if name in {"cli-source-summary", "cli-source-row"}:
        from notebooklm.cli.services.source_serializers import (
            source_row_payload,
            source_summary_payload,
        )

        source = _valid_dataclass_sample(cls)
        payload = (
            source_summary_payload(source)
            if name == "cli-source-summary"
            else source_row_payload(source)
        )
        return {"keys": list(payload)}
    if name.startswith("cli-source-final-"):
        from notebooklm.cli.services.source_serializers import (
            source_row_payload,
            source_summary_payload,
        )

        source = _valid_dataclass_sample(cls)
        variant = name.removeprefix("cli-source-final-")
        if variant == "get":
            row = source_row_payload(source)
            return {"keys": ["source", "found"], "nested_keys": {"source": list(row)}}
        summary = source_summary_payload(source)
        if variant == "add":
            return {"keys": ["source"], "nested_keys": {"source": list(summary)}}
        if variant == "add-drive":
            return {
                "keys": ["action", "source", "notebook_id"],
                "nested_keys": {"source": [*summary, "drive_file_id", "mime_type"]},
            }
        if variant == "add-drive-file":
            return {
                "keys": ["action", "source", "document_id", "notebook_id"],
                "nested_keys": {"source": list(summary)},
            }
        if variant == "list":
            row = {"index": 1, **source_row_payload(source)}
            return {
                "keys": ["notebook_id", "notebook_title", "sources", "count"],
                "nested_keys": {"sources": list(row)},
            }
        raise ValueError(f"unknown CLI source final projection: {variant}")
    if name.startswith("source-view-"):
        from notebooklm._app.views import source_view

        view = source_view(_valid_dataclass_sample(cls))
        variant = name.removeprefix("source-view-")
        if variant in {"mcp-add", "mcp-add-drive", "mcp-add-drive-file"}:
            root_keys = ["source"]
            if variant == "mcp-add-drive":
                root_keys.extend(["file_id", "mime_type"])
            elif variant == "mcp-add-drive-file":
                root_keys.append("document_id")
            root_keys.extend(["notebook_id", "status"])
            shape: dict[str, object] = {
                "keys": root_keys,
                "nested_keys": {"source": list(view)},
            }
            if variant != "mcp-add-drive-file":
                shape["optional_keys"] = ["warning", "title_override_applied"]
            return shape
        if variant == "mcp-read-full":
            return {
                "keys": [
                    "notebook_id",
                    "source_id",
                    "source",
                    "content",
                    "char_count",
                    "truncated",
                    "output_format",
                ],
                "nested_keys": {"source": list(view)},
            }
        if variant in {"mcp-wait", "rest-wait"}:
            shape: dict[str, object] = {
                "keys": [
                    "notebook_id",
                    "ok",
                    "ready",
                    "timed_out",
                    "failed",
                    "not_found",
                    "ready_count",
                    "timed_out_count",
                    "failed_count",
                    "not_found_count",
                    "total_count",
                ],
                "nested_keys": {
                    "ready": list(view),
                    "timed_out": ["source_id", "error"],
                    "failed": ["source_id", "error"],
                    "not_found": ["source_id", "error"],
                },
            }
            if variant == "mcp-wait":
                shape["optional_keys"] = ["source_id", "title_override_applied", "warning"]
                shape["nested_optional_keys"] = {"ready": ["warning"]}
            return shape
        raise ValueError(f"unknown source view wrapper: {variant}")
    if name.startswith("list-wrapper-"):
        from notebooklm._app.views import notebook_view, source_view

        channel, model, variant = name.removeprefix("list-wrapper-").split("-", 2)
        if model == "notebook":
            row = notebook_view(_valid_dataclass_sample(cls))
        elif model == "source":
            row = source_view(_valid_dataclass_sample(cls))
        else:
            from notebooklm._app.serialize import to_jsonable

            row = typing.cast(dict[str, object], to_jsonable(_valid_dataclass_sample(cls)))
        collection_key = f"{model}s"
        if channel == "mcp":
            keys = [collection_key, "total", "offset", "has_more"]
            if model == "source":
                keys.insert(0, "notebook_id")
            return {"keys": keys, "nested_keys": {collection_key: list(row)}}
        keys = [collection_key]
        if model in {"source", "artifact", "note"}:
            keys.insert(0, "notebook_id")
        shape: dict[str, object] = {
            "keys": keys,
            "nested_keys": {collection_key: list(row)},
        }
        if variant == "paged":
            typing.cast(list[str], shape["keys"]).append("meta")
            typing.cast(dict[str, list[str]], shape["nested_keys"])["meta"] = [
                "total",
                "has_more",
                "limit",
                "offset",
            ]
        return shape
    if name == "cli-source-rename":
        from notebooklm._app.source_mutations import SourceRenameResult
        from notebooklm.cli._source_render import _source_rename_payload

        source = _valid_dataclass_sample(cls)
        payload = _source_rename_payload(
            SourceRenameResult(source=source, notebook_id="contract-notebook")
        )
        return {"keys": list(payload)}
    if name == "cli-source-clean-candidate":
        from notebooklm._app.source_clean import candidates_payload

        payload = candidates_payload((("contract-source", "Contract", "error", "reason"),))
        return {"keys": list(payload[0])}
    if name == "cli-source-delete-by-title":
        from notebooklm._app.source_mutations import SourceDeleteByTitleResult
        from notebooklm.cli._source_render import _source_delete_by_title_payload

        source = _valid_dataclass_sample(cls)
        payload = _source_delete_by_title_payload(
            SourceDeleteByTitleResult(
                source_id=source.id,
                title=source.title or "Contract",
                notebook_id="contract-notebook",
                success=True,
                status="completed",
            )
        )
        return {"keys": list(payload)}
    if name == "cli-label-title-join":
        from notebooklm.cli.services.label_listing import _label_serialize
        from notebooklm.types import Label

        payload = _label_serialize(
            Label(
                id="contract-label",
                name="Contract",
                source_ids=["contract-source"],
            ),
            {"contract-source": "Contract source"},
        )
        return {"keys": list(payload["sources"][0])}
    if name in {"cli-label-list", "cli-label-payload"}:
        label = cls(
            id="contract-label",
            name="Contract",
            source_ids=["contract-source"],
        )
        if name == "cli-label-list":
            from notebooklm.cli.services.label_listing import _label_serialize

            payload = _label_serialize(label, {"contract-source": "Contract source"})
            return {
                "keys": list(payload),
                "nested_keys": {"sources": list(payload["sources"][0])},
            }
        from notebooklm.cli.label_cmd import _label_payload

        return {"keys": list(_label_payload(label))}
    if name.startswith("cli-label-final-"):
        from notebooklm.cli.label_cmd import _label_payload

        label = cls(
            id="contract-label",
            name="Contract",
            source_ids=["contract-source"],
        )
        base = _label_payload(label)
        variant = name.removeprefix("cli-label-final-")
        if variant == "command":
            payload = {"notebook_id": "contract-notebook", **base}
            return {"keys": list(payload)}
        if variant == "generate":
            payload = {
                "notebook_id": "contract-notebook",
                "scope": "unlabeled",
                "labels": [base],
                "count": 1,
            }
            return {
                "keys": list(payload),
                "nested_keys": {"labels": list(payload["labels"][0])},
            }
        membership_key = {
            "add": "added_source_ids",
            "remove": "removed_source_ids",
        }.get(variant)
        if membership_key is not None:
            payload = {
                "notebook_id": "contract-notebook",
                membership_key: ["contract-source"],
                **base,
            }
            return {"keys": list(payload)}
        raise ValueError(f"unknown CLI label final projection: {variant}")
    if name in {
        "cli-notebook-metadata-root",
        "cli-notebook-metadata-flattened",
        "cli-notebook-metadata-source",
        "mcp-notebook-metadata-notebook",
    }:
        from notebooklm._app.serialize import to_jsonable
        from notebooklm._app.views import notebook_view
        from notebooklm.types import Notebook, NotebookMetadata, SourceSummary, SourceType

        notebook = typing.cast(Notebook, _valid_dataclass_sample(Notebook))
        summary = SourceSummary(kind=next(iter(SourceType)), title="Contract", url=None)
        metadata = NotebookMetadata(notebook=notebook, sources=[summary])
        if name.startswith("cli-"):
            payload = metadata.to_dict()
            if name == "cli-notebook-metadata-flattened":
                return {"keys": [key for key in payload if key != "sources"]}
            if name == "cli-notebook-metadata-source":
                return {"keys": list(payload["sources"][0])}
            return {
                "keys": list(payload),
                "nested_keys": {"sources": list(payload["sources"][0])},
            }
        payload = typing.cast(dict[str, object], to_jsonable(metadata))
        payload["notebook"] = notebook_view(notebook)
        typing.cast(dict[str, object], payload["notebook"])["sources_count"] = len(metadata.sources)
        if name == "mcp-notebook-metadata-notebook":
            return {"keys": list(typing.cast(dict[str, object], payload["notebook"]))}
        return {
            "keys": list(payload),
            "nested_keys": {
                "notebook": list(typing.cast(dict[str, object], payload["notebook"])),
                "sources": list(typing.cast(list[dict[str, object]], payload["sources"])[0]),
            },
        }
    if name in {"mcp-notebook-describe", "mcp-notebook-describe-with-metadata"}:
        from notebooklm._app.notebooks import NotebookDescribeResult
        from notebooklm._app.serialize import to_jsonable
        from notebooklm._app.views import notebook_view
        from notebooklm.types import (
            Notebook,
            NotebookDescription,
            NotebookMetadata,
            SourceSummary,
            SourceType,
            SuggestedTopic,
        )

        description = NotebookDescription(
            summary="Contract",
            suggested_topics=[SuggestedTopic(question="Question", prompt="Prompt")],
        )
        payload = typing.cast(
            dict[str, object],
            to_jsonable(
                NotebookDescribeResult(
                    notebook_id="contract-notebook",
                    description=description,
                )
            ),
        )
        if name.endswith("with-metadata"):
            notebook = typing.cast(Notebook, _valid_dataclass_sample(Notebook))
            summary = SourceSummary(kind=next(iter(SourceType)), title="Contract", url=None)
            metadata = NotebookMetadata(notebook=notebook, sources=[summary])
            metadata_block = typing.cast(dict[str, object], to_jsonable(metadata))
            metadata_block["notebook"] = notebook_view(notebook)
            typing.cast(dict[str, object], metadata_block["notebook"])["sources_count"] = 1
            payload["metadata"] = metadata_block
            return {
                "keys": list(payload),
                "nested_keys": {
                    "description": list(typing.cast(dict[str, object], payload["description"])),
                    "metadata": list(metadata_block),
                    "metadata.notebook": list(
                        typing.cast(dict[str, object], metadata_block["notebook"])
                    ),
                    "metadata.sources": list(
                        typing.cast(list[dict[str, object]], metadata_block["sources"])[0]
                    ),
                },
            }
        return {
            "keys": list(payload),
            "optional_keys": ["metadata"],
            "nested_keys": {
                "description": list(typing.cast(dict[str, object], payload["description"]))
            },
        }
    if name == "mcp-notebook-create":
        from notebooklm._app.views import notebook_view

        record = notebook_view(_valid_dataclass_sample(cls))
        record.pop("id")
        payload = {
            "status": "created",
            "notebook_id": "contract-notebook",
            **record,
        }
        return {"keys": list(payload)}
    if name == "mcp-source-rename-result":
        from notebooklm._app.serialize import to_jsonable
        from notebooklm._app.source_mutations import SourceRenameResult

        serialized = to_jsonable(
            SourceRenameResult(
                source=_valid_dataclass_sample(cls),
                notebook_id="contract-notebook",
            )
        )
        payload = {"status": "renamed", **serialized}
        return {
            "keys": list(payload),
            "nested_keys": {"source": list(typing.cast(dict[str, object], payload["source"]))},
        }
    if name == "generation-status-view":
        from notebooklm._app.artifacts import status_view
        from notebooklm._app.serialize import to_jsonable

        view = typing.cast(
            dict[str, object], to_jsonable(status_view(_valid_dataclass_sample(cls)))
        )
        return {"keys": ["notebook_id", *view]}
    raise ValueError(f"unknown runtime projection derivation: {name}")


def _derived_projection_shape(
    derivation: object, cls: type[Any], source_root: Path
) -> tuple[str, dict[str, object]]:
    if derivation == "manual-reviewed+fingerprint":
        return "manual-reviewed+fingerprint", {}
    if isinstance(derivation, str) and derivation.startswith("runtime:"):
        name = derivation.removeprefix("runtime:")
        return derivation, _runtime_projection_shape(name, cls)
    if isinstance(derivation, dict):
        config = typing.cast(dict[str, object], derivation)
        scope = config.get("function", config.get("variable"))
        label = f"{config['kind']}:{config['path']}:{scope}"
        return label, _ast_projection_shape(source_root, config, cls)
    raise ValueError(f"invalid projection derivation: {derivation!r}")


def _add_channel_projection(
    rows: dict[str, dict[str, list[dict[str, object]]]],
    model_key: str,
    projection: dict[str, object],
) -> None:
    projections = rows.setdefault(model_key, {"projections": []})["projections"]
    if projection not in projections:
        projections.append(projection)


def _normalize_conditional_key_groups(value: object) -> list[dict[str, object]]:
    """Normalize co-occurring conditional keys without flattening them into optionals."""
    groups = typing.cast(tuple[dict[str, object], ...] | list[dict[str, object]], value)
    normalized: list[dict[str, object]] = []
    seen_conditions: set[str] = set()
    for group in groups:
        condition = str(group["condition"])
        keys = list(typing.cast(tuple[str, ...] | list[str], group["keys"]))
        if not condition or not keys:
            raise ValueError("conditional key groups require a condition and at least one key")
        if condition in seen_conditions:
            raise ValueError(f"duplicate conditional key group: {condition}")
        seen_conditions.add(condition)
        normalized.append({"condition": condition, "keys": keys})
    return normalized


def _projection_declared_keys(projection: typing.Mapping[str, object]) -> set[str]:
    """Return every explicitly declared adapter key in a projection shape."""
    keys: set[str] = set()
    for field in ("keys", "optional_keys"):
        keys.update(typing.cast(tuple[str, ...] | list[str], projection.get(field, ())))
    for field in ("nested_keys", "nested_optional_keys"):
        values = typing.cast(
            dict[str, tuple[str, ...] | list[str]], projection.get(field, {})
        ).values()
        for value in values:
            keys.update(value)
    for variants in typing.cast(
        dict[str, dict[str, tuple[str, ...] | list[str]]],
        projection.get("nested_union_keys", {}),
    ).values():
        for value in variants.values():
            keys.update(value)
    for group in typing.cast(
        tuple[dict[str, object], ...] | list[dict[str, object]],
        projection.get("conditional_key_groups", ()),
    ):
        keys.update(typing.cast(tuple[str, ...] | list[str], group.get("keys", ())))
    return keys


def _validate_secret_projection(model_key: str, projection: typing.Mapping[str, object]) -> None:
    """Permit only the explicit non-credential AuthTokens identity contribution."""
    if model_key not in _SECRET_BEARING_PUBLIC_MODELS:
        return
    if projection.get("redacted_projection") != "safe-field-contribution":
        raise ValueError(
            f"secret-bearing models require a redacted adapter projection: {model_key}"
        )
    if projection.get("id") not in _AUTH_TOKENS_ALLOWED_PROJECTION_IDS:
        raise ValueError(
            "AuthTokens redacted projection must use one of the reviewed projection ids: "
            f"{sorted(_AUTH_TOKENS_ALLOWED_PROJECTION_IDS)}"
        )
    if "keys" not in projection or projection.get("nested_fields") is not None:
        raise ValueError(f"secret projection cannot recursively serialize {model_key}")
    contribution_keys = set(
        typing.cast(
            tuple[str, ...] | list[str],
            projection.get("model_contribution_keys", ()),
        )
    )
    if not contribution_keys or not contribution_keys <= _AUTH_TOKENS_SAFE_CONTRIBUTION_FIELDS:
        raise ValueError(
            "AuthTokens redacted projection contribution fields must be limited to "
            f"{sorted(_AUTH_TOKENS_SAFE_CONTRIBUTION_FIELDS)}"
        )
    emitted_keys = set(
        typing.cast(
            tuple[str, ...] | list[str],
            projection.get("emitted_model_contribution_keys", ()),
        )
    )
    control_keys = set(
        typing.cast(
            tuple[str, ...] | list[str],
            projection.get("control_model_contribution_keys", ()),
        )
    )
    if (
        not emitted_keys
        or emitted_keys & control_keys
        or emitted_keys | control_keys != contribution_keys
        or not emitted_keys <= _AUTH_TOKENS_SAFE_EMITTED_VALUE_FIELDS
        or not control_keys <= _AUTH_TOKENS_CONTROL_CONTRIBUTION_FIELDS
    ):
        raise ValueError(
            "AuthTokens projection must partition contribution fields into safe emitted and "
            "control-only origins"
        )
    credential_keys = _projection_declared_keys(projection) & _AUTH_TOKENS_CREDENTIAL_OUTPUT_FIELDS
    if credential_keys:
        raise ValueError(
            f"AuthTokens redacted projection exposes credential-bearing keys: "
            f"{sorted(credential_keys)}"
        )


def _validate_model_contribution_keys(
    model_key: str,
    cls: type[typing.Any],
    value: object,
    *,
    identity: str,
) -> tuple[str, ...]:
    if (
        not isinstance(value, tuple | list)
        or not value
        or not all(isinstance(key, str) and key for key in value)
    ):
        raise ValueError(f"model_contribution_keys must be a non-empty string list at {identity}")
    keys = tuple(typing.cast(tuple[str, ...] | list[str], value))
    if len(keys) != len(set(keys)):
        raise ValueError(f"duplicate model_contribution_keys at {identity}: {keys}")
    dataclass_fields = {field.name for field in dataclasses.fields(cls)}
    reviewed_properties = _REVIEWED_MODEL_CONTRIBUTION_PROPERTIES.get(model_key, frozenset())
    invalid_reviewed_properties = sorted(
        name
        for name in reviewed_properties
        if not any(isinstance(vars(base).get(name), property) for base in cls.__mro__)
    )
    if invalid_reviewed_properties:
        raise ValueError(
            f"reviewed model contribution properties are stale for {model_key}: "
            f"{invalid_reviewed_properties}"
        )
    unknown = sorted(set(keys) - dataclass_fields - reviewed_properties)
    if unknown:
        raise ValueError(
            f"unknown model_contribution_keys for {model_key} at {identity}: {unknown}"
        )
    return keys


def _exact_channel_projections(exported_inventory: dict[str, object]) -> dict[str, object]:
    """Build reviewed sink-backed projections and their transitive model shapes."""
    import notebooklm

    model_classes = {
        _model_key(cls): cls for cls in _public_model_exports() if dataclasses.is_dataclass(cls)
    }
    public_dataclasses = {
        cls for key, cls in model_classes.items() if key not in _SECRET_BEARING_PUBLIC_MODELS
    }
    source_root = Path(notebooklm.__file__).resolve().parents[1]
    channels: dict[str, object] = {}

    for channel, specs in _CHANNEL_PROJECTION_SPECS.items():
        rows: dict[str, dict[str, list[dict[str, object]]]] = {}
        projection_ids = [str(spec["id"]) for spec in specs]
        if len(projection_ids) != len(set(projection_ids)):
            duplicates = sorted(
                projection_id
                for projection_id in set(projection_ids)
                if projection_ids.count(projection_id) > 1
            )
            raise ValueError(f"duplicate projection ids for {channel}: {duplicates}")

        for spec in specs:
            model_key = str(spec["model"])
            if model_key not in model_classes:
                raise ValueError(f"unknown public projection model: {model_key}")
            if "model_contribution_keys" in spec:
                _validate_model_contribution_keys(
                    model_key,
                    model_classes[model_key],
                    spec["model_contribution_keys"],
                    identity=str(spec["id"]),
                )
            _validate_secret_projection(model_key, spec)
            cls = model_classes[model_key]
            evidence = typing.cast(tuple[str, ...], spec["evidence"])
            evidence_fingerprints = _validate_projection_evidence(evidence, source_root)
            derived_shape: dict[str, object] = {}
            derivation_label: str | None = None
            if "derive" in spec:
                derivation_label, derived_shape = _derived_projection_shape(
                    spec["derive"], cls, source_root
                )
            keys = (
                typing.cast(list[str], derived_shape["keys"])
                if "keys" in derived_shape
                else _projection_keys(spec, cls, exported_inventory)
            )
            projection = {
                "id": spec["id"],
                "mode": spec["mode"],
                "keys": keys,
                "evidence": list(evidence),
                "evidence_shape_fingerprints": evidence_fingerprints,
            }
            if derivation_label is not None:
                projection["shape_derivation"] = derivation_label
            nested_keys = derived_shape.get("nested_keys", spec.get("nested_keys"))
            if nested_keys is not None:
                projection["nested_keys"] = {
                    key: list(value)
                    for key, value in typing.cast(
                        dict[str, tuple[str, ...] | list[str]], nested_keys
                    ).items()
                }
            nested_union_keys = spec.get("nested_union_keys")
            if nested_union_keys is not None:
                projection["nested_union_keys"] = {
                    field: {
                        variant: list(variant_keys) for variant, variant_keys in variants.items()
                    }
                    for field, variants in typing.cast(
                        dict[str, dict[str, tuple[str, ...] | list[str]]], nested_union_keys
                    ).items()
                }
            optional_keys = derived_shape.get("optional_keys", spec.get("optional_keys"))
            if optional_keys is not None:
                projection["optional_keys"] = list(
                    typing.cast(tuple[str, ...] | list[str], optional_keys)
                )
            conditional_key_groups = spec.get("conditional_key_groups")
            if conditional_key_groups is not None:
                projection["conditional_key_groups"] = _normalize_conditional_key_groups(
                    conditional_key_groups
                )
            nested_optional_keys = derived_shape.get(
                "nested_optional_keys", spec.get("nested_optional_keys")
            )
            if nested_optional_keys is not None:
                projection["nested_optional_keys"] = {
                    key: list(value)
                    for key, value in typing.cast(
                        dict[str, tuple[str, ...] | list[str]], nested_optional_keys
                    ).items()
                }
            model_contribution_keys = spec.get("model_contribution_keys")
            if model_contribution_keys is not None:
                projection["model_contribution_keys"] = list(
                    typing.cast(tuple[str, ...] | list[str], model_contribution_keys)
                )
            for contribution_field in (
                "emitted_model_contribution_keys",
                "control_model_contribution_keys",
            ):
                contribution_value = spec.get(contribution_field)
                if contribution_value is not None:
                    projection[contribution_field] = list(
                        typing.cast(tuple[str, ...] | list[str], contribution_value)
                    )
            projection_condition = spec.get("projection_condition")
            if projection_condition is not None:
                projection["projection_condition"] = str(projection_condition)
            adapter_surface = spec.get("adapter_surface")
            if adapter_surface is not None:
                projection["adapter_surface"] = str(adapter_surface)
            contribution_semantics = spec.get("contribution_semantics")
            if contribution_semantics is not None:
                projection["contribution_semantics"] = str(contribution_semantics)
            redacted_projection = spec.get("redacted_projection")
            if redacted_projection is not None:
                projection["redacted_projection"] = str(redacted_projection)
            _add_channel_projection(
                rows,
                model_key,
                projection,
            )

            nested_fields = spec.get("nested_fields")
            if nested_fields is None:
                continue
            field_names = (
                [field.name for field in dataclasses.fields(cls)]
                if nested_fields == "all"
                else list(typing.cast(tuple[str, ...], nested_fields))
            )
            try:
                hints = typing.get_type_hints(cls.__init__)
            except (NameError, TypeError):
                hints = {field.name: field.type for field in dataclasses.fields(cls)}
            for field_name in field_names:
                for nested in _models_in_annotation(
                    hints.get(field_name), public_dataclasses, seen={cls}
                ):
                    nested_key = _model_key(nested)
                    nested_projection: dict[str, object] = {
                        "id": (
                            f"{str(spec['id'])}.nested-{field_name}-{nested_key.rsplit('.', 1)[-1]}"
                        ),
                        "mode": "nested-dataclass",
                        "keys": list(exported_inventory[nested_key]["to_jsonable_keys"]),
                        "evidence": [f"nested-via:{model_key}.{field_name}"],
                    }
                    metadata_by_field = typing.cast(
                        dict[str, dict[str, object]],
                        spec.get("nested_projection_metadata", {}),
                    )
                    metadata = metadata_by_field.get(field_name, {})
                    if "model_contribution_keys" in metadata:
                        _validate_model_contribution_keys(
                            nested_key,
                            nested,
                            metadata["model_contribution_keys"],
                            identity=str(nested_projection["id"]),
                        )
                    for metadata_key in (
                        "model_contribution_keys",
                        "projection_condition",
                        "contribution_semantics",
                    ):
                        if metadata_key not in metadata:
                            continue
                        metadata_value = metadata[metadata_key]
                        nested_projection[metadata_key] = (
                            list(
                                typing.cast(
                                    tuple[str, ...] | list[str],
                                    metadata_value,
                                )
                            )
                            if metadata_key == "model_contribution_keys"
                            else str(metadata_value)
                        )
                    _add_channel_projection(
                        rows,
                        nested_key,
                        nested_projection,
                    )
        channels[channel] = {model: rows[model] for model in sorted(rows)}
    return channels


def _validate_no_secret_channel_models(channel_models: dict[str, object]) -> None:
    violations: dict[str, list[str]] = {}
    allowed_sites: list[tuple[str, str]] = []
    for channel, models_value in channel_models.items():
        if isinstance(models_value, set):
            secret_models = models_value & _SECRET_BEARING_PUBLIC_MODELS
            if secret_models:
                violations[channel] = sorted(secret_models)
            continue
        models = typing.cast(dict[str, dict[str, list[dict[str, object]]]], models_value)
        for model_key in sorted(set(models) & _SECRET_BEARING_PUBLIC_MODELS):
            for projection in models[model_key]["projections"]:
                _validate_secret_projection(model_key, projection)
                projection_id = str(projection["id"])
                if (channel, projection_id) not in _AUTH_TOKENS_ALLOWED_PROJECTIONS:
                    raise ValueError(
                        "AuthTokens redacted projection is allowed only at the reviewed "
                        f"channel/id pairs, not {(channel, projection_id)}"
                    )
                allowed_sites.append((channel, projection_id))
    if allowed_sites and sorted(allowed_sites) != sorted(_AUTH_TOKENS_ALLOWED_PROJECTIONS):
        raise ValueError(
            f"AuthTokens requires exactly the two reviewed adapter projections; got {allowed_sites}"
        )
    if violations:
        raise ValueError(
            f"secret-bearing models require a redacted adapter projection: {violations}"
        )


def _all_channel_projection_ids(channels: dict[str, object]) -> set[str]:
    """Return every explicit and auto-derived projection identity."""
    return {
        str(projection["id"])
        for channel_rows in channels.values()
        for model_row in typing.cast(dict[str, dict[str, object]], channel_rows).values()
        for projection in typing.cast(list[dict[str, object]], model_row["projections"])
    }


def derive_json_envelope_contract() -> dict[str, object]:
    """Freeze exported keys separately from per-channel adapter reachability."""
    import notebooklm
    from notebooklm._app.serialize import to_jsonable
    from tests._baselines.adapter_sink_reachability import (
        derive_adapter_sink_reachability_contract,
    )

    exported_inventory: dict[str, object] = {}
    for cls, export_paths in sorted(
        _public_model_exports().items(), key=lambda item: _model_key(item[0])
    ):
        if not dataclasses.is_dataclass(cls):
            continue
        if _model_key(cls) in _SECRET_BEARING_PUBLIC_MODELS:
            continue
        sample = _valid_dataclass_sample(cls)
        payload = to_jsonable(sample)
        if not isinstance(payload, dict):
            raise TypeError(f"to_jsonable({_model_key(cls)}) did not return a dict")
        exported_inventory[_model_key(cls)] = {
            "module": cls.__module__,
            "qualname": cls.__qualname__,
            "exports": export_paths,
            "dataclass_fields": [field.name for field in dataclasses.fields(cls)],
            "to_jsonable_keys": list(payload),
        }

    channels = _exact_channel_projections(exported_inventory)
    _validate_no_secret_channel_models(channels)
    adapter_sink_reachability = derive_adapter_sink_reachability_contract(
        Path(notebooklm.__file__).resolve().parents[1],
        known_projection_ids=_all_channel_projection_ids(channels),
    )

    return {
        "schema_version": 1,
        "exported_inventory_selection": (
            "non-secret dataclasses in __all__ of every audit-discovered public module"
        ),
        "exported_dataclass_key_inventory": exported_inventory,
        "channels_selection": (
            "reviewed serializer sinks and projection helpers; the MCP channel includes tool "
            "results plus explicitly labelled auxiliary connector/file-route JSON; each "
            "projection pins its actual keys and preserves only transitively serialized public "
            "dataclass fields"
        ),
        "channels": channels,
        "adapter_sink_reachability": adapter_sink_reachability,
        "supplemental_import_references": _supplemental_channel_import_references(),
        "secret_bearing_exclusions": {
            "notebooklm.auth.AuthTokens": {
                "adapter_reachable": True,
                "allowed_projections": [
                    {"channel": channel, "projection_id": projection_id}
                    for channel, projection_id in _AUTH_TOKENS_ALLOWED_PROJECTIONS
                ],
                "allowed_model_contribution_keys": [
                    "authuser",
                    "account_email",
                    "storage_path",
                    "_profile_session_generation",
                ],
                "allowed_emitted_value_fields": sorted(_AUTH_TOKENS_SAFE_EMITTED_VALUE_FIELDS),
                "recursive_serialization_allowed": False,
                "policy": (
                    "excluded from exported/to_jsonable inventory; only an explicitly marked "
                    "safe-field contribution may expose values from authuser/account_email; "
                    "storage_path/profile-generation may only select cache/fallback control "
                    "flow, and credentials are never adapter keys"
                ),
            }
        },
    }


__all__ = [
    "_validate_secret_projection",
    "derive_json_envelope_contract",
]
