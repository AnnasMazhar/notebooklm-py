"""Guardrail: the MCP and REST source adapters share ONE source-policy definition.

The batch/wait caps and the fatal-vs-isolate classifier live in the transport-neutral
``_app`` core (``_app.source_batch`` / ``_app.source_wait``). This gate forbids either
adapter from re-declaring the cap constants locally (which is how they drifted before —
the MCP copy swallowed fatal errors and skipped the caps) and pins that both consult
the same shared ``batch_item_is_fatal``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src" / "notebooklm"

_POLICY_NAMES = frozenset(
    {
        "MAX_BATCH_URLS",
        "MAX_WAIT_TIMEOUT",
        "MAX_WAIT_SOURCE_IDS",
        "MAX_WAIT_CONCURRENT_SOURCES",
    }
)

# The adapter modules that must IMPORT the policy, never re-declare it.
_ADAPTERS = [
    _SRC / "server" / "routes" / "sources.py",
    _SRC / "mcp" / "tools" / "sources.py",
    _SRC / "mcp" / "tools" / "_waitagg.py",
]

# The _app modules that are the sole definers.
_DEFINERS = [
    _SRC / "_app" / "source_batch.py",
    _SRC / "_app" / "source_wait.py",
]


def _module_level_assigned_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:  # module level only — imports/aliases are ImportFrom, not Assign
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


@pytest.mark.parametrize("adapter", _ADAPTERS, ids=lambda p: str(p.relative_to(_SRC)))
def test_adapters_do_not_redeclare_the_cap_policy(adapter: Path) -> None:
    """An adapter re-`MAX_* = ...` assignment (drift risk) fails here; imports are fine."""
    redeclared = _module_level_assigned_names(adapter) & _POLICY_NAMES
    assert not redeclared, (
        f"{adapter.relative_to(_SRC)} re-declares source-policy caps {sorted(redeclared)}; "
        "import them from _app.source_batch / _app.source_wait instead so MCP and REST "
        "can't drift."
    )


def test_app_defines_every_cap_exactly_once() -> None:
    """Each cap is a module-level assignment in exactly one _app definer module."""
    definers: dict[str, list[str]] = {name: [] for name in _POLICY_NAMES}
    for path in _DEFINERS:
        for name in _module_level_assigned_names(path) & _POLICY_NAMES:
            definers[name].append(path.name)
    for name, where in definers.items():
        assert len(where) == 1, f"{name} must be defined once in _app; found in {where}"


def test_both_adapters_share_the_same_fatal_classifier() -> None:
    """Both adapters bind the exact same ``batch_item_is_fatal`` object (no fork)."""
    pytest.importorskip("fastapi")
    pytest.importorskip("fastmcp")
    from notebooklm._app.source_batch import batch_item_is_fatal as canonical
    from notebooklm.mcp.tools import sources as mcp_sources
    from notebooklm.server.routes import sources as rest_sources

    assert mcp_sources.batch_item_is_fatal is canonical
    assert rest_sources.batch_item_is_fatal is canonical
