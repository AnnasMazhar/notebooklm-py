"""Golden, override, and live-RPC registry evidence derivation."""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from notebooklm._idempotency import IDEMPOTENCY_REGISTRY
from notebooklm.rpc import RPCMethod

if __package__:
    from ._operation_catalog_ast import _attribute_parts, _literal_string, _parse
    from ._operation_catalog_specs import NativeKey, _b
else:  # pragma: no cover - direct script execution
    from _operation_catalog_ast import _attribute_parts, _literal_string, _parse
    from _operation_catalog_specs import NativeKey, _b

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "notebooklm"
GOLDEN_PATH = REPO_ROOT / "tests" / "_guardrails" / "test_golden_decode_coverage.py"
WEB_EXECUTION_RUNTIME_PATH = SRC_ROOT / "_web" / "runtime.py"
RPC_REGISTRY_EVIDENCE_PATH = REPO_ROOT / "scripts" / "operation_catalog_rpc_registry.json"

_OMISSION_DISPOSITIONS: Mapping[str, str] = {
    "current": "unsupported_current_product_surface",
    "enterprise": "excluded_enterprise_surface",
    "other": "excluded_non_consumer_or_unclassified_service",
}
_METHOD_PATH_RE = re.compile(r"^/[A-Za-z][A-Za-z0-9_]*\.[A-Za-z][A-Za-z0-9_]*$")
_CAPTURE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def collect_golden_evidence() -> tuple[dict[RPCMethod, list[list[str]]], dict[RPCMethod, str]]:
    """Read ``GOLDEN_COVERAGE``/``GOLDEN_EXEMPT`` without importing tests.

    The values are resolved from the guardrail AST so the catalog uses the
    existing authority while remaining importable outside pytest.
    """
    tree = _parse(GOLDEN_PATH)
    string_constants: dict[str, str] = {}
    tuple_constants: dict[str, tuple[str, ...]] = {}
    coverage_node: ast.Dict | None = None
    exempt_node: ast.Dict | None = None
    for statement in tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        value = statement.value
        for target in targets:
            if not isinstance(target, ast.Name) or value is None:
                continue
            text = _literal_string(value)
            if text is not None:
                string_constants[target.id] = text
            elif isinstance(value, ast.Tuple):
                items = tuple(_literal_string(item) or "" for item in value.elts)
                if all(items):
                    tuple_constants[target.id] = items
            if target.id == "GOLDEN_COVERAGE" and isinstance(value, ast.Dict):
                coverage_node = value
            if target.id == "GOLDEN_EXEMPT" and isinstance(value, ast.Dict):
                exempt_node = value

    def method_key(node: ast.AST | None) -> RPCMethod | None:
        parts = _attribute_parts(node) if node is not None else ()
        reversed_parts = iter(reversed(parts))
        method_name = next(reversed_parts, None)
        owner_name = next(reversed_parts, None)
        if owner_name != "RPCMethod" or method_name is None:
            return None
        return RPCMethod.__members__.get(method_name)

    def pointer(node: ast.AST) -> list[str] | None:
        if isinstance(node, ast.Name):
            tuple_values = tuple_constants.get(node.id)
            return list(tuple_values) if tuple_values else None
        if not isinstance(node, ast.Tuple) or len(node.elts) != 2:
            return None
        resolved_values: list[str] = []
        for item in node.elts:
            if isinstance(item, ast.Name):
                value = string_constants.get(item.id)
            else:
                value = _literal_string(item)
            if value is None:
                return None
            resolved_values.append(value)
        return resolved_values

    coverage: dict[RPCMethod, list[list[str]]] = {}
    if coverage_node is not None:
        for key_node, value_node in zip(coverage_node.keys, coverage_node.values, strict=True):
            method = method_key(key_node)
            if method is None or not isinstance(value_node, (ast.Tuple, ast.List)):
                continue
            pointers = [resolved for item in value_node.elts if (resolved := pointer(item))]
            coverage[method] = pointers

    exemptions: dict[RPCMethod, str] = {}
    if exempt_node is not None:
        for key_node, value_node in zip(exempt_node.keys, exempt_node.values, strict=True):
            method = method_key(key_node)
            if method is None:
                continue
            if isinstance(value_node, ast.Name):
                reason = string_constants.get(value_node.id)
            else:
                reason = _literal_string(value_node)
            if reason is not None:
                exemptions[method] = reason
    return coverage, exemptions


_VARIANT_GOLDEN_QUALNAMES: Mapping[NativeKey, frozenset[str]] = {
    _b(RPCMethod.ADD_SOURCE, "text"): frozenset(
        {"TestSourceMutationsGoldenDecoded::test_add_text_decoded_golden"}
    ),
    _b(RPCMethod.ADD_SOURCE, "url"): frozenset(
        {"TestSourceMutationsGoldenDecoded::test_add_url_decoded_golden"}
    ),
    _b(RPCMethod.CREATE_NOTE, "plain"): frozenset(
        {
            "TestNotesGoldenDecoded::test_create_decoded_golden",
            "TestMindMapsGoldenDecoded::test_generate_mind_map_decoded_golden",
        }
    ),
}


def collect_variant_golden_evidence() -> dict[NativeKey, tuple[str, Any, str]]:
    """Project method-family goldens honestly onto native variant rows.

    The existing guardrail is intentionally method-family scoped.  The catalog
    is variant scoped, so families with registered wire variants require an
    explicit pointer allocation instead of inheriting every sibling's proof.
    """
    coverage, exemptions = collect_golden_evidence()
    rows: dict[NativeKey, tuple[str, Any, str]] = {}
    variant_methods = {
        method
        for method, variant, _entry in IDEMPOTENCY_REGISTRY.iter_entries()
        if variant is not None
    }
    for method, variant, _entry in IDEMPOTENCY_REGISTRY.iter_entries():
        key = (method, variant)
        if method in exemptions:
            rows[key] = ("golden_exempt", exemptions[method], "method_contract")
            continue
        if method not in coverage:
            rows[key] = ("not_recorded", [], "variant")
            continue
        if method not in variant_methods:
            rows[key] = ("golden_covered", coverage[method], "method_family")
            continue
        selected_qualnames = _VARIANT_GOLDEN_QUALNAMES.get(key, frozenset())
        selected = [pointer for pointer in coverage[method] if pointer[1] in selected_qualnames]
        rows[key] = (
            "golden_covered" if selected else "not_recorded",
            selected,
            "variant",
        )
    return rows


def _native_key_text(binding: NativeKey) -> str:
    method, variant = binding
    return f"{method.name}:{variant if variant is not None else '<default>'}"


def _override_honored() -> tuple[bool, dict[str, Any]]:
    """Prove method -> override -> URL/body/decode/dispatch dataflow.

    This is deliberately stronger than counting a resolver name.  The same
    local must feed the encoded request body, URL builder, and decoder, while
    the encoded request is handed to the transport's request builder.
    """
    tree = _parse(WEB_EXECUTION_RUNTIME_PATH)
    runtime_owners = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "WebExecutionRuntime"
    ]
    execute_once_methods = [
        node
        for owner in runtime_owners
        for node in owner.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_execute_once"
    ]
    execute_once = execute_once_methods[0] if len(runtime_owners) == len(execute_once_methods) == 1 else None
    checks: dict[str, bool] = {
        "method_to_resolver": False,
        "resolved_id_to_body": False,
        "resolved_id_to_url": False,
        "encoded_body_to_request_builder": False,
        "request_builder_to_dispatch": False,
        "resolved_id_to_decode": False,
    }
    if execute_once is not None:
        for node in ast.walk(execute_once):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = node.value
                target_names = {target.id for target in targets if isinstance(target, ast.Name)}
                if "resolved_id" in target_names and isinstance(value, ast.Call):
                    checks["method_to_resolver"] = _attribute_parts(value.func)[-1:] == (
                        "resolve_rpc_id",
                    ) and [_attribute_parts(arg) for arg in value.args] == [
                        ("method", "name"),
                        ("method", "value"),
                    ]
                if "rpc_request" in target_names and isinstance(value, ast.Call):
                    checks["resolved_id_to_body"] = _attribute_parts(value.func)[-1:] == (
                        "encode_rpc_request",
                    ) and any(
                        keyword.arg == "rpc_id_override"
                        and isinstance(keyword.value, ast.Name)
                        and keyword.value.id == "resolved_id"
                        for keyword in value.keywords
                    )
            if not isinstance(node, ast.Call):
                continue
            call_name = _attribute_parts(node.func)[-1:]
            if call_name == ("build_url",):
                checks["resolved_id_to_url"] = any(
                    keyword.arg == "rpc_id_override"
                    and isinstance(keyword.value, ast.Name)
                    and keyword.value.id == "resolved_id"
                    for keyword in node.keywords
                )
            elif call_name == ("build_request_body",):
                checks["encoded_body_to_request_builder"] = bool(
                    node.args
                    and isinstance(node.args[0], ast.Name)
                    and node.args[0].id == "rpc_request"
                )
            elif call_name == ("perform_authed_post",):
                checks["request_builder_to_dispatch"] = any(
                    keyword.arg == "build_request"
                    and isinstance(keyword.value, ast.Name)
                    and keyword.value.id == "_build"
                    for keyword in node.keywords
                )
            elif call_name == ("_decode_response",):
                checks["resolved_id_to_decode"] = (
                    len(node.args) >= 2
                    and isinstance(node.args[1], ast.Name)
                    and node.args[1].id == "resolved_id"
                )
    return all(checks.values()), {
        "source_contract": "_web/runtime.py:WebExecutionRuntime._execute_once",
        "dataflow": checks,
        "behavior_test": (
            "tests/unit/test_operation_catalog.py::"
            "test_runtime_rpc_override_reaches_url_body_and_decoder_for_every_binding"
        ),
    }


def _normalize_registry_omissions(
    snapshot: Mapping[str, Any] | None,
) -> list[dict[str, str]]:
    """Project ``capture_rpc_registry.py --json`` unmapped rows deterministically."""
    if snapshot is None:
        return []
    raw = snapshot.get("unmapped", {})
    if not isinstance(raw, Mapping):
        raise ValueError("rpc registry snapshot 'unmapped' must be an object")
    rows: list[dict[str, str]] = []
    for rpc_id, value in sorted(raw.items()):
        if not isinstance(rpc_id, str) or not isinstance(value, Mapping):
            raise ValueError("rpc registry snapshot contains a malformed unmapped row")
        method = value.get("method")
        family = value.get("family")
        if not isinstance(method, str) or not isinstance(family, str):
            raise ValueError("rpc registry unmapped rows require string method/family")
        disposition = _OMISSION_DISPOSITIONS.get(family)
        if disposition is None:
            raise ValueError(f"rpc registry unmapped row has unknown family {family!r}")
        rows.append(
            {
                "rpc_id": rpc_id,
                "method": method,
                "family": family,
                "disposition": disposition,
            }
        )
    return rows


def load_rpc_registry_evidence(path: Path = RPC_REGISTRY_EVIDENCE_PATH) -> Mapping[str, Any]:
    """Load the committed scrubbed output that makes baseline derivation offline.

    Refresh it from a live authenticated capture with the ``source_command``
    recorded in the file, retaining only the non-secret fields in the evidence
    schema.  The ADR-0022 derive never performs network or credential I/O.
    """
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("committed RPC registry evidence must be a JSON object")
    return value


def audit_rpc_registry_evidence(snapshot: Mapping[str, Any]) -> list[str]:
    """Validate the committed capture is non-vacuous, internally consistent evidence."""
    errors: list[str] = []
    required_metadata = {
        "schema_version",
        "captured_on",
        "source_command",
        "capture_kind",
        "scrubbed",
        "registry_total",
        "counts",
        "confirmed",
        "absent",
        "present_unparsed",
        "unmapped",
    }
    if set(snapshot) != required_metadata:
        errors.append(
            "RPC registry evidence top-level schema differs: "
            f"missing={sorted(required_metadata - set(snapshot))}, "
            f"unexpected={sorted(set(snapshot) - required_metadata)}"
        )
    if missing := sorted(required_metadata - set(snapshot)):
        errors.append(f"RPC registry evidence metadata is incomplete: {missing}")
    if snapshot.get("schema_version") != 1:
        errors.append("RPC registry evidence schema_version must be 1")
    captured_on = snapshot.get("captured_on")
    if not isinstance(captured_on, str) or _CAPTURE_DATE_RE.fullmatch(captured_on) is None:
        errors.append("RPC registry evidence captured_on must be an ISO date")
    if snapshot.get("source_command") != "uv run python scripts/capture_rpc_registry.py --json":
        errors.append("RPC registry evidence source_command is not the canonical capture command")
    if snapshot.get("capture_kind") != "public_web_bundle" or snapshot.get("scrubbed") is not True:
        errors.append("RPC registry evidence must identify a scrubbed public web-bundle capture")
    counts = snapshot.get("counts")
    if not isinstance(counts, Mapping):
        return [*errors, "RPC registry evidence counts must be an object"]
    count_keys = {"confirmed", "absent", "present_unparsed", "unmapped"}
    if set(counts) != count_keys:
        errors.append("RPC registry evidence counts has unexpected or missing fields")
    for key in count_keys:
        rows = snapshot.get(key)
        if not isinstance(rows, Mapping):
            errors.append(f"RPC registry evidence {key!r} must be an object")
            continue
        if counts.get(key) != len(rows):
            errors.append(
                f"RPC registry evidence count mismatch for {key}: "
                f"counts={counts.get(key)!r}, rows={len(rows)}"
            )
        if not isinstance(counts.get(key), int) or counts.get(key, -1) < 0:
            errors.append(f"RPC registry evidence count {key!r} must be a non-negative integer")
    confirmed = snapshot.get("confirmed")
    if counts.get("confirmed") != len(RPCMethod):
        errors.append(
            "RPC registry evidence confirmed count must cover every current RPCMethod "
            f"({counts.get('confirmed')!r} != {len(RPCMethod)})"
        )
    if isinstance(confirmed, Mapping):
        expected_names = {method.value: method.name for method in RPCMethod}
        captured_names: dict[str, Any] = {}
        for rpc_id, row in confirmed.items():
            if not isinstance(rpc_id, str) or not isinstance(row, Mapping):
                errors.append("RPC registry evidence contains a malformed confirmed row")
                continue
            captured_names[rpc_id] = row.get("name")
            method_path = row.get("method")
            if not isinstance(method_path, str) or _METHOD_PATH_RE.fullmatch(method_path) is None:
                errors.append(
                    f"RPC registry confirmed row {rpc_id!r} lacks a full /Service.Method path"
                )
            if set(row) != {"name", "method"}:
                errors.append(f"RPC registry confirmed row {rpc_id!r} has unexpected fields")
        if captured_names != expected_names:
            errors.append("RPC registry evidence confirmed rows do not match current RPCMethod ids")
    if snapshot.get("absent"):
        errors.append("RPC registry evidence reports active RPC ids absent from the live bundle")
    if snapshot.get("present_unparsed"):
        errors.append("RPC registry evidence reports active RPC ids present but unparsed")
    unmapped = snapshot.get("unmapped")
    if isinstance(unmapped, Mapping):
        for rpc_id, row in unmapped.items():
            if not isinstance(rpc_id, str) or not isinstance(row, Mapping):
                errors.append("RPC registry evidence contains a malformed unmapped row")
                continue
            method_path = row.get("method")
            if set(row) != {"method", "family"}:
                errors.append(f"RPC registry unmapped row {rpc_id!r} has unexpected fields")
            if not isinstance(method_path, str) or _METHOD_PATH_RE.fullmatch(method_path) is None:
                errors.append(
                    f"RPC registry unmapped row {rpc_id!r} lacks a full /Service.Method path"
                )
    try:
        omissions = _normalize_registry_omissions(snapshot)
    except ValueError as exc:
        errors.append(str(exc))
    else:
        if not omissions:
            errors.append("RPC registry omissions evidence must not be empty/vacuous")
        if not any(row["family"] == "current" for row in omissions):
            errors.append("RPC registry evidence must inventory current-family product omissions")
    registry_total = snapshot.get("registry_total")
    confirmed_count = counts.get("confirmed")
    unmapped_count = counts.get("unmapped")
    observed_total = (
        confirmed_count + unmapped_count
        if isinstance(confirmed_count, int) and isinstance(unmapped_count, int)
        else -1
    )
    if not isinstance(registry_total, int) or registry_total != observed_total:
        errors.append(
            "RPC registry evidence registry_total must equal confirmed + unmapped "
            f"({registry_total!r} != {observed_total!r})"
        )
    return errors


def audit_live_registry_against_evidence(snapshot: Mapping[str, Any]) -> list[str]:
    """Compare a fresh ``capture_rpc_registry --json`` result to committed evidence."""
    errors: list[str] = []
    committed_snapshot = load_rpc_registry_evidence()
    buckets = ("confirmed", "absent", "present_unparsed", "unmapped")
    required_top_level = {
        *buckets,
        "enums",
        "quota_codes",
        "proto_assertions",
        "counts",
    }
    if set(snapshot) != required_top_level:
        errors.append(
            "fresh RPC registry capture top-level schema differs: "
            f"missing={sorted(required_top_level - set(snapshot))}, "
            f"unexpected={sorted(set(snapshot) - required_top_level)}"
        )
    enums = snapshot.get("enums")
    enum_buckets = ("changed", "stale", "new", "unparsed")
    if not isinstance(enums, Mapping) or set(enums) != set(enum_buckets):
        errors.append("fresh RPC registry capture enums must contain changed/stale/new/unparsed")
    else:
        for bucket in enum_buckets:
            if not isinstance(enums[bucket], list):
                errors.append(f"fresh RPC registry capture enums.{bucket} must be a list")
            elif not all(isinstance(row, Mapping) for row in enums[bucket]):
                errors.append(f"fresh RPC registry capture enums.{bucket} rows must be objects")
    quota_codes = snapshot.get("quota_codes")
    if not isinstance(quota_codes, Mapping) or not all(
        isinstance(code, str) and isinstance(message, str) for code, message in quota_codes.items()
    ):
        errors.append("fresh RPC registry capture quota_codes must be a string mapping")
    elif not quota_codes:
        errors.append("fresh RPC registry capture quota_codes must be non-vacuous")
    proto_assertions = snapshot.get("proto_assertions")
    if not isinstance(proto_assertions, list) or not all(
        isinstance(assertion, str) for assertion in proto_assertions
    ):
        errors.append("fresh RPC registry capture proto_assertions must be a string list")
    elif not proto_assertions:
        errors.append("fresh RPC registry capture proto_assertions must be non-vacuous")
    counts = snapshot.get("counts")
    if not isinstance(counts, Mapping):
        errors.append("fresh RPC registry capture counts must be an object")
    else:
        expected_count_keys = {
            *buckets,
            "ours",
            *(f"enum_{bucket}" for bucket in enum_buckets),
        }
        if set(counts) != expected_count_keys:
            errors.append("fresh RPC registry capture counts has unexpected or missing fields")
        for key in expected_count_keys:
            if not isinstance(counts.get(key), int) or counts.get(key, -1) < 0:
                errors.append(
                    f"fresh RPC registry capture count {key!r} must be a non-negative integer"
                )
        for bucket in buckets:
            rows = snapshot.get(bucket)
            if not isinstance(rows, Mapping):
                errors.append(f"fresh RPC registry capture {bucket!r} must be an object")
            elif counts.get(bucket) != len(rows):
                errors.append(
                    f"fresh RPC registry capture count mismatch for {bucket}: "
                    f"counts={counts.get(bucket)!r}, rows={len(rows)}"
                )
        if counts.get("ours") != len(RPCMethod):
            errors.append("fresh RPC registry capture did not inspect every current RPCMethod")
        if isinstance(enums, Mapping):
            for bucket in enum_buckets:
                rows = enums.get(bucket)
                if isinstance(rows, list) and counts.get(f"enum_{bucket}") != len(rows):
                    errors.append(
                        f"fresh RPC registry enum count mismatch for {bucket}: "
                        f"counts={counts.get(f'enum_{bucket}')!r}, rows={len(rows)}"
                    )
        confirmed_count = counts.get("confirmed")
        unmapped_count = counts.get("unmapped")
        fresh_total = (
            confirmed_count + unmapped_count
            if isinstance(confirmed_count, int) and isinstance(unmapped_count, int)
            else 0
        )
        committed_total = committed_snapshot.get("registry_total")
        if isinstance(committed_total, int) and fresh_total < committed_total:
            errors.append(
                "fresh RPC registry capture is truncated: "
                f"{fresh_total} parsed registrations < committed "
                f"{committed_total}"
            )
    if snapshot.get("absent"):
        errors.append("fresh RPC registry capture reports active ids absent from the live bundle")
    if snapshot.get("present_unparsed"):
        errors.append("fresh RPC registry capture reports active ids present but unparsed")
    fresh_confirmed = snapshot.get("confirmed")
    if isinstance(fresh_confirmed, Mapping):
        for rpc_id, row in fresh_confirmed.items():
            if not isinstance(rpc_id, str) or not isinstance(row, Mapping):
                errors.append("fresh RPC registry capture contains a malformed confirmed row")
                continue
            if set(row) != {"name", "method"}:
                errors.append(f"fresh RPC registry confirmed row {rpc_id!r} has unexpected fields")
            method_path = row.get("method")
            if not isinstance(method_path, str) or _METHOD_PATH_RE.fullmatch(method_path) is None:
                errors.append(
                    f"fresh RPC registry confirmed row {rpc_id!r} lacks a full /Service.Method path"
                )
    fresh_unmapped = snapshot.get("unmapped")
    if isinstance(fresh_unmapped, Mapping):
        for rpc_id, row in fresh_unmapped.items():
            if not isinstance(rpc_id, str) or not isinstance(row, Mapping):
                errors.append("fresh RPC registry capture contains a malformed unmapped row")
                continue
            if set(row) != {"method", "family"}:
                errors.append(f"fresh RPC registry unmapped row {rpc_id!r} has unexpected fields")
            method_path = row.get("method")
            if not isinstance(method_path, str) or _METHOD_PATH_RE.fullmatch(method_path) is None:
                errors.append(
                    f"fresh RPC registry unmapped row {rpc_id!r} lacks a full /Service.Method path"
                )
    try:
        fresh = _normalize_registry_omissions(snapshot)
        committed = _normalize_registry_omissions(committed_snapshot)
    except ValueError as exc:
        return [*errors, str(exc)]
    if fresh != committed:
        fresh_ids = {row["rpc_id"] for row in fresh}
        committed_ids = {row["rpc_id"] for row in committed}
        errors.append(
            "fresh live RPC omissions differ from committed evidence; refresh "
            "scripts/operation_catalog_rpc_registry.json "
            f"(added={sorted(fresh_ids - committed_ids)}, "
            f"removed={sorted(committed_ids - fresh_ids)})"
        )
    committed_confirmed = committed_snapshot.get("confirmed")
    if fresh_confirmed != committed_confirmed:
        errors.append(
            "fresh live confirmed id/name/method projection differs from committed evidence"
        )
    return errors
