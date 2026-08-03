#!/usr/bin/env python3
"""Generate the ADR-0028 shim ``pyproject.toml`` files from the canonical one.

The shims must mirror **every** canonical extra, pinned to the exact canonical
version. Maintaining that by hand is the kind of duplication that silently rots:
a new extra added to the canonical dist would simply be missing downstream, and
``pip install "notebooklm-py[newextra]"`` would resolve to fewer packages
without erroring. So the files are generated, and ``--check`` fails CI when the
committed copies drift from what this script would produce.

Generated files are committed rather than built on the fly so that a release
builds exactly the reviewed bytes, and so ``pip install packaging/shims/...``
works from a plain checkout.

Usage::

    python packaging/shims/generate.py           # write
    python packaging/shims/generate.py --check   # verify only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

REPO_ROOT = Path(__file__).resolve().parents[2]
SHIMS_DIR = REPO_ROOT / "packaging" / "shims"
CANONICAL_PYPROJECT = REPO_ROOT / "pyproject.toml"

CANONICAL_NAME = "gemini-notebook-py"

#: Shim dist name -> the sentence explaining why that name is published.
SHIMS: dict[str, str] = {
    "notebooklm-py": (
        "Former name of gemini-notebook-py. Installing this package installs "
        "gemini-notebook-py; `import notebooklm` is unchanged and permanent."
    ),
    "gemini-notebook": (
        "Alias of gemini-notebook-py. Installing this package installs "
        "gemini-notebook-py; `import notebooklm` is unchanged and permanent."
    ),
}

_HEADER = """\
# GENERATED FILE — DO NOT EDIT.
# Regenerate with: python packaging/shims/generate.py
#
# ADR-0028 forwarding shim. Ships no Python modules and no console scripts;
# it exists so that `pip install {shim}` (and every extra of it) resolves to
# the canonical distribution. See packaging/shims/README.md.
"""


def _canonical() -> dict:
    return tomllib.loads(CANONICAL_PYPROJECT.read_text(encoding="utf-8"))["project"]


def _extras(project: dict) -> list[str]:
    """Canonical extra names, excluding the self-referential ``all``.

    ``all`` is re-synthesised below rather than copied: the canonical value is
    a self-reference (``gemini-notebook-py[...]``) that would resolve to the
    wrong thing if pasted verbatim into a differently-named dist.
    """
    return sorted(name for name in project.get("optional-dependencies", {}) if name != "all")


def render(shim: str) -> str:
    project = _canonical()
    version = project["version"]
    extras = _extras(project)

    lines = [_HEADER.format(shim=shim), "[project]", f'name = "{shim}"']
    lines.append(f'version = "{version}"')
    lines.append(f'description = "{SHIMS[shim]}"')
    lines.append(f'requires-python = "{project["requires-python"]}"')
    lines.append('license = {text = "MIT"}')
    authors = ", ".join(
        "{" + f'name = "{a["name"]}", email = "{a["email"]}"' + "}" for a in project["authors"]
    )
    lines.append(f"authors = [{authors}]")
    lines.append('readme = "README.md"')
    # The exact-version pin is what keeps a shim install and a canonical
    # install from ever disagreeing about which code is present.
    lines.append("dependencies = [")
    lines.append(f'    "{CANONICAL_NAME}=={version}",')
    lines.append("]")
    lines.append("")
    lines.append("# Every canonical extra is mirrored so that, e.g.,")
    lines.append(f'# `pip install "{shim}[mcp]"` installs exactly what')
    lines.append(f'# `pip install "{CANONICAL_NAME}[mcp]"` installs.')
    lines.append("[project.optional-dependencies]")
    for extra in extras:
        lines.append(f'{extra} = ["{CANONICAL_NAME}[{extra}]=={version}"]')
    all_extras = ",".join(
        extra for extra in extras if extra != "cookies" and extra != "impersonate"
    )
    lines.append(f'all = ["{CANONICAL_NAME}[{all_extras}]=={version}"]')
    lines.append("")
    # NOTE: deliberately no [project.scripts]. See README.md clause 1.
    lines.append("[build-system]")
    lines.append('requires = ["hatchling"]')
    lines.append('build-backend = "hatchling.build"')
    lines.append("")
    lines.append("# No Python package to ship — force an empty wheel payload.")
    lines.append("[tool.hatch.build.targets.wheel]")
    lines.append("bypass-selection = true")
    lines.append("")
    lines.append("[tool.hatch.build.targets.sdist]")
    lines.append('include = ["README.md", "pyproject.toml"]')
    return "\n".join(lines) + "\n"


def render_readme(shim: str) -> str:
    version = _canonical()["version"]
    if shim == "notebooklm-py":
        lead = (
            f"# {shim}\n\n"
            f"**This project is now published as "
            f"[`{CANONICAL_NAME}`](https://pypi.org/project/{CANONICAL_NAME}/).**\n\n"
            "Google renamed NotebookLM to Gemini Notebook, so the distribution name "
            "followed. This package is kept as a permanent forwarding shim: it ships "
            f"no code of its own and installs `{CANONICAL_NAME}` for you.\n"
        )
    else:
        lead = (
            f"# {shim}\n\n"
            f"**Alias for [`{CANONICAL_NAME}`](https://pypi.org/project/{CANONICAL_NAME}/).**\n\n"
            "This package ships no code of its own; it installs "
            f"`{CANONICAL_NAME}`.\n"
        )
    return (
        lead
        + f"""
Nothing about your code changes:

```python
import notebooklm  # permanent — the import name is not being renamed
```

Every extra is mirrored, so `pip install "{shim}[mcp]"` installs exactly what
`pip install "{CANONICAL_NAME}[mcp]"` installs.

## If you use pipx or `uv tool install`

This shim declares no console scripts (two distributions owning the same
commands would corrupt each other on uninstall). Isolated tool installers only
expose the requested distribution's entry points, so:

- `uvx --from "{shim}[mcp]" notebooklm-mcp` — works.
- `pipx install {shim}` — add `--include-deps` to get the commands, or
  install `{CANONICAL_NAME}` directly (recommended).

## Upgrading from a pre-0.9 install

`{shim} <= 0.8` shipped the `notebooklm` package files directly and collides
with `{CANONICAL_NAME}`. If you have both:

```bash
pip uninstall notebooklm-py && pip install --force-reinstall {CANONICAL_NAME}
```

`--force-reinstall` is required: uninstalling the old distribution deletes the
shared files, and a plain upgrade would consider the current install satisfied
and never restore them.

Current version: {version}
"""
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed shims match; exit non-zero on drift",
    )
    args = parser.parse_args(argv)

    drift: list[str] = []
    for shim in SHIMS:
        for relative, content in (
            ("pyproject.toml", render(shim)),
            ("README.md", render_readme(shim)),
        ):
            path = SHIMS_DIR / shim / relative
            if args.check:
                current = path.read_text(encoding="utf-8") if path.is_file() else None
                if current != content:
                    drift.append(str(path.relative_to(REPO_ROOT)))
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

    if drift:
        print(
            "Shim packaging is out of date with the canonical pyproject.toml:\n  "
            + "\n  ".join(drift)
            + "\n\nRegenerate with: python packaging/shims/generate.py",
            file=sys.stderr,
        )
        return 1
    if args.check:
        print("shims are up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
