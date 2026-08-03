"""The ADR-0028 forwarding shims must stay equivalent to the canonical dist.

``packaging/shims/{notebooklm-py,gemini-notebook}/`` publish no code; they exist
so that the former canonical name and the bare brand name keep resolving to
``gemini-notebook-py``. That only holds if their metadata tracks the canonical
``pyproject.toml`` exactly — and metadata drift is silent by nature. Adding a
new canonical extra without mirroring it does not fail anything at build time;
it just makes ``pip install "notebooklm-py[newextra]"`` quietly install fewer
packages than the same command against the canonical name.

So each clause of the contract in ``packaging/shims/README.md`` gets a test:

* extras parity, both directions;
* an exact ``==`` pin on the canonical version, and version lockstep;
* no Python modules and no console scripts (duplicate ``bin/`` ownership is the
  uninstall-corruption hazard ADR-0028 constraint 3 describes);
* the committed files match what ``generate.py`` produces.

``[mcp]`` gets its own assertion because already-shipped desktop extensions
hardcode ``notebooklm-py[mcp]``: losing that extra breaks installs we can no
longer patch.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SHIMS_DIR = REPO_ROOT / "packaging" / "shims"
CANONICAL_NAME = "gemini-notebook-py"
SHIM_NAMES = ["notebooklm-py", "gemini-notebook"]


def _load(path: pathlib.Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def canonical() -> dict:
    return _load(REPO_ROOT / "pyproject.toml")["project"]


@pytest.fixture(params=SHIM_NAMES)
def shim(request) -> dict:
    path = SHIMS_DIR / request.param / "pyproject.toml"
    assert path.is_file(), f"missing shim pyproject: {path}"
    data = _load(path)
    data["_name"] = request.param
    return data


def test_canonical_dist_is_named_for_the_new_brand(canonical) -> None:
    assert canonical["name"] == CANONICAL_NAME


def test_shim_declares_its_own_name(shim) -> None:
    assert shim["project"]["name"] == shim["_name"]


def test_shim_version_is_in_lockstep(shim, canonical) -> None:
    """A shim released at a different version than the dist it pins is unusable."""
    assert shim["project"]["version"] == canonical["version"]


def test_shim_pins_the_canonical_dist_exactly(shim, canonical) -> None:
    """``==`` and nothing looser: a range would let the two drift apart."""
    deps = shim["project"]["dependencies"]
    assert deps == [f"{CANONICAL_NAME}=={canonical['version']}"], (
        f"{shim['_name']} must depend on exactly the canonical dist at the exact version"
    )


def test_shim_mirrors_every_canonical_extra(shim, canonical) -> None:
    """Extras are not inherited; a missing one is a silently smaller install."""
    canonical_extras = set(canonical.get("optional-dependencies", {}))
    shim_extras = set(shim["project"].get("optional-dependencies", {}))
    missing = canonical_extras - shim_extras
    extra = shim_extras - canonical_extras
    assert not missing, (
        f"{shim['_name']} is missing extras {sorted(missing)}; "
        f'`pip install "{shim["_name"]}[{sorted(missing)[0]}]"` would resolve to fewer '
        "packages than the canonical dist. Regenerate: python packaging/shims/generate.py"
    )
    assert not extra, (
        f"{shim['_name']} declares extras the canonical dist does not: {sorted(extra)}"
    )


def test_shim_mcp_extra_forwards_with_the_exact_pin(shim, canonical) -> None:
    """Shipped desktop extensions hardcode `notebooklm-py[mcp]` — it cannot break."""
    mcp = shim["project"]["optional-dependencies"]["mcp"]
    assert mcp == [f"{CANONICAL_NAME}[mcp]=={canonical['version']}"]


def test_every_shim_extra_targets_the_canonical_dist_at_the_pinned_version(shim, canonical) -> None:
    version = canonical["version"]
    for name, requirements in shim["project"]["optional-dependencies"].items():
        assert len(requirements) == 1, f"{shim['_name']}[{name}] should forward to one requirement"
        (requirement,) = requirements
        assert requirement.startswith(f"{CANONICAL_NAME}["), (
            f"{shim['_name']}[{name}] must forward to the canonical dist, got {requirement!r}"
        )
        assert requirement.endswith(f"=={version}"), (
            f"{shim['_name']}[{name}] must pin =={version}, got {requirement!r}"
        )


def test_shim_declares_no_console_scripts(shim) -> None:
    """Two dists owning the same commands corrupt each other on uninstall.

    This is ADR-0028 constraint 3 applied to ``bin/``: the reason the shims
    accept the pipx ergonomics hit rather than shipping duplicate scripts.
    """
    assert "scripts" not in shim["project"], (
        f"{shim['_name']} must declare no console scripts — duplicate bin/ ownership "
        "recreates the uninstall-corruption hazard inside every dual-install venv"
    )
    assert "gui-scripts" not in shim["project"]
    assert "entry-points" not in shim["project"]


def test_shim_ships_no_python_package(shim) -> None:
    """A shim that shipped `notebooklm/` would recreate the file collision."""
    shim_dir = SHIMS_DIR / shim["_name"]
    python_files = list(shim_dir.rglob("*.py"))
    assert not python_files, (
        f"{shim['_name']} must ship no Python files, found: "
        f"{[str(p.relative_to(REPO_ROOT)) for p in python_files]}"
    )
    wheel_config = shim.get("tool", {}).get("hatch", {}).get("build", {}).get("targets", {})
    assert wheel_config.get("wheel", {}).get("bypass-selection") is True, (
        "the shim wheel must explicitly ship an empty payload"
    )


def test_shim_readme_points_at_the_canonical_dist(shim) -> None:
    readme = (SHIMS_DIR / shim["_name"] / "README.md").read_text(encoding="utf-8")
    assert CANONICAL_NAME in readme
    # The pipx caveat is a real behavioural difference; the README is the only
    # place a user encounters it before hitting "command not found".
    assert "--include-deps" in readme, "the pipx caveat must be documented on the shim page"
    assert "import notebooklm" in readme, "the permanent import name must be stated up front"


def test_committed_shims_match_the_generator() -> None:
    """`--check` is the CI gate; run it here so drift fails the normal suite too."""
    result = subprocess.run(
        [sys.executable, str(SHIMS_DIR / "generate.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, (
        f"shim packaging is stale:\n{result.stdout}\n{result.stderr}\n"
        "Regenerate with: python packaging/shims/generate.py"
    )


def test_canonical_dist_never_carries_a_pre_rename_version(canonical) -> None:
    """`gemini-notebook-py` may only ever be published at >= 0.9.0a0.

    The two dist names divide at 0.9.0: below it the real, file-shipping
    distribution is `notebooklm-py`; from 0.9.0 it is `gemini-notebook-py` and
    `notebooklm-py` is an extras-only shim (ADR-0028).

    The failure this guards is narrow but severe and entirely plausible:
    merging this branch into main and then setting the version back to 0.8.x
    for a maintenance patch. `publish.yml` validates that the TAG matches
    pyproject, not that the NAME matches the version, so `v0.8.1` would sail
    through and publish the canonical dist at a pre-rename version — below the
    floor every shim pins against, and below the boundary `_stale_install`
    uses to tell a shim from a colliding real install.
    """
    from packaging.version import Version

    version = Version(canonical["version"])
    assert version >= Version("0.9.0a0"), (
        f"{CANONICAL_NAME} is at {version}, but that dist name only exists from "
        "0.9.0 onward. A 0.8.x patch must be released from the pre-rename line "
        "(where project.name is still 'notebooklm-py'), not from this branch."
    )


def test_both_shim_names_are_present() -> None:
    """Guard the set itself: a deleted shim directory would skip its parametrised cases."""
    present = {p.name for p in SHIMS_DIR.iterdir() if p.is_dir()}
    assert present == set(SHIM_NAMES), f"unexpected shim directories: {present}"
