"""AST-derived public API, transport authority, and recency audits."""

from __future__ import annotations

import ast
import hashlib
import inspect
import typing
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path

from notebooklm._operations import Operation
from notebooklm.client import NotebookLMClient
from notebooklm.rpc import RPCMethod

if __package__:
    from ._operation_catalog_authorities import (
        _GET_SOURCES,
        _GET_TYPED,
        APP_AUTHORITY_SOURCE_CONTRACTS,
        APP_OPERATION_AUTHORITIES,
        NON_RPC_AUTHORITY_RULES,
        NON_RPC_SOURCE_CONTRACTS,
        RECENCY_CONTRACTS,
        SHARED_RPC_AUTHORITY_RULES,
        _rules,
    )
    from ._operation_catalog_specs import (
        NATIVE_BINDING_DISPOSITIONS,
        OPERATION_SPECS,
        NativeKey,
        OperationSpec,
        _b,
        _p,
        native_key_text,
    )
    from .audit_public_api_compat import CLIENT_NAMESPACE_ATTRIBUTES
else:  # pragma: no cover - direct script execution
    from _operation_catalog_authorities import (
        _GET_SOURCES,
        _GET_TYPED,
        APP_AUTHORITY_SOURCE_CONTRACTS,
        APP_OPERATION_AUTHORITIES,
        NON_RPC_AUTHORITY_RULES,
        NON_RPC_SOURCE_CONTRACTS,
        RECENCY_CONTRACTS,
        SHARED_RPC_AUTHORITY_RULES,
        _rules,
    )
    from _operation_catalog_specs import (
        NATIVE_BINDING_DISPOSITIONS,
        OPERATION_SPECS,
        NativeKey,
        OperationSpec,
        _b,
        _p,
        native_key_text,
    )
    from audit_public_api_compat import CLIENT_NAMESPACE_ATTRIBUTES

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "notebooklm"
APP_ROOT = SRC_ROOT / "_app"
_native_key_text = native_key_text


def _literal_string(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _qualname(stack: Sequence[str]) -> str:
    return ".".join(stack) if stack else "<module>"


def _attribute_parts(node: ast.AST) -> tuple[str, ...]:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return tuple(reversed(parts))


class _ReferenceCollector(ast.NodeVisitor):
    """Collect RPCMethod references and public facade calls with owners."""

    def __init__(self, relative_path: str, namespace_names: set[str]) -> None:
        self.relative_path = relative_path
        self.namespace_names = namespace_names
        self.stack: list[str] = []
        self.bindings: list[dict[str, set[str]]] = []
        self.literal_bindings: list[dict[str, str]] = []
        self.rpc_references: list[tuple[str, str]] = []
        self.rpc_calls: list[tuple[str, str | None, str]] = []
        self.unresolved_rpc_calls: list[tuple[str, str]] = []
        self.public_calls: list[tuple[str, str]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.stack.append(node.name)
        self.bindings.append(defaultdict(set))
        literals: dict[str, str] = {}
        positional = [*node.args.posonlyargs, *node.args.args]
        if node.args.defaults:
            for arg, default in zip(
                positional[-len(node.args.defaults) :], node.args.defaults, strict=True
            ):
                if (value := _literal_string(default)) is not None:
                    literals[arg.arg] = value
        for arg, kw_default in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True):
            if kw_default is not None and (value := _literal_string(kw_default)) is not None:
                literals[arg.arg] = value
        self.literal_bindings.append(literals)
        self.generic_visit(node)
        self.literal_bindings.pop()
        self.bindings.pop()
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        parts = _attribute_parts(node)
        if len(parts) >= 2 and parts[-2] == "RPCMethod" and parts[-1] in RPCMethod.__members__:
            self.rpc_references.append((parts[-1], _qualname(self.stack)))
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if self.bindings:
            methods = {
                parts[-1]
                for item in ast.walk(node.value)
                if isinstance(item, ast.Attribute)
                and len(parts := _attribute_parts(item)) >= 2
                and parts[-2] == "RPCMethod"
                and parts[-1] in RPCMethod.__members__
            }
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.bindings[-1][target.id].update(methods)
                    if (literal := _literal_string(node.value)) is not None:
                        self.literal_bindings[-1][target.id] = literal
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if self.bindings and isinstance(node.target, ast.Name) and node.value is not None:
            methods = {
                parts[-1]
                for item in ast.walk(node.value)
                if isinstance(item, ast.Attribute)
                and len(parts := _attribute_parts(item)) >= 2
                and parts[-2] == "RPCMethod"
                and parts[-1] in RPCMethod.__members__
            }
            self.bindings[-1][node.target.id].update(methods)
            if (literal := _literal_string(node.value)) is not None:
                self.literal_bindings[-1][node.target.id] = literal
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        parts = _attribute_parts(node.func)
        if parts and parts[-1] in {"rpc_call", "_rpc_call"}:
            method_node = (
                node.args[0]
                if node.args
                else next(
                    (keyword.value for keyword in node.keywords if keyword.arg == "method"),
                    None,
                )
            )
            method_names = {
                item_parts[-1]
                for item in (ast.walk(method_node) if method_node is not None else ())
                if isinstance(item, ast.Attribute)
                and len(item_parts := _attribute_parts(item)) >= 2
                and item_parts[-2] == "RPCMethod"
                and item_parts[-1] in RPCMethod.__members__
            }
            if isinstance(method_node, ast.Name):
                for scope in reversed(self.bindings):
                    if method_node.id in scope:
                        method_names.update(scope[method_node.id])
                        break
            variant: str | None = None
            variant_resolved = True
            for keyword in node.keywords:
                if keyword.arg == "operation_variant":
                    variant = _literal_string(keyword.value)
                    if (
                        isinstance(keyword.value, ast.Constant) and keyword.value.value is None
                    ) or variant is not None:
                        variant_resolved = True
                    elif isinstance(keyword.value, ast.Name):
                        variant_resolved = False
                        for literal_scope in reversed(self.literal_bindings):
                            if keyword.value.id in literal_scope:
                                variant = literal_scope[keyword.value.id]
                                variant_resolved = True
                                break
                    else:
                        variant_resolved = False
            owner = _qualname(self.stack)
            if method_node is None or not method_names:
                self.unresolved_rpc_calls.append((owner, "method"))
            elif not variant_resolved:
                self.unresolved_rpc_calls.append((owner, "operation_variant"))
            for method_name in method_names:
                self.rpc_calls.append((method_name, variant, owner))
        for index, part in enumerate(parts[:-1]):
            if part in self.namespace_names:
                self.public_calls.append((f"{part}.{parts[index + 1]}", _qualname(self.stack)))
                break
        self.generic_visit(node)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def collect_public_client_namespaces() -> dict[str, type[object]]:
    """Return every public class-typed namespace annotation on the client."""

    hints = typing.get_type_hints(NotebookLMClient)
    return {
        namespace: cls
        for namespace, cls in sorted(hints.items())
        if not namespace.startswith("_") and inspect.isclass(cls)
    }


def audit_public_namespace_contract() -> list[str]:
    """Require compat-audit and live client namespace discovery to agree exactly."""

    discovered = set(collect_public_client_namespaces())
    expected = set(CLIENT_NAMESPACE_ATTRIBUTES)
    if discovered == expected:
        return []
    return [
        "public client namespaces disagree with CLIENT_NAMESPACE_ATTRIBUTES: "
        f"missing={sorted(discovered - expected)}, stale={sorted(expected - discovered)}"
    ]


def collect_public_namespace_methods() -> dict[str, str]:
    """Return every public callable on each annotated client namespace.

    Reading class annotations avoids constructing a client/HTTP session.  The
    MRO walk deliberately includes inherited public helpers, which is why
    ``chat.set_bound_loop`` and ``chat.reset_after_open`` cannot disappear from
    this inventory.
    """
    methods: dict[str, str] = {}
    for namespace, cls in collect_public_client_namespaces().items():
        for base in reversed(cls.__mro__):
            if base is object or not base.__module__.startswith("notebooklm"):
                continue
            for name, raw in vars(base).items():
                if name.startswith("_"):
                    continue
                target = raw.__func__ if isinstance(raw, (classmethod, staticmethod)) else raw
                if inspect.isfunction(target) or inspect.ismethoddescriptor(target):
                    methods[f"{namespace}.{name}"] = f"{base.__module__}.{base.__qualname__}"
    return dict(sorted(methods.items()))


def collect_public_client_members() -> dict[str, dict[str, str]]:
    """Inventory every public root-client method/property across its MRO."""
    members: dict[str, dict[str, str]] = {}
    for base in reversed(NotebookLMClient.__mro__):
        if base is object or not base.__module__.startswith("notebooklm"):
            continue
        for name, raw in vars(base).items():
            if name.startswith("_"):
                continue
            kind: str | None = None
            if isinstance(raw, property):
                kind = "property"
            elif isinstance(raw, classmethod):
                kind = "classmethod"
            elif isinstance(raw, staticmethod):
                kind = "staticmethod"
            elif inspect.isfunction(raw) or inspect.ismethoddescriptor(raw):
                kind = "method"
            if kind is not None:
                members[name] = {
                    "declared_by": f"{base.__module__}.{base.__qualname__}",
                    "kind": kind,
                }
    return dict(sorted(members.items()))


def collect_app_callers(namespace_names: set[str] | None = None) -> dict[str, list[str]]:
    """AST-walk ``_app`` and map namespace method calls to their owners."""
    if namespace_names is None:
        namespace_names = {name.split(".", 1)[0] for name in collect_public_namespace_methods()}
    callers: dict[str, set[str]] = defaultdict(set)
    for path in sorted(APP_ROOT.glob("*.py")):
        relative = path.relative_to(SRC_ROOT).as_posix()
        collector = _ReferenceCollector(relative, namespace_names)
        collector.visit(_parse(path))
        for method, owner in collector.public_calls:
            callers[method].add(f"{relative}:{owner}")
    for site, methods in collect_dynamic_app_dispatches().items():
        for method in methods:
            callers[method].add(site)
    return {method: sorted(owners) for method, owners in sorted(callers.items())}


def _assigned_string_dict(tree: ast.Module, name: str) -> set[str]:
    for statement in tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        if not any(isinstance(target, ast.Name) and target.id == name for target in targets):
            continue
        if isinstance(statement.value, ast.Dict):
            return {
                value
                for node in statement.value.values
                if (value := _literal_string(node)) is not None
            }
    return set()


def _download_registry_attrs() -> set[str]:
    tree = _parse(APP_ROOT / "download_specs.py")
    attrs: set[str] = set()
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        if _attribute_parts(call.func)[-1:] != ("DownloadRegistryEntry",):
            continue
        for keyword in call.keywords:
            if keyword.arg == "download_attr" and (value := _literal_string(keyword.value)):
                attrs.add(value)
    return attrs


def collect_dynamic_app_dispatches() -> dict[str, list[str]]:
    """Resolve the two reviewed data-driven ``_app`` facade dispatch tables."""
    generation_methods = _assigned_string_dict(_parse(APP_ROOT / "generate.py"), "_KIND_TO_METHOD")
    download_methods = _download_registry_attrs()
    return {
        "_app/download.py:_bind_download_fn": sorted(
            f"artifacts.{method}" for method in download_methods
        ),
        "_app/generate.py:execute_generation": sorted(
            f"artifacts.{method}" for method in generation_methods
        ),
    }


def collect_unresolved_app_dispatches() -> list[str]:
    """Find dynamic namespace ``getattr`` sites not covered by a derived registry."""
    known = set(collect_dynamic_app_dispatches())
    unresolved: set[str] = set()
    namespace_names = {name.split(".", 1)[0] for name in collect_public_namespace_methods()}

    class Visitor(ast.NodeVisitor):
        def __init__(self, relative: str) -> None:
            self.relative = relative
            self.stack: list[str] = []

        def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._visit_function(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._visit_function(node)

        def visit_Call(self, node: ast.Call) -> None:
            if _attribute_parts(node.func)[-1:] == ("getattr",) and len(node.args) >= 2:
                base = _attribute_parts(node.args[0])
                if (
                    any(part in namespace_names for part in base)
                    and _literal_string(node.args[1]) is None
                ):
                    site = f"{self.relative}:{_qualname(self.stack)}"
                    if site not in known:
                        unresolved.add(site)
            self.generic_visit(node)

    for path in sorted(APP_ROOT.glob("*.py")):
        relative = path.relative_to(SRC_ROOT).as_posix()
        Visitor(relative).visit(_parse(path))
    return sorted(unresolved)


def collect_rpc_references() -> dict[RPCMethod, dict[str, list[str]]]:
    """AST-walk production code and classify current native references."""
    inventory: dict[RPCMethod, dict[str, set[str]]] = {
        method: defaultdict(set) for method in RPCMethod
    }
    for path in sorted(SRC_ROOT.rglob("*.py")):
        relative = path.relative_to(SRC_ROOT).as_posix()
        if relative in {"rpc/types.py", "_idempotency_policy.py"}:
            continue
        collector = _ReferenceCollector(relative, set())
        collector.visit(_parse(path))
        if relative.startswith("_row_adapters/"):
            role = "decoders"
        elif relative.startswith("_types/"):
            role = "projectors"
        elif relative.startswith("rpc/"):
            role = "protocol_support"
        else:
            role = "support_references"
        for method_name, owner in collector.rpc_references:
            inventory[RPCMethod[method_name]][role].add(f"{relative}:{owner}")
        if role == "support_references":
            for method_name, _variant, owner in collector.rpc_calls:
                inventory[RPCMethod[method_name]]["execution_authorities"].add(
                    f"{relative}:{owner}"
                )
    return {
        method: {role: sorted(sites) for role, sites in sorted(roles.items())}
        for method, roles in inventory.items()
    }


def collect_native_execution_sites() -> dict[NativeKey, list[str]]:
    """Return direct transport-reaching call sites per native method/variant."""
    sites: dict[NativeKey, set[str]] = defaultdict(set)
    for path in sorted(SRC_ROOT.rglob("*.py")):
        relative = path.relative_to(SRC_ROOT).as_posix()
        if relative.startswith(("_row_adapters/", "_types/", "rpc/")) or relative in {
            "_idempotency_policy.py",
        }:
            continue
        collector = _ReferenceCollector(relative, set())
        collector.visit(_parse(path))
        for method_name, variant, owner in collector.rpc_calls:
            sites[(RPCMethod[method_name], variant)].add(f"{relative}:{owner}")
    return {
        key: sorted(values)
        for key, values in sorted(sites.items(), key=lambda item: _native_key_text(item[0]))
    }


GENERIC_RPC_FORWARDERS = frozenset(
    {
        "_notebooks.py:NotebooksAPI._rpc_call",
        "_research.py:ResearchAPI._rpc_call",
        "_sources.py:SourcesAPI._rpc_call",
        "client.py:NotebookLMClient.rpc_call",
    }
)


def collect_unresolved_rpc_dispatches() -> list[str]:
    """Return feature RPC calls whose method/variant cannot be statically resolved."""
    unresolved: set[str] = set()
    for path in sorted(SRC_ROOT.rglob("*.py")):
        relative = path.relative_to(SRC_ROOT).as_posix()
        if relative.startswith(("rpc/", "_row_adapters/", "_types/")) or relative in {
            "_idempotency_policy.py",
            "_rpc_executor.py",
        }:
            continue
        collector = _ReferenceCollector(relative, set())
        collector.visit(_parse(path))
        for owner, field in collector.unresolved_rpc_calls:
            site = f"{relative}:{owner}"
            if site not in GENERIC_RPC_FORWARDERS:
                unresolved.add(f"{site} ({field})")
    return sorted(unresolved)


def collect_function_sites() -> set[str]:
    """Return every production function/method qualname as an exact source site."""
    sites: set[str] = set()

    class Visitor(ast.NodeVisitor):
        def __init__(self, relative: str) -> None:
            self.relative = relative
            self.stack: list[str] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            self.stack.append(node.name)
            sites.add(f"{self.relative}:{_qualname(self.stack)}")
            self.generic_visit(node)
            self.stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._visit_function(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._visit_function(node)

    for path in sorted(SRC_ROOT.rglob("*.py")):
        relative = path.relative_to(SRC_ROOT).as_posix()
        Visitor(relative).visit(_parse(path))
    return sites


def collect_function_ast_fingerprints() -> dict[str, str]:
    """Return stable AST fingerprints for every production function/method."""

    fingerprints: dict[str, str] = {}

    class Visitor(ast.NodeVisitor):
        def __init__(self, relative: str) -> None:
            self.relative = relative
            self.stack: list[str] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            self.stack.append(node.name)
            site = f"{self.relative}:{_qualname(self.stack)}"
            normalized = ast.dump(node, annotate_fields=True, include_attributes=False)
            fingerprints[site] = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            self.generic_visit(node)
            self.stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._visit_function(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._visit_function(node)

    for path in sorted(SRC_ROOT.rglob("*.py")):
        relative = path.relative_to(SRC_ROOT).as_posix()
        Visitor(relative).visit(_parse(path))
    return fingerprints


def collect_function_call_targets() -> dict[str, set[tuple[str, ...]]]:
    """Return call-target attribute paths keyed by exact production function site."""
    calls: dict[str, set[tuple[str, ...]]] = defaultdict(set)

    class Visitor(ast.NodeVisitor):
        def __init__(self, relative: str) -> None:
            self.relative = relative
            self.stack: list[str] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._visit_function(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._visit_function(node)

        def visit_Call(self, node: ast.Call) -> None:
            if self.stack and (target := _attribute_parts(node.func)):
                calls[f"{self.relative}:{_qualname(self.stack)}"].add(target)
            self.generic_visit(node)

    for path in sorted(SRC_ROOT.rglob("*.py")):
        relative = path.relative_to(SRC_ROOT).as_posix()
        Visitor(relative).visit(_parse(path))
    return calls


def _declared_module_exports(relative: str) -> set[str]:
    """Return literal names in one production module's ``__all__``."""

    tree = _parse(SRC_ROOT / relative)
    for statement in tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        if not any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets):
            continue
        if not isinstance(statement.value, (ast.List, ast.Tuple)):
            return set()
        return {
            value for item in statement.value.elts if (value := _literal_string(item)) is not None
        }
    return set()


def collect_app_authority_source_evidence() -> dict[str, dict[str, object]]:
    """Derive helper-body and internal-call-edge evidence for app authorities."""

    call_targets = collect_function_call_targets()
    fingerprints = collect_function_ast_fingerprints()
    evidence: dict[str, dict[str, object]] = {}
    for site, contract in sorted(APP_AUTHORITY_SOURCE_CONTRACTS.items()):
        helper_targets = call_targets.get(site, set())
        observed_required_calls = sorted(
            ".".join(required)
            for required in contract.required_calls
            if any(target[-len(required) :] == required for target in helper_targets)
        )
        internal_callers = sorted(
            caller
            for caller, targets in call_targets.items()
            if any(
                target[-len(contract.caller_target) :] == contract.caller_target
                for target in targets
            )
        )
        evidence[site] = {
            "function_ast_sha256": fingerprints.get(site),
            "observed_required_calls": observed_required_calls,
            "public_export": contract.public_export
            if contract.public_export in _declared_module_exports(site.split(":", 1)[0])
            else None,
            "internal_call_edges": [
                {
                    "caller": caller,
                    "caller_ast_sha256": fingerprints.get(caller),
                    "target": ".".join(contract.caller_target),
                }
                for caller in internal_callers
            ],
        }
    return evidence


def _operation_authorities(
    spec: OperationSpec,
    native_execution_sites: Mapping[NativeKey, list[str]],
    shared_bindings: set[NativeKey],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for binding in spec.native_bindings:
        if binding in shared_bindings:
            rules = SHARED_RPC_AUTHORITY_RULES.get((spec.operation, binding), ())
        else:
            rules = _rules(
                *(
                    (site, "direct semantic binding")
                    for site in native_execution_sites.get(binding, [])
                )
            )
        rows.extend(
            {
                "transport_kind": "rpc",
                "binding": _native_key_text(binding),
                "site": rule.site,
                "discriminator": rule.discriminator,
            }
            for rule in rules
        )
    rows.extend(
        {
            "transport_kind": transport_kind,
            "binding": binding,
            "site": site,
            "discriminator": discriminator,
        }
        for transport_kind, binding, site, discriminator in NON_RPC_AUTHORITY_RULES.get(
            spec.operation, ()
        )
    )
    rows.extend(
        {
            "transport_kind": "orchestrator",
            "binding": rule.binding,
            "site": rule.site,
            "discriminator": rule.discriminator,
        }
        for rule in APP_OPERATION_AUTHORITIES.get(spec.operation, ())
    )
    return sorted(
        rows,
        key=lambda row: (
            row["transport_kind"],
            row["binding"],
            row["site"],
            row["discriminator"],
        ),
    )


def audit_operation_authorities() -> list[str]:
    """Validate exact RPC, non-RPC, and app authority rules bidirectionally."""
    errors: list[str] = []
    native_sites = collect_native_execution_sites()
    specs_by_native: dict[NativeKey, list[OperationSpec]] = defaultdict(list)
    for spec in OPERATION_SPECS:
        for binding in spec.native_bindings:
            specs_by_native[binding].append(spec)
    shared = {binding for binding, specs in specs_by_native.items() if len(specs) > 1}
    expected_rule_keys = {
        (spec.operation, binding) for binding in shared for spec in specs_by_native[binding]
    }
    actual_rule_keys = set(SHARED_RPC_AUTHORITY_RULES)
    if missing := sorted(
        f"{operation.value}/{_native_key_text(binding)}"
        for operation, binding in expected_rule_keys - actual_rule_keys
    ):
        errors.append(f"shared operation bindings lack exact authority rules: {missing}")
    if stale := sorted(
        f"{operation.value}/{_native_key_text(binding)}"
        for operation, binding in actual_rule_keys - expected_rule_keys
    ):
        errors.append(f"stale shared operation authority rules: {stale}")
    for (operation, binding), rules in SHARED_RPC_AUTHORITY_RULES.items():
        valid_sites = set(native_sites.get(binding, []))
        for rule in rules:
            if rule.site not in valid_sites:
                errors.append(
                    f"{operation.value}/{_native_key_text(binding)} authority is not a direct "
                    f"binding site: {rule.site}"
                )
            if not rule.discriminator.strip():
                errors.append(
                    f"{operation.value}/{_native_key_text(binding)} has an empty discriminator"
                )

    function_sites = collect_function_sites()
    manual_non_rpc_sites = {
        site
        for rules in NON_RPC_AUTHORITY_RULES.values()
        for _kind, _binding, site, _discriminator in rules
    }
    if manual_non_rpc_sites != set(NON_RPC_SOURCE_CONTRACTS):
        errors.append(
            "non-RPC authority/source contracts disagree: "
            f"uncontracted={sorted(manual_non_rpc_sites - set(NON_RPC_SOURCE_CONTRACTS))}, "
            f"unallocated={sorted(set(NON_RPC_SOURCE_CONTRACTS) - manual_non_rpc_sites)}"
        )
    call_targets = collect_function_call_targets()
    for site, required_targets in NON_RPC_SOURCE_CONTRACTS.items():
        actual_targets = call_targets.get(site, set())
        for required in required_targets:
            if not any(target[-len(required) :] == required for target in actual_targets):
                errors.append(
                    f"non-RPC authority {site} no longer reaches transport call "
                    f"{'.'.join(required)}"
                )
    manual_app_sites = {rule.site for rules in APP_OPERATION_AUTHORITIES.values() for rule in rules}
    if unallocated_contracts := sorted(set(APP_AUTHORITY_SOURCE_CONTRACTS) - manual_app_sites):
        errors.append(f"app authority source contracts are unallocated: {unallocated_contracts}")
    public_helper_sites = {
        rule.site
        for rules in APP_OPERATION_AUTHORITIES.values()
        for rule in rules
        if rule.binding == "public_helper"
    }
    if uncontracted_helpers := sorted(public_helper_sites - set(APP_AUTHORITY_SOURCE_CONTRACTS)):
        errors.append(
            f"public-helper app authorities lack source contracts: {uncontracted_helpers}"
        )
    fingerprints = collect_function_ast_fingerprints()
    for site, contract in APP_AUTHORITY_SOURCE_CONTRACTS.items():
        if site not in fingerprints:
            errors.append(f"app authority helper no longer exists: {site}")
            continue
        actual_targets = call_targets.get(site, set())
        for required in contract.required_calls:
            if not any(target[-len(required) :] == required for target in actual_targets):
                errors.append(
                    f"app authority {site} no longer reaches required loop call "
                    f"{'.'.join(required)}"
                )
        module_exports = _declared_module_exports(site.split(":", 1)[0])
        if contract.public_export not in module_exports:
            errors.append(f"app authority {site} lost public export {contract.public_export}")
        internal_callers = {
            caller
            for caller, targets in call_targets.items()
            if any(
                target[-len(contract.caller_target) :] == contract.caller_target
                for target in targets
            )
        }
        if internal_callers != {contract.internal_caller}:
            errors.append(
                f"app authority {site} internal callers changed: "
                f"expected={[contract.internal_caller]}, actual={sorted(internal_callers)}"
            )
    for spec in OPERATION_SPECS:
        expected_app = {rule.site for rule in APP_OPERATION_AUTHORITIES.get(spec.operation, ())}
        actual_app = set(spec.app_authorities)
        if expected_app != actual_app:
            errors.append(
                f"{spec.operation.value} app authorities disagree with reviewed rules: "
                f"spec={sorted(actual_app)}, rules={sorted(expected_app)}"
            )
        expected_paths = {
            binding
            for _kind, binding, _site, _discriminator in NON_RPC_AUTHORITY_RULES.get(
                spec.operation, ()
            )
        }
        if set(spec.web_paths) != expected_paths:
            errors.append(
                f"{spec.operation.value} non-RPC bindings disagree: "
                f"spec={sorted(spec.web_paths)}, rules={sorted(expected_paths)}"
            )
        for row in _operation_authorities(spec, native_sites, shared):
            if row["site"] not in function_sites:
                errors.append(
                    f"{spec.operation.value} authority path no longer exists: {row['site']}"
                )
    for binding, reason in NATIVE_BINDING_DISPOSITIONS.items():
        if native_sites.get(binding):
            errors.append(
                f"{_native_key_text(binding)} is disposed as callsite-free but now executes at "
                f"{native_sites[binding]} ({reason})"
            )
    if unresolved := collect_unresolved_rpc_dispatches():
        errors.append(f"unresolved feature RPC calls: {unresolved}")
    if unresolved_app := collect_unresolved_app_dispatches():
        errors.append(f"unresolved dynamic _app namespace dispatches: {unresolved_app}")
    return errors


def audit_recency_contracts() -> list[str]:
    """Validate structured GET_NOTEBOOK counts and their source authorities."""
    errors: list[str] = []
    required = {spec.operation for spec in OPERATION_SPECS if spec.recency_effect != "none"}
    if missing := sorted(operation.value for operation in required - set(RECENCY_CONTRACTS)):
        errors.append(f"recency-effect prose lacks a structured contract: {missing}")
    if stale := sorted(operation.value for operation in set(RECENCY_CONTRACTS) - required):
        errors.append(f"structured recency contracts have no reviewed operation row: {stale}")
    specs = {spec.operation: spec for spec in OPERATION_SPECS}
    for operation, rules in RECENCY_CONTRACTS.items():
        spec = specs.get(operation)
        if spec is None:
            continue
        covered_public = {method for rule in rules for method in rule.public_methods}
        if covered_public != set(spec.public_methods):
            errors.append(
                f"{operation.value} recency contracts do not partition every public method: "
                f"missing={sorted(set(spec.public_methods) - covered_public)}, "
                f"unrelated={sorted(covered_public - set(spec.public_methods))}"
            )
        valid_authorities = {
            rule.site
            for rule in SHARED_RPC_AUTHORITY_RULES.get((operation, _b(RPCMethod.GET_NOTEBOOK)), ())
        }
        for rule in rules:
            if rule.minimum_calls < 0 or (
                rule.maximum_calls is not None and rule.maximum_calls < rule.minimum_calls
            ):
                errors.append(f"{operation.value} has an invalid recency call range")
            if not rule.unit or not rule.condition.strip():
                errors.append(f"{operation.value} recency contract lacks unit/condition")
            if not set(rule.public_methods) <= set(spec.public_methods):
                errors.append(f"{operation.value} recency contract names unrelated public methods")
            if rule.maximum_calls != 0 and not rule.authority_sites:
                errors.append(f"{operation.value} recency contract lacks GET_NOTEBOOK authorities")
            if not set(rule.authority_sites) <= valid_authorities:
                errors.append(
                    f"{operation.value} recency authorities disagree with exact RPC rules: "
                    f"{sorted(set(rule.authority_sites) - valid_authorities)}"
                )

    metadata_rules = RECENCY_CONTRACTS.get(Operation.NOTEBOOK_METADATA, ())
    if len(metadata_rules) != 1 or (
        metadata_rules[0].minimum_calls,
        metadata_rules[0].maximum_calls,
        set(metadata_rules[0].authority_sites),
    ) != (2, 2, {_GET_TYPED, _GET_SOURCES}):
        errors.append(
            "notebook.get_metadata must pin exactly two distinct GET_NOTEBOOK authorities"
        )
    update_rules = RECENCY_CONTRACTS.get(Operation.NOTEBOOK_UPDATE, ())
    if len(update_rules) != 1 or (
        update_rules[0].minimum_calls,
        update_rules[0].maximum_calls,
        set(update_rules[0].public_methods),
    ) != (1, 1, set(_p("notebooks", "update", "rename", "set_emoji"))):
        errors.append("notebook.update must pin exactly one GET_NOTEBOOK for every public mutation")
    chat_rules = RECENCY_CONTRACTS.get(Operation.CHAT_CONFIGURE, ())
    chat_ranges = {
        tuple(sorted(rule.public_methods)): (rule.minimum_calls, rule.maximum_calls)
        for rule in chat_rules
    }
    if chat_ranges != {
        ("chat.get_settings",): (1, 1),
        ("chat.configure", "chat.set_mode"): (0, 0),
    }:
        errors.append("chat.configure must split read and mutation recency conditions")
    metadata_tree = _parse(SRC_ROOT / "_notebook_metadata.py")
    metadata_fn = _find_class_method(metadata_tree, "NotebookMetadataService", "get_metadata")
    gather_shapes = []
    if metadata_fn is not None:
        for call in (node for node in ast.walk(metadata_fn) if isinstance(node, ast.Call)):
            if _attribute_parts(call.func)[-2:] == ("asyncio", "gather"):
                gather_shapes.append(
                    [_attribute_parts(arg.func) for arg in call.args if isinstance(arg, ast.Call)]
                )
    if gather_shapes != [[("self", "_get_notebook"), ("self", "_source_lister", "list")]]:
        errors.append(
            "NotebookMetadataService.get_metadata must gather exactly notebook lookup + source list"
        )

    notebooks_tree = _parse(SRC_ROOT / "_notebooks.py")
    update_fn = _find_class_method(notebooks_tree, "NotebooksAPI", "update")
    if update_fn is None or _call_count(update_fn, ("self", "get")) != 1:
        errors.append("NotebooksAPI.update must perform exactly one unconditional self.get call")
    elif any(
        argument.arg == "return_object"
        for argument in (*update_fn.args.args, *update_fn.args.kwonlyargs)
    ):
        errors.append("NotebooksAPI.update recency contract forbids a return_object bypass")

    chat_tree = _parse(SRC_ROOT / "_chat" / "api.py")
    expected_chat_gets = {"configure": 0, "set_mode": 0, "get_settings": 1}
    for method_name, expected_gets in expected_chat_gets.items():
        method_node = _find_class_method(chat_tree, "ChatAPI", method_name)
        actual_gets = (
            _rpc_binding_call_count(method_node, RPCMethod.GET_NOTEBOOK)
            if method_node is not None
            else -1
        )
        if actual_gets != expected_gets:
            errors.append(
                f"ChatAPI.{method_name} must contain exactly {expected_gets} GET_NOTEBOOK binding(s)"
            )
    return errors


def _find_class_method(
    tree: ast.Module,
    class_name: str,
    method_name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for statement in tree.body:
        if not isinstance(statement, ast.ClassDef) or statement.name != class_name:
            continue
        return next(
            (
                node
                for node in statement.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == method_name
            ),
            None,
        )
    return None


def _call_count(node: ast.AST, suffix: tuple[str, ...]) -> int:
    return sum(
        1
        for call in ast.walk(node)
        if isinstance(call, ast.Call) and _attribute_parts(call.func)[-len(suffix) :] == suffix
    )


def _rpc_binding_call_count(node: ast.AST, method: RPCMethod) -> int:
    count = 0
    for call in (item for item in ast.walk(node) if isinstance(item, ast.Call)):
        if _attribute_parts(call.func)[-1:] not in {("rpc_call",), ("_rpc_call",)}:
            continue
        method_node = (
            call.args[0]
            if call.args
            else next(
                (keyword.value for keyword in call.keywords if keyword.arg == "method"),
                None,
            )
        )
        if method_node is not None and any(
            isinstance(item, ast.Attribute)
            and _attribute_parts(item)[-2:] == ("RPCMethod", method.name)
            for item in ast.walk(method_node)
        ):
            count += 1
    return count
