"""Fail-closed completeness guard for the semantic operation catalog."""

from __future__ import annotations

import pytest
from scripts.audit_operation_catalog import (
    LOCAL_PUBLIC_METHODS,
    audit_operation_catalog,
    build_operation_catalog,
    collect_public_namespace_methods,
)

from notebooklm._idempotency import IDEMPOTENCY_REGISTRY
from notebooklm._operations import Operation
from notebooklm.rpc import RPCMethod

pytestmark = pytest.mark.repo_lint


def test_operation_catalog_is_total_and_current() -> None:
    """Every enum member, native row, and namespace method has a disposition."""
    assert audit_operation_catalog() == []


def test_catalog_projection_covers_the_live_authorities() -> None:
    catalog = build_operation_catalog()

    assert {row["key"] for row in catalog["operations"]} == {
        operation.value for operation in Operation
    }
    assert {row["key"] for row in catalog["native_bindings"]} == {
        f"{method.name}:{variant if variant is not None else '<default>'}"
        for method, variant, _entry in IDEMPOTENCY_REGISTRY.iter_entries()
    }
    assert set(catalog["public_methods"]) == set(collect_public_namespace_methods())


def test_catalog_names_every_inherited_and_local_only_public_helper() -> None:
    public_methods = build_operation_catalog()["public_methods"]

    assert public_methods["chat.set_bound_loop"]["disposition"] == "local_only"
    assert public_methods["chat.reset_after_open"]["disposition"] == "local_only"
    assert set(LOCAL_PUBLIC_METHODS) <= set(public_methods)


def test_every_active_binding_honors_runtime_rpc_overrides() -> None:
    rows = build_operation_catalog()["native_bindings"]

    assert len(rows) == sum(1 for _entry in IDEMPOTENCY_REGISTRY.iter_entries())
    assert {row["override_evidence"] for row in rows} == {
        "_rpc_executor.py:RpcExecutor._execute_once"
    }
    assert all(row["override_honored"] for row in rows)


def test_polymorphic_native_surfaces_keep_all_reviewed_dispositions() -> None:
    rows = {row["key"]: row for row in build_operation_catalog()["native_bindings"]}

    assert rows["UPDATE_LABEL:add_sources"]["semantic_operations"] == ["label.update"]
    assert rows["UPDATE_LABEL:add_notebooks"]["semantic_operations"] == ["collection.update"]
    assert rows["LIST_LABELS:<default>"]["semantic_operations"] == [
        "collection.get",
        "collection.list",
        "collection.notebooks",
        "label.get",
        "label.list",
        "label.sources",
    ]
    assert rows["SHARE_ARTIFACT:<default>"]["semantic_operations"] == [
        "sharing.legacy_share_artifact"
    ]


def test_plan_named_greenfield_omissions_remain_covered() -> None:
    coverage = build_operation_catalog()["greenfield_omission_coverage"]

    assert {
        "source listing",
        "settings and account limits",
        "individual sharing",
        "prompt suggestions",
        "report suggestions",
        "generic artifact actions",
        "artifact retry",
        "mind maps",
        "data tables",
        "exports and download formats",
    } == set(coverage)


def test_committed_live_registry_omissions_are_non_vacuous_and_disposed() -> None:
    omissions = build_operation_catalog()["product_omissions"]

    assert omissions["source"] == "scripts/operation_catalog_rpc_registry.json"
    assert omissions["capture_counts"]["unmapped"] == len(omissions["unmapped_live_rpcs"])
    assert omissions["unmapped_live_rpcs"]
    assert all(row["disposition"] for row in omissions["unmapped_live_rpcs"])
    assert any(row["family"] == "current" for row in omissions["unmapped_live_rpcs"])


def test_no_native_idempotency_row_is_left_unclassified() -> None:
    assert all(
        entry.policy.value != "unclassified"
        for _method, _variant, entry in IDEMPOTENCY_REGISTRY.iter_entries()
    )
    assert {row["rpc_method"] for row in build_operation_catalog()["native_bindings"]} == {
        method.name for method in RPCMethod
    }
