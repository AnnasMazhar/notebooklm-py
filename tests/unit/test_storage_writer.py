"""Unit tests for the canonical ``storage_writer`` (refactor (b), b-PR1).

Covers the relocated intent-shaped API's per-intent lock-failure policy and the
value-free outcome contract. The CAS ``merge_cookie_delta`` body is exercised
verbatim by the existing 51-test CAS save-race suite (via the
``save_cookies_to_storage`` delegate) and is not re-tested here.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import httpx
import pytest

from notebooklm._auth import storage as storage_mod
from notebooklm._auth import storage_writer as sw


@contextlib.contextmanager
def _unavailable_lock(lock_path, *, blocking, log_prefix):
    yield "unavailable"


def _patch_lock_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the unified ``_file_lock`` primitive to report the sentinel as
    permanently unavailable (infrastructure failure)."""
    monkeypatch.setattr(storage_mod, "_file_lock", _unavailable_lock)


# --- value-free outcome contract -------------------------------------------


def test_write_outcome_is_value_free() -> None:
    """``WriteOutcome`` carries only an enum status — never any payload."""
    ok = sw.WriteOutcome(sw.WriteStatus.OK)
    bad = sw.WriteOutcome(sw.WriteStatus.LOCK_UNAVAILABLE)
    assert ok.ok and not ok.lock_unavailable
    assert bad.lock_unavailable and not bad.ok
    # A sentinel secret must never be able to reach repr/str (there is no field
    # to carry it — this pins that contract).
    assert "SENTINEL" not in repr(ok)
    for outcome in (ok, bad):
        assert set(vars(outcome)) == {"status"}


# --- update_account_metadata: full-file RMW, fails CLOSED -------------------


def test_update_account_metadata_writes_in_band(tmp_path: Path) -> None:
    path = tmp_path / "storage_state.json"
    path.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")
    sw.update_account_metadata(path, authuser=2, email="a@example.com")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["notebooklm"] == {
        "version": 1,
        "account": {"authuser": 2, "email": "a@example.com"},
    }


def test_update_account_metadata_fails_closed_on_lock_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "storage_state.json"
    path.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")
    _patch_lock_unavailable(monkeypatch)
    with pytest.raises(sw.LockUnavailableError):
        sw.update_account_metadata(path, authuser=1, email="a@example.com")
    # Fail-closed: the file must be untouched (no partial account write).
    assert "notebooklm" not in json.loads(path.read_text(encoding="utf-8"))


# --- clear_in_band_account: best-effort, swallows lock unavailability -------


def test_clear_in_band_account_swallows_lock_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "storage_state.json"
    sw.update_account_metadata(path, authuser=1, email="a@example.com")  # seed a record
    _patch_lock_unavailable(monkeypatch)
    # Best-effort: no raise, and the record is left intact.
    sw.clear_in_band_account(path)
    assert "notebooklm" in json.loads(path.read_text(encoding="utf-8"))


# --- persist_minted_jar: full replace, fails CLOSED ------------------------


def _minted_jar() -> httpx.Cookies:
    jar = httpx.Cookies()
    for name in ("SID", "APISID", "SAPISID"):
        jar.set(name, "v", domain=".google.com", path="/")
    return jar


def test_persist_minted_jar_replaces_cookies_and_rebinds_account(tmp_path: Path) -> None:
    path = tmp_path / "storage_state.json"
    path.write_text(
        json.dumps({"cookies": [{"name": "OLD", "value": "x", "domain": ".google.com"}]}),
        encoding="utf-8",
    )
    sw.persist_minted_jar(path, _minted_jar(), email="minted@example.com")
    data = json.loads(path.read_text(encoding="utf-8"))
    names = {c["name"] for c in data["cookies"]}
    assert names == {"SID", "APISID", "SAPISID"}  # replaced, not merged
    assert data["notebooklm"]["account"] == {"authuser": 0, "email": "minted@example.com"}


def test_persist_minted_jar_fails_closed_on_lock_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "storage_state.json"
    _patch_lock_unavailable(monkeypatch)
    with pytest.raises(sw.LockUnavailableError):
        sw.persist_minted_jar(path, _minted_jar(), email="minted@example.com")


# --- write_master_token: now locked + atomic, fails CLOSED -----------------


def test_write_master_token_roundtrip_and_mode(tmp_path: Path) -> None:
    import sys

    path = tmp_path / "master_token.json"
    sw.write_master_token(path, email="e@x.com", master_token="aas_et/M", android_id="abc")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {
        "version": 1,
        "email": "e@x.com",
        "android_id": "abc",
        "master_token": "aas_et/M",
    }
    if sys.platform != "win32":
        assert (path.stat().st_mode & 0o777) == 0o600  # full-account credential


def test_write_master_token_fails_closed_on_lock_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "master_token.json"
    _patch_lock_unavailable(monkeypatch)
    with pytest.raises(sw.LockUnavailableError):
        sw.write_master_token(path, email="e@x.com", master_token="aas_et/M", android_id="abc")


# --- bounded acquire tristate ----------------------------------------------


def test_acquire_storage_lock_held_then_released(tmp_path: Path) -> None:
    lock_path = tmp_path / ".storage_state.json.lock"
    with sw._acquire_storage_lock(lock_path, log_prefix="test") as state:
        assert state == "held"
    # After release the same-process acquire succeeds again (in-process lock freed).
    with sw._acquire_storage_lock(lock_path, log_prefix="test") as state:
        assert state == "held"


def test_acquire_storage_lock_times_out_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under persistent contention the bounded acquire yields 'unavailable'
    within the deadline rather than blocking forever."""
    lock_path = tmp_path / ".storage_state.json.lock"

    @contextlib.contextmanager
    def always_contended(lp, *, blocking, log_prefix):
        yield "contended"

    monkeypatch.setattr(storage_mod, "_file_lock", always_contended)
    with sw._acquire_storage_lock(lock_path, log_prefix="test", deadline_seconds=0.05) as state:
        assert state == "unavailable"
