"""Closed-world adapter JSON sink reachability allocations."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from scripts.audit_adapter_json_sinks import (
    AdapterJsonSink,
    assert_exact_sink_dispositions,
    assert_no_unreviewed_cli_json_serialization,
    assert_no_unreviewed_direct_json_emissions,
    assert_supported_adapter_registrations,
    discover_adapter_json_sinks,
    fingerprint_adapter_evidence,
    fingerprint_adapter_helpers,
)
from scripts.audit_adapter_projection_paths import (
    PrivateDataclassProjectionPath,
    discover_private_dataclass_projection_paths,
)

_ALLOCATION_PATH = Path(__file__).with_name("adapter_sink_allocations.json")
_PRIVATE_PATH_ALLOCATION_PATH = Path(__file__).with_name("adapter_private_path_allocations.json")

_NON_PUBLIC_CATEGORIES = {
    "cli-scalar-success": (
        "reviewed CLI success envelope contains request identifiers, literals, or private "
        "transport DTO scalars only; no public dataclass projection reaches this site"
    ),
    "cli-scalar-error": (
        "reviewed CLI error extra contains exception/private DTO scalars only; no public "
        "dataclass projection reaches this error site"
    ),
    "mcp-scalar-result": (
        "reviewed MCP result contains request identifiers, confirmation fields, or private "
        "transport DTO scalars only; no public dataclass projection reaches this site"
    ),
    "rest-scalar-response": (
        "reviewed REST response contains request identifiers or private transport DTO scalars "
        "only; no public dataclass projection reaches this site"
    ),
    "no-content-response": (
        "reviewed response is an empty 204/Response terminal and carries no public dataclass"
    ),
    "binary-or-broker-response": (
        "reviewed file/broker response carries bytes, paths, URLs, or scalar transfer metadata "
        "and no public dataclass projection at this terminal"
    ),
    "non-json-http-response": (
        "reviewed auxiliary HTTP terminal is plain text, HTML, redirect, or streamed file "
        "content; it is inventoried only to prevent a future JSON response bypass"
    ),
    "non-json-mcp-resource": (
        "reviewed MCP resource terminal is a static text or HTML document and carries no "
        "public dataclass; it is inventoried to prevent a future JSON resource bypass"
    ),
    "production-dead-terminal": (
        "reviewed terminal contains a compatibility branch whose public-typed value is never "
        "populated by its production executor"
    ),
}
_INFRASTRUCTURE_CATEGORIES = {
    "central-json-forwarder": (
        "reviewed central CLI JSON renderer/error funnel; callers own projection allocations "
        "and the helper argument/body fingerprints are pinned separately"
    )
}
_UNREACHABLE_PRIVATE_PATH_CATEGORIES = {
    "internal-runtime-configuration": (
        "reviewed private DTO carries a public configuration dataclass only through internal "
        "client construction; neither the DTO nor the field reaches an adapter serializer"
    ),
    "production-dead-public-branch": (
        "reviewed public-typed private DTO field is never populated on any production "
        "return path and therefore cannot reach an adapter channel"
    ),
}
_GENERIC_REVIEW_NOTES = {
    "reviewed",
    "not public",
    "no public model",
    "no public dataclass",
    "scalar only",
    "unreachable",
}
_STRUCTURAL_HELPER_SYMBOLS = {
    "tool-error-funnel": (
        "notebooklm.mcp._errors.mcp_errors",
        "notebooklm.mcp._errors.to_tool_error",
        "notebooklm.mcp._errors.tool_error_payload",
    ),
    "json-helper-return": (
        "notebooklm.server._errors.error_response",
        "notebooklm.server._errors.http_error_response",
    ),
}


def _site_locators(sinks: Iterable[AdapterJsonSink]) -> dict[str, AdapterJsonSink]:
    counts: Counter[tuple[str, str, str, str]] = Counter()
    rows: dict[str, AdapterJsonSink] = {}
    for sink in sinks:
        key = (sink.channel, sink.path, sink.owner, sink.kind)
        counts[key] += 1
        locator = "|".join((*key, str(counts[key])))
        if locator in rows:
            raise ValueError(f"duplicate adapter sink locator: {locator}")
        rows[locator] = sink
    return rows


def _projection_id_channel(projection_id: str) -> str:
    return projection_id.split(".", 1)[0]


def _sink_projection_channel(channel: str) -> str:
    return {
        "cli --json": "cli",
        "mcp tool result": "mcp",
        "mcp auxiliary response": "mcp",
        "rest response": "rest",
    }[channel]


def _projection_id_model(projection_id: str) -> str | None:
    parts = projection_id.split(".", 2)
    return parts[1] if len(parts) == 3 else None


def _load_reviewed_allocations() -> dict[str, dict[str, object]]:
    raw = json.loads(_ALLOCATION_PATH.read_text(encoding="utf-8"))
    if raw.get("schema_version") != 1 or not isinstance(raw.get("allocations"), list):
        raise ValueError("invalid adapter sink allocation schema")
    rows: dict[str, dict[str, object]] = {}
    for item in raw["allocations"]:
        if not isinstance(item, dict) or not isinstance(item.get("locator"), str):
            raise ValueError("invalid adapter sink allocation row")
        locator = item["locator"]
        if locator in rows:
            raise ValueError(f"duplicate reviewed adapter sink allocation: {locator}")
        disposition = {key: value for key, value in item.items() if key != "locator"}
        rows[locator] = disposition
    return rows


def _specific_review_note(allocation: dict[str, object], *, identity: str) -> str:
    note = allocation.get("review_note")
    if (
        not isinstance(note, str)
        or len(note.strip()) < 40
        or note.strip().lower() in _GENERIC_REVIEW_NOTES
    ):
        raise ValueError(f"missing/suspiciously generic review_note at {identity}")
    return note


def _validate_non_public_variants(allocation: dict[str, object], *, identity: str) -> None:
    variants = allocation.get("non_public_variants")
    if variants is None:
        return
    if "projection_ids" not in allocation or not isinstance(variants, list) or not variants:
        raise ValueError(f"invalid non-public projection variants at {identity}")
    conditions: set[str] = set()
    for variant in variants:
        if not isinstance(variant, dict):
            raise ValueError(f"invalid non-public projection variant at {identity}")
        condition = variant.get("condition")
        category = variant.get("category")
        if (
            not isinstance(condition, str)
            or len(condition.strip()) < 20
            or condition in conditions
            or not isinstance(category, str)
            or category not in _NON_PUBLIC_CATEGORIES
        ):
            raise ValueError(f"invalid non-public projection variant at {identity}")
        conditions.add(condition)
        _specific_review_note(variant, identity=f"{identity}:{condition}")


def _private_path_key(row: PrivateDataclassProjectionPath) -> tuple[str, str, str]:
    return (
        str(row.private_model),
        str(row.field_path),
        str(row.public_model),
    )


def _load_private_path_allocations() -> dict[tuple[str, str, str], dict[str, object]]:
    raw = json.loads(_PRIVATE_PATH_ALLOCATION_PATH.read_text(encoding="utf-8"))
    if raw.get("schema_version") != 1 or not isinstance(raw.get("allocations"), list):
        raise ValueError("invalid adapter private-path allocation schema")
    rows: dict[tuple[str, str, str], dict[str, object]] = {}
    for item in raw["allocations"]:
        if not isinstance(item, dict) or not all(
            isinstance(item.get(key), str)
            for key in ("private_model", "field_path", "public_model")
        ):
            raise ValueError("invalid adapter private-path allocation row")
        key = (item["private_model"], item["field_path"], item["public_model"])
        if key in rows:
            raise ValueError(f"duplicate adapter private-path allocation: {key}")
        rows[key] = {
            field: value
            for field, value in item.items()
            if field not in {"private_model", "field_path", "public_model"}
        }
    return rows


def _validate_unreachable_source_evidence(
    source_root: Path,
    allocation: dict[str, object],
    *,
    identity: tuple[str, str, str],
) -> None:
    evidence = allocation.get("source_evidence")
    if not isinstance(evidence, list) or not evidence:
        raise ValueError(f"missing source evidence for unreachable private path {identity}")

    requirements: dict[str, list[str]] = {}
    expected_fingerprints: dict[str, str] = {}
    for row in evidence:
        if not isinstance(row, dict):
            raise ValueError(f"invalid source evidence row for unreachable private path {identity}")
        symbol = row.get("symbol")
        fragments = row.get("required_ast_fragments")
        fingerprint = row.get("semantic_fingerprint")
        if (
            not isinstance(symbol, str)
            or not symbol
            or symbol in requirements
            or not isinstance(fragments, list)
            or not fragments
            or not all(isinstance(fragment, str) and fragment for fragment in fragments)
            or not isinstance(fingerprint, str)
            or not fingerprint.startswith("sha256:")
        ):
            raise ValueError(f"invalid source evidence row for unreachable private path {identity}")
        requirements[symbol] = fragments
        expected_fingerprints[symbol] = fingerprint

    actual_fingerprints = fingerprint_adapter_evidence(source_root, requirements)
    changed = {
        symbol: {
            "expected": expected_fingerprints[symbol],
            "actual": actual_fingerprints[symbol],
        }
        for symbol in requirements
        if expected_fingerprints[symbol] != actual_fingerprints[symbol]
    }
    if changed:
        raise ValueError(
            f"unreachable private-path source evidence changed at {identity}: {changed}"
        )


def _allocate_private_paths(
    source_root: Path,
    *,
    known_projection_ids: set[str],
    terminal_allocations: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    discovered_rows = discover_private_dataclass_projection_paths(source_root)
    discovered = {_private_path_key(row): row for row in discovered_rows}
    reviewed = _load_private_path_allocations()
    missing = sorted(set(discovered) - set(reviewed))
    stale = sorted(set(reviewed) - set(discovered))
    if missing or stale:
        raise ValueError(
            f"adapter private-path allocations are not exact: missing={missing}, stale={stale}"
        )

    serialized: list[dict[str, object]] = []
    for key, path in discovered.items():
        allocation = reviewed[key]
        linked = "projection_ids" in allocation or "terminal_locators" in allocation
        unreachable = "unreachable_category" in allocation
        if linked == unreachable:
            raise ValueError(
                "adapter private-path allocation requires exactly one linked/unreachable "
                f"disposition at {key}"
            )
        if unreachable:
            category = allocation["unreachable_category"]
            if (
                not isinstance(category, str)
                or category not in _UNREACHABLE_PRIVATE_PATH_CATEGORIES
            ):
                raise ValueError(f"unknown unreachable private-path category at {key}: {category}")
            _specific_review_note(allocation, identity=repr(key))
            _validate_unreachable_source_evidence(source_root, allocation, identity=key)
        else:
            projection_ids = allocation.get("projection_ids")
            terminal_locators = allocation.get("terminal_locators")
            if (
                not isinstance(projection_ids, list)
                or not projection_ids
                or not all(
                    isinstance(projection_id, str) and projection_id
                    for projection_id in projection_ids
                )
            ):
                raise ValueError(f"invalid private-path projection_ids at {key}")
            if len(projection_ids) != len(set(projection_ids)):
                raise ValueError(f"duplicate private-path projection_ids at {key}")
            if (
                not isinstance(terminal_locators, list)
                or not terminal_locators
                or not all(isinstance(locator, str) and locator for locator in terminal_locators)
            ):
                raise ValueError(f"invalid private-path terminal_locators at {key}")
            if len(terminal_locators) != len(set(terminal_locators)):
                raise ValueError(f"duplicate private-path terminal_locators at {key}")
            unknown_ids = sorted(set(projection_ids) - known_projection_ids)
            unknown_terminals = sorted(set(terminal_locators) - set(terminal_allocations))
            if unknown_ids or unknown_terminals:
                raise ValueError(
                    f"stale private-path links at {key}: projection_ids={unknown_ids}, "
                    f"terminals={unknown_terminals}"
                )
            public_model_name = key[2].rsplit(".", 1)[-1]
            wrong_model_ids = sorted(
                projection_id
                for projection_id in projection_ids
                if _projection_id_model(projection_id) != public_model_name
                and not projection_id.endswith(f"-{public_model_name}")
            )
            if wrong_model_ids:
                raise ValueError(
                    f"wrong-model private-path projection ids at {key}: {wrong_model_ids}"
                )
            allocated_ids = {
                projection_id
                for locator in terminal_locators
                for projection_id in terminal_allocations[locator].get("projection_ids", [])
            }
            unlinked_ids = sorted(set(projection_ids) - allocated_ids)
            if unlinked_ids:
                raise ValueError(
                    f"private-path projection ids not allocated to linked terminals at {key}: "
                    f"{unlinked_ids}"
                )
            unrelated_terminals = sorted(
                locator
                for locator in terminal_locators
                if not (
                    set(terminal_allocations[locator].get("projection_ids", []))
                    & set(projection_ids)
                )
            )
            if unrelated_terminals:
                raise ValueError(
                    f"private-path terminal links do not intersect projection ids at {key}: "
                    f"{unrelated_terminals}"
                )
        serialized.append({**path.to_dict(), "allocation": allocation})
    return serialized


def derive_adapter_sink_reachability_contract(
    source_root: Path,
    *,
    known_projection_ids: Iterable[str],
) -> dict[str, object]:
    """Build the exact reviewed terminal/result and private DTO path inventory."""
    sinks = discover_adapter_json_sinks(source_root)
    assert_no_unreviewed_direct_json_emissions(sinks)
    assert_no_unreviewed_cli_json_serialization(source_root)
    assert_supported_adapter_registrations(source_root)
    discovered = _site_locators(sinks)
    reviewed = _load_reviewed_allocations()
    missing = sorted(set(discovered) - set(reviewed))
    stale = sorted(set(reviewed) - set(discovered))
    if missing or stale:
        raise ValueError(
            f"adapter sink allocation locators are not exact: missing={missing}, stale={stale}"
        )

    known_ids = set(known_projection_ids)
    exact_dispositions: dict[str, object] = {}
    serialized_sites: list[dict[str, object]] = []
    helper_symbols: set[str] = set()
    for locator, sink in discovered.items():
        allocation = reviewed[locator]
        _validate_non_public_variants(allocation, identity=locator)
        selected = {
            key
            for key in ("projection_ids", "non_public_category", "infrastructure_category")
            if key in allocation
        }
        if len(selected) != 1:
            raise ValueError(
                f"adapter sink allocation requires one disposition at {locator}: "
                f"found {sorted(selected)}"
            )
        helper_rows = allocation.get("helper_symbols", [])
        if not isinstance(helper_rows, list) or not all(
            isinstance(symbol, str) and symbol for symbol in helper_rows
        ):
            raise ValueError(f"invalid helper_symbols at adapter sink {locator}")
        resolved_helper_symbols = sorted(
            {*helper_rows, *_STRUCTURAL_HELPER_SYMBOLS.get(sink.kind, ())}
        )
        helper_symbols.update(resolved_helper_symbols)

        if "projection_ids" in allocation:
            projection_ids = allocation["projection_ids"]
            if (
                not isinstance(projection_ids, list)
                or not projection_ids
                or not all(
                    isinstance(projection_id, str) and projection_id
                    for projection_id in projection_ids
                )
            ):
                raise ValueError(f"invalid projection_ids at adapter sink {locator}")
            unknown = sorted(set(projection_ids) - known_ids)
            if unknown:
                raise ValueError(f"stale projection ids at adapter sink {locator}: {unknown}")
            if len(projection_ids) != len(set(projection_ids)):
                raise ValueError(f"duplicate projection ids at adapter sink {locator}")
            wrong_channel = sorted(
                projection_id
                for projection_id in projection_ids
                if _projection_id_channel(projection_id) != _sink_projection_channel(sink.channel)
            )
            if wrong_channel:
                raise ValueError(
                    f"cross-channel projection ids at adapter sink {locator}: {wrong_channel}"
                )
            disposition: dict[str, object] = {"projection_ids": projection_ids}
        elif "non_public_category" in allocation:
            category = allocation["non_public_category"]
            if not isinstance(category, str) or category not in _NON_PUBLIC_CATEGORIES:
                raise ValueError(
                    f"unknown non-public adapter sink category at {locator}: {category}"
                )
            disposition = {
                "non_public_model_reason": _specific_review_note(allocation, identity=locator)
            }
        else:
            category = allocation["infrastructure_category"]
            if not isinstance(category, str) or category not in _INFRASTRUCTURE_CATEGORIES:
                raise ValueError(f"unknown infrastructure sink category at {locator}: {category}")
            disposition = {
                "infrastructure_reason": _specific_review_note(allocation, identity=locator)
            }
        exact_dispositions[sink.id] = disposition
        serialized_sites.append(
            {
                "locator": locator,
                **sink.to_dict(),
                "allocation": allocation,
                "resolved_helper_symbols": resolved_helper_symbols,
            }
        )

    helper_fingerprints = fingerprint_adapter_helpers(source_root, helper_symbols)
    allocated_projection_ids = {
        projection_id
        for allocation in reviewed.values()
        for projection_id in allocation.get("projection_ids", [])
    }
    unallocated_projection_ids = sorted(known_ids - allocated_projection_ids)
    if unallocated_projection_ids:
        raise ValueError(
            "compatibility projection ids have no adapter terminal allocation: "
            f"{unallocated_projection_ids}"
        )
    assert_exact_sink_dispositions(
        sinks,
        exact_dispositions,
        known_projection_ids=known_ids,
        known_helper_symbols=helper_fingerprints,
    )
    private_paths = _allocate_private_paths(
        source_root,
        known_projection_ids=known_ids,
        terminal_allocations=reviewed,
    )
    return {
        "schema_version": 1,
        "selection": (
            "all CLI JSON success/error/direct terminals, all registered MCP tool/resource "
            "returns, every return from MCP custom connector/file routes (including non-JSON "
            "terminals), all REST router/app routes and central JSON error terminals, plus "
            "package-wide source-derived private DTO public-dataclass paths"
        ),
        "site_count": len(serialized_sites),
        "sites": serialized_sites,
        "delegated_helper_fingerprints": helper_fingerprints,
        "private_dataclass_projection_paths": private_paths,
    }


__all__ = ["derive_adapter_sink_reachability_contract"]
