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


# --- replace_from_remint: browser-capture re-mint (b-PR2) ------------------


def _captured_state() -> dict:
    """A minimal captured storage-state dict (auth cookies on ``.google.com``)."""
    return {
        "cookies": [
            {"name": "SID", "value": "v", "domain": ".google.com", "path": "/"},
            {"name": "SAPISID", "value": "s", "domain": ".google.com", "path": "/"},
        ],
        "origins": [],
    }


def test_replace_from_remint_carry_account_preserves_namespace(tmp_path: Path) -> None:
    """[capture-1] regression: an unattended (carry_account=True) re-mint keeps
    the pre-existing ``notebooklm`` account namespace. Pre-b-PR2 the bare
    ``atomic_write_json`` re-mint dropped it, misrouting the account."""
    path = tmp_path / "storage_state.json"
    path.write_text(
        json.dumps(
            {
                "cookies": [{"name": "OLD", "value": "x", "domain": ".google.com"}],
                "origins": [],
                "notebooklm": {"version": 1, "account": {"authuser": 3, "email": "keep@x.com"}},
            }
        ),
        encoding="utf-8",
    )
    outcome = sw.replace_from_remint(path, _captured_state(), carry_account=True)
    assert outcome.ok
    data = json.loads(path.read_text(encoding="utf-8"))
    # Cookies replaced (not merged) …
    assert {c["name"] for c in data["cookies"]} == {"SID", "SAPISID"}
    # … and the account binding survived the re-mint.
    assert data["notebooklm"] == {"version": 1, "account": {"authuser": 3, "email": "keep@x.com"}}


def test_replace_from_remint_no_carry_drops_stale_binding(tmp_path: Path) -> None:
    """The interactive arm (carry_account=False) drops the stale binding — the
    user may have signed into a different account; the CLI adapter's repair
    re-establishes it."""
    path = tmp_path / "storage_state.json"
    path.write_text(
        json.dumps(
            {
                "cookies": [{"name": "OLD", "value": "x", "domain": ".google.com"}],
                "origins": [],
                "notebooklm": {"version": 1, "account": {"authuser": 3, "email": "stale@x.com"}},
            }
        ),
        encoding="utf-8",
    )
    outcome = sw.replace_from_remint(path, _captured_state(), carry_account=False)
    assert outcome.ok
    data = json.loads(path.read_text(encoding="utf-8"))
    assert {c["name"] for c in data["cookies"]} == {"SID", "SAPISID"}
    assert "notebooklm" not in data  # stale binding dropped


def test_replace_from_remint_takes_storage_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[capture-2] lock-contract: the re-mint write serializes on the storage
    lock and **fails closed** (no write) when the lock is unavailable, instead of
    racing a concurrent keepalive with a lockless write."""
    path = tmp_path / "storage_state.json"
    _patch_lock_unavailable(monkeypatch)
    outcome = sw.replace_from_remint(path, _captured_state(), carry_account=True)
    assert outcome.lock_unavailable
    assert not path.exists()  # nothing written without the lock


def test_replace_from_remint_filters_domains_but_keeps_trusted_subdomains(
    tmp_path: Path,
) -> None:
    """The write-time filter runs INSIDE the writer: an unallowlisted-domain
    cookie never reaches disk, while trusted Google subdomains
    (``*.googleusercontent.com`` / ``drive.google.com``) survive
    (main's preserve-trusted-roots behavior)."""
    path = tmp_path / "storage_state.json"
    captured = {
        "cookies": [
            {"name": "SID", "value": "v", "domain": ".google.com", "path": "/"},
            {"name": "MEDIA", "value": "m", "domain": "lh3.googleusercontent.com", "path": "/"},
            {"name": "DRV", "value": "d", "domain": "drive.google.com", "path": "/"},
            # Unallowlisted sibling-product cookie — must be dropped.
            {"name": "YT", "value": "y", "domain": ".youtube.com", "path": "/"},
        ],
        "origins": [],
    }
    outcome = sw.replace_from_remint(path, captured, carry_account=False)
    assert outcome.ok
    names = {c["name"] for c in json.loads(path.read_text(encoding="utf-8"))["cookies"]}
    assert "YT" not in names  # unallowlisted domain filtered out at the chokepoint
    assert {"SID", "MEDIA", "DRV"} <= names  # trusted Google roots preserved


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


def test_persist_minted_jar_filters_unallowlisted_but_keeps_rebind(tmp_path: Path) -> None:
    """L4 gap fix (b-PR2): the minted jar is domain-filtered before it reaches
    disk (an unallowlisted cookie is dropped, trusted Google subdomains survive),
    while the rebind to the minted account (authuser=0 + minted email) stays."""
    path = tmp_path / "storage_state.json"
    jar = httpx.Cookies()
    for name in ("SID", "APISID", "SAPISID"):
        jar.set(name, "v", domain=".google.com", path="/")
    jar.set("MEDIA", "m", domain="lh3.googleusercontent.com", path="/")
    # An unallowlisted sibling-product cookie that must NOT reach disk.
    jar.set("YT", "y", domain=".youtube.com", path="/")

    sw.persist_minted_jar(path, jar, email="minted@example.com")
    data = json.loads(path.read_text(encoding="utf-8"))
    names = {c["name"] for c in data["cookies"]}
    assert "YT" not in names  # L4: unallowlisted cookie filtered out at persist
    assert {"SID", "APISID", "SAPISID", "MEDIA"} <= names  # trusted roots survive
    # Rebind semantics unchanged by the added filter.
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
