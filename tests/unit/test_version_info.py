"""Unit tests for ``version_string()`` — the version + short-commit helper.

The two commit sources (build-embedded ``_commit.py`` vs. live ``git``) are
stubbed so the three resolution outcomes are covered deterministically,
independent of whether the test runs from a checkout or an installed wheel.
``version_string`` is ``lru_cache``d, so each test clears the cache first.
"""

from __future__ import annotations

import pathlib

import pytest

from notebooklm import _version_info as vi


def _reset() -> None:
    vi.version_string.cache_clear()


def test_embedded_commit_wins_and_skips_git(monkeypatch) -> None:
    """The baked-in commit is used; live git is never consulted."""
    _reset()
    monkeypatch.setattr(vi, "_embedded_commit", lambda: "abc12345")
    monkeypatch.setattr(vi, "_live_commit", lambda: _must_not_be_called())
    assert vi.version_string() == f"{vi.__version__} (abc12345)"
    _reset()


def test_falls_back_to_live_git(monkeypatch) -> None:
    """No embedded commit → the live-git commit is used."""
    _reset()
    monkeypatch.setattr(vi, "_embedded_commit", lambda: None)
    monkeypatch.setattr(vi, "_live_commit", lambda: "def67890")
    assert vi.version_string() == f"{vi.__version__} (def67890)"
    _reset()


def test_bare_version_when_no_commit(monkeypatch) -> None:
    """Neither source knows the commit → bare version, no parens."""
    _reset()
    monkeypatch.setattr(vi, "_embedded_commit", lambda: None)
    monkeypatch.setattr(vi, "_live_commit", lambda: None)
    assert vi.version_string() == vi.__version__
    _reset()


def _must_not_be_called():  # pragma: no cover - only runs if the guard fails
    raise AssertionError("_live_commit must not be called when a commit is embedded")


# ---------------------------------------------------------------------------
# invoked_prog: which of the six ADR-0028 console-script names was typed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv0", "expected"),
    [
        # Both permanent brands are honoured, for each entry point.
        ("/usr/local/bin/notebooklm-mcp", "notebooklm-mcp"),
        ("/usr/local/bin/gemini-notebook-mcp", "gemini-notebook-mcp"),
        # Windows launchers append .exe to the very same script.
        (r"C:\Python\Scripts\gemini-notebook-mcp.exe", "gemini-notebook-mcp"),
        (r"C:\Python\Scripts\notebooklm-mcp.EXE", "notebooklm-mcp"),
    ],
)
def test_invoked_prog_reports_the_name_that_was_run(monkeypatch, argv0, expected) -> None:
    monkeypatch.setattr(vi.sys, "argv", [argv0])
    assert vi.invoked_prog(vi.MCP_PROG_NAMES, "notebooklm-mcp") == expected


@pytest.mark.parametrize(
    "argv0",
    [
        # `python -m notebooklm.mcp` — argv[0] is a module path, and printing
        # "__main__.py" as the program name would be worse than the default.
        "/site-packages/notebooklm/mcp/__main__.py",
        # A wrapper, symlink, or alias under an unrelated name must not get to
        # inject arbitrary text into --help output.
        "/opt/corp/bin/run-the-thing",
        # Another entry point's script name: recognised overall, but not one
        # of *this* parser's names, so it must not be echoed here.
        "/usr/local/bin/gemini-notebook-server",
        # Degenerate argv.
        "",
    ],
)
def test_invoked_prog_falls_back_to_the_default(monkeypatch, argv0) -> None:
    monkeypatch.setattr(vi.sys, "argv", [argv0])
    assert vi.invoked_prog(vi.MCP_PROG_NAMES, "notebooklm-mcp") == "notebooklm-mcp"


def test_invoked_prog_survives_empty_argv(monkeypatch) -> None:
    """Embedders can leave ``sys.argv`` empty; indexing it blindly would raise."""
    monkeypatch.setattr(vi.sys, "argv", [])
    assert vi.invoked_prog(vi.CLI_PROG_NAMES, "notebooklm") == "notebooklm"


def test_prog_name_sets_are_disjoint_and_cover_the_declared_scripts() -> None:
    """The three allowlists must partition the six console scripts in pyproject.

    A name in two sets would let one entry point answer to another's command;
    a console script missing from every set would silently never be reported.
    """
    tomllib = pytest.importorskip("tomllib")
    root = pathlib.Path(__file__).resolve().parents[2]
    declared = set(
        tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    )
    sets = [vi.CLI_PROG_NAMES, vi.MCP_PROG_NAMES, vi.SERVER_PROG_NAMES]
    union: set[str] = set()
    for s in sets:
        assert not (union & s), f"prog-name allowlists overlap on {union & s}"
        union |= set(s)
    assert union == declared, (
        "the prog-name allowlists and [project.scripts] have drifted: "
        f"only in pyproject={declared - union}, only in allowlists={union - declared}"
    )
