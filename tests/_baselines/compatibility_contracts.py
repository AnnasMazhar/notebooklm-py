"""Derivations for the semantic-refactor compatibility baselines.

The baselines in this module freeze contracts that the release-to-release
public API audit does not fully describe:

* structural and pickle identity for every public dataclass and enum;
* the public metrics field vocabulary and terminal RPC event behavior; and
* the shared ``to_jsonable`` field-key schema used by CLI, MCP, and REST.

All inventories come from the ``__all__`` surfaces of the public modules
discovered by ``scripts/audit_public_api_compat.py`` and live dataclass
metadata.  There is no hand-maintained model list to go stale.
"""

from __future__ import annotations

import asyncio
import dataclasses
import enum
import importlib
import inspect
import pickle
from pathlib import Path
from typing import Any


def _audit_public_modules() -> list[str]:
    """Return the public modules discovered by the compatibility audit."""
    import scripts.audit_public_api_compat as audit

    import notebooklm

    package_dir = Path(notebooklm.__file__).resolve().parent
    modules = {audit.PUBLIC_PACKAGE}
    for path in package_dir.glob("*.py"):
        stem = path.stem
        if stem.startswith("_") or stem in audit.EXCLUDED_TOP_LEVEL_MODULES:
            continue
        modules.add(f"{audit.PUBLIC_PACKAGE}.{stem}")
    for name in audit.EXTRA_PUBLIC_PACKAGES:
        if (package_dir / name / "__init__.py").is_file():
            modules.add(f"{audit.PUBLIC_PACKAGE}.{name}")
    return sorted(modules)


def _public_model_exports() -> dict[type[Any], list[str]]:
    """Return every dataclass/enum exported by every audited public module.

    Values are deduplicated by class identity while retaining every public
    export path that reaches the identity.  This includes compatibility
    re-exports such as ``notebooklm.AuthTokens`` /
    ``notebooklm.auth.AuthTokens`` without snapshotting the class twice.
    """
    models: dict[type[Any], list[str]] = {}
    for module_name in _audit_public_modules():
        module = importlib.import_module(module_name)
        for name in getattr(module, "__all__", ()):
            value = getattr(module, name)
            if not isinstance(value, type):
                continue
            if not (dataclasses.is_dataclass(value) or issubclass(value, enum.Enum)):
                continue
            models.setdefault(value, []).append(f"{module_name}.{name}")
    return models


def _model_key(cls: type[Any]) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def _annotation_repr(annotation: object) -> str:
    """Render postponed and live annotations without resolving facade globals.

    Several public classes intentionally rewrite ``__module__`` to
    ``notebooklm.types`` for pickle compatibility.  Resolving all hints through
    that facade can therefore fail for defining-module-only names.  The raw
    dataclass field annotation is the stable contract here.
    """
    if isinstance(annotation, str):
        return annotation
    return inspect.formatannotation(annotation)


def _declaring_owner(cls: type[Any], method_name: str) -> type[Any]:
    return next(base for base in cls.__mro__ if method_name in base.__dict__)


def _method_policy(cls: type[Any], method_name: str) -> dict[str, object]:
    """Describe equality/hash/repr semantics without pinning code locations."""
    owner = _declaring_owner(cls, method_name)
    method = owner.__dict__[method_name]
    if method is None:
        return {"policy": "disabled"}
    if owner is not cls:
        return {
            "policy": "inherited",
            "owner": f"{owner.__module__}.{owner.__qualname__}",
        }

    target = inspect.unwrap(method)
    code = getattr(target, "__code__", None)
    policy = "dataclass-generated" if getattr(code, "co_filename", None) == "<string>" else "custom"
    return {"policy": policy}


def _sample_dataclass(cls: type[Any]) -> object:
    """Build a pickleable field-complete instance without invoking validators.

    Defaults/default factories are preserved.  Required fields receive
    ``None`` because the round-trip contract concerns class identity and field
    state, not domain validation; bypassing ``__init__`` also keeps this helper
    generic as new public models are added.
    """
    instance = cls.__new__(cls)
    overrides: dict[str, object] = {}
    if _model_key(cls) == "notebooklm.auth.AuthTokens":
        # The normal constructor creates an httpx cookie jar containing an
        # unpickleable RLock.  Preserve the valid legacy mapping-only shape
        # instead.  These are fixed non-secrets, and only output KEY names
        # (never values) reach the json_envelope baseline.
        overrides = {
            "cookies": {"SID": "contract-redacted"},
            "csrf_token": "contract-redacted",
            "session_id": "contract-redacted",
        }
    for field in dataclasses.fields(cls):
        if field.name in overrides:
            value = overrides[field.name]
        elif field.default is not dataclasses.MISSING:
            value = field.default
        elif field.default_factory is not dataclasses.MISSING:
            value = field.default_factory()
        else:
            value = None
        object.__setattr__(instance, field.name, value)
    return instance


def _pickle_round_trips(value: object, *, identity: bool) -> bool:
    restored = pickle.loads(pickle.dumps(value))
    return restored is value if identity else restored == value


def _dataclass_contract(cls: type[Any]) -> dict[str, object]:
    params = cls.__dataclass_params__
    fields = dataclasses.fields(cls)
    signature = inspect.signature(cls)
    slots = cls.__dict__.get("__slots__", ())
    if isinstance(slots, str):
        slots = (slots,)
    has_slots = "__slots__" in cls.__dict__

    return {
        "kind": "dataclass",
        "module": cls.__module__,
        "qualname": cls.__qualname__,
        "dataclass_flags": {
            "eq": params.eq,
            "frozen": params.frozen,
            "init": params.init,
            "keyword_only": any(field.kw_only for field in fields),
            "match_args": "__match_args__" in cls.__dict__,
            "order": params.order,
            "repr": params.repr,
            "slots": has_slots,
            "unsafe_hash": params.unsafe_hash,
            "weakref_slot": has_slots and "__weakref__" in slots,
        },
        "slots": list(slots),
        "constructor_order": list(signature.parameters),
        "match_args": list(getattr(cls, "__match_args__", ())),
        "fields": [
            {
                "name": field.name,
                "type": _annotation_repr(field.type),
                "init": field.init,
                "repr": field.repr,
                "compare": field.compare,
                "hash": field.hash,
                "keyword_only": field.kw_only,
            }
            for field in fields
        ],
        "equality": _method_policy(cls, "__eq__"),
        "hash": _method_policy(cls, "__hash__"),
        "repr": _method_policy(cls, "__repr__"),
        "pickle_round_trip": _pickle_round_trips(_sample_dataclass(cls), identity=False),
    }


def _enum_contract(cls: type[enum.Enum]) -> dict[str, object]:
    members = list(cls.__members__.items())
    sample = members[0][1]
    return {
        "kind": "enum",
        "module": cls.__module__,
        "qualname": cls.__qualname__,
        "members": [
            {
                "name": name,
                "value": member.value
                if isinstance(member.value, str | int | float | bool | type(None))
                else repr(member.value),
            }
            for name, member in members
        ],
        "equality": _method_policy(cls, "__eq__"),
        "hash": _method_policy(cls, "__hash__"),
        "repr": _method_policy(cls, "__repr__"),
        "pickle_round_trip": _pickle_round_trips(sample, identity=True),
    }


def derive_public_model_contract() -> dict[str, object]:
    """Derive structural/pickle contracts for all exported models."""
    models: dict[str, object] = {}
    for cls, export_paths in sorted(
        _public_model_exports().items(), key=lambda item: _model_key(item[0])
    ):
        model_key = _model_key(cls)
        if model_key in models:
            raise ValueError(f"distinct public model identities collide at {model_key}")
        model = _enum_contract(cls) if issubclass(cls, enum.Enum) else _dataclass_contract(cls)
        model["exports"] = export_paths
        models[model_key] = model
    return {
        "schema_version": 1,
        "selection": (
            "every dataclass and enum in __all__ of every public module discovered by "
            "scripts/audit_public_api_compat.py, deduplicated by class identity"
        ),
        "models": models,
    }


def _field_type_contract(cls: type[Any]) -> list[dict[str, str]]:
    return [
        {"name": field.name, "type": _annotation_repr(field.type)}
        for field in dataclasses.fields(cls)
    ]


class _ContractError(RuntimeError):
    """Stable exception name for the telemetry error-path characterization."""


async def _metrics_emission_scenarios() -> dict[str, object]:
    import httpx

    from notebooklm._client_metrics import ClientMetrics
    from notebooklm._logging import reset_request_id, set_request_id
    from notebooklm._middleware.core import RpcRequest, RpcResponse
    from notebooklm._middleware.metrics import MetricsMiddleware
    from notebooklm.types import RpcTelemetryEvent

    async def run(*, rpc_method: str | None, failure: bool) -> dict[str, object]:
        events: list[RpcTelemetryEvent] = []

        async def capture(event: RpcTelemetryEvent) -> None:
            events.append(event)

        metrics = ClientMetrics(on_rpc_event=capture)
        middleware = MetricsMiddleware(metrics)
        context = {} if rpc_method is None else {"rpc_method": rpc_method}
        request = RpcRequest(url="https://contract.invalid", headers={}, body=b"", context=context)

        async def terminal(current: RpcRequest) -> RpcResponse:
            await asyncio.sleep(0)
            if failure:
                raise _ContractError("contract-error")
            return RpcResponse(httpx.Response(200), context=current.context)

        token = set_request_id("contract-request-id")
        try:
            try:
                await middleware(request, terminal)
            except _ContractError:
                if not failure:
                    raise
        finally:
            reset_request_id(token)

        projected = []
        for event in events:
            projected.append(
                {
                    "method": event.method,
                    "status": event.status,
                    "elapsed_seconds": "non-negative-float"
                    if isinstance(event.elapsed_seconds, float) and event.elapsed_seconds >= 0.0
                    else "invalid",
                    "request_id": event.request_id,
                    "error_type": event.error_type,
                }
            )
        snapshot = dataclasses.asdict(metrics.snapshot())
        latency = snapshot["rpc_latency_seconds_total"]
        if latency > 0.0:
            snapshot["rpc_latency_seconds_total"] = "positive-float"
        return {"event_count": len(events), "events": projected, "snapshot": snapshot}

    return {
        "rpc_success": await run(rpc_method="CONTRACT_RPC", failure=False),
        "rpc_error": await run(rpc_method="CONTRACT_RPC", failure=True),
        "non_rpc_success": await run(rpc_method=None, failure=False),
        "non_rpc_error": await run(rpc_method=None, failure=True),
    }


def derive_metrics_contract() -> dict[str, object]:
    """Derive public metrics fields and location-independent emission semantics."""
    from notebooklm.types import ClientMetricsSnapshot, RpcTelemetryEvent

    return {
        "schema_version": 1,
        "client_metrics_snapshot_fields": _field_type_contract(ClientMetricsSnapshot),
        "rpc_telemetry_event_fields": _field_type_contract(RpcTelemetryEvent),
        "rpc_telemetry_emission_scenarios": asyncio.run(_metrics_emission_scenarios()),
    }


def derive_json_envelope_contract() -> dict[str, object]:
    """Freeze ``to_jsonable`` keys for a conservative public-model superset."""
    from notebooklm._app.serialize import to_jsonable

    models: dict[str, object] = {}
    for cls, export_paths in sorted(
        _public_model_exports().items(), key=lambda item: _model_key(item[0])
    ):
        if not dataclasses.is_dataclass(cls):
            continue
        sample = _sample_dataclass(cls)
        payload = to_jsonable(sample)
        if not isinstance(payload, dict):
            raise TypeError(f"to_jsonable({_model_key(cls)}) did not return a dict")
        models[_model_key(cls)] = {
            "module": cls.__module__,
            "qualname": cls.__qualname__,
            "exports": export_paths,
            "dataclass_fields": [field.name for field in dataclasses.fields(cls)],
            "to_jsonable_keys": list(payload),
        }

    return {
        "schema_version": 1,
        "selection": (
            "conservative superset: every dataclass in __all__ of every audit-discovered public "
            "module; public models are shared by CLI --json, MCP results, and REST responses"
        ),
        "reachability_categories": ["cli --json", "mcp tool result", "rest response"],
        "models": models,
    }


__all__ = [
    "derive_json_envelope_contract",
    "derive_metrics_contract",
    "derive_public_model_contract",
]
