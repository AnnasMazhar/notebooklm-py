"""Hatchling build hook: bake the git commit and version into ``_commit.py``.

Runs for both the sdist and the wheel so the commit survives the standard
release path (``python -m build`` builds the wheel *from* the sdist, which has
no ``.git``):

- **sdist** (built from a checkout) embeds ``src/notebooklm/_commit.py`` into
  the tarball, so a wheel later built from that sdist still carries the commit.
- **wheel** (built directly from a checkout) embeds ``notebooklm/_commit.py``.
  When built from a carried-forward sdist the hook finds no ``.git`` and skips —
  the file the sdist carried is packaged normally.

``notebooklm._version_info`` reads it back at runtime so ``--version`` /
``server_info`` report the commit even with no ``.git`` around.

Only trusts git when ``.git`` sits at the build root — mirrors the runtime
guard so an sdist unpacked *inside another repo* can't bake in the enclosing
repo's HEAD. Every step is best-effort: any failure → no file → bare version.

The same file also carries ``VERSION``, the version this wheel was built at.
Unlike ``.dist-info`` metadata it lives *inside* the package, so it describes
the files on disk rather than a directory that some other distribution may also
claim. ``notebooklm._dist_version`` uses it to break the dual-install ownership
tie that ADR-0028 creates (two dist names shipping one import package). Note
the asymmetry with the commit: the commit needs git and so is skipped without
it, whereas the version comes from build metadata and is always stamped.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CommitBuildHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict) -> None:
        root = Path(self.root)
        # The version is always known from build metadata; the commit needs a
        # repo. Only trust git if THIS root is the repo — not an enclosing one.
        commit = _git_commit(str(root)) if (root / ".git").exists() else None
        dist_version = self.metadata.version or ""
        if not commit and not dist_version:
            return
        gen = root / "build" / "_commit.py"
        lines = [f'COMMIT = "{commit}"\n' if commit else "COMMIT = None\n"]
        lines.append(f'VERSION = "{dist_version}"\n' if dist_version else "VERSION = None\n")
        try:
            gen.parent.mkdir(parents=True, exist_ok=True)
            gen.write_text("".join(lines), encoding="utf-8")
        except OSError:
            return  # best-effort: read-only checkout etc. → bare version
        # sdist keeps the src layout; wheel flattens src/notebooklm -> notebooklm.
        dest = (
            "src/notebooklm/_commit.py" if self.target_name == "sdist" else "notebooklm/_commit.py"
        )
        build_data.setdefault("force_include", {})[str(gen)] = dest


def _git_commit(root: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", root, "rev-parse", "--short=8", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None
