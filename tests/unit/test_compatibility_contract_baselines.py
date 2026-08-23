"""Coverage checks for semantic-refactor compatibility baseline derivations."""

from __future__ import annotations

import asyncio
import dataclasses
import enum
import importlib
from pathlib import Path

import pytest
import scripts.audit_public_api_compat as public_audit

import notebooklm
import notebooklm.types as public_types
from tests._baselines.compatibility_contracts import (
    _dataclass_contract,
    _legacy_state_contract,
    _logical_rpc_scenario,
    _secret_serialization_violations,
    _validate_no_secret_channel_models,
    derive_json_envelope_contract,
    derive_metrics_contract,
    derive_public_model_contract,
)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True, match_args=False)
class _ObservableFlagsSample:
    value: int


def _exported_models() -> dict[str, type]:
    package_dir = Path(notebooklm.__file__).resolve().parent
    modules = {public_audit.PUBLIC_PACKAGE}
    for path in package_dir.glob("*.py"):
        if path.stem.startswith("_") or path.stem in public_audit.EXCLUDED_TOP_LEVEL_MODULES:
            continue
        modules.add(f"{public_audit.PUBLIC_PACKAGE}.{path.stem}")
    modules.update(
        f"{public_audit.PUBLIC_PACKAGE}.{name}"
        for name in public_audit.EXTRA_PUBLIC_PACKAGES
        if (package_dir / name / "__init__.py").is_file()
    )

    models: dict[str, type] = {}
    for module_name in sorted(modules):
        module = importlib.import_module(module_name)
        for name in getattr(module, "__all__", ()):
            value = getattr(module, name)
            if isinstance(value, type) and (
                dataclasses.is_dataclass(value) or issubclass(value, enum.Enum)
            ):
                models[f"{value.__module__}.{value.__qualname__}"] = value
    return models


def test_public_model_contract_covers_every_exported_dataclass_and_enum() -> None:
    contract = derive_public_model_contract()

    assert set(contract["models"]) == set(_exported_models())
    assert "notebooklm.auth.AuthTokens" in contract["models"]
    assert "notebooklm.artifacts.RateLimitRetryEvent" in contract["models"]
    assert "notebooklm.rpc.types.RPCMethod" in contract["models"]
    outcomes = {name: model["pickle_round_trip"] for name, model in contract["models"].items()}
    assert sum(outcome["status"] == "success" for outcome in outcomes.values()) == 85
    assert outcomes["notebooklm.auth.AuthTokens"] == {
        "status": "failure",
        "stage": "dumps",
        "error_type": "TypeError",
        "error_category": "unpickleable-thread-lock",
    }


def test_public_model_contract_pins_custom_state_hooks_and_legacy_round_trips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = derive_public_model_contract()["models"]
    notebook = contract["notebooklm.types.Notebook"]
    reference = contract["notebooklm.types.ChatReference"]

    assert notebook["pickle_state_hooks"]["__setstate__"] == {
        "present": True,
        "owner": "notebooklm.types.Notebook",
    }
    assert reference["pickle_state_hooks"]["__setstate__"] == {
        "present": True,
        "owner": "notebooklm.types.ChatReference",
    }
    assert notebook["legacy_state_round_trip"]["status"] == "success"
    assert reference["legacy_state_round_trip"]["status"] == "success"
    assert all(notebook["legacy_state_round_trip"]["invariants"].values())
    assert all(reference["legacy_state_round_trip"]["invariants"].values())

    monkeypatch.delattr(public_types.Notebook, "__setstate__")
    assert _legacy_state_contract(public_types.Notebook) == {
        "status": "failure",
        "reason": "missing-__setstate__",
    }


def test_json_envelope_covers_every_exported_dataclass_and_exact_field_order() -> None:
    contract = derive_json_envelope_contract()
    expected = {
        name: cls
        for name, cls in _exported_models().items()
        if dataclasses.is_dataclass(cls) and name != "notebooklm.auth.AuthTokens"
    }
    inventory = contract["exported_dataclass_key_inventory"]

    assert set(inventory) == set(expected)
    assert "notebooklm.auth.AuthTokens" not in inventory
    assert "notebooklm.artifacts.RateLimitRetryEvent" in inventory
    for name, cls in expected.items():
        field_names = [field.name for field in dataclasses.fields(cls)]
        assert inventory[name]["dataclass_fields"] == field_names
        assert inventory[name]["to_jsonable_keys"] == field_names

    assert set(contract["channels"]) == {"cli --json", "mcp tool result", "rest response"}
    for reachable in contract["channels"].values():
        assert reachable
        assert set(reachable) <= set(inventory)
        assert "notebooklm.auth.AuthTokens" not in reachable
        assert "notebooklm.types.AskResult" in reachable
        assert "notebooklm.types.ChatReference" in reachable
        for row in reachable.values():
            assert row["projections"]
            assert all(projection["keys"] for projection in row["projections"])
            assert all(projection["evidence"] for projection in row["projections"])

    cli = contract["channels"]["cli --json"]
    ask_keys = cli["notebooklm.types.AskResult"]["projections"][0]["keys"]
    assert "raw_response" not in ask_keys
    assert "answer_document" not in ask_keys
    cited = cli["notebooklm.types.CitedSourceSelection"]["projections"]
    assert [projection["keys"] for projection in cited] == [
        ["cited_only", "cited_sources_selected", "cited_only_fallback"],
        ["cited_only", "cited_only_fallback"],
    ]
    notebook_projections = cli["notebooklm.types.Notebook"]["projections"]
    create = next(
        projection
        for projection in notebook_projections
        if projection["mode"] == "manual-create-projection"
    )
    assert create["keys"] == ["notebook"]
    assert create["nested_keys"]["notebook"] == [
        "id",
        "title",
        "role",
        "created_at",
        "last_viewed_at",
        "modified_at",
    ]
    assert (
        "notebooklm.types.CitedSourceSelection"
        in contract["supplemental_import_references"]["cli --json"]
    )
    for channel in ("cli --json", "mcp tool result"):
        for model in ("notebooklm.types.MindMap", "notebooklm.types.MindMapResult"):
            projections = contract["channels"][channel][model]["projections"]
            assert all(projection["mode"] != "dataclass-full" for projection in projections)
            assert all("tree/id-projection" in projection["mode"] for projection in projections)
    assert (
        contract["secret_bearing_exclusions"]["notebooklm.auth.AuthTokens"]["adapter_reachable"]
        is False
    )


def test_json_envelope_rejects_secret_bearing_channel_reachability() -> None:
    with pytest.raises(ValueError, match="require a redacted adapter projection"):
        _validate_no_secret_channel_models(
            {"cli --json": {"notebooklm.types.Notebook", "notebooklm.auth.AuthTokens"}}
        )

    source = """
from dataclasses import asdict
from notebooklm.auth import AuthTokens

def leak(client, explicit: AuthTokens):
    copied = client.auth
    to_jsonable(copied)
    asdict(explicit)
    to_jsonable({"credentials": [client.auth]})
"""
    assert _secret_serialization_violations(source, filename="mutation.py") == [
        "mutation.py:7",
        "mutation.py:8",
        "mutation.py:9",
    ]


def test_metrics_contract_covers_exact_public_fields_and_emission_branches() -> None:
    contract = derive_metrics_contract()

    assert [item["name"] for item in contract["client_metrics_snapshot_fields"]] == [
        field.name for field in dataclasses.fields(public_types.ClientMetricsSnapshot)
    ]
    assert [item["name"] for item in contract["rpc_telemetry_event_fields"]] == [
        field.name for field in dataclasses.fields(public_types.RpcTelemetryEvent)
    ]
    scenarios = contract["logical_rpc_scenarios"]
    success = scenarios["success"]
    transport_error = scenarios["transport_error"]
    decode_error = scenarios["decode_error"]
    assert success["result"] == {"ok": True}
    assert success["raised"] is None
    assert success["leaf_calls"] == 1
    assert success["events"][0]["status"] == "success"
    assert success["metrics_snapshot"]["rpc_calls_started"] == 1
    assert success["metrics_snapshot"]["rpc_calls_succeeded"] == 1
    assert success["metrics_snapshot"]["rpc_calls_failed"] == 0
    assert success["metrics_snapshot"]["rpc_decode_errors"] == 0
    assert transport_error["raised"] == "_ContractError"
    assert transport_error["events"][0]["status"] == "error"
    assert transport_error["metrics_snapshot"]["rpc_calls_started"] == 1
    assert transport_error["metrics_snapshot"]["rpc_calls_succeeded"] == 0
    assert transport_error["metrics_snapshot"]["rpc_calls_failed"] == 1
    assert decode_error["raised"] == "DecodingError"
    assert decode_error["events"][0]["status"] == "success"
    assert decode_error["metrics_snapshot"]["rpc_calls_started"] == 1
    assert decode_error["metrics_snapshot"]["rpc_calls_succeeded"] == 1
    assert decode_error["metrics_snapshot"]["rpc_calls_failed"] == 0
    assert decode_error["metrics_snapshot"]["rpc_decode_errors"] == 1

    supplemental = contract["supplemental_non_rpc_middleware_scenarios"]
    assert supplemental["non_rpc_success"]["snapshot"] == dataclasses.asdict(
        public_types.ClientMetricsSnapshot()
    )
    assert supplemental["non_rpc_error"]["snapshot"] == dataclasses.asdict(
        public_types.ClientMetricsSnapshot()
    )


def test_metrics_contract_changes_when_live_wiring_is_removed() -> None:
    live = asyncio.run(_logical_rpc_scenario("success"))
    without_middleware = asyncio.run(_logical_rpc_scenario("success", drop_metrics_middleware=True))
    without_executor_metrics = asyncio.run(
        _logical_rpc_scenario("success", disconnect_executor_metrics=True)
    )

    assert without_middleware != live
    assert without_middleware["events"] == []
    assert without_middleware["metrics_snapshot"]["rpc_calls_started"] == 1
    assert without_middleware["metrics_snapshot"]["rpc_calls_succeeded"] == 0
    assert without_executor_metrics != live
    assert without_executor_metrics["metrics_snapshot"]["rpc_calls_started"] == 0
    assert without_executor_metrics["metrics_snapshot"]["rpc_calls_succeeded"] == 1


def test_dataclass_flags_are_derived_from_observable_cross_version_shape() -> None:
    contract = _dataclass_contract(_ObservableFlagsSample)

    assert contract["dataclass_flags"] == {
        "eq": True,
        "frozen": True,
        "init": True,
        "keyword_only": True,
        "match_args": False,
        "order": False,
        "repr": True,
        "slots": True,
        "unsafe_hash": False,
        "weakref_slot": False,
    }
    assert contract["slots"] == ["value"]
    assert contract["fields"][0]["keyword_only"] is True
