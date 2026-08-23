"""Focused tests for operation-catalog derivation and fail-closed behavior."""

from __future__ import annotations

import dataclasses

import pytest

from notebooklm._idempotency import IdempotencyPolicy, IdempotencyRegistry
from notebooklm._operations import CallPolicy, Operation, OperationDef
from notebooklm.rpc import RPCMethod
from scripts import audit_operation_catalog as catalog


def test_operation_definition_is_inert_frozen_slotted_vocabulary() -> None:
    definition = OperationDef(Operation.NOTEBOOK_LIST, CallPolicy.READ, str, int)

    assert definition.key is Operation.NOTEBOOK_LIST
    assert definition.input_type is str
    assert definition.output_type is int
    assert not hasattr(definition, "__dict__")
    with pytest.raises(dataclasses.FrozenInstanceError):
        definition.policy = CallPolicy.MUTATION  # type: ignore[misc]


def test_audit_bites_when_a_new_native_variant_has_no_disposition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = IdempotencyRegistry()
    for method, variant, entry in catalog.IDEMPOTENCY_REGISTRY.iter_entries():
        registry.register(method, entry.policy, variant=variant, notes=entry.notes)
    registry.register(
        RPCMethod.LIST_NOTEBOOKS,
        IdempotencyPolicy.IDEMPOTENT_SET_OP,
        variant="future_variant",
        notes="synthetic drift",
    )
    monkeypatch.setattr(catalog, "IDEMPOTENCY_REGISTRY", registry)

    errors = catalog.audit_operation_catalog()

    assert any("LIST_NOTEBOOKS:future_variant" in error for error in errors)


def test_audit_bites_when_a_new_public_namespace_method_has_no_disposition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    discovered = catalog.collect_public_namespace_methods()
    discovered["sources.future_method"] = "notebooklm._sources.SourcesAPI"
    monkeypatch.setattr(catalog, "collect_public_namespace_methods", lambda: discovered)

    errors = catalog.audit_operation_catalog()

    assert any("sources.future_method" in error for error in errors)


def test_audit_bites_when_an_operation_spec_is_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        catalog,
        "OPERATION_SPECS",
        tuple(
            spec
            for spec in catalog.OPERATION_SPECS
            if spec.operation is not Operation.NOTEBOOK_LIST
        ),
    )

    errors = catalog.audit_operation_catalog()

    assert any("notebook.list" in error for error in errors)


def test_app_ast_walk_records_transport_neutral_orchestrators() -> None:
    callers = catalog.collect_app_callers()

    assert "_app/source_wait.py:execute_source_wait" in callers["sources.wait_until_ready"]
    assert "_app/collections.py:execute_collection_list" in callers["collections.list"]
    assert "_app/download.py:_fetch_artifacts_once" in callers["artifacts.list"]


def test_rpc_ast_walk_distinguishes_calls_from_decoder_references() -> None:
    references = catalog.collect_rpc_references()
    sites = catalog.collect_native_execution_sites()

    assert sites[(RPCMethod.GET_NOTEBOOK, None)] == [
        "_chat/api.py:ChatAPI.get_settings",
        "_notebooks.py:NotebooksAPI.get",
        "_notebooks.py:NotebooksAPI.get_raw",
        "_source/listing.py:SourceLister.list",
    ]
    assert any("_row_adapters/" in site for site in references[RPCMethod.GET_NOTEBOOK]["decoders"])
    assert all("_row_adapters/" not in site for site in sites[(RPCMethod.GET_NOTEBOOK, None)])


def test_golden_evidence_is_read_from_the_existing_guardrail() -> None:
    covered, exempt = catalog.collect_golden_evidence()

    assert covered[RPCMethod.GET_NOTEBOOK]
    assert covered[RPCMethod.ADD_SOURCE]
    assert "returns None" in exempt[RPCMethod.DELETE_NOTEBOOK]


def test_capture_rpc_registry_snapshot_supplies_product_omissions() -> None:
    projection = catalog.build_operation_catalog(
        {"unmapped": {"NewOne": {"method": "/LabsTailwindService.NewThing", "family": "current"}}}
    )

    assert projection["product_omissions"]["unmapped_live_rpcs"] == [
        {
            "rpc_id": "NewOne",
            "method": "/LabsTailwindService.NewThing",
            "family": "current",
            "disposition": "unsupported_current_product_surface",
        }
    ]


def test_committed_rpc_evidence_audit_rejects_a_vacuous_snapshot() -> None:
    confirmed = {
        method.value: {"name": method.name, "method": f"/Synthetic.{method.name}"}
        for method in RPCMethod
    }
    snapshot = {
        "schema_version": 1,
        "counts": {
            "confirmed": len(RPCMethod),
            "absent": 0,
            "present_unparsed": 0,
            "unmapped": 0,
        },
        "confirmed": confirmed,
        "absent": {},
        "present_unparsed": {},
        "unmapped": {},
    }

    assert catalog.audit_rpc_registry_evidence(snapshot) == [
        "RPC registry omissions evidence must not be empty/vacuous",
        "RPC registry evidence must inventory current-family product omissions",
    ]


def test_fresh_capture_check_bites_when_live_omissions_drift() -> None:
    snapshot = dict(catalog.load_rpc_registry_evidence())
    unmapped = dict(snapshot["unmapped"])
    unmapped["NewOne"] = {
        "method": "/LabsTailwindOrchestrationService.NewThing",
        "family": "current",
    }
    snapshot["unmapped"] = unmapped

    errors = catalog.audit_live_registry_against_evidence(snapshot)

    assert len(errors) == 1
    assert "added=['NewOne']" in errors[0]


def test_known_divergences_remain_reported_but_do_not_fail_audit() -> None:
    assert catalog.audit_operation_catalog() == []
    divergences = catalog.build_operation_catalog()["known_divergences"]

    assert {row["operation"] for row in divergences} == {
        "artifact.download",
        "artifact.retry",
        "artifact.wait",
        "source.refresh",
        "source.wait",
    }
