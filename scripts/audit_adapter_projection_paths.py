"""Derive public-dataclass paths carried through private adapter DTOs.

The terminal sink inventory cannot infer that serializing a private result may
also serialize a public model nested several fields below it.  This companion
catalog parses private dataclass annotations without importing private adapter
modules.  That keeps the audit safe under a minimal install and avoids import-
time registration or optional-dependency side effects.

Dynamic ``Any`` results and transformations that erase a model into ``dict``
remain explicit reviewed edges; they are never treated as proven by this
annotation catalog.
"""

from __future__ import annotations

import ast
import builtins
import dataclasses
import importlib
import importlib.util
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PrivateDataclassProjectionPath:
    """One annotation-proven private DTO → public dataclass path."""

    private_model: str
    field_path: str
    public_model: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class _TypeRef:
    name: str
    suffix: str = ""


@dataclass(frozen=True)
class _SourceClass:
    key: str
    module: str
    name: str
    is_dataclass: bool
    fields: tuple[tuple[str, ast.expr], ...]
    bases: tuple[ast.expr, ...]
    aliases: Mapping[str, str]
    declared_symbols: frozenset[str]


_BUILTIN_ANNOTATIONS = frozenset({*vars(builtins), "None", "NoneType"})
_SEQUENCE_ANNOTATIONS = frozenset(
    {
        "AsyncIterable",
        "AsyncIterator",
        "Collection",
        "Iterable",
        "Iterator",
        "Sequence",
        "frozenset",
        "list",
        "set",
        "tuple",
    }
)
_MAPPING_ANNOTATIONS = frozenset({"dict", "Mapping", "MutableMapping"})
_TRANSPARENT_ANNOTATIONS = frozenset(
    {
        "Annotated",
        "ClassVar",
        "Final",
        "Literal",
        "Optional",
        "Required",
        "Union",
    }
)


def _model_key(cls: type[Any]) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def _public_dataclass_aliases() -> dict[str, str]:
    """Map every audited public export path onto its canonical model key."""
    import scripts.audit_public_api_compat as public_audit

    import notebooklm

    package_dir = Path(notebooklm.__file__).resolve().parent
    module_names = {public_audit.PUBLIC_PACKAGE}
    for path in package_dir.glob("*.py"):
        if path.stem.startswith("_") or path.stem in public_audit.EXCLUDED_TOP_LEVEL_MODULES:
            continue
        module_names.add(f"{public_audit.PUBLIC_PACKAGE}.{path.stem}")
    for name in public_audit.EXTRA_PUBLIC_PACKAGES:
        if (package_dir / name / "__init__.py").is_file():
            module_names.add(f"{public_audit.PUBLIC_PACKAGE}.{name}")

    aliases: dict[str, str] = {}
    for module_name in sorted(module_names):
        module = importlib.import_module(module_name)
        for name in getattr(module, "__all__", ()):
            value = getattr(module, name)
            if isinstance(value, type) and dataclasses.is_dataclass(value):
                aliases[f"{module_name}.{name}"] = _model_key(value)
                aliases[_model_key(value)] = _model_key(value)
    return aliases


def _module_name(path: Path, source_root: Path) -> tuple[str, str]:
    relative = path.relative_to(source_root).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
        module = ".".join(parts)
        return module, module
    module = ".".join(parts)
    return module, module.rpartition(".")[0]


def _resolve_import(node: ast.ImportFrom, package: str) -> str:
    if node.level == 0:
        if node.module is None:
            raise ValueError("absolute from-import has no module")
        return node.module
    relative = "." * node.level + (node.module or "")
    try:
        return importlib.util.resolve_name(relative, package)
    except (ImportError, ValueError) as exc:
        raise ValueError(
            f"could not resolve annotation import {relative!r} from {package}"
        ) from exc


def _is_dataclass_decorator(node: ast.expr) -> bool:
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Name):
        return target.id == "dataclass"
    return isinstance(target, ast.Attribute) and target.attr == "dataclass"


def _assigned_names(node: ast.Assign | ast.AnnAssign) -> set[str]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return {target.id for target in targets if isinstance(target, ast.Name)}


def _module_statements(statements: list[ast.stmt]) -> list[ast.stmt]:
    """Flatten module control-flow blocks without entering functions/classes."""
    rows: list[ast.stmt] = []
    for statement in statements:
        rows.append(statement)
        child_blocks: list[list[ast.stmt]] = []
        if isinstance(statement, ast.If | ast.While | ast.For | ast.AsyncFor):
            child_blocks.extend((statement.body, statement.orelse))
        elif isinstance(statement, ast.Try):
            child_blocks.extend((statement.body, statement.orelse, statement.finalbody))
            child_blocks.extend(handler.body for handler in statement.handlers)
        elif isinstance(statement, ast.With | ast.AsyncWith):
            child_blocks.append(statement.body)
        for block in child_blocks:
            rows.extend(_module_statements(block))
    return rows


def _source_classes(source_root: Path, relative_roots: tuple[str, ...]) -> dict[str, _SourceClass]:
    classes: dict[str, _SourceClass] = {}
    for relative_root in relative_roots:
        for path in sorted((source_root / relative_root).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            module, package = _module_name(path, source_root)
            aliases: dict[str, str] = {}
            declared_symbols = set(_BUILTIN_ANNOTATIONS)
            module_statements = _module_statements(tree.body)
            for node in module_statements:
                if isinstance(node, ast.ImportFrom):
                    imported_module = _resolve_import(node, package)
                    for alias in node.names:
                        if alias.name == "*":
                            continue
                        local_name = alias.asname or alias.name
                        aliases[local_name] = f"{imported_module}.{alias.name}"
                        declared_symbols.add(local_name)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        local_name = alias.asname or alias.name.partition(".")[0]
                        aliases[local_name] = alias.name
                        declared_symbols.add(local_name)
                elif isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                    declared_symbols.add(node.name)
                elif isinstance(node, ast.Assign | ast.AnnAssign):
                    declared_symbols.update(_assigned_names(node))

            for node in tree.body:
                if not isinstance(node, ast.ClassDef):
                    continue
                fields = tuple(
                    (statement.target.id, statement.annotation)
                    for statement in node.body
                    if isinstance(statement, ast.AnnAssign)
                    and isinstance(statement.target, ast.Name)
                )
                key = f"{module}.{node.name}"
                classes[key] = _SourceClass(
                    key=key,
                    module=module,
                    name=node.name,
                    is_dataclass=any(_is_dataclass_decorator(item) for item in node.decorator_list),
                    fields=fields,
                    bases=tuple(node.bases),
                    aliases=aliases,
                    declared_symbols=frozenset(declared_symbols),
                )
    return classes


def _dotted_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent is not None else None
    return None


def _resolve_name(name: str, owner: _SourceClass) -> str:
    head, separator, tail = name.partition(".")
    if head in owner.aliases:
        base = owner.aliases[head]
        return f"{base}.{tail}" if separator else base
    if separator:
        if head in owner.declared_symbols:
            return name
        raise ValueError(f"unresolved annotation name {name!r} in {owner.key}")
    if name in owner.declared_symbols:
        return f"{owner.module}.{name}"
    raise ValueError(f"unresolved annotation name {name!r} in {owner.key}")


def _annotation_refs(node: ast.expr, owner: _SourceClass) -> list[_TypeRef]:
    if isinstance(node, ast.Constant):
        if node.value is None:
            return []
        if not isinstance(node.value, str):
            return []
        try:
            parsed = ast.parse(node.value, mode="eval").body
        except SyntaxError as exc:
            raise ValueError(f"unparseable annotation {node.value!r} in {owner.key}") from exc
        return _annotation_refs(parsed, owner)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return [*_annotation_refs(node.left, owner), *_annotation_refs(node.right, owner)]
    if isinstance(node, ast.Subscript):
        container = _dotted_name(node.value)
        container_name = container.rpartition(".")[2] if container is not None else ""
        elements = list(node.slice.elts) if isinstance(node.slice, ast.Tuple) else [node.slice]
        if container_name in {"Callable", "Literal"}:
            return []
        if container_name in _MAPPING_ANNOTATIONS:
            suffixes = ["{key}", "{value}"]
        elif container_name in _SEQUENCE_ANNOTATIONS:
            suffixes = ["[]"] * len(elements)
        elif container_name in _TRANSPARENT_ANNOTATIONS:
            elements = elements[:1] if container_name == "Annotated" else elements
            suffixes = [""] * len(elements)
        else:
            refs = _annotation_refs(node.value, owner)
            suffixes = [""] * len(elements)
            return [
                *refs,
                *[
                    _TypeRef(ref.name, f"{suffix}{ref.suffix}")
                    for element, suffix in zip(elements, suffixes, strict=True)
                    for ref in _annotation_refs(element, owner)
                ],
            ]
        return [
            _TypeRef(ref.name, f"{suffix}{ref.suffix}")
            for element, suffix in zip(elements, suffixes, strict=False)
            for ref in _annotation_refs(element, owner)
        ]
    name = _dotted_name(node)
    if name is not None:
        if name in _BUILTIN_ANNOTATIONS or name.rpartition(".")[2] in _BUILTIN_ANNOTATIONS:
            return []
        return [_TypeRef(_resolve_name(name, owner))]
    raise ValueError(f"unsupported annotation AST {type(node).__name__} in {owner.key}")


def _is_dataclass_pseudo_field(annotation: ast.expr) -> bool:
    if not isinstance(annotation, ast.Subscript):
        return False
    container = _dotted_name(annotation.value)
    return container is not None and container.rpartition(".")[2] in {"ClassVar", "InitVar"}


def _paths_from_class(
    cls: _SourceClass,
    *,
    classes: Mapping[str, _SourceClass],
    public_aliases: Mapping[str, str],
    prefix: str,
    seen: frozenset[str],
) -> list[tuple[str, str]]:
    if cls.key in seen:
        return []
    rows: list[tuple[str, str]] = []
    for base in cls.bases:
        for reference in _annotation_refs(base, cls):
            base_cls = classes.get(reference.name)
            if base_cls is not None and base_cls.is_dataclass:
                rows.extend(
                    _paths_from_class(
                        base_cls,
                        classes=classes,
                        public_aliases=public_aliases,
                        prefix=prefix,
                        seen=seen | {cls.key},
                    )
                )
    for field_name, annotation in cls.fields:
        if _is_dataclass_pseudo_field(annotation):
            continue
        field_prefix = f"{prefix}.{field_name}" if prefix else field_name
        for reference in _annotation_refs(annotation, cls):
            path = f"{field_prefix}{reference.suffix}"
            public_model = public_aliases.get(reference.name)
            if public_model is not None:
                rows.append((path, public_model))
                continue
            nested = classes.get(reference.name)
            if nested is not None and nested.is_dataclass:
                rows.extend(
                    _paths_from_class(
                        nested,
                        classes=classes,
                        public_aliases=public_aliases,
                        prefix=path,
                        seen=seen | {cls.key},
                    )
                )
    return rows


def discover_private_dataclass_projection_paths(
    source_root: Path | None = None,
    *,
    relative_roots: tuple[str, ...] = (
        "notebooklm/_app",
        "notebooklm/cli",
        "notebooklm/mcp",
        "notebooklm/server",
    ),
    public_model_aliases: Mapping[str, str] | None = None,
) -> list[PrivateDataclassProjectionPath]:
    """Return the exact source-derived private DTO projection-path catalog."""
    if source_root is None:
        import notebooklm

        source_root = Path(notebooklm.__file__).resolve().parents[1]
    aliases = dict(public_model_aliases or _public_dataclass_aliases())
    classes = _source_classes(source_root, relative_roots)
    public_models = set(aliases.values())
    rows: set[PrivateDataclassProjectionPath] = set()
    for cls in classes.values():
        if not cls.is_dataclass or cls.key in public_models:
            continue
        for field_path, public_model in _paths_from_class(
            cls,
            classes=classes,
            public_aliases=aliases,
            prefix="",
            seen=frozenset(),
        ):
            rows.add(
                PrivateDataclassProjectionPath(
                    private_model=cls.key,
                    field_path=field_path,
                    public_model=public_model,
                )
            )
    return sorted(rows, key=lambda row: (row.private_model, row.field_path, row.public_model))


__all__ = [
    "PrivateDataclassProjectionPath",
    "discover_private_dataclass_projection_paths",
]
