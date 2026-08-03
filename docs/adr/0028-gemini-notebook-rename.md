# ADR-0028: Renaming the package for Google's "Gemini Notebook" rebrand

## Status

Proposed — v2, revised after a four-lens review (packaging, compatibility,
surface coverage, strategy) of the 2026-08-03 original. The original decided a
full rename including the import package; v2 reverses that (see Alternatives).

## Context

On 2026-07-16 Google renamed NotebookLM to **Gemini Notebook**
([announcement](https://blog.google/innovation-and-ai/products/gemini-notebook/notebooklm-gemini-notebook/)).
The product is unchanged for our purposes — same app, same `batchexecute` wire
protocol at `notebooklm.google.com` — but the project's discoverable identity
(PyPI listing, repo name, docs) now points at a retired brand. New users will
search for "gemini notebook python". PyPI availability checked 2026-08-03:
`gemini-notebook`, `gemini-notebook-py`, `gemini-notebook-client` are all
unregistered; PyPI has no reservation mechanism, so squatting is a live risk.

The name is carried on two very different kinds of surface:

- **Discoverability surfaces**: PyPI dist name, GitHub repo, README/docs
  branding, MCP/desktop-extension display names, Docker image, skill archive.
  These are what searchers and new users see.
- **Operational plumbing**: the `notebooklm` import package (~2 000 in-tree
  references), ~40 `NOTEBOOKLM_*` env vars (including write-side protocol vars
  such as the credential scrub in `_auth/refresh.py` and compose-interpolation
  vars in `deploy/`), `~/.notebooklm` config home, `logging.getLogger("notebooklm")`
  namespaces (a documented API), `NotebookLMClient`, the MCP server identity
  `"notebooklm"` written into users' client configs by `notebooklm mcp install`,
  installed skill directories (`.claude/skills/notebooklm/`), and a dense
  lattice of name-pinning guardrail tests and `scripts/` tooling.

Constraints that shape the decision:

1. **Google renames products often**; this brand is weeks old. Anything
   expensive or irreversible keyed to the new name is a bet on brand stability.
2. **Renaming plumbing strands users where we have no deprecation channel**:
   configs `mcp install` already wrote to users' machines, self-host `.env` /
   `docker-compose.yml` files attached to past releases, users' `logging`
   configs, pickles of `notebooklm.*` classes, installed skill copies.
3. **pip has no conflict mechanism.** Any two dists that both ship the
   `notebooklm` package (an old `notebooklm-py` ≤0.8 install plus a renamed
   canonical dist) silently clobber shared files, and uninstalling either
   corrupts the survivor.
4. **Publishing is OIDC Trusted-Publishing only** (`publish.yml`), keyed on
   owner/repo + workflow. GitHub's redirect after a repo rename does **not**
   apply to OIDC claims, so a repo rename breaks every configured publisher
   until re-registered.
5. **README currently promises the opposite** ("The package keeps the
   `notebooklm-py` name", July 2026 note). This ADR reverses a published
   commitment and must retract it explicitly, not silently.
6. This is a single-maintainer project; every permanent dual surface is
   permanent toil. A third-party `pynotebooklm` dist already crowds the
   namespace. ADR-0018 provides the deprecation machinery for anything we do
   deprecate.

## Decision

Rename the **distribution and the discoverability surfaces** to
**`gemini-notebook-py`**; keep the **import package `notebooklm` and all
operational plumbing permanently**. Dist-name ≠ import-name is an established
Python pattern (`beautifulsoup4`/`bs4`, `scikit-learn`/`sklearn`,
`pillow`/`PIL`), and every argument this ADR's Context makes against renaming
the wire layer applies equally to the import package: churn with no functional
gain, brand-stability risk, and — decisive — the plumbing writes its name into
places we cannot patch after the fact.

| Surface | Disposition |
|---|---|
| PyPI dist `notebooklm-py` | → `gemini-notebook-py` canonical; old name becomes a permanent extras-forwarding shim |
| Bare `gemini-notebook` | registered as redirect metapackage (anti-squat) |
| GitHub repo | → `teng-lin/gemini-notebook-py` (auto-redirects) |
| CLI | `gemini-notebook`, `gemini-notebook-mcp`, `gemini-notebook-server` added; `notebooklm*` scripts kept **indefinitely** (they match the import name; no deprecation) |
| Docker image / skill zip / `.mcpb` display name | new names added, old kept during wind-down |
| `import notebooklm`, `NOTEBOOKLM_*` env vars, `~/.notebooklm`, logger namespace, MCP identities (`SERVER_KEY`/`SERVER_NAME`/manifest `name`/skill dir), `[tool.notebooklm]` table | **permanent — never renamed** |
| `NotebookLMClient` | kept; `GeminiNotebookClient` added as a permanent (non-deprecated) alias |

No `gnb` short alias: a 3-letter binary is collision-prone (srsRAN ships a
`gnb` executable) and the acronym dies with the next rebrand.

The `-py` suffix signals continuity and, with the "Unofficial" description,
reduces the risk a bare `gemini-*` name reads as official Google. "Gemini" is
a hotter, more actively defended mark than "NotebookLM"; the bare-name
metapackage must carry a real dependency (not an empty squat, which PEP 541
frowns on) and we accept we may have to surrender it if challenged.

### Phase 0 — immediately

- Register `gemini-notebook-py` and `gemini-notebook` on PyPI. Placeholders
  (`0.1.0`) **depend on `notebooklm-py`** so an early `pip install` works
  rather than dead-ends; yank them once the real release ships. `publish.yml`'s
  tag/version validation rejects out-of-band versions, so this is a one-off
  manual/TestPyPI-style upload using PyPI *pending publishers* registered for
  both names (plus keeping `notebooklm-py`'s publisher) — all against the
  current repo name.
- README: replace the "keeps the name" note with the rename plan and its
  rationale; add `gemini`, `gemini-notebook` keywords (pyproject + manifest).

### Phase 1a — 0.9.0, additive (fully reversible)

1. New console scripts `gemini-notebook{,-mcp,-server}` alongside the old
   three. No startup hints — the old names are not deprecated. The `__main__`
   entry points derive their displayed `prog` from the invoked name
   (`mcp/__main__.py:181` and `server/__main__.py:113` currently hardcode the
   legacy names, so the new commands would advertise the old ones in
   `--help`).
2. `GeminiNotebookClient = NotebookLMClient` exported from `notebooklm`
   (static assignment: subclassing, `isinstance`, pickle, and mypy all keep
   working; no `__getattr__` indirection).
3. `__version__` resolution keyed to the distribution that actually supplied
   the imported files (`importlib.metadata.packages_distributions()`), with
   name lookups (`gemini-notebook-py`, then `notebooklm-py`) only as
   fallback. `src/notebooklm/__init__.py:40` is currently keyed to the old
   dist only and would report `0.0.0.dev0` under the renamed dist — but a
   plain name-ordered lookup is wrong too: with the Phase-0 placeholder plus
   a stale real `notebooklm-py` co-installed, canonical-first would report
   the placeholder's version for files the old dist supplied. Same fix in
   the skill version stamp (`_app/skill.py`).
4. Docs/README lead with "Gemini Notebook"; old name mentioned once per page.
5. Same-PR guardrail updates: `tests/_guardrails/test_public_surface.py`
   (new export), CLI contract baseline regen (ADR-0022 machinery) for the new
   scripts, `test_version_pyproject_sync.py`.

Stopping forever after 1a is a fine outcome: nothing has flipped.

### Phase 1b — 0.10.0, the identity flip (gated; see Guardrails for go/no-go)

Ordered steps; the repo rename is its own verified step, **not** bundled with
the first dual publish:

1. **Repo rename first, quiet window**: rename to
   `teng-lin/gemini-notebook-py`; immediately re-point the Trusted-Publishing
   configs for all three dists at the new repo; verify with a TestPyPI publish
   before any real release. Same-PR sweep of hardcoded repo strings:
   `project.urls`, fancy-pypi-readme substitutions, badges, and every
   `github.repository ==` guard (`publish-docker.yml`, `publish-mcpb.yml`,
   `verify-package.yml:150,158` — string compares don't get redirects),
   OCI source label, TestPyPI summary URL,
   `tests/_guardrails/test_pypi_readme_substitutions.py`.
2. **Dist rename**: `project.name = "gemini-notebook-py"`; update the
   self-referential `all` extra and re-lock `uv.lock`. Hatch config is
   untouched (the import package doesn't move — ever).
3. **Multi-dist publishing**: shim pyprojects live in `packaging/shims/
   {notebooklm-py,gemini-notebook}/`; `publish.yml` builds canonical + shims
   from one tag (versions asserted in lockstep), smoke-installs the shim with
   `--find-links dist/` (its pin isn't on PyPI yet), and uploads **canonical
   first**, shims after. Wheel globs/artifact names flip to
   `gemini_notebook_py-*` here (dist-keyed, not import-keyed — `publish.yml:81`,
   `testpypi-publish.yml`, artifact names).
4. **Shim spec**: `notebooklm-py` shim ships zero Python files, zero console
   scripts, and **mirrors every extra** (`[mcp]`, `[browser]`, … →
   `gemini-notebook-py[<extra>]==<version>`) — the shipped desktop extension
   hardcodes `notebooklm-py[mcp]` and must keep resolving. Pin `==`, released
   in lockstep with every canonical release (automated by step 3), for as
   long as dual publishing runs.
5. **Dual-install hazard**: `gemini-notebook-py` still ships the `notebooklm`
   package, so it collides file-for-file with a stale `notebooklm-py` ≤0.8
   install. Mitigation — partial by construction: an import-time check
   (`PackageNotFoundError`-guarded `importlib.metadata` lookup, PEP 440
   comparison via `packaging.version`, never a string compare) that warns
   loudly when a `notebooklm-py` dist < 0.9 is present alongside
   `gemini-notebook-py`, naming the fix
   (`pip uninstall notebooklm-py && pip install -U gemini-notebook-py`).
   The check only runs when the canonical files win the collision; a
   stale-*last* install overwrites `__init__.py` and is undetectable at
   runtime — that order is covered by docs/release notes only, and the ADR
   claims no complete runtime mitigation. Test matrix: canonical-only,
   stale-first, stale-last. Expect dependency-confusion-style scanner flags
   when a long-lived dist turns shim; pre-empt in the release notes.
6. **`verify-package.yml`**: replace the `--no-deps` old-name install steps
   (which break against an empty shim) with the dual-install smoke: shim +
   canonical in one venv, `import notebooklm` verified after installing each
   dist name (there is no second import path — the import package is
   permanent), all **six** scripts.
7. **`mcp install`**: `_app/mcp_install.py` writes
   `uvx --from "gemini-notebook-py[mcp]" gemini-notebook-mcp` under the
   **unchanged** server key `"notebooklm"` (no duplicate entries on
   re-install, no orphaning); `desktop-extension/run_server.py` likewise.
   Previously written configs keep working via the shim indefinitely.
8. **`deploy/`**: compose/env.example/Makefile/tailscale move to the new
   image name; all `NOTEBOOKLM_*` vars stay (permanent), so existing `.env`
   files keep working. Docker pushed to both repositories;
   `test_unit/test_deploy_compose_default.py` updated same PR.
9. Same-PR guardrail updates: `test_install_docs.py` + SKILL.md/AGENTS.md
   install commands (wheel-embedded agent instructions must not keep teaching
   `pip install notebooklm-py`), the skill recovery hint in
   `cli/skill_cmd.py` (`pip install --force-reinstall notebooklm-py` → new
   dist), `test_skill_packaging.py`, `test_mcp_desktop_extension.py`,
   mcp-install tests, CLI baselines.

### Phase 2 — bake and docs sweep

Dual publishing runs; docs (~1 900 refs), `examples/` prose, issue/PR
templates, `SECURITY.md`, `CLAUDE.md`/`CONTRIBUTING.md` rewritten to lead with
the new name. Watch signals: download split, shim bug reports, brand
stability. `import notebooklm` examples are **correct forever** — no code
sample churn.

### Phase 3 — wind-down (no removal cliff)

When go/no-go criteria say so (Guardrails), dual asset publishing (Docker,
skill zip) ends. `notebooklm-py` gets a final shim pinned
`gemini-notebook-py>=<current major>,<next major>` with a tombstone README —
and because the import package never goes away, that terminal shim keeps
**working** (install + `import notebooklm`) rather than silently breaking.
The open range (chosen over a frozen `==` so upgrades keep flowing to shim
users) carries an obligation: every canonical release inside a
shim-advertised range must preserve all legacy extra names and console
scripts — the shim-equivalence test below enforces this for as long as any
published shim's range is open, so a future release cannot drop `[mcp]` out
from under `notebooklm-py[mcp]` installs.
There is no Phase-4 hard removal: old console scripts, env vars, and config
home are permanent by decision, so no user ever hits a cliff.

### Guardrails

- **Gate for 1b (go/no-go, recorded here so it isn't vibes later):** 0.8.0
  final shipped; brand stable ≥3 months post-announcement (≥2026-10-16);
  Trusted Publishing verified for all three dists via TestPyPI; the 1a
  release out with no shim-related regressions.
- **Gate for Phase 3:** ≥75 % of combined downloads on the new dist for 2
  consecutive months, or 12 months after 1b, whichever first.
- **Shim equivalence test** (in canonical repo CI): during dual publishing,
  shim metadata mirrors every canonical extra and pins the exact canonical
  version; after wind-down, every canonical release inside any published
  shim's open range must retain all legacy extra names and console scripts.
- **Version-resolution test**: `notebooklm.__version__` correct when only
  `gemini-notebook-py` is installed (the failure mode is a silent
  `0.0.0.dev0`), and when the imported files come from one dist while a
  different version is installed under the other name.
- **Dual-install smoke** in `verify-package.yml` (Phase 1b step 6).
- **Stale-install check** (Phase 1b step 5) has a unit test for both the
  warn and the clean path.

## Consequences

- Permanent dist/import mismatch (`pip install gemini-notebook-py`,
  `import notebooklm`) — well-precedented, documented in the README's first
  screenful. In exchange: no 2 000-reference tree move, no logger-namespace
  break, no pickle/mypy alias machinery, no `git blame` damage, no
  `scripts/`+mypy+coverage+ruff config sweep, and the plumbing keeps working
  in every config file we've ever written to a user's machine.
- Old-name users are never broken: the shim (with extras) resolves
  indefinitely, and its terminal pin still yields a working install.
- Dual publishing is bounded by an explicit Phase-3 gate rather than open-ended
  maintainer toil; shim releases are automated in `publish.yml`, not manual.
- The safe-stop points are explicit: after 1a nothing has flipped; the point
  of no return is 1b step 1 (repo rename), which is why it is separately
  gated and verified before the first dual publish.
- If Google renames again after 1b, we are carrying one stale-brand dist name
  — the same position we are in today, with the same playbook, and the
  plumbing unaffected.
- Risks accepted: a PEP 541 / trademark challenge on the `gemini-*` names
  (fallback: keep `notebooklm-py` canonical — everything still works);
  scanner noise when the old dist turns shim; `pynotebooklm` adjacency
  confusion (README disambiguates).
- Reversed commitment: the README's July 2026 "keeps the name" note is
  retracted in Phase 0 with rationale, not silently edited.

## Alternatives considered

- **Full rename including the import package** (v1 of this ADR: alias package
  at 0.9, `git mv src/notebooklm src/gemini_notebook` at 1.0, removals at
  2.0). Rejected on review: the flip forces a semver-unrelated 1.0; `sys.modules`
  aliasing is invisible to mypy and breaks typed consumers; pickles of private
  `notebooklm._types.*` paths break across the flip; the logger namespace (a
  documented API) flips silently; env-var twinning requires write-side
  duplication including a credential scrub (`_auth/refresh.py:361`) where a
  missed twin is a security regression, a quiet-gate that self-recurses, and a
  Click `envvar=` bypass no grep gate can see; and the 2.0 removal cliff
  strands every config `mcp install` ever wrote. Each had a known fix; the sum
  was a large, risky program buying nothing the dist rename doesn't.
- **`GEMINI_NOTEBOOK_*` env-var twins** (subset of the above). Deferred
  indefinitely; any future attempt must solve, from v1's review: non-warning
  resolve path for the quiet gate and diagnostics, write-side dual-export plus
  twinned credential scrub, a custom `click.Option` for `envvar=`, an
  allowlist-based (not grep) CI gate, and the test-suite home-isolation
  fixture (`tests/conftest.py:31-75`).
- **Bare `gemini-notebook` as canonical.** Most official-looking name, highest
  challenge risk; kept as a redirect instead.
- **Hard cutover / publish only the new name.** Breaks every installed user,
  MCP config, and CI pipeline at once; contradicts ADR-0018.
- **Never rename; keywords only.** Cheapest, and the old page's search rank is
  real — but it cedes the project's identity as "NotebookLM" disappears from
  Google's own UI. Phase 0+1a alone approximate this alternative's cost while
  leaving the flip optional, which is partly why 1b is gated rather than
  scheduled.
- **`gnb` short CLI alias.** Dropped: PATH collision (srsRAN's `gnb`) and a
  brand-coupled acronym — exactly the churn constraint 1 warns about.
