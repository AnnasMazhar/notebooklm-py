"""Derivations for the semantic-refactor compatibility baselines.

The baselines in this module freeze contracts that the release-to-release
public API audit does not fully describe:

* structural and pickle identity for every public dataclass and enum;
* the public metrics field vocabulary and composed logical-RPC event behavior; and
* per-channel ``to_jsonable`` field-key schemas for CLI, MCP, and REST.

All inventories come from the ``__all__`` surfaces of the public modules
discovered by ``scripts/audit_public_api_compat.py`` and live dataclass
metadata.  There is no hand-maintained model list to go stale.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import enum
import importlib
import inspect
import pickle
import types
import typing
from collections.abc import Mapping
from datetime import datetime, timezone
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


def _sample_value(annotation: object, field_name: str, stack: tuple[type[Any], ...]) -> object:
    """Return a small valid value for one required constructor parameter."""
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if annotation is str:
        return "contract"
    if annotation is int:
        return 1
    if annotation is float:
        return 1.0
    if annotation is bool:
        return False
    if annotation is bytes:
        return b"contract"
    if annotation is Any:
        return None
    if origin is typing.Literal:
        return args[0]
    if origin in (typing.Union, types.UnionType):
        if type(None) in args:
            return None
        return _sample_value(args[0], field_name, stack)
    if origin is list:
        return []
    if origin is tuple:
        return ()
    if origin is dict or origin is Mapping:
        return {}
    if origin is set:
        return set()
    if origin is frozenset:
        return frozenset()
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return next(iter(annotation))
    if isinstance(annotation, type) and dataclasses.is_dataclass(annotation):
        if annotation in stack:
            return None
        return _valid_dataclass_sample(annotation, stack=stack)
    if field_name == "status":
        return "success"
    return None


def _valid_dataclass_sample(cls: type[Any], *, stack: tuple[type[Any], ...] = ()) -> object:
    """Construct a valid public instance through its real ``__init__`` path."""
    try:
        hints = typing.get_type_hints(cls.__init__)
    except (NameError, TypeError):
        hints = {}
    kwargs: dict[str, object] = {}
    for field in dataclasses.fields(cls):
        if not field.init:
            continue
        if field.default is not dataclasses.MISSING:
            continue
        if field.default_factory is not dataclasses.MISSING:
            continue
        annotation = hints.get(field.name, field.type)
        kwargs[field.name] = _sample_value(annotation, field.name, (*stack, cls))
    return cls(**kwargs)


def _pickle_contract(value: object, *, identity: bool) -> dict[str, object]:
    """Characterize real pickle behavior without turning failure into a crash."""
    try:
        payload = pickle.dumps(value)
    except Exception as exc:  # noqa: BLE001 - failure is the measured contract
        category = (
            "unpickleable-thread-lock"
            if isinstance(exc, TypeError) and "RLock" in str(exc)
            else "other"
        )
        return {
            "status": "failure",
            "stage": "dumps",
            "error_type": type(exc).__qualname__,
            "error_category": category,
        }
    try:
        restored = pickle.loads(payload)
    except Exception as exc:  # noqa: BLE001 - failure is the measured contract
        return {
            "status": "failure",
            "stage": "loads",
            "error_type": type(exc).__qualname__,
            "error_category": "other",
        }
    matched = restored is value if identity else restored == value
    return {
        "status": "success" if matched else "mismatch",
        "comparison": "identity" if identity else "equality",
        "restored_type": _model_key(type(restored)),
    }


def _state_hook_contract(cls: type[Any], method_name: str) -> dict[str, object]:
    """Record first-party pickle-state hooks, ignoring interpreter-added object hooks."""
    for owner in cls.__mro__:
        if method_name not in owner.__dict__:
            continue
        if owner is object or not owner.__module__.startswith("notebooklm"):
            return {"present": False}
        return {
            "present": True,
            "owner": f"{owner.__module__}.{owner.__qualname__}",
        }
    return {"present": False}


def _legacy_state_contract(cls: type[Any]) -> dict[str, object] | None:
    """Exercise the two public legacy-state migrations protected by ``__setstate__``."""
    key = _model_key(cls)
    if key == "notebooklm.types.Notebook":
        timestamp = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        current = cls(id="contract-notebook", title="Contract", modified_at=timestamp)
        state = dict(current.__dict__)
        state.pop("last_viewed_at", None)
        state.pop("chat_sessions", None)
        expected = {
            "last_viewed_at_restored": timestamp,
            "chat_sessions_restored": [],
        }
    elif key == "notebooklm.types.ChatReference":
        current = cls(
            source_id="contract-source",
            answer_start_char=3,
            answer_end_char=8,
        )
        state = dict(current.__dict__)
        state.pop("fragment_start_char", None)
        state.pop("fragment_end_char", None)
        expected = {
            "fragment_start_char_restored": 3,
            "fragment_end_char_restored": 8,
            "answer_start_char_mirrored": 3,
            "answer_end_char_mirrored": 8,
        }
    else:
        return None

    hook = getattr(cls, "__setstate__", None)
    if not callable(hook):
        return {"status": "failure", "reason": "missing-__setstate__"}
    restored = cls.__new__(cls)
    hook(restored, state)
    if key == "notebooklm.types.Notebook":
        observed = {
            "last_viewed_at_restored": restored.last_viewed_at,
            "chat_sessions_restored": restored.chat_sessions,
        }
    else:
        observed = {
            "fragment_start_char_restored": restored.fragment_start_char,
            "fragment_end_char_restored": restored.fragment_end_char,
            "answer_start_char_mirrored": restored.answer_start_char,
            "answer_end_char_mirrored": restored.answer_end_char,
        }
    return {
        "status": "success" if observed == expected else "mismatch",
        "invariants": {name: observed[name] == value for name, value in expected.items()},
        "current_after_legacy_restore": _pickle_contract(restored, identity=False),
    }


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
        "pickle_state_hooks": {
            "__getstate__": _state_hook_contract(cls, "__getstate__"),
            "__setstate__": _state_hook_contract(cls, "__setstate__"),
        },
        "pickle_round_trip": _pickle_contract(_valid_dataclass_sample(cls), identity=False),
        "legacy_state_round_trip": _legacy_state_contract(cls),
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
        "pickle_state_hooks": {
            "__getstate__": _state_hook_contract(cls, "__getstate__"),
            "__setstate__": _state_hook_contract(cls, "__setstate__"),
        },
        "pickle_round_trip": _pickle_contract(sample, identity=True),
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


def _normalized_metrics_snapshot(snapshot: object) -> dict[str, object]:
    values = dataclasses.asdict(snapshot)
    for name, value in values.items():
        if isinstance(value, float) and value > 0.0:
            values[name] = "positive-float"
    return values


def _normalized_events(events: list[object]) -> list[dict[str, object]]:
    projected: list[dict[str, object]] = []
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
    return projected


async def _logical_rpc_scenario(
    outcome: str,
    *,
    drop_metrics_middleware: bool = False,
    disconnect_executor_metrics: bool = False,
) -> dict[str, object]:
    """Run one public ``rpc_call`` through production assembly and RpcExecutor."""
    from unittest.mock import AsyncMock

    import httpx

    from notebooklm import correlation_id
    from notebooklm._client_metrics import ClientMetrics
    from notebooklm._middleware.core import RpcResponse, build_chain
    from notebooklm._middleware.metrics import MetricsMiddleware
    from notebooklm.auth import AuthTokens
    from notebooklm.exceptions import DecodingError
    from notebooklm.rpc import RPCMethod
    from notebooklm.types import RpcTelemetryEvent
    from tests._fixtures.kernel_test_helpers import install_http_client_for_test
    from tests._helpers.client_factory import build_client_shell_for_tests

    events: list[RpcTelemetryEvent] = []

    def decode(
        _raw: str,
        rpc_id: str,
        *,
        allow_null: bool = False,
        raise_on_null_status: bool = False,
    ) -> dict[str, bool]:
        del allow_null, raise_on_null_status
        if outcome == "decode_error":
            raise DecodingError("contract decode drift", method_id=rpc_id)
        return {"ok": True}

    auth = AuthTokens(
        cookies={"SID": "contract-redacted"},
        csrf_token="contract-redacted",
        session_id="contract-redacted",
    )
    client = build_client_shell_for_tests(auth, on_rpc_event=events.append, decode_response=decode)
    install_http_client_for_test(
        client._collaborators.kernel,
        AsyncMock(spec=httpx.AsyncClient),
    )
    if disconnect_executor_metrics:
        client._rpc_executor._metrics = ClientMetrics(on_rpc_event=events.append)

    leaf_calls = 0

    async def fake_terminal(request: object) -> RpcResponse:
        nonlocal leaf_calls
        leaf_calls += 1
        await asyncio.sleep(0)
        if outcome == "transport_error":
            raise _ContractError("contract transport failure")
        return RpcResponse(
            response=httpx.Response(200, text=")]}'\n[]"),
            context=request.context,
        )

    middlewares = list(client._composed.middlewares)
    if drop_metrics_middleware:
        middlewares = [item for item in middlewares if not isinstance(item, MetricsMiddleware)]
    client._composed.chain_host._authed_post_chain = build_chain(middlewares, fake_terminal)

    raised: str | None = None
    result: object = None
    with correlation_id("contract-request-id"):
        try:
            result = await client.rpc_call(RPCMethod.GET_NOTEBOOK, ["contract-notebook"])
        except (DecodingError, _ContractError) as exc:
            raised = type(exc).__qualname__

    return {
        "result": result,
        "raised": raised,
        "leaf_calls": leaf_calls,
        "events": _normalized_events(events),
        "metrics_snapshot": _normalized_metrics_snapshot(client.metrics_snapshot()),
    }


async def _logical_rpc_scenarios() -> dict[str, object]:
    return {
        "success": await _logical_rpc_scenario("success"),
        "transport_error": await _logical_rpc_scenario("transport_error"),
        "decode_error": await _logical_rpc_scenario("decode_error"),
    }


async def _supplemental_middleware_scenarios() -> dict[str, object]:
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

        return {
            "event_count": len(events),
            "events": _normalized_events(events),
            "snapshot": _normalized_metrics_snapshot(metrics.snapshot()),
        }

    return {
        "rpc_success": await run(rpc_method="CONTRACT_RPC", failure=False),
        "rpc_error": await run(rpc_method="CONTRACT_RPC", failure=True),
        "non_rpc_success": await run(rpc_method=None, failure=False),
        "non_rpc_error": await run(rpc_method=None, failure=True),
    }


def derive_metrics_contract() -> dict[str, object]:
    """Derive public metrics fields and location-independent emission semantics."""
    from notebooklm.types import ClientMetricsSnapshot, RpcTelemetryEvent

    async def derive_scenarios() -> tuple[dict[str, object], dict[str, object]]:
        return await _logical_rpc_scenarios(), await _supplemental_middleware_scenarios()

    logical, supplemental = asyncio.run(derive_scenarios())
    return {
        "schema_version": 1,
        "client_metrics_snapshot_fields": _field_type_contract(ClientMetricsSnapshot),
        "rpc_telemetry_event_fields": _field_type_contract(RpcTelemetryEvent),
        "logical_rpc_scenarios": logical,
        "supplemental_non_rpc_middleware_scenarios": supplemental,
    }


_SECRET_BEARING_PUBLIC_MODELS = frozenset({"notebooklm.auth.AuthTokens"})
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


_CHANNEL_PROJECTION_SPECS: dict[str, tuple[dict[str, object], ...]] = {
    "cli --json": (
        {
            "model": "notebooklm.types.Artifact",
            "mode": "manual-list-row-projection",
            "keys": ("id", "title", "type", "type_id", "status", "status_id", "created_at"),
            "evidence": ("notebooklm/cli/artifact_cmd.py:artifact_list",),
        },
        {
            "model": "notebooklm.types.Artifact",
            "mode": "manual-get-projection",
            "keys": (
                "notebook_id",
                "id",
                "title",
                "type",
                "type_id",
                "status",
                "status_id",
                "created_at",
                "found",
            ),
            "evidence": ("notebooklm/cli/artifact_cmd.py:artifact_get",),
        },
        {
            "model": "notebooklm.types.GenerationStatus",
            "mode": "manual-poll-projection",
            "keys": ("task_id", "status", "url", "error", "error_code", "metadata"),
            "evidence": ("notebooklm/cli/artifact_cmd.py:artifact_poll",),
        },
        {
            "model": "notebooklm.types.GenerationStatus",
            "mode": "manual-wait-projection",
            "keys": ("artifact_id", "status", "url", "error"),
            "evidence": (
                "notebooklm/cli/artifact_cmd.py:artifact_wait",
                "notebooklm/cli/artifact_cmd.py:artifact_retry",
            ),
        },
        {
            "model": "notebooklm.types.GenerationStatus",
            "mode": "manual-retry-kickoff-projection",
            "keys": ("task_id", "status", "url", "error", "error_code"),
            "evidence": ("notebooklm/cli/artifact_cmd.py:if not wait",),
        },
        {
            "model": "notebooklm.types.GenerationStatus",
            "mode": "manual-generation-completed-projection",
            "keys": ("task_id", "status", "url"),
            "evidence": (
                "notebooklm/_app/generate_retry.py:generation_outcome_from_status",
                "notebooklm/cli/generate_cmd.py:_output_generation_outcome",
            ),
        },
        {
            "model": "notebooklm.types.GenerationStatus",
            "mode": "manual-generation-pending-projection",
            "keys": ("task_id", "status"),
            "evidence": (
                "notebooklm/_app/generate_retry.py:generation_outcome_from_status",
                "notebooklm/cli/generate_cmd.py:_output_generation_outcome",
            ),
        },
        {
            "model": "notebooklm.types.GenerationStatus",
            "mode": "manual-generation-failure-envelope",
            "keys": ("error", "code", "message"),
            "evidence": (
                "notebooklm/_app/generate_retry.py:generation_outcome_from_status",
                "notebooklm/cli/generate_cmd.py:_output_generation_outcome",
                "notebooklm/cli/error_handler.py:response: dict =",
            ),
        },
        {
            "model": "notebooklm.types.GenerationStatus",
            "mode": "nested-timeout-transition-projection",
            "keys": ("task_id", "status", "url", "error", "error_code", "metadata"),
            "evidence": ("notebooklm/cli/error_handler.py:_generation_status_extra",),
        },
        {
            "model": "notebooklm.types.AskResult",
            "mode": "app-view:ask_result_view",
            "evidence": (
                "notebooklm/_app/views.py:ask_result_view",
                "notebooklm/cli/chat_cmd.py:ask_result_view",
            ),
            "nested_fields": ("references", "turn_key", "next_steps"),
        },
        {
            "model": "notebooklm.types.CitedSourceSelection",
            "mode": "manual-completed-wait-projection",
            "keys": ("cited_only", "cited_sources_selected", "cited_only_fallback"),
            "evidence": ("notebooklm/cli/research_cmd.py:_completed_wait_payload",),
        },
        {
            "model": "notebooklm.types.CitedSourceSelection",
            "mode": "manual-direct-import-projection",
            "keys": ("cited_only", "cited_only_fallback"),
            "evidence": ("notebooklm/cli/research_cmd.py:_render_import_result",),
        },
        {
            "model": "notebooklm.types.Collection",
            "mode": "manual-mutation-projection",
            "keys": ("id", "name", "emoji", "notebook_ids"),
            "evidence": ("notebooklm/cli/collection_cmd.py:_collection_payload",),
        },
        {
            "model": "notebooklm.types.Collection",
            "mode": "manual-list-row-projection",
            "keys": ("id", "name", "emoji", "notebook_count"),
            "evidence": ("notebooklm/cli/collection_cmd.py:collection_list",),
        },
        {
            "model": "notebooklm.types.Label",
            "mode": "manual-field-projection",
            "keys": ("id", "name", "emoji", "source_ids", "sources"),
            "evidence": ("notebooklm/cli/services/label_listing.py:_label_serialize",),
        },
        {
            "model": "notebooklm.types.MindMap",
            "mode": "manual-tree/id-projection",
            "keys": ("mind_map", "note_id", "kind"),
            "evidence": ("notebooklm/cli/generate_cmd.py:_output_mind_map_result",),
        },
        {
            "model": "notebooklm.types.MindMapResult",
            "mode": "manual-tree/id-projection",
            "keys": ("mind_map", "note_id", "kind"),
            "evidence": ("notebooklm/cli/generate_cmd.py:_output_mind_map_result",),
        },
        {
            "model": "notebooklm.types.Note",
            "mode": "manual-list-row-projection",
            "keys": ("id", "title", "preview"),
            "evidence": ("notebooklm/cli/note_cmd.py:note_list",),
        },
        {
            "model": "notebooklm.types.Note",
            "mode": "dataclass-get-projection",
            "keys": ("id", "notebook_id", "title", "content", "created_at", "found"),
            "evidence": ("notebooklm/cli/note_cmd.py:payload = asdict",),
        },
        {
            "model": "notebooklm.types.Note",
            "mode": "manual-create-projection",
            "keys": ("id", "notebook_id", "title", "created"),
            "evidence": ("notebooklm/cli/note_cmd.py:note_create",),
        },
        {
            "model": "notebooklm.types.Notebook",
            "mode": "manual-list-row-projection",
            "keys": (
                "id",
                "title",
                "is_owner",
                "role",
                "created_at",
                "last_viewed_at",
                "modified_at",
            ),
            "evidence": ("notebooklm/cli/notebook_cmd.py:notebook_viewed_keys",),
        },
        {
            "model": "notebooklm.types.Notebook",
            "mode": "manual-create-projection",
            "keys": ("notebook",),
            "nested_keys": {
                "notebook": (
                    "id",
                    "title",
                    "role",
                    "created_at",
                    "last_viewed_at",
                    "modified_at",
                )
            },
            "evidence": ("notebooklm/cli/notebook_cmd.py:create_cmd",),
        },
        {
            "model": "notebooklm.types.Notebook",
            "mode": "manual-create-and-use-projection",
            "keys": ("notebook", "active_notebook_id"),
            "nested_keys": {
                "notebook": (
                    "id",
                    "title",
                    "role",
                    "created_at",
                    "last_viewed_at",
                    "modified_at",
                )
            },
            "evidence": ("notebooklm/cli/notebook_cmd.py:active_notebook_id",),
        },
        {
            "model": "notebooklm.types.Notebook",
            "mode": "manual-collection-member-projection",
            "keys": ("id", "title"),
            "evidence": ("notebooklm/cli/collection_cmd.py:collection_notebooks",),
        },
        {
            "model": "notebooklm.types.NotebookMetadata",
            "mode": "manual-to-dict-projection",
            "keys": (
                "id",
                "title",
                "created_at",
                "last_viewed_at",
                "modified_at",
                "is_owner",
                "role",
                "sources",
            ),
            "evidence": ("notebooklm/cli/notebook_cmd.py:metadata.to_dict",),
        },
        {
            "model": "notebooklm.types.NotebookDescription",
            "mode": "manual-summary-projection",
            "keys": ("notebook_id", "summary"),
            "evidence": ("notebooklm/cli/notebook_cmd.py:summary_cmd",),
        },
        {
            "model": "notebooklm.types.NotebookDescription",
            "mode": "manual-summary-with-topics-projection",
            "keys": ("notebook_id", "summary", "suggested_topics"),
            "evidence": ("notebooklm/cli/notebook_cmd.py:suggested_topics",),
        },
        {
            "model": "notebooklm.types.PromptSuggestion",
            "mode": "manual-field-projection",
            "keys": ("title", "prompt"),
            "evidence": ("notebooklm/cli/chat_cmd.py:suggest_prompts_cmd",),
        },
        {
            "model": "notebooklm.types.ReportSuggestion",
            "mode": "manual-field-projection",
            "keys": ("title", "description", "prompt"),
            "evidence": ("notebooklm/cli/artifact_cmd.py:artifact_suggestions",),
        },
        {
            "model": "notebooklm.types.ResearchTask",
            "mode": "manual-empty-status-projection",
            "keys": ("status", "tasks"),
            "evidence": ("notebooklm/cli/research_cmd.py:result.public_dict",),
        },
        {
            "model": "notebooklm.types.ResearchTask",
            "mode": "manual-status-projection",
            "keys": ("task_id", "status", "query", "sources", "summary", "report", "tasks"),
            "evidence": ("notebooklm/cli/research_cmd.py:result.public_dict",),
        },
        {
            "model": "notebooklm.types.ResearchSource",
            "mode": "nested-to-public-dict-projection",
            "keys": ("url", "title", "result_type"),
            "optional_keys": (
                "research_task_id",
                "report_markdown",
                "source_ordinal",
                "hint",
            ),
            "evidence": ("notebooklm/_types/research.py:to_public_dict",),
        },
        {
            "model": "notebooklm.types.ShareStatus",
            "mode": "manual-status-projection",
            "keys": (
                "notebook_id",
                "is_public",
                "access",
                "view_level",
                "share_url",
                "max_individuals_share_limit",
                "is_public_sharing_allowed",
                "is_public_sharing_denied",
                "shared_users",
            ),
            "evidence": ("notebooklm/cli/share_cmd.py:share_status",),
        },
        {
            "model": "notebooklm.types.ShareStatus",
            "mode": "manual-public-projection",
            "keys": ("notebook_id", "is_public", "share_url"),
            "evidence": ("notebooklm/cli/share_cmd.py:share_public",),
        },
        {
            "model": "notebooklm.types.ShareStatus",
            "mode": "manual-view-level-projection",
            "keys": ("notebook_id", "view_level"),
            "evidence": ("notebooklm/cli/share_cmd.py:share_view_level",),
        },
        {
            "model": "notebooklm.types.SharedUser",
            "mode": "nested-manual-field-projection",
            "keys": ("email", "permission", "display_name"),
            "evidence": ("notebooklm/cli/share_cmd.py:shared_users",),
        },
        {
            "model": "notebooklm.types.Source",
            "mode": "manual-summary-projection",
            "keys": ("id", "title", "type", "url"),
            "evidence": ("notebooklm/cli/services/source_serializers.py:source_summary_payload",),
        },
        {
            "model": "notebooklm.types.Source",
            "mode": "manual-row-projection",
            "keys": (
                "id",
                "title",
                "type",
                "url",
                "status",
                "status_id",
                "created_at",
                "drive_document_id",
                "drive_status",
                "is_drive_degraded",
            ),
            "evidence": ("notebooklm/cli/services/source_serializers.py:source_row_payload",),
        },
        {
            "model": "notebooklm.types.SourceFulltext",
            "mode": "manual-field-projection",
            "keys": ("source_id", "title", "kind", "content", "url", "char_count"),
            "evidence": ("notebooklm/cli/services/source_serializers.py:source_fulltext_payload",),
        },
        {
            "model": "notebooklm.types.SourceFulltext",
            "mode": "manual-file-output-projection",
            "keys": ("path", "bytes", "source_id", "title", "kind"),
            "evidence": ("notebooklm/cli/_source_render.py:content_bytes",),
        },
        {
            "model": "notebooklm.types.SourceGuide",
            "mode": "manual-guide-projection",
            "keys": ("source_id", "summary", "keywords"),
            "evidence": ("notebooklm/cli/_source_render.py:_render_source_guide_result",),
        },
        {
            "model": "notebooklm.types.SourceSummary",
            "mode": "nested-manual-to-dict-projection",
            "keys": ("type", "title", "url"),
            "evidence": ("notebooklm/_types/notebooks.py:class SourceSummary",),
        },
        {
            "model": "notebooklm.types.SuggestedTopic",
            "mode": "nested-scalar-field-projection",
            "keys": ("question",),
            "evidence": ("notebooklm/cli/notebook_cmd.py:topic.question",),
        },
    ),
    "mcp tool result": (
        {
            "model": "notebooklm.types.AccountLimits",
            "mode": "nested-manual-account-success-projection",
            "keys": (
                "email",
                "authuser",
                "available",
                "notebook_limit",
                "source_limit",
                "tier",
                "output_language",
                "output_language_is_default",
            ),
            "evidence": ("notebooklm/mcp/tools/meta.py:_account_block",),
        },
        {
            "model": "notebooklm.types.Artifact",
            "mode": "manual-studio-full-projection",
            "keys": ("id", "title", "type", "status_label", "url"),
            "evidence": ("notebooklm/mcp/tools/_studio_items.py:studio_items",),
        },
        {
            "model": "notebooklm.types.Artifact",
            "mode": "manual-studio-compact-projection",
            "keys": ("id", "title", "type", "status_label", "created_at"),
            "evidence": ("notebooklm/mcp/tools/_studio_items.py:compact_studio_item",),
        },
        {
            "model": "notebooklm.types.Artifact",
            "mode": "manual-studio-summary-projection",
            "keys": (
                "id",
                "title",
                "type",
                "status_label",
                "url",
                "created_at",
                "generation_prompt",
            ),
            "evidence": ("notebooklm/mcp/tools/_studio_items.py:summarize_studio_item",),
        },
        {
            "model": "notebooklm.types.GenerationStatus",
            "mode": "app-status-view-projection",
            "keys": (
                "notebook_id",
                "task_id",
                "status",
                "url",
                "error",
                "error_code",
                "metadata",
                "is_complete",
                "media_ready",
            ),
            "evidence": (
                "notebooklm/_app/artifacts.py:status_view",
                "notebooklm/mcp/tools/studio.py:studio_status",
            ),
        },
        {
            "model": "notebooklm.types.GenerationStatus",
            "mode": "manual-retry-projection",
            "keys": ("notebook_id", "artifact_id", "task_id", "status"),
            "evidence": ("notebooklm/mcp/tools/studio.py:studio_retry",),
        },
        {
            "model": "notebooklm.types.GenerationStatus",
            "mode": "manual-generate-projection",
            "keys": ("notebook_id", "kind", "task_id", "status", "url", "error"),
            "evidence": (
                "notebooklm/_app/generate_retry.py:generation_outcome_from_status",
                "notebooklm/mcp/tools/_studio_payloads.py:_generation_payload",
            ),
        },
        {
            "model": "notebooklm.types.AskResult",
            "mode": "app-view:ask_result_view",
            "evidence": (
                "notebooklm/_app/views.py:ask_result_view",
                "notebooklm/mcp/tools/chat.py:ask_result_view",
            ),
            "nested_fields": ("references", "turn_key", "next_steps"),
        },
        {
            "model": "notebooklm.types.ChatReference",
            "mode": "nested-lite-projection",
            "keys": ("source_id", "citation_number", "cited_text"),
            "evidence": ("notebooklm/mcp/tools/chat.py:_LITE_REFERENCE_FIELDS",),
        },
        {
            "model": "notebooklm.types.MindMap",
            "mode": "manual-tree/id-projection",
            "keys": ("notebook_id", "kind", "mind_map", "mind_map_id"),
            "evidence": ("notebooklm/mcp/tools/_studio_payloads.py:_generation_payload",),
        },
        {
            "model": "notebooklm.types.MindMapResult",
            "mode": "manual-tree/id-projection",
            "keys": ("notebook_id", "kind", "mind_map", "mind_map_id"),
            "evidence": ("notebooklm/mcp/tools/_studio_payloads.py:_generation_payload",),
        },
        {
            "model": "notebooklm.types.Note",
            "mode": "manual-studio-full-projection",
            "keys": ("id", "title", "type", "content"),
            "evidence": ("notebooklm/mcp/tools/_studio_items.py:studio_items",),
        },
        {
            "model": "notebooklm.types.Note",
            "mode": "manual-studio-compact-projection",
            "keys": ("id", "title", "type", "status_label", "created_at"),
            "evidence": ("notebooklm/mcp/tools/_studio_items.py:compact_studio_item",),
        },
        {
            "model": "notebooklm.types.Note",
            "mode": "manual-studio-summary-projection",
            "keys": ("id", "title", "type", "created_at", "content_preview", "char_count"),
            "evidence": ("notebooklm/mcp/tools/_studio_items.py:summarize_studio_item",),
        },
        {
            "model": "notebooklm.types.Notebook",
            "mode": "app-view:notebook_view",
            "evidence": (
                "notebooklm/_app/views.py:notebook_view",
                "notebooklm/mcp/tools/notebooks.py:_notebook_view",
            ),
        },
        {
            "model": "notebooklm.types.NotebookMetadata",
            "mode": "dataclass-root-with-notebook-view",
            "keys": ("notebook", "sources"),
            "evidence": ("notebooklm/mcp/tools/notebooks.py:metadata_block = to_jsonable",),
            "nested_fields": ("sources",),
        },
        {
            "model": "notebooklm.types.NotebookDescription",
            "mode": "nested-dataclass",
            "keys": ("summary", "suggested_topics"),
            "evidence": ("notebooklm/mcp/tools/notebooks.py:output = to_jsonable(result)",),
            "nested_fields": "all",
        },
        {
            "model": "notebooklm.types.PromptSuggestion",
            "mode": "manual-field-projection",
            "keys": ("title", "prompt"),
            "evidence": ("notebooklm/mcp/tools/chat.py:suggested_prompts",),
        },
        {
            "model": "notebooklm.types.ResearchStart",
            "mode": "manual-start-projection",
            "keys": ("notebook_id", "query", "mode", "poll_task_id"),
            "evidence": ("notebooklm/mcp/tools/research.py:start_fields",),
        },
        {
            "model": "notebooklm.types.ResearchTask",
            "mode": "manual-status-projection",
            "keys": (
                "notebook_id",
                "task_id",
                "poll_task_id",
                "kind",
                "status",
                "status_code",
                "termination_reason",
                "discovery_mode",
                "created_at",
                "updated_at",
                "duration_seconds",
                "query",
                "sources",
                "sources_total",
                "sources_returned",
                "sources_offset",
                "summary",
                "report",
                "report_char_count",
                "report_truncated",
            ),
            "optional_keys": ("reason_message", "hint", "cancelled", "deprecation"),
            "evidence": ("notebooklm/mcp/tools/research.py:payload",),
        },
        {
            "model": "notebooklm.types.ResearchSource",
            "mode": "nested-dataclass-with-report-gate",
            "keys": (
                "url",
                "title",
                "result_type",
                "research_task_id",
                "source_ordinal",
                "hint",
            ),
            "optional_keys": ("report_markdown",),
            "evidence": (
                "notebooklm/mcp/tools/research.py:to_jsonable(result.sources)",
                'notebooklm/mcp/tools/research.py:del src["report_markdown"]',
            ),
        },
        {
            "model": "notebooklm.types.ShareStatus",
            "mode": "app-view:share_status_view",
            "evidence": (
                "notebooklm/_app/views.py:share_status_view",
                "notebooklm/mcp/tools/sharing.py:_status_payload",
            ),
        },
        {
            "model": "notebooklm.types.ShareStatus",
            "mode": "app-view:share_status_view+view_level",
            "evidence": ("notebooklm/mcp/tools/sharing.py:view_level",),
        },
        {
            "model": "notebooklm.types.SharedUser",
            "mode": "nested-manual-field-projection",
            "keys": ("email", "permission", "display_name", "avatar_url"),
            "evidence": ("notebooklm/_app/views.py:shared_users",),
        },
        {
            "model": "notebooklm.types.Source",
            "mode": "app-view:source_view",
            "evidence": (
                "notebooklm/_app/views.py:source_view",
                "notebooklm/mcp/tools/sources.py:_source_view",
            ),
        },
        {
            "model": "notebooklm.types.Source",
            "mode": "manual-compact-projection",
            "keys": (
                "id",
                "title",
                "kind",
                "status_label",
                "drive_status_label",
                "created_at",
            ),
            "evidence": ("notebooklm/mcp/tools/sources.py:_source_compact",),
        },
        {
            "model": "notebooklm.types.SourceFulltext",
            "mode": "manual-content-projection",
            "keys": (
                "notebook_id",
                "source_id",
                "source",
                "content",
                "char_count",
                "truncated",
                "output_format",
            ),
            "evidence": ('notebooklm/mcp/tools/sources.py:detail == "full"',),
        },
        {
            "model": "notebooklm.types.SourceGuide",
            "mode": "manual-guide-projection",
            "keys": ("notebook_id", "source_id", "summary", "keywords"),
            "evidence": ('notebooklm/mcp/tools/sources.py:detail == "summary"',),
        },
        {
            "model": "notebooklm.types.UserSettings",
            "mode": "manual-account-success-projection",
            "keys": (
                "email",
                "authuser",
                "available",
                "notebook_limit",
                "source_limit",
                "tier",
                "output_language",
                "output_language_is_default",
            ),
            "evidence": ("notebooklm/mcp/tools/meta.py:_account_block",),
        },
    ),
    "rest response": (
        {
            "model": "notebooklm.types.AccountLimits",
            "mode": "nested-manual-account-success-projection",
            "keys": (
                "email",
                "authuser",
                "available",
                "notebook_limit",
                "source_limit",
                "tier",
                "output_language",
                "output_language_is_default",
            ),
            "evidence": ("notebooklm/server/routes/meta.py:_account_block",),
        },
        {
            "model": "notebooklm.types.Artifact",
            "mode": "dataclass-full",
            "evidence": ("notebooklm/server/routes/artifacts.py:to_jsonable(artifacts)",),
            "nested_fields": "all",
        },
        {
            "model": "notebooklm.types.GenerationStatus",
            "mode": "app-status-view-projection",
            "keys": (
                "notebook_id",
                "task_id",
                "status",
                "url",
                "error",
                "error_code",
                "metadata",
                "is_complete",
                "media_ready",
            ),
            "evidence": (
                "notebooklm/_app/artifacts.py:status_view",
                "notebooklm/server/routes/artifacts.py:projected =",
            ),
        },
        {
            "model": "notebooklm.types.GenerationStatus",
            "mode": "manual-retry-projection",
            "keys": ("notebook_id", "artifact_id", "task_id", "status"),
            "evidence": ("notebooklm/server/routes/artifacts.py:async def retry",),
        },
        {
            "model": "notebooklm.types.GenerationStatus",
            "mode": "manual-generate-projection",
            "keys": ("notebook_id", "kind", "task_id", "status", "url", "error"),
            "evidence": (
                "notebooklm/_app/generate_retry.py:generation_outcome_from_status",
                "notebooklm/server/routes/artifacts.py:_generation_payload",
            ),
        },
        {
            "model": "notebooklm.types.AskResult",
            "mode": "app-view:ask_result_view",
            "evidence": (
                "notebooklm/_app/views.py:ask_result_view",
                "notebooklm/server/routes/chat.py:ask_result_view",
            ),
            "nested_fields": ("references", "turn_key", "next_steps"),
        },
        {
            "model": "notebooklm.types.MindMap",
            "mode": "dataclass-full",
            "evidence": ("notebooklm/server/routes/artifacts.py:to_jsonable(result.mind_map)",),
            "nested_fields": "all",
        },
        {
            "model": "notebooklm.types.MindMapResult",
            "mode": "dataclass-full",
            "evidence": ("notebooklm/server/routes/artifacts.py:to_jsonable(result.mind_map)",),
            "nested_fields": "all",
        },
        {
            "model": "notebooklm.types.Note",
            "mode": "dataclass-full",
            "evidence": ("notebooklm/server/routes/notes.py:to_jsonable",),
            "nested_fields": "all",
        },
        {
            "model": "notebooklm.types.Notebook",
            "mode": "app-view:notebook_view",
            "evidence": (
                "notebooklm/_app/views.py:notebook_view",
                "notebooklm/server/routes/notebooks.py:notebook_view",
            ),
        },
        {
            "model": "notebooklm.types.PromptSuggestion",
            "mode": "manual-field-projection",
            "keys": ("title", "prompt"),
            "evidence": ("notebooklm/server/routes/notebooks.py:suggestions",),
        },
        {
            "model": "notebooklm.types.ResearchStart",
            "mode": "dataclass-full-with-poll-id",
            "keys": ("task_id", "report_id", "notebook_id", "query", "mode", "poll_id"),
            "evidence": ("notebooklm/server/routes/research.py:to_jsonable(result)",),
        },
        {
            "model": "notebooklm.types.ResearchTask",
            "mode": "manual-status-projection",
            "keys": (
                "notebook_id",
                "run_id",
                "task_id",
                "kind",
                "status",
                "status_code",
                "termination_reason",
                "reason_message",
                "hint",
                "discovery_mode",
                "created_at",
                "updated_at",
                "duration_seconds",
                "query",
                "sources",
                "summary",
                "report",
            ),
            "evidence": ("notebooklm/server/routes/research.py:research_status",),
        },
        {
            "model": "notebooklm.types.ResearchSource",
            "mode": "nested-dataclass-projection",
            "keys": (
                "url",
                "title",
                "result_type",
                "research_task_id",
                "report_markdown",
                "source_ordinal",
                "hint",
            ),
            "evidence": ("notebooklm/server/routes/research.py:to_jsonable(result.sources)",),
        },
        {
            "model": "notebooklm.types.ShareStatus",
            "mode": "app-view:share_status_view",
            "evidence": (
                "notebooklm/_app/views.py:share_status_view",
                "notebooklm/server/routes/share.py:share_status_view",
            ),
        },
        {
            "model": "notebooklm.types.ShareStatus",
            "mode": "app-view:share_status_view+view_level",
            "evidence": ("notebooklm/server/routes/share.py:include_view_level=True",),
        },
        {
            "model": "notebooklm.types.SharedUser",
            "mode": "nested-manual-field-projection",
            "keys": ("email", "permission", "display_name", "avatar_url"),
            "evidence": ("notebooklm/_app/views.py:shared_users",),
        },
        {
            "model": "notebooklm.types.Source",
            "mode": "app-view:source_view",
            "evidence": (
                "notebooklm/_app/views.py:source_view",
                "notebooklm/server/routes/sources.py:source_view",
            ),
        },
        {
            "model": "notebooklm.types.SourceFulltext",
            "mode": "manual-content-projection",
            "keys": (
                "notebook_id",
                "source_id",
                "content",
                "char_count",
                "truncated",
                "output_format",
            ),
            "evidence": ("notebooklm/server/routes/sources.py:get_source_content",),
        },
        {
            "model": "notebooklm.types.SourceGuide",
            "mode": "manual-guide-projection",
            "keys": ("notebook_id", "source_id", "summary", "keywords"),
            "evidence": ('notebooklm/server/routes/sources.py:detail == "summary"',),
        },
        {
            "model": "notebooklm.types.UserSettings",
            "mode": "manual-account-success-projection",
            "keys": (
                "email",
                "authuser",
                "available",
                "notebook_limit",
                "source_limit",
                "tier",
                "output_language",
                "output_language_is_default",
            ),
            "evidence": ("notebooklm/server/routes/meta.py:_account_block",),
        },
    ),
}


def _annotation_names(annotation: ast.expr | None) -> set[str]:
    if annotation is None:
        return set()
    return {
        node.id if isinstance(node, ast.Name) else node.attr
        for node in ast.walk(annotation)
        if isinstance(node, ast.Name | ast.Attribute)
    }


def _secret_serialization_violations(source: str, *, filename: str) -> list[str]:
    """Find credential-model values flowing into recursive dataclass serializers."""
    tree = ast.parse(source, filename=filename)
    secret_aliases = {"AuthTokens"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            if alias.name == "AuthTokens":
                secret_aliases.add(alias.asname or alias.name)

    secret_variables: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and _annotation_names(node.annotation) & secret_aliases:
            secret_variables.add(node.arg)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if _annotation_names(node.annotation) & secret_aliases:
                secret_variables.add(node.target.id)

    def is_secret(expression: ast.expr) -> bool:
        if isinstance(expression, ast.Name):
            return expression.id in secret_variables
        if isinstance(expression, ast.Attribute):
            return expression.attr == "auth"
        if isinstance(expression, ast.Call):
            target = expression.func
            if isinstance(target, ast.Name):
                if target.id in secret_aliases:
                    return True
            elif isinstance(target, ast.Attribute) and target.attr == "AuthTokens":
                return True
            return any(is_secret(argument) for argument in expression.args) or any(
                keyword.value is not None and is_secret(keyword.value)
                for keyword in expression.keywords
            )
        if isinstance(expression, ast.Dict):
            return any(is_secret(value) for value in expression.values)
        if isinstance(expression, ast.List | ast.Tuple | ast.Set):
            return any(is_secret(element) for element in expression.elts)
        return False

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and is_secret(node.value):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id not in secret_variables:
                        secret_variables.add(target.id)
                        changed = True
            if (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.value is not None
                and is_secret(node.value)
                and node.target.id not in secret_variables
            ):
                secret_variables.add(node.target.id)
                changed = True

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if _serializer_name(node) not in {"asdict", "to_jsonable"}:
            continue
        if is_secret(node.args[0]):
            violations.append(f"{filename}:{node.lineno}")
    return sorted(violations)


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
            relative_path = path.relative_to(source_root)
            source = path.read_text(encoding="utf-8")
            secret_violations.extend(
                _secret_serialization_violations(source, filename=str(relative_path))
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


def _validate_projection_evidence(evidence: tuple[str, ...], source_root: Path) -> None:
    for item in evidence:
        relative_path, separator, token = item.partition(":")
        path = source_root / relative_path
        if not separator or not path.is_file() or token not in path.read_text(encoding="utf-8"):
            raise ValueError(f"stale adapter projection evidence: {item}")


def _add_channel_projection(
    rows: dict[str, dict[str, list[dict[str, object]]]],
    model_key: str,
    projection: dict[str, object],
) -> None:
    projections = rows.setdefault(model_key, {"projections": []})["projections"]
    if projection not in projections:
        projections.append(projection)


def _exact_channel_projections(exported_inventory: dict[str, object]) -> dict[str, object]:
    """Build reviewed sink-backed projections and their transitive model shapes."""
    import notebooklm

    model_classes = {
        _model_key(cls): cls
        for cls in _public_model_exports()
        if dataclasses.is_dataclass(cls) and _model_key(cls) not in _SECRET_BEARING_PUBLIC_MODELS
    }
    public_dataclasses = set(model_classes.values())
    source_root = Path(notebooklm.__file__).resolve().parents[1]
    channels: dict[str, object] = {}

    for channel, specs in _CHANNEL_PROJECTION_SPECS.items():
        rows: dict[str, dict[str, list[dict[str, object]]]] = {}

        for spec in specs:
            model_key = str(spec["model"])
            if model_key not in model_classes:
                raise ValueError(f"unknown public projection model: {model_key}")
            cls = model_classes[model_key]
            evidence = typing.cast(tuple[str, ...], spec["evidence"])
            _validate_projection_evidence(evidence, source_root)
            keys = _projection_keys(spec, cls, exported_inventory)
            projection = {
                "mode": spec["mode"],
                "keys": keys,
                "evidence": list(evidence),
            }
            if "nested_keys" in spec:
                projection["nested_keys"] = {
                    key: list(value)
                    for key, value in typing.cast(
                        dict[str, tuple[str, ...]], spec["nested_keys"]
                    ).items()
                }
            if "optional_keys" in spec:
                projection["optional_keys"] = list(
                    typing.cast(tuple[str, ...], spec["optional_keys"])
                )
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
                    _add_channel_projection(
                        rows,
                        nested_key,
                        {
                            "mode": "nested-dataclass",
                            "keys": list(exported_inventory[nested_key]["to_jsonable_keys"]),
                            "evidence": [f"nested-via:{model_key}.{field_name}"],
                        },
                    )
        channels[channel] = {model: rows[model] for model in sorted(rows)}
    return channels


def _validate_no_secret_channel_models(channel_models: dict[str, set[str]]) -> None:
    violations = {
        channel: sorted(models & _SECRET_BEARING_PUBLIC_MODELS)
        for channel, models in channel_models.items()
        if models & _SECRET_BEARING_PUBLIC_MODELS
    }
    if violations:
        raise ValueError(
            f"secret-bearing models require a redacted adapter projection: {violations}"
        )


def derive_json_envelope_contract() -> dict[str, object]:
    """Freeze exported keys separately from per-channel adapter reachability."""
    from notebooklm._app.serialize import to_jsonable

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
    channel_model_keys = {
        channel: set(typing.cast(dict[str, object], models)) for channel, models in channels.items()
    }
    _validate_no_secret_channel_models(channel_model_keys)

    return {
        "schema_version": 1,
        "exported_inventory_selection": (
            "non-secret dataclasses in __all__ of every audit-discovered public module"
        ),
        "exported_dataclass_key_inventory": exported_inventory,
        "channels_selection": (
            "reviewed serializer sinks and projection helpers; each projection pins its actual "
            "keys and preserves only transitively serialized public dataclass fields"
        ),
        "channels": channels,
        "supplemental_import_references": _supplemental_channel_import_references(),
        "secret_bearing_exclusions": {
            "notebooklm.auth.AuthTokens": {
                "adapter_reachable": False,
                "policy": "requires an explicit redacted projection; credential fields are not envelopes",
            }
        },
    }


__all__ = [
    "derive_json_envelope_contract",
    "derive_metrics_contract",
    "derive_public_model_contract",
]
