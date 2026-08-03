"""Human-facing identity of a running entry point: version string and prog name.

The commit resolves in two ways, in order:

1. ``_commit.py`` — baked in at build time by ``hatch_build.py`` when the build
   ran from a git checkout, so an installed package knows its commit with no
   ``.git`` nearby (released wheels and ``pip install git+…@main`` both go
   through that path; ``pip install .`` does too when the build backend can see
   ``.git``).
2. A live ``git rev-parse`` — for running straight from a source checkout
   (editable / ``PYTHONPATH``) where the build hook never ran.

Neither present (build couldn't see git) → bare version, no hash.

:func:`invoked_prog` answers the adjacent question — *which* command name the
user typed. ADR-0028 ships ``gemini-notebook{,-mcp,-server}`` alongside the
legacy ``notebooklm*`` scripts, all pointing at these same callables, so a
hardcoded ``prog`` would make the new commands advertise the old names in
``--help`` / ``--version``.
"""

from __future__ import annotations

import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from . import __version__

# src/notebooklm/_version_info.py -> parents[2] == repo root (holds .git).
# Slice (not [2]) yields () when the install lives too shallow to have a
# parents[2] — avoids an IndexError at import time on a pathological layout.
_REPO_ROOT = next(iter(Path(__file__).resolve().parents[2:3]), None)


def _embedded_commit() -> str | None:
    """The commit baked in by the build hook, or ``None`` when absent."""
    try:
        from ._commit import COMMIT
    except ImportError:
        return None
    return COMMIT or None


def _live_commit() -> str | None:
    """First 8 chars of HEAD from a git checkout, or ``None``."""
    if _REPO_ROOT is None or not (_REPO_ROOT / ".git").exists():
        return None
    try:
        out = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "rev-parse", "--short=8", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


@lru_cache(maxsize=1)
def version_string() -> str:
    """``"0.8.0 (5d748a26)"`` when the commit is known, else ``"0.8.0"``."""
    rev = _embedded_commit() or _live_commit()
    return f"{__version__} ({rev})" if rev else __version__


# The console-script names each entry point may legitimately answer to
# (ADR-0028). Both brands are permanent; neither is deprecated.
CLI_PROG_NAMES = frozenset({"notebooklm", "gemini-notebook"})
MCP_PROG_NAMES = frozenset({"notebooklm-mcp", "gemini-notebook-mcp"})
SERVER_PROG_NAMES = frozenset({"notebooklm-server", "gemini-notebook-server"})


def invoked_prog(allowed: frozenset[str], default: str) -> str:
    """The console-script name the user typed, when it is one we recognise.

    ``allowed`` is the set of script names bound to *this* entry point; anything
    else falls back to ``default``. The allowlist — rather than trusting
    ``sys.argv[0]`` outright — is what keeps the output truthful in the cases
    that actually occur:

    * ``python -m notebooklm.mcp`` sets ``argv[0]`` to a ``__main__.py`` path,
      which would otherwise be printed as the program name;
    * a wrapper script, symlink, or shell alias with an arbitrary name would
      otherwise let unrelated text appear in ``--help`` output.

    Windows' ``.exe`` suffix is stripped before matching, since the launcher
    reports ``notebooklm-mcp.exe`` for the same script POSIX calls
    ``notebooklm-mcp``.

    Both path separators are split on explicitly rather than via
    :class:`~pathlib.Path`, whose flavour follows the *host* OS: a
    ``WindowsPath`` splits ``\\`` but a ``PosixPath`` treats it as an ordinary
    filename character. That difference is invisible in production (Windows
    argv reaches a Windows interpreter) but would make the Windows cases
    untestable anywhere else — and the ADR-0028 script matrix is exactly the
    kind of thing that needs to be verifiable on CI's Linux runners.
    """
    argv0 = sys.argv[0] if sys.argv else ""
    if not argv0:
        return default
    name = argv0.replace("\\", "/").rsplit("/", 1)[-1]
    if name.lower().endswith(".exe"):
        name = name[: -len(".exe")]
    return name if name in allowed else default


if __name__ == "__main__":  # ponytail: smallest runnable check for the branch
    v = version_string()
    assert v.startswith(__version__), v
    print(v)
