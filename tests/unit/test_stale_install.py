"""Unit tests for the ADR-0028 dual-install collision warning.

The install states that matter are metadata states, so they are constructed
directly. Real end-to-end installs (both orders, plus the two uninstall orders)
are exercised by the ``verify-package`` workflow.

The two failure modes worth guarding against are opposite: staying silent in a
genuinely broken environment, and crying wolf on the *expected* post-0.9 state
where the extras-only shim and the canonical dist coexist by design. Most cases
below pin the second.
"""

from __future__ import annotations

import pytest

from notebooklm import _stale_install as si


class _FakePath:
    def __init__(self, posix: str) -> None:
        self._posix = posix

    def as_posix(self) -> str:
        return self._posix


class _FakeDist:
    def __init__(self, version: str, files: list[str] | None) -> None:
        self.version = version
        self.files = [_FakePath(p) for p in files] if files is not None else None


#: RECORD of a pre-shim release: real package files.
_REAL_FILES = [
    "notebooklm/__init__.py",
    "notebooklm/client.py",
    "notebooklm_py-0.8.0.dist-info/METADATA",
]
#: RECORD of the extras-only shim: metadata only, no package files.
_SHIM_FILES = ["notebooklm_py-0.9.0.dist-info/METADATA", "notebooklm_py-0.9.0.dist-info/RECORD"]


@pytest.fixture
def env(monkeypatch):
    def _install(dists: dict[str, _FakeDist]):
        def from_name(name: str):
            try:
                return dists[name]
            except KeyError:
                raise si.PackageNotFoundError(name) from None

        monkeypatch.setattr(si.Distribution, "from_name", staticmethod(from_name))

    return _install


def test_warns_when_a_pre_shim_dist_collides(env) -> None:
    env(
        {
            "notebooklm-py": _FakeDist("0.8.0", _REAL_FILES),
            "gemini-notebook-py": _FakeDist("0.9.0", _REAL_FILES),
        }
    )
    message = si.stale_install_warning()
    assert message is not None
    # The warning is only useful if it carries the exact remediation, including
    # --force-reinstall (a plain upgrade leaves the deleted files missing).
    assert si.REMEDIATION in message
    assert "--force-reinstall" in message


def test_silent_for_the_expected_shim_plus_canonical_state(env) -> None:
    """The designed post-0.9 state must never warn — it is not a collision."""
    env(
        {
            "notebooklm-py": _FakeDist("0.9.0", _SHIM_FILES),
            "gemini-notebook-py": _FakeDist("0.9.0", _REAL_FILES),
        }
    )
    assert si.stale_install_warning() is None


def test_dist_info_entries_alone_do_not_count_as_package_files(env) -> None:
    """`notebooklm_py-*.dist-info/...` must not be mistaken for `notebooklm/...`.

    A bare ``startswith("notebooklm")`` would match the shim's own dist-info
    paths and warn on every healthy install.
    """
    assert si._ships_package_files(_FakeDist("0.9.0", _SHIM_FILES)) is False


def test_silent_when_only_the_legacy_dist_is_installed(env) -> None:
    """A plain pre-0.9 install is not broken; there is nothing to collide with."""
    env({"notebooklm-py": _FakeDist("0.8.0", _REAL_FILES)})
    assert si.stale_install_warning() is None


def test_silent_when_only_the_canonical_dist_is_installed(env) -> None:
    env({"gemini-notebook-py": _FakeDist("0.9.0", _REAL_FILES)})
    assert si.stale_install_warning() is None


def test_falls_back_to_a_version_compare_when_record_is_stripped(env) -> None:
    """No RECORD (some distro packagers strip it) → decide on version."""
    env(
        {
            "notebooklm-py": _FakeDist("0.8.0", None),
            "gemini-notebook-py": _FakeDist("0.9.0", _REAL_FILES),
        }
    )
    assert si.stale_install_warning() is not None

    env(
        {
            "notebooklm-py": _FakeDist("0.9.0", None),
            "gemini-notebook-py": _FakeDist("0.9.0", _REAL_FILES),
        }
    )
    assert si.stale_install_warning() is None


def test_record_evidence_beats_the_version_heuristic(env) -> None:
    """A hypothetical future shim below 0.9.0 must not be flagged on version alone.

    This is why detection is marker-first: the version threshold is today's
    boundary, but the RECORD is the actual truth about file collisions.
    """
    env(
        {
            "notebooklm-py": _FakeDist("0.8.99", _SHIM_FILES),
            "gemini-notebook-py": _FakeDist("0.9.0", _REAL_FILES),
        }
    )
    assert si.stale_install_warning() is None


@pytest.mark.parametrize("bad_version", ["", "not-a-version", None])
def test_unparseable_version_without_record_does_not_cry_wolf(env, bad_version) -> None:
    env(
        {
            "notebooklm-py": _FakeDist(bad_version, None),
            "gemini-notebook-py": _FakeDist("0.9.0", _REAL_FILES),
        }
    )
    assert si.stale_install_warning() is None


def test_warn_helper_never_raises(monkeypatch) -> None:
    """This runs inside ``import notebooklm``; it must not be able to break it."""

    def boom():
        raise RuntimeError("metadata is on fire")

    monkeypatch.setattr(si, "stale_install_warning", boom)
    si.warn_on_stale_install()  # must not raise


def test_warn_helper_logs_the_message(monkeypatch, caplog) -> None:
    monkeypatch.setattr(si, "stale_install_warning", lambda: "collision detected")
    with caplog.at_level("WARNING", logger=si.__name__):
        si.warn_on_stale_install()
    assert "collision detected" in caplog.text


def test_importing_notebooklm_is_silent_in_this_environment(caplog) -> None:
    """Guard against the check firing on a normal development install."""
    import importlib

    with caplog.at_level("WARNING", logger=si.__name__):
        importlib.reload(si)
        si.warn_on_stale_install()
    assert caplog.text == ""
