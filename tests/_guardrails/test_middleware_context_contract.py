"""Fail-closed guards for the typed middleware call-state contract."""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from notebooklm._middleware.context import RpcCallState

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_STATE_FILES = [
    *sorted((ROOT / "src/notebooklm/_middleware").glob("*.py")),
    ROOT / "src/notebooklm/_runtime/transport.py",
]
_TEMPORARY_CONTEXT_OWNERS = {
    "src/notebooklm/_middleware/core.py",
    "src/notebooklm/_middleware/semaphore.py",
}


def _mutable_context_accesses(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    try:
        relpath = path.relative_to(ROOT).as_posix()
    except ValueError:
        relpath = path.name
    return [
        f"{relpath}:{node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "context"
    ]


def test_production_uses_typed_state_except_bounded_semaphore_bridge() -> None:
    violations: list[str] = []
    for path in PRODUCTION_STATE_FILES:
        relpath = path.relative_to(ROOT).as_posix()
        if relpath in _TEMPORARY_CONTEXT_OWNERS:
            continue
        violations.extend(_mutable_context_accesses(path))

    assert violations == []


def test_rpc_call_state_is_frozen_and_closed() -> None:
    state = RpcCallState.create(log_label="RPC TEST", rpc_method="TEST")

    with pytest.raises(dataclasses.FrozenInstanceError):
        state.log_label = "changed"  # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError)):
        state.ad_hoc = True  # type: ignore[attr-defined]


def test_rpc_call_state_preserves_identity_and_bounded_progress() -> None:
    deadline = object()
    budget = object()
    state = RpcCallState.create(
        retry_deadline=deadline,  # type: ignore[arg-type]
        refresh_budget=budget,  # type: ignore[arg-type]
    )

    state.record_queue_wait(0.25)
    state.advance_retry_attempt()

    assert state.retry_deadline is deadline
    assert state.refresh_budget is budget
    assert state.queue_wait_seconds == 0.25
    assert state.retry_attempt == 1


def test_context_access_guard_detects_new_owner(tmp_path: Path) -> None:
    path = tmp_path / "leak.py"
    path.write_text("def leak(request):\n    return request.context['new-key']\n", encoding="utf-8")

    accesses = _mutable_context_accesses(path)

    assert accesses == ["leak.py:2"]
