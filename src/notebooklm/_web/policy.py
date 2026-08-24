"""Reviewed semantic/native policy bindings for active web operations.

``CallPolicy`` describes the whole semantic workflow.  It is deliberately not
the retry authority: individual web calls continue to resolve retry behavior
from :mod:`notebooklm._idempotency`.  This ledger makes the relationship exact
and fail-closed without feeding semantic policy back into the executor.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

from .._idempotency import (
    IDEMPOTENCY_REGISTRY,
    IdempotencyPolicy,
    IdempotencyRegistry,
)
from .._operations import CallPolicy, Operation, OperationDef
from ..rpc import RPCMethod


@dataclass(frozen=True, slots=True)
class NativePolicyBinding:
    """One exact native method/variant and its reviewed retry classification."""

    method: RPCMethod
    variant: str | None
    expected_policy: IdempotencyPolicy
    role: str


@dataclass(frozen=True, slots=True)
class WebCallPolicyBinding:
    """Whole-workflow policy plus every native call reachable in its web handler."""

    policy: CallPolicy
    native_bindings: tuple[NativePolicyBinding, ...]
    known_divergence: str | None = None


def _native(
    method: RPCMethod,
    expected_policy: IdempotencyPolicy,
    role: str,
    *,
    variant: str | None = None,
) -> NativePolicyBinding:
    return NativePolicyBinding(method, variant, expected_policy, role)


_IDEMPOTENT = IdempotencyPolicy.IDEMPOTENT_SET_OP
_PROBE_CREATE = IdempotencyPolicy.PROBE_THEN_CREATE
_NO_RETRY = IdempotencyPolicy.NON_IDEMPOTENT_NO_RETRY
_APP_GENERATION_DIVERGENCE = (
    "The exported notebooklm.artifacts.with_rate_limit_retry helper re-invokes the internal "
    "facade operation after rate limiting. P4.2 removes that internal use while preserving the "
    "public helper and adapter-neutral retry presentation policy."
)


WEB_CALL_POLICY_BINDINGS: Final[Mapping[Operation, WebCallPolicyBinding]] = MappingProxyType(
    {
        Operation.NOTEBOOK_LIST: WebCallPolicyBinding(
            CallPolicy.READ,
            (_native(RPCMethod.LIST_NOTEBOOKS, _IDEMPOTENT, "ordered collection read"),),
        ),
        Operation.NOTEBOOK_GET: WebCallPolicyBinding(
            CallPolicy.MUTATION,
            (_native(RPCMethod.GET_NOTEBOOK, _IDEMPOTENT, "read with recency side effect"),),
        ),
        Operation.NOTEBOOK_CREATE: WebCallPolicyBinding(
            CallPolicy.MUTATION,
            (
                _native(RPCMethod.LIST_NOTEBOOKS, _IDEMPOTENT, "baseline/probe/quota read"),
                _native(RPCMethod.CREATE_NOTEBOOK, _PROBE_CREATE, "guarded create"),
                _native(RPCMethod.GET_USER_SETTINGS, _IDEMPOTENT, "quota diagnosis"),
            ),
        ),
        Operation.NOTEBOOK_UPDATE: WebCallPolicyBinding(
            CallPolicy.MUTATION,
            (
                _native(RPCMethod.RENAME_NOTEBOOK, _IDEMPOTENT, "property mutation"),
                _native(RPCMethod.GET_NOTEBOOK, _IDEMPOTENT, "post-mutation readback"),
            ),
        ),
        Operation.NOTEBOOK_DELETE: WebCallPolicyBinding(
            CallPolicy.MUTATION,
            (_native(RPCMethod.DELETE_NOTEBOOK, _IDEMPOTENT, "idempotent delete"),),
        ),
        Operation.SOURCE_LIST: WebCallPolicyBinding(
            CallPolicy.MUTATION,
            (_native(RPCMethod.GET_NOTEBOOK, _IDEMPOTENT, "read with recency side effect"),),
        ),
        Operation.SOURCE_GET: WebCallPolicyBinding(
            CallPolicy.MUTATION,
            (_native(RPCMethod.GET_NOTEBOOK, _IDEMPOTENT, "read with recency side effect"),),
        ),
        Operation.SOURCE_ADD_URL: WebCallPolicyBinding(
            CallPolicy.MUTATION,
            (
                _native(RPCMethod.GET_NOTEBOOK, _IDEMPOTENT, "baseline/probe/wait read"),
                _native(
                    RPCMethod.ADD_SOURCE,
                    _PROBE_CREATE,
                    "guarded generic/YouTube create",
                    variant="url",
                ),
                _native(RPCMethod.UPDATE_SOURCE, _IDEMPOTENT, "optional title readback"),
            ),
        ),
        Operation.ARTIFACT_LIST: WebCallPolicyBinding(
            CallPolicy.READ,
            (
                _native(RPCMethod.LIST_ARTIFACTS, _IDEMPOTENT, "studio catalog read"),
                _native(
                    RPCMethod.GET_NOTES_AND_MIND_MAPS,
                    _IDEMPOTENT,
                    "conditional note-backed mind-map merge",
                ),
            ),
        ),
        Operation.ARTIFACT_GET: WebCallPolicyBinding(
            CallPolicy.READ,
            (
                _native(RPCMethod.LIST_ARTIFACTS, _IDEMPOTENT, "catalog identity scan"),
                _native(
                    RPCMethod.GET_NOTES_AND_MIND_MAPS,
                    _IDEMPOTENT,
                    "note-backed mind-map identity scan",
                ),
            ),
        ),
        Operation.ARTIFACT_GENERATE_AUDIO: WebCallPolicyBinding(
            CallPolicy.STATEFUL_START,
            (
                _native(
                    RPCMethod.GET_NOTEBOOK,
                    _IDEMPOTENT,
                    "conditional default-source resolution",
                ),
                _native(
                    RPCMethod.CREATE_ARTIFACT,
                    _PROBE_CREATE,
                    "audio artifact allocation",
                ),
            ),
            known_divergence=_APP_GENERATION_DIVERGENCE,
        ),
        Operation.NOTE_LIST: WebCallPolicyBinding(
            CallPolicy.READ,
            (
                _native(
                    RPCMethod.GET_NOTES_AND_MIND_MAPS,
                    _IDEMPOTENT,
                    "plain-note collection read",
                ),
            ),
        ),
        Operation.NOTE_GET: WebCallPolicyBinding(
            CallPolicy.READ,
            (
                _native(
                    RPCMethod.GET_NOTES_AND_MIND_MAPS,
                    _IDEMPOTENT,
                    "plain-note identity scan",
                ),
            ),
        ),
        Operation.NOTE_CREATE: WebCallPolicyBinding(
            CallPolicy.MUTATION,
            (
                _native(
                    RPCMethod.CREATE_NOTE,
                    _NO_RETRY,
                    "non-idempotent plain-note allocation",
                    variant="plain",
                ),
            ),
        ),
        Operation.NOTE_UPDATE: WebCallPolicyBinding(
            CallPolicy.MUTATION,
            (_native(RPCMethod.UPDATE_NOTE, _IDEMPOTENT, "note content/title set-op"),),
        ),
        Operation.NOTE_DELETE: WebCallPolicyBinding(
            CallPolicy.MUTATION,
            (_native(RPCMethod.DELETE_NOTE, _IDEMPOTENT, "idempotent note delete"),),
        ),
    }
)


def audit_web_call_policy_bindings(
    definitions: Mapping[Operation, OperationDef[Any, Any]],
    *,
    bindings: Mapping[Operation, WebCallPolicyBinding] = WEB_CALL_POLICY_BINDINGS,
    registry: IdempotencyRegistry = IDEMPOTENCY_REGISTRY,
) -> tuple[str, ...]:
    """Return deterministic active-binding drift errors without changing retry behavior."""
    errors: list[str] = []
    missing = set(definitions) - set(bindings)
    stale = set(bindings) - set(definitions)
    if missing:
        errors.append(
            "active web operations lack policy bindings: "
            + ", ".join(sorted(operation.value for operation in missing))
        )
    if stale:
        errors.append(
            "policy bindings name inactive web operations: "
            + ", ".join(sorted(operation.value for operation in stale))
        )

    for operation in sorted(set(definitions) & set(bindings), key=lambda item: item.value):
        definition = definitions[operation]
        binding = bindings[operation]
        if definition.key is not operation:
            errors.append(f"{operation.value}: definition key is {definition.key.value}")
        if definition.policy is not binding.policy:
            errors.append(
                f"{operation.value}: semantic policy is {definition.policy.value}, "
                f"reviewed binding expects {binding.policy.value}"
            )
        native_keys = [(item.method, item.variant) for item in binding.native_bindings]
        if len(native_keys) != len(set(native_keys)):
            errors.append(f"{operation.value}: duplicate native policy bindings")
        if not binding.native_bindings:
            errors.append(f"{operation.value}: active web operation has no native policy binding")
        for native in binding.native_bindings:
            try:
                actual = registry.get_entry(native.method, operation_variant=native.variant).policy
            except Exception as exc:  # pragma: no cover - registry owns exact exception types
                errors.append(
                    f"{operation.value}: cannot resolve {native.method.name}:"
                    f"{native.variant or '<default>'}: {type(exc).__name__}"
                )
                continue
            if actual is not native.expected_policy:
                errors.append(
                    f"{operation.value}: {native.method.name}:"
                    f"{native.variant or '<default>'} idempotency is {actual.value}, "
                    f"reviewed binding expects {native.expected_policy.value}"
                )
    return tuple(errors)


def web_call_policy_report() -> dict[str, object]:
    """Return the stable catalog projection of active semantic/native parity."""

    def operation_key(item: tuple[Operation, WebCallPolicyBinding]) -> str:
        operation, _binding = item
        return operation.value

    return {
        operation.value: {
            "call_policy": binding.policy.value,
            "known_divergence": binding.known_divergence,
            "native_bindings": [
                {
                    "rpc_method": native.method.name,
                    "variant": native.variant,
                    "idempotency_policy": native.expected_policy.value,
                    "role": native.role,
                }
                for native in binding.native_bindings
            ],
        }
        for operation, binding in sorted(WEB_CALL_POLICY_BINDINGS.items(), key=operation_key)
    }


__all__ = [
    "NativePolicyBinding",
    "WEB_CALL_POLICY_BINDINGS",
    "WebCallPolicyBinding",
    "audit_web_call_policy_bindings",
    "web_call_policy_report",
]
