"""Every hardcoded repository slug must name the same repository.

ADR-0028 Phase 1 step 1 renames the GitHub repo. GitHub redirects web and git
traffic after a rename, but **string comparisons do not get redirects**: a
workflow guarded by ``if: github.repository == '<old-slug>'`` silently evaluates
false forever after, and the job stops running. It does not fail — it *skips* —
so nightly, rpc-health, verify-artifacts, and both publish workflows would go
quietly dormant and nobody would see red.

This guard makes that failure mode loud in advance. It does not know or care
which slug is correct; it asserts only that every place naming the repository
names the **same** one, and that the workflow guards agree with
``project.urls`` in ``pyproject.toml``. So the rename becomes a single
consistent edit: change them all and this stays green, change some and it fails
with the exact stragglers listed.

Historical records are excluded by design — CHANGELOG entries and ADRs describe
the past and must keep the slug they were written with.
"""

from __future__ import annotations

import pathlib
import re

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

#: `github.repository == '<owner>/<name>'` (or `!=`), in any workflow guard.
_REPO_GUARD_RE = re.compile(r"github\.repository\s*[=!]=\s*'([^']+)'")

#: Any `github.com/<owner>/<name>` reference in a tracked non-historical file.
_REPO_URL_RE = re.compile(r"github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)")

#: Slugs that legitimately point at OTHER projects (actions, upstream docs).
_FOREIGN_SLUG_PREFIXES = (
    "actions/",
    "pypa/",
    "docker/",
    "astral-sh/",
    "github/",
    "anthropics/",
    "peps/",
)


def _workflow_files() -> list[pathlib.Path]:
    return sorted(WORKFLOWS.glob("*.yml"))


def _canonical_slug() -> str:
    """The repository slug declared in ``pyproject.toml``'s ``project.urls``."""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    repository = data["project"]["urls"]["Repository"]
    match = _REPO_URL_RE.search(repository)
    assert match, f"project.urls.Repository is not a GitHub URL: {repository!r}"
    return match.group(1).removesuffix(".git")


def test_pyproject_urls_all_name_the_same_repository() -> None:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    slugs = set()
    for url in data["project"]["urls"].values():
        match = _REPO_URL_RE.search(url)
        if match:
            slugs.add(match.group(1).removesuffix(".git").removesuffix("#readme"))
    assert len(slugs) == 1, f"project.urls point at more than one repository: {sorted(slugs)}"


@pytest.mark.parametrize("path", _workflow_files(), ids=lambda p: p.name)
def test_workflow_repository_guards_match_the_canonical_slug(path: pathlib.Path) -> None:
    """A guard naming a stale slug disables its job silently — never loudly."""
    canonical = _canonical_slug()
    found = set(_REPO_GUARD_RE.findall(path.read_text(encoding="utf-8")))
    stale = {slug for slug in found if slug != canonical}
    assert not stale, (
        f"{path.relative_to(REPO_ROOT)} guards on repository slug(s) {sorted(stale)}, but "
        f"pyproject.toml declares {canonical!r}. String comparisons do not follow GitHub's "
        "post-rename redirect: this job would silently SKIP rather than fail. Update every "
        "`github.repository ==` guard in the same change as the rename (ADR-0028 step 1)."
    )


def test_fancy_pypi_readme_substitutions_use_the_canonical_slug() -> None:
    """These rewrite README links on the PyPI page; a stale slug 404s for readers."""
    canonical = _canonical_slug()
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    substitutions = data["tool"]["hatch"]["metadata"]["hooks"]["fancy-pypi-readme"]["substitutions"]
    stale: list[str] = []
    for entry in substitutions:
        for match in _REPO_URL_RE.finditer(entry["replacement"]):
            slug = match.group(1)
            if slug != canonical:
                stale.append(f"{entry['replacement']} (-> {slug})")
    assert not stale, (
        "fancy-pypi-readme substitutions name a repository other than "
        f"{canonical!r}; every link on the PyPI page would break:\n  " + "\n  ".join(stale)
    )


def test_oci_source_label_uses_the_canonical_slug() -> None:
    """`org.opencontainers.image.source` is what links a pushed image to its repo."""
    canonical = _canonical_slug()
    docker = (WORKFLOWS / "publish-docker.yml").read_text(encoding="utf-8")
    labels = re.findall(r"org\.opencontainers\.image\.source=\S+", docker)
    assert labels, "expected an OCI source label in publish-docker.yml"
    for label in labels:
        match = _REPO_URL_RE.search(label)
        assert match and match.group(1) == canonical, (
            f"OCI source label {label!r} does not name {canonical!r}"
        )


def test_no_workflow_names_a_second_first_party_repository() -> None:
    """Catch a half-done rename that leaves both slugs live across workflows."""
    canonical = _canonical_slug()
    owner = canonical.split("/", 1)[0]
    offenders: dict[str, set[str]] = {}
    for path in _workflow_files():
        text = path.read_text(encoding="utf-8")
        for slug in set(_REPO_URL_RE.findall(text)):
            slug = slug.removesuffix(".git")
            if slug.startswith(_FOREIGN_SLUG_PREFIXES):
                continue
            # Only first-party slugs (same owner) are in scope; third-party
            # links in comments are none of this guard's business.
            if slug.startswith(f"{owner}/") and slug != canonical:
                offenders.setdefault(path.name, set()).add(slug)
    assert not offenders, (
        f"workflows reference a first-party repository other than {canonical!r} — a "
        "partially-applied rename:\n"
        + "\n".join(f"  {name}: {sorted(slugs)}" for name, slugs in sorted(offenders.items()))
    )
