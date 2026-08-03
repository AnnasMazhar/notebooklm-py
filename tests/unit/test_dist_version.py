"""Unit tests for ADR-0028 distribution-ownership version resolution.

The scenario under test only exists because two distribution names
(``notebooklm-py`` and ``gemini-notebook-py``) ship the *same* import package.
When both are installed, both ``RECORD`` files list ``notebooklm/__init__.py``,
so nothing about the metadata alone says which one wrote the bytes on disk.
:mod:`notebooklm._dist_version` decides by hashing the imported file and
matching it against each candidate's recorded digest.

These tests drive the resolver with fake distributions rather than building and
installing real wheels: the interesting states (mismatched versions, identical
contents, stripped RECORD hashes, both install orders) are all *metadata*
states, and constructing them directly is what makes each one individually
assertable. The companion end-to-end coverage — real dual installs in a real
venv — lives in the ``verify-package`` workflow.

The load-bearing property throughout: **the answer never depends on the order
distributions are considered in.** Every dual-install case here is asserted in
both orders, because "whichever dist name sorts first wins" is exactly the
wrong-version bug this module exists to prevent.
"""

from __future__ import annotations

import base64
import hashlib

import pytest

from notebooklm import _dist_version as dv


class _FakeHash:
    def __init__(self, mode: str, value: str) -> None:
        self.mode = mode
        self.value = value


class _FakePath:
    """Stand-in for ``importlib.metadata.PackagePath``."""

    def __init__(self, posix: str, file_hash: _FakeHash | None) -> None:
        self._posix = posix
        self.hash = file_hash

    def as_posix(self) -> str:
        return self._posix


class _FakeDist:
    def __init__(
        self,
        name: str,
        version: str,
        *,
        files: list[_FakePath] | None,
    ) -> None:
        self.version = version
        self.files = files
        self.metadata = {"Name": name}


def _digest(data: bytes, mode: str = "sha256") -> _FakeHash:
    """A RECORD-style digest of ``data`` (PEP 376 urlsafe-b64, unpadded)."""
    raw = hashlib.new(mode, data).digest()
    return _FakeHash(mode, base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii"))


def _owning(name: str, version: str, contents: bytes, *, mode: str = "sha256") -> _FakeDist:
    """A dist whose RECORD claims our file with a digest of ``contents``."""
    return _FakeDist(
        name,
        version,
        files=[
            _FakePath("notebooklm/other.py", _digest(b"unrelated")),
            _FakePath(dv._OWNED_FILE, _digest(contents, mode)),
        ],
    )


def _claiming_without_hash(name: str, version: str) -> _FakeDist:
    """A dist that claims our file but whose RECORD carries no digest."""
    return _FakeDist(name, version, files=[_FakePath(dv._OWNED_FILE, None)])


@pytest.fixture
def env(monkeypatch):
    """Install a fake distribution environment and on-disk file contents."""

    def _install(dists: dict[str, _FakeDist], *, disk: bytes | None, stamp: str | None = None):
        def from_name(name: str) -> _FakeDist:
            try:
                return dists[name]
            except KeyError:
                raise dv.PackageNotFoundError(name) from None

        monkeypatch.setattr(dv.Distribution, "from_name", staticmethod(from_name))
        monkeypatch.setattr(dv, "_imported_bytes", lambda: disk)
        monkeypatch.setattr(dv, "_stamped_version", lambda: stamp)

    return _install


# ---------------------------------------------------------------------------
# Single install — the common case
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["gemini-notebook-py", "notebooklm-py"])
def test_single_install_reports_its_own_version(env, name) -> None:
    """One candidate owns the file, so no hashing is needed to decide."""
    env({name: _owning(name, "0.9.0", b"payload")}, disk=b"payload")
    assert dv.resolve_version() == "0.9.0"


def test_single_install_wins_even_when_its_hash_is_stale(env) -> None:
    """A lone candidate is the owner by elimination, mismatched digest or not.

    Editable installs and post-install patching both leave RECORD digests
    stale; refusing to report a version in that state would be a regression
    against today's behaviour for no safety gain.
    """
    env({"gemini-notebook-py": _owning("gemini-notebook-py", "0.9.0", b"as-built")}, disk=b"edited")
    assert dv.resolve_version() == "0.9.0"


# ---------------------------------------------------------------------------
# Dual install — ownership decided by content
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("winner", ["gemini-notebook-py", "notebooklm-py"])
def test_dual_install_resolves_to_whichever_dist_wrote_the_files(env, winner) -> None:
    """Both install orders resolve correctly, including stale-last.

    Parametrising over the winner is the point: with ``notebooklm-py`` winning,
    the *older* dist's files are on disk, and any rule that prefers the new
    canonical name would report a version the running code does not have.
    """
    loser = "notebooklm-py" if winner == "gemini-notebook-py" else "gemini-notebook-py"
    on_disk = b"the bytes python imported"
    env(
        {
            winner: _owning(winner, "9.9.9", on_disk),
            loser: _owning(loser, "1.1.1", b"some other build"),
        },
        disk=on_disk,
        stamp="0.0.0-stamp-should-not-be-used",
    )
    assert dv.resolve_version() == "9.9.9"


def test_identical_contents_fall_back_to_the_build_stamp(env) -> None:
    """Same bytes in both dists → two hash matches → ambiguous, so use the stamp.

    Two matches is not "pick either": the versions differ, so picking either
    one is a coin flip. The stamp lives inside the package and therefore
    describes the files themselves.
    """
    same = b"byte-identical builds"
    env(
        {
            "gemini-notebook-py": _owning("gemini-notebook-py", "0.9.0", same),
            "notebooklm-py": _owning("notebooklm-py", "0.8.0", same),
        },
        disk=same,
        stamp="0.9.0",
    )
    assert dv.resolve_version() == "0.9.0"


def test_missing_record_hashes_fall_back_to_the_build_stamp(env) -> None:
    """Zero hash matches is as ambiguous as two — same fallback."""
    env(
        {
            "gemini-notebook-py": _claiming_without_hash("gemini-notebook-py", "0.9.0"),
            "notebooklm-py": _claiming_without_hash("notebooklm-py", "0.8.0"),
        },
        disk=b"whatever",
        stamp="0.9.1",
    )
    assert dv.resolve_version() == "0.9.1"


def test_unreadable_imported_file_falls_back_to_the_build_stamp(env) -> None:
    """No bytes to hash → no match is provable → stamp, not a guess."""
    env(
        {
            "gemini-notebook-py": _owning("gemini-notebook-py", "0.9.0", b"a"),
            "notebooklm-py": _owning("notebooklm-py", "0.8.0", b"b"),
        },
        disk=None,
        stamp="0.9.2",
    )
    assert dv.resolve_version() == "0.9.2"


def test_unknown_hash_algorithm_is_not_a_match(env) -> None:
    """An algorithm we cannot compute is unverifiable, never a silent match."""
    contents = b"payload"
    bogus = _FakeDist(
        "notebooklm-py",
        "0.8.0",
        files=[_FakePath(dv._OWNED_FILE, _FakeHash("not-a-real-algorithm", "xxx"))],
    )
    env(
        {
            "gemini-notebook-py": _owning("gemini-notebook-py", "0.9.0", contents),
            "notebooklm-py": bogus,
        },
        disk=contents,
        stamp="unused",
    )
    # The canonical dist is still the unique *verified* match, so it wins on
    # evidence rather than by being the only one left standing.
    assert dv.resolve_version() == "0.9.0"


def test_ambiguous_with_no_stamp_reports_the_dev_fallback(env, caplog) -> None:
    """Unresolvable → the not-installed marker plus an actionable warning.

    Reporting one of the two candidate versions here would be a guess presented
    as fact; the fallback is visibly not a real version.
    """
    same = b"identical"
    env(
        {
            "gemini-notebook-py": _owning("gemini-notebook-py", "0.9.0", same),
            "notebooklm-py": _owning("notebooklm-py", "0.8.0", same),
        },
        disk=same,
        stamp=None,
    )
    with caplog.at_level("WARNING", logger=dv.__name__):
        assert dv.resolve_version() == "0.0.0.dev0"
    assert "--force-reinstall" in caplog.text, "the warning must name the remediation"


# ---------------------------------------------------------------------------
# No claiming distribution
# ---------------------------------------------------------------------------


def test_source_checkout_prefers_the_stamp_then_gives_the_dev_fallback(env) -> None:
    env({}, disk=b"x", stamp="0.9.0")
    assert dv.resolve_version() == "0.9.0"
    env({}, disk=b"x", stamp=None)
    assert dv.resolve_version() == "0.0.0.dev0"


def test_stripped_record_still_reports_a_lone_installed_dist(env) -> None:
    """Distro packagers strip RECORD; one installed dist is still unambiguous."""
    env({"notebooklm-py": _FakeDist("notebooklm-py", "0.8.0", files=None)}, disk=b"x", stamp=None)
    assert dv.resolve_version() == "0.8.0"


def test_stripped_records_on_both_dists_do_not_pick_by_name_order(env) -> None:
    """Two installed dists, no RECORD evidence at all → refuse to choose.

    This is the case that would most tempt a "just take the first name" rule.
    """
    env(
        {
            "gemini-notebook-py": _FakeDist("gemini-notebook-py", "0.9.0", files=None),
            "notebooklm-py": _FakeDist("notebooklm-py", "0.8.0", files=None),
        },
        disk=b"x",
        stamp=None,
    )
    assert dv.resolve_version() == "0.0.0.dev0"


# ---------------------------------------------------------------------------
# Robustness: version reporting must never break `import notebooklm`
# ---------------------------------------------------------------------------


def test_hostile_metadata_does_not_raise(env, monkeypatch) -> None:
    class _Exploding:
        @property
        def files(self):
            raise RuntimeError("corrupt RECORD")

        @property
        def version(self):
            raise RuntimeError("corrupt METADATA")

        metadata: dict[str, str] = {}

    def from_name(name: str):
        if name == "notebooklm-py":
            return _Exploding()
        raise dv.PackageNotFoundError(name)

    monkeypatch.setattr(dv.Distribution, "from_name", staticmethod(from_name))
    monkeypatch.setattr(dv, "_stamped_version", lambda: None)
    assert dv.resolve_version() == "0.0.0.dev0"


def test_candidate_dists_are_exactly_the_two_known_names() -> None:
    """A third name must be a deliberate edit, not an accident."""
    assert set(dv._CANDIDATE_DISTS) == {"gemini-notebook-py", "notebooklm-py"}
