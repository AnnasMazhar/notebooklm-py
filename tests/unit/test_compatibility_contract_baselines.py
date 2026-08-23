"""Coverage checks for semantic-refactor compatibility baseline derivations."""

from __future__ import annotations

import dataclasses
import enum
import importlib
from pathlib import Path

import scripts.audit_public_api_compat as public_audit

import notebooklm
import notebooklm.types as public_types
from tests._baselines.compatibility_contracts import (
    _dataclass_contract,
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
    assert all(model["pickle_round_trip"] is True for model in contract["models"].values())
    assert "notebooklm.auth.AuthTokens" in contract["models"]
    assert "notebooklm.artifacts.RateLimitRetryEvent" in contract["models"]
    assert "notebooklm.rpc.types.RPCMethod" in contract["models"]


def test_json_envelope_covers_every_exported_dataclass_and_exact_field_order() -> None:
    contract = derive_json_envelope_contract()
    expected = {
        name: cls for name, cls in _exported_models().items() if dataclasses.is_dataclass(cls)
    }

    assert set(contract["models"]) == set(expected)
    assert "notebooklm.auth.AuthTokens" in contract["models"]
    assert "notebooklm.artifacts.RateLimitRetryEvent" in contract["models"]
    for name, cls in expected.items():
        field_names = [field.name for field in dataclasses.fields(cls)]
        assert contract["models"][name]["dataclass_fields"] == field_names
        assert contract["models"][name]["to_jsonable_keys"] == field_names


def test_metrics_contract_covers_exact_public_fields_and_emission_branches() -> None:
    contract = derive_metrics_contract()

    assert [item["name"] for item in contract["client_metrics_snapshot_fields"]] == [
        field.name for field in dataclasses.fields(public_types.ClientMetricsSnapshot)
    ]
    assert [item["name"] for item in contract["rpc_telemetry_event_fields"]] == [
        field.name for field in dataclasses.fields(public_types.RpcTelemetryEvent)
    ]
    scenarios = contract["rpc_telemetry_emission_scenarios"]
    assert scenarios["rpc_success"]["event_count"] == 1
    assert scenarios["rpc_error"]["event_count"] == 1
    assert scenarios["non_rpc_success"]["event_count"] == 0
    assert scenarios["non_rpc_error"]["event_count"] == 0
    assert scenarios["rpc_success"]["snapshot"]["rpc_calls_succeeded"] == 1
    assert scenarios["rpc_success"]["snapshot"]["rpc_calls_failed"] == 0
    assert scenarios["rpc_success"]["snapshot"]["rpc_latency_seconds_total"] == "positive-float"
    assert scenarios["rpc_error"]["snapshot"]["rpc_calls_succeeded"] == 0
    assert scenarios["rpc_error"]["snapshot"]["rpc_calls_failed"] == 1
    assert scenarios["rpc_error"]["snapshot"]["rpc_latency_seconds_total"] == "positive-float"
    assert scenarios["non_rpc_success"]["snapshot"] == dataclasses.asdict(
        public_types.ClientMetricsSnapshot()
    )
    assert scenarios["non_rpc_error"]["snapshot"] == dataclasses.asdict(
        public_types.ClientMetricsSnapshot()
    )


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
