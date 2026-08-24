"""Regression tests for the independent GitHub Actions test matrix."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.repo_lint

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "test.yml"

_P0_AUDIT_MYPY_TARGETS = {
    "scripts/_operation_catalog_ast.py",
    "scripts/_operation_catalog_authorities.py",
    "scripts/_operation_catalog_evidence.py",
    "scripts/_operation_catalog_specs.py",
    "scripts/audit_adapter_json_sinks.py",
    "scripts/audit_adapter_projection_paths.py",
    "scripts/audit_operation_catalog.py",
}


def test_test_matrix_is_independent_and_preserves_ci_contract():
    """The test matrix must remain an independent 3-by-5 required check."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]

    assert {"quality", "test"} <= set(jobs)
    assert jobs["quality"]["name"] == "Code Quality"
    assert jobs["test"]["name"] == "Test (${{ matrix.os }}, Python ${{ matrix.python-version }})"
    assert "needs" not in jobs["test"]
    assert jobs["test"]["strategy"]["fail-fast"] is False

    matrix = jobs["test"]["strategy"]["matrix"]
    assert matrix == {
        "os": ["ubuntu-latest", "macos-latest", "windows-latest"],
        "python-version": ["3.10", "3.11", "3.12", "3.13", "3.14"],
    }


def test_quality_mypy_step_covers_p0_audit_modules() -> None:
    """The audit code that derives committed P0 contracts stays type-checked in CI."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    quality_steps = workflow["jobs"]["quality"]["steps"]
    command = next(step["run"] for step in quality_steps if step.get("name") == "Run type checking")

    assert set(command.split()) >= _P0_AUDIT_MYPY_TARGETS
