"""Regression tests for the independent GitHub Actions test matrix."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.repo_lint

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "test.yml"
NIGHTLY_WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "nightly.yml"

_P0_AUDIT_MYPY_TARGETS = {
    "scripts/_operation_catalog_ast.py",
    "scripts/_operation_catalog_authorities.py",
    "scripts/_operation_catalog_evidence.py",
    "scripts/_operation_catalog_specs.py",
    "scripts/audit_adapter_json_sinks.py",
    "scripts/audit_adapter_projection_paths.py",
    "scripts/audit_operation_catalog.py",
}


def _step(job: dict[str, object], name: str) -> dict[str, object]:
    steps = job["steps"]
    assert isinstance(steps, list)
    return next(step for step in steps if isinstance(step, dict) and step.get("name") == name)


def test_test_matrix_is_independent_and_preserves_ci_contract() -> None:
    """The required matrix covers every Python plus one secondary-OS cell."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]

    assert {"quality", "test", "repo-lint"} <= set(jobs)
    assert jobs["quality"]["name"] == "Code Quality"
    assert jobs["test"]["name"] == "Test (${{ matrix.os }}, Python ${{ matrix.python-version }})"
    assert "needs" not in jobs["test"]
    assert jobs["test"]["strategy"]["fail-fast"] is False

    matrix = jobs["test"]["strategy"]["matrix"]
    assert matrix == {
        "include": [
            {
                "os": "ubuntu-latest",
                "python-version": "3.10",
                "canonical": False,
                "windows_playwright": False,
            },
            {
                "os": "ubuntu-latest",
                "python-version": "3.11",
                "canonical": False,
                "windows_playwright": False,
            },
            {
                "os": "ubuntu-latest",
                "python-version": "3.12",
                "canonical": True,
                "windows_playwright": False,
            },
            {
                "os": "ubuntu-latest",
                "python-version": "3.13",
                "canonical": False,
                "windows_playwright": False,
            },
            {
                "os": "ubuntu-latest",
                "python-version": "3.14",
                "canonical": False,
                "windows_playwright": False,
            },
            {
                "os": "macos-latest",
                "python-version": "3.12",
                "canonical": False,
                "windows_playwright": False,
            },
            {
                "os": "windows-latest",
                "python-version": "3.12",
                "canonical": False,
                "windows_playwright": True,
            },
        ]
    }


def test_pr_matrix_runs_once_without_coverage_and_canonical_owns_reality() -> None:
    """Every cell runs the suite once; canonical alone owns browser contracts."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    test_job = workflow["jobs"]["test"]

    marker_filter = "not repo_lint and not requires_playwright and not requires_chromium"
    suite_step = _step(test_job, "Run tests without coverage")
    suite_command = str(suite_step["run"])
    assert "if" not in suite_step
    assert marker_filter in suite_command
    assert "-n auto" in suite_command
    assert "--dist loadgroup" in suite_command
    assert "--no-cov" in suite_command

    workflow_text = WORKFLOW.read_text(encoding="utf-8")
    assert "--cov" not in workflow_text
    step_names = {step.get("name") for step in test_job["steps"]}
    assert "Run tests with coverage" not in step_names
    assert "Run compatibility tests without coverage" not in step_names
    assert "Assert per-file coverage floors" not in step_names

    canonical_steps = {
        "Get Playwright version",
        "Cache Playwright browsers",
        "Install Playwright browsers",
        "Install Playwright system dependencies (Linux)",
        "Run required external-reality probes",
        "Run critical contract guards",
    }
    for name in canonical_steps:
        assert _step(test_job, name)["if"] == "matrix.canonical"

    reality_command = str(_step(test_job, "Run required external-reality probes")["run"])
    assert "-m reality" in reality_command
    assert "--require-reality" in reality_command

    critical_command = str(_step(test_job, "Run critical contract guards")["run"])
    assert "-n auto" in critical_command
    assert "--timeout=180" in critical_command
    assert "--no-cov" in critical_command
    assert "test_operation_catalog_is_total_and_current" in critical_command
    assert "test_baseline_matches_committed_file" in critical_command
    assert "test_no_flat_cookie_projection_reaches_an_http_request" in critical_command
    assert "test_no_cli_module_imports_minting_primitives" in critical_command
    assert "test_no_bare_master_token_derivation_outside_paths_module" in critical_command
    assert "test_raw_sync_playwright_is_confined_to_policy_gateway" in critical_command
    assert "tests/unit/test_ci_test_matrix.py" in critical_command

    smoke = _step(test_job, "Run Windows Playwright compatibility smoke serially")
    assert smoke["if"] == "matrix.windows_playwright"
    smoke_command = str(smoke["run"])
    assert (
        "tests/unit/test_windows_compatibility.py::TestPlaywrightSmokeTest::"
        "test_playwright_initializes_with_context_manager"
    ) in smoke_command
    assert "-m requires_playwright" in smoke_command
    assert "-n 0" in smoke_command
    assert "--no-cov" in smoke_command


def test_nightly_coverage_is_sha_pinned_secret_free_and_enforces_floors() -> None:
    """Scheduled/manual nightly owns global and per-file coverage enforcement."""
    workflow = yaml.safe_load(NIGHTLY_WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers) == {"schedule", "workflow_dispatch"}

    resolve_job = workflow["jobs"]["resolve-branch"]
    resolve_checkout = next(
        step
        for step in resolve_job["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert resolve_checkout["with"] == {
        "ref": "refs/heads/${{ steps.resolve.outputs.branch }}",
        "fetch-depth": 1,
        "persist-credentials": False,
    }

    job = workflow["jobs"]["coverage"]
    assert job["needs"] == "resolve-branch"
    assert job["if"] == "needs.resolve-branch.outputs.is_standard == 'true'"
    assert job["runs-on"] == "ubuntu-latest"
    assert "environment" not in job
    assert "secrets." not in str(job)

    checkout = next(
        step for step in job["steps"] if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert checkout["uses"] == "actions/checkout@v7"
    assert checkout["with"] == {
        "ref": "${{ needs.resolve-branch.outputs.sha }}",
        "fetch-depth": 1,
        "persist-credentials": False,
    }

    e2e_checkout = next(
        step
        for step in workflow["jobs"]["e2e"]["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert e2e_checkout["with"] == checkout["with"]

    setup_python = _step(job, "Set up Python")
    assert setup_python["uses"] == "actions/setup-python@v7"
    assert setup_python["with"]["python-version"] == "3.12"
    assert _step(job, "Install uv")["uses"] == (
        "astral-sh/setup-uv@37802adc94f370d6bfd71619e3f0bf239e1f3b78"
    )

    install_command = str(_step(job, "Install dependencies")["run"])
    assert "uv sync --frozen" in install_command
    for extra in {"browser", "dev", "markdown", "mcp", "server", "impersonate", "cookies"}:
        assert f"--extra {extra}" in install_command

    coverage_step = _step(job, "Run tests with coverage")
    coverage_command = str(coverage_step["run"])
    assert "-n auto" in coverage_command
    assert "--dist loadgroup" in coverage_command
    assert "not repo_lint and not requires_playwright and not requires_chromium" in coverage_command
    assert "--cov=src/notebooklm" in coverage_command
    assert "--cov-report=json:coverage.json" in coverage_command
    assert "--cov-fail-under=90" in coverage_command

    floor_step = _step(job, "Assert per-file coverage floors")
    assert floor_step["run"] == (
        "uv run python scripts/check_coverage_thresholds.py --coverage-json coverage.json"
    )
    assert job["steps"].index(coverage_step) < job["steps"].index(floor_step)


def test_repository_lint_is_a_bounded_manual_only_job() -> None:
    """Deep repo audits do not multiply across ordinary pull-request cells."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = workflow["jobs"]["repo-lint"]

    assert job["name"] == "Repository Lint (manual)"
    assert job["if"] == "github.event_name == 'workflow_dispatch'"
    assert "needs" not in job

    command = str(_step(job, "Run repository lint tests")["run"])
    assert "-m repo_lint" in command
    assert "-n auto" in command
    assert "--timeout=180" in command
    assert "--no-cov" in command
    assert "--cov=" not in command


def test_cassette_and_fixture_scans_run_once_in_quality() -> None:
    """Portable secret scans are not repeated across compatibility cells."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    quality_names = {step.get("name") for step in workflow["jobs"]["quality"]["steps"]}
    test_names = {step.get("name") for step in workflow["jobs"]["test"]["steps"]}
    scans = {"Assert cassettes are sanitized", "Check fixtures for credential leaks"}

    assert scans <= quality_names
    assert scans.isdisjoint(test_names)


def test_quality_mypy_step_covers_p0_audit_modules() -> None:
    """The audit code that derives committed P0 contracts stays type-checked in CI."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    quality_steps = workflow["jobs"]["quality"]["steps"]
    command = next(step["run"] for step in quality_steps if step.get("name") == "Run type checking")

    assert set(command.split()) >= _P0_AUDIT_MYPY_TARGETS
