"""Behavioral wiring tests for the maintainer live-auth matrix."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

from scripts import live_auth_matrix


def _args(*, skip_browser: bool) -> argparse.Namespace:
    argv = [
        "--profile",
        "source",
        "--account",
        "maintainer@example.com",
        "--base-url",
        "https://notebooklm.google.com",
        "--timeout",
        "10",
    ]
    if skip_browser:
        argv.append("--skip-browser")
    return live_auth_matrix.parse_args(argv)


def _source_profile(tmp_path: Path) -> Path:
    source = tmp_path / "profiles" / "source"
    source.mkdir(parents=True)
    (source / "storage_state.json").write_text('{"cookies": []}', encoding="utf-8")
    (source / "master_token.json").write_text('{"token": "test-only"}', encoding="utf-8")
    return source


def test_skip_browser_still_runs_storage_and_access_gate_cells(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _source_profile(tmp_path)
    monkeypatch.setattr(live_auth_matrix, "DEFAULT_HOME", tmp_path)
    matrix = live_auth_matrix.Matrix(_args(skip_browser=True))
    calls: list[str] = []

    always = (
        "phase_baseline",
        "phase_master_refresh",
        "phase_import_filter",
        "phase_hosts",
        "phase_rpc_bundle_health",
        "phase_rpc_health",
        "phase_concurrency",
        "phase_storage_mid_session",
        "phase_sibling_concurrent_mid_session",
        "phase_master_token_mid_session",
        "phase_rest_auth_recovery",
        "phase_mcp_auth_recovery",
        "phase_rpc_access_gate_contract",
        "phase_fault_injection",
        "phase_crash_safety",
    )
    for name in always:
        monkeypatch.setattr(matrix, name, lambda name=name: calls.append(name))
    monkeypatch.setattr(
        matrix,
        "phase_browser_discovery",
        lambda: pytest.fail("browser discovery must be skipped"),
    )
    monkeypatch.setattr(
        matrix,
        "phase_browser_login",
        lambda: pytest.fail("browser login must be skipped"),
    )
    monkeypatch.setattr(
        matrix,
        "phase_browser_mid_session",
        lambda: pytest.fail("browser refresh must be skipped"),
    )
    monkeypatch.setattr(matrix, "revision", lambda: "test-revision")
    monkeypatch.setattr(
        matrix,
        "worktree_info",
        lambda: {"worktree_dirty": False, "worktree_diff_hash": "test"},
    )

    assert matrix.run() == 0
    capsys.readouterr()
    assert calls == list(always)


def test_storage_mid_session_cell_disables_every_external_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _source_profile(tmp_path)
    monkeypatch.setattr(live_auth_matrix, "DEFAULT_HOME", tmp_path)
    matrix = live_auth_matrix.Matrix(_args(skip_browser=True))
    observed: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        env = kwargs["env"]
        assert isinstance(env, dict)
        observed["command"] = command
        observed["env"] = env
        copied = matrix.temp / "mid-session-storage" / "profiles" / "mid-session-storage"
        assert not (copied / "master_token.json").exists()
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps({"before": 2, "after": 2, "reload_calls": 1, "live_cookies": 8}),
            "",
        )

    monkeypatch.setattr(live_auth_matrix.subprocess, "run", fake_run)
    try:
        matrix.phase_storage_mid_session()
    finally:
        live_auth_matrix.shutil.rmtree(matrix.temp, ignore_errors=True)

    env = observed["env"]
    assert isinstance(env, dict)
    assert env["NOTEBOOKLM_REFRESH_BROWSER"] == ""
    assert env["NOTEBOOKLM_REFRESH_CMD"] == ""
    assert env["NOTEBOOKLM_REFRESH_CMD_MIDSESSION"] == ""
    assert env["NOTEBOOKLM_PROFILE"] == "mid-session-storage"
    command = observed["command"]
    assert isinstance(command, list)
    child_script = command[-1]
    assert "try_storage_cookie_reload = tracked_reload" in child_script
    assert "external recovery rung reached" in child_script
    assert matrix.results == [
        {
            "name": "mid-session-storage-reload",
            "status": "pass",
            "returncode": 0,
            "json": {"before": 2, "after": 2, "reload_calls": 1, "live_cookies": 8},
        }
    ]


def test_baseline_report_keeps_count_but_not_private_notebook_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _source_profile(tmp_path)
    monkeypatch.setattr(live_auth_matrix, "DEFAULT_HOME", tmp_path)
    matrix = live_auth_matrix.Matrix(_args(skip_browser=True))
    responses = iter(
        (
            subprocess.CompletedProcess(["auth"], 0, '{"status": "ok"}', ""),
            subprocess.CompletedProcess(
                ["list"],
                0,
                '{"count": 1, "notebooks": [{"id": "private-id", "title": "Private"}]}',
                "",
            ),
        )
    )
    monkeypatch.setattr(matrix, "cli", lambda *args, **kwargs: next(responses))
    try:
        matrix.phase_baseline()
    finally:
        live_auth_matrix.shutil.rmtree(matrix.temp, ignore_errors=True)

    assert matrix.results[1]["json"] == {"count": 1}
    assert "private-id" not in json.dumps(matrix.results)


def test_realistic_recovery_cells_wire_real_process_and_adapter_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _source_profile(tmp_path)
    monkeypatch.setattr(live_auth_matrix, "DEFAULT_HOME", tmp_path)
    matrix = live_auth_matrix.Matrix(_args(skip_browser=True))
    matrix.args.rpc_health_full = True
    matrix.args.read_only_notebook_id = "read-only-id"
    matrix.args.generation_notebook_id = "generation-id"
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        env = kwargs["env"]
        assert isinstance(env, dict)
        if env.get("NOTEBOOKLM_PROFILE") == "mid-session-browser":
            browser_profile = (
                matrix.temp / "mid-session-browser" / "profiles" / "mid-session-browser"
            )
            assert not (browser_profile / "master_token.json").exists()
        calls.append((command, env))
        return subprocess.CompletedProcess(command, 0, "{}", "")

    monkeypatch.setattr(live_auth_matrix.subprocess, "run", fake_run)
    try:
        matrix.phase_rpc_health()
        matrix.phase_sibling_concurrent_mid_session()
        matrix.phase_master_token_mid_session()
        matrix.phase_rest_auth_recovery()
        matrix.phase_mcp_auth_recovery()
        matrix.phase_browser_mid_session()
    finally:
        live_auth_matrix.shutil.rmtree(matrix.temp, ignore_errors=True)

    by_profile = {env["NOTEBOOKLM_PROFILE"]: (command, env) for command, env in calls}
    assert len(by_profile) == len(calls), "a phase issued more than one subprocess call"

    rpc_command, rpc_env = by_profile["rpc-health"]
    assert rpc_command[-1] == "--full"
    assert "check_rpc_health.py" in rpc_command[-2]
    assert rpc_env["NOTEBOOKLM_READ_ONLY_NOTEBOOK_ID"] == "read-only-id"
    assert rpc_env["NOTEBOOKLM_GENERATION_NOTEBOOK_ID"] == "generation-id"

    sibling_script = by_profile["mid-session-sibling"][0][-1]
    assert "--master-token-refresh" in sibling_script
    assert "asyncio.gather" in sibling_script
    assert "reload_calls <= 3" in sibling_script

    master_script = by_profile["mid-session-master"][0][-1]
    assert 'state["cookies"] = []' in master_script
    assert "tracked_master" in master_script
    assert "master_calls == 1" in master_script

    rest_script = by_profile["rest-live"][0][-1]
    assert 'http.get("/v1/notebooks"' in rest_script
    assert "app.state.notebooklm.client is None" in rest_script
    assert "--master-token-refresh" in rest_script

    mcp_script = by_profile["mcp-live"][0][-1]
    assert 'mcp.call_tool("notebook_list"' in mcp_script
    assert "keepalive=600.0" in mcp_script
    assert 'assert "total" in before and "notebooks" in before' in mcp_script

    browser_command, browser_env = by_profile["mid-session-browser"]
    browser_script = browser_command[-1]
    assert 'state["cookies"] = []' in browser_script
    assert "tracked_refresh" in browser_script
    assert "refresh_calls == 1" in browser_script
    assert browser_env["NOTEBOOKLM_PROFILE"] == "mid-session-browser"
    assert browser_env["NOTEBOOKLM_HEADLESS_REAUTH"] == ""
