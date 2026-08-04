"""Guard the rebrand-host lane's ISOLATION in ``.github/workflows/rpc-health.yml``.

The nightly health workflow files its "Non-transient ERROR detected" issue with
a dedup probe that searches **by title alone**. So an issue-filing lane that can
fail every night is not merely noisy — after its first issue opens, the dedup
check suppresses every subsequent issue sharing that title, including the
legacy-degradation issue that is the only named trigger for revisiting the
default backend host.

The rebrand-host probe (``notebook.google.com``) is exactly such a lane: nothing
in this repository has ever observed that host serving batchexecute, so it may
legitimately report "absent" forever. It therefore gets its own title, its own
dedup key, and a *state-change* trigger rather than a recurring error.

These assertions are cheap and the failure they prevent is silent and total —
a suppressed alarm looks identical to a healthy night.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.repo_lint

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "rpc-health.yml"

# The legacy lane's title. The rebrand lane must never reuse it, nor any string
# that would collide with its ``in:title "..."`` dedup search.
LEGACY_ERROR_TITLE = "RPC Health Check: Non-transient ERROR detected"
REBRAND_TITLE = "Rebrand host RPC availability changed"


def _steps() -> list[dict[str, Any]]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["health-check"]["steps"]
    assert isinstance(steps, list)
    return steps


def _step(name: str) -> dict[str, Any]:
    for step in _steps():
        if step.get("name") == name:
            return step
    raise AssertionError(f"step not found in rpc-health.yml: {name!r}")


def _issue_steps() -> list[dict[str, Any]]:
    return [
        step
        for step in _steps()
        if str(step.get("uses", "")).startswith("peter-evans/create-issue-from-file")
    ]


def test_health_step_feeds_the_rebrand_state_file() -> None:
    """Without the state file the lane cannot report a *change*, only a state."""
    run = _step("Run RPC Health Check")["run"]
    assert "--rebrand-state-file rebrand-state.json" in run


def test_nightly_never_forces_a_base_url() -> None:
    """The scheduled run must stay on the default host.

    ``--base-url`` exists for manual investigation. Pointing the nightly at the
    rebrand host would stop collecting the legacy signal, which is the only
    named trigger for revisiting the default.
    """
    assert "--base-url" not in _step("Run RPC Health Check")["run"]


def test_rebrand_issue_title_is_distinct_from_every_other_lane() -> None:
    titles = [step["with"]["title"] for step in _issue_steps()]
    assert REBRAND_TITLE in titles
    assert LEGACY_ERROR_TITLE in titles
    assert len(titles) == len(set(titles)), f"duplicate issue titles share a dedup key: {titles}"


def test_rebrand_dedup_probe_searches_its_own_title() -> None:
    run = _step("Check for existing rebrand-host issue")["run"]
    assert f'in:title "{REBRAND_TITLE}"' in run
    assert LEGACY_ERROR_TITLE not in run


def test_rebrand_issue_fires_only_on_a_state_change() -> None:
    """A permanently-absent rebrand host must file nothing, ever."""
    creation = next(step for step in _issue_steps() if step["with"]["title"] == REBRAND_TITLE)
    condition = creation["if"]
    assert "steps.rebrand.outputs.changed == 'true'" in condition
    assert "steps.dup_rebrand.outputs.open == '0'" in condition
    # And it must not be gated on the health exit code — that is the other lane.
    assert "steps.health.outputs.exit_code" not in condition


def test_legacy_issue_lanes_are_untouched_by_the_rebrand_lane() -> None:
    """Every health-script lane still keys off ``steps.health.outputs.exit_code``.

    The bundle-drift lane is a different script (``steps.bundle``) and keeps its
    own gate; everything else the health script drives must stay exit-coded.
    """
    expected = {
        "RPC ID Mismatch Detected": "1",
        "RPC Health Check: Authentication Failure": "2",
        LEGACY_ERROR_TITLE: "3",
        "Studio customization cohort flipped — re-capture VideoStyle codes": "4",
    }
    seen = set()
    for step in _issue_steps():
        title = step["with"]["title"]
        if title not in expected:
            continue
        seen.add(title)
        assert f"steps.health.outputs.exit_code == '{expected[title]}'" in step["if"]
    assert seen == set(expected)
