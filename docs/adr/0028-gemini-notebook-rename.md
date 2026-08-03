# ADR-0028: Renaming the package for Google's "Gemini Notebook" rebrand

## Status

Proposed (2026-08-03).

## Context

On 2026-07-16 Google renamed NotebookLM to **Gemini Notebook**
([announcement](https://blog.google/innovation-and-ai/products/gemini-notebook/notebooklm-gemini-notebook/)).
The product is unchanged for our purposes — same standalone app, same
`batchexecute` wire protocol — but every user-facing name this project carries
now points at a brand Google has retired. New users will search for
"gemini notebook python", not "notebooklm python".

The name is baked into far more than the PyPI listing. The public surface as
of 0.8.0rc1:

| Surface | Current name |
|---|---|
| PyPI distribution | `notebooklm-py` |
| Import package | `notebooklm` (`src/notebooklm/`, ~2 000 in-tree references) |
| Console scripts | `notebooklm`, `notebooklm-mcp`, `notebooklm-server` |
| Environment variables | ~40 distinct `NOTEBOOKLM_*` names (`_env.py`, `paths.py`, `mcp/`, `server/`, …) |
| Config home | `~/.notebooklm` (`NOTEBOOKLM_HOME` / `NOTEBOOKLM_PROFILE`, `paths.py`) |
| Primary class | `NotebookLMClient` |
| GitHub repo | `teng-lin/notebooklm-py` |
| Desktop extension | `notebooklm-mcp.mcpb`, manifest `name: "notebooklm-mcp"` |
| Docker image | `<namespace>/notebooklm-mcp` (`publish-docker.yml`) |
| Skill archive | `notebooklm-skill.zip` (`publish-mcpb.yml`) |
| CI artifacts / globs | `notebooklm-py-dist`, `dist/notebooklm_py-*.whl`, coverage/mypy/ruff config keyed on `notebooklm` |

A hard cutover would strand every existing install, script, cron job, MCP
config, and CI pipeline at once. Never renaming leaves the project invisible
under the name users now search for. PyPI name availability was checked on
2026-08-03: `gemini-notebook`, `gemini-notebook-py`, and
`gemini-notebook-client` are all unregistered; PyPI has no reservation
mechanism, so squatting is a live risk while we wait.

Two standing constraints shape the plan:

1. **Google renames products often.** The wire protocol still lives at
   `notebooklm.google.com` and the internal RPC layer is keyed on obfuscated
   method IDs, not names. Renaming the *internal* code buys nothing and risks
   churn if the brand shifts again.
2. **ADR-0018 already defines the deprecation machinery** —
   `warn_deprecated(message, *, removal, stacklevel)` in `_deprecation.py`,
   silenced by the quiet-deprecations gate. Every compatibility fallback in
   this plan routes through it; no ad-hoc warnings.

## Decision

Adopt **`gemini-notebook-py`** as the new distribution name and
**`gemini_notebook`** as the new import package, migrated in three phases so
that no release breaks an existing user without a deprecation window, and the
old names keep working (with warnings) until 2.0.

The `-py` suffix is kept deliberately: it signals continuity with
`notebooklm-py`, matches the existing convention, and — together with the
"Unofficial" description — reduces the risk that a bare `gemini-*` name reads
as an official Google package and draws a PyPI trademark complaint. We also
register the bare `gemini-notebook` name as a redirect metapackage so it
cannot be squatted.

Name mapping (old → new):

| Old | New | Old kept until |
|---|---|---|
| `notebooklm-py` (PyPI) | `gemini-notebook-py` | 2.0 (shim releases stop) |
| `import notebooklm` | `import gemini_notebook` | 2.0 |
| `notebooklm` CLI | `gemini-notebook` CLI (short alias `gnb`) | 2.0 |
| `notebooklm-mcp` / `notebooklm-server` | `gemini-notebook-mcp` / `gemini-notebook-server` | 2.0 |
| `NOTEBOOKLM_<X>` env vars | `GEMINI_NOTEBOOK_<X>` (1:1) | 2.0 |
| `~/.notebooklm` | `~/.gemini-notebook` | indefinite read fallback |
| `NotebookLMClient` | `GeminiNotebookClient` | 2.0 (alias) |
| repo `teng-lin/notebooklm-py` | `teng-lin/gemini-notebook-py` | GitHub auto-redirects |

What is explicitly **not** renamed: the `rpc/` layer's wire-level naming, the
default base URL (`notebooklm.google.com` — Google's endpoint, not our brand),
VCR cassette contents, and historical CHANGELOG/ADR text. Internal private
modules are renamed only when the `src/` tree physically moves (Phase 3), as
a mechanical consequence, not a goal.

## The phases

### Phase 0 — immediately, independent of any release

- **Register the PyPI names.** Upload minimal placeholder sdists (version
  `0.0.1`, README pointing at this repo) for `gemini-notebook-py` and
  `gemini-notebook`. This is the only way PyPI lets you hold a name. Configure
  Trusted Publishing for `gemini-notebook-py` mirroring the existing
  `publish.yml` setup.
- Add a "NotebookLM is now Gemini Notebook" note to `README.md` and
  `docs/index`-level docs; add `gemini`, `gemini-notebook` to `keywords` in
  `pyproject.toml` and `desktop-extension/manifest.json`. No behavior changes.

### Phase 1 — 0.9.0, the compatibility release (additive only)

Everything new is added; nothing old changes behavior. A user who upgrades
and touches nothing sees at most a startup hint.

1. **Env vars.** Introduce a single resolver (extend `_env.py`) used by every
   env read: `GEMINI_NOTEBOOK_<X>` wins; `NOTEBOOKLM_<X>` still honored, with
   a once-per-process `warn_deprecated(..., removal="2.0")` when only the old
   name is set. All ~40 variables go through the resolver — no per-call-site
   fallback logic (grep gate in CI, see Guardrails).
2. **Console scripts.** Add `gemini-notebook`, `gnb`, `gemini-notebook-mcp`,
   `gemini-notebook-server` entry points targeting the same mains. The old
   three stay, and print a one-line deprecation hint on startup (stderr,
   suppressed by the quiet gate).
3. **Import alias.** Add a `gemini_notebook` package that re-exports
   `notebooklm`'s public surface (`from notebooklm import *` plus explicit
   `__all__`, `__version__`, submodule aliasing via `sys.modules` so
   `gemini_notebook.types` etc. resolve). `notebooklm` remains the real code —
   zero churn to the 2 000 internal references in this phase.
4. **Class alias.** `GeminiNotebookClient = NotebookLMClient` exported from
   both packages. No warning yet in either direction.
5. **Config home.** `paths.py` precedence becomes:
   `GEMINI_NOTEBOOK_HOME` > `NOTEBOOKLM_HOME` (deprecated) >
   `~/.gemini-notebook` if it exists > `~/.notebooklm` if it exists >
   `~/.gemini-notebook` (fresh default). No silent data copy — profile dirs
   hold live auth state under `filelock`; a `notebooklm doctor` /
   `gemini-notebook doctor` check offers an explicit one-shot migration
   (rename the directory, leave a `~/.notebooklm` symlink for old scripts).
6. **Dual publish begins.** `gemini-notebook-py 0.9.0` is the canonical dist
   (full code). `notebooklm-py 0.9.0` becomes a **shim**: an empty
   distribution whose only install requirement is
   `gemini-notebook-py==0.9.0`, with a README explaining the rename. Shims
   contain no Python files, so installing both never clobbers site-packages.
   The bare `gemini-notebook` metapackage likewise depends on
   `gemini-notebook-py`.
7. **Repo rename.** Rename `teng-lin/notebooklm-py` →
   `teng-lin/gemini-notebook-py` at 0.9.0 release time. GitHub redirects all
   old URLs, remotes, and clones. Same-PR sweep: `project.urls`,
   `hatch-fancy-pypi-readme` substitution URLs in `pyproject.toml`, badge
   URLs, `publish-docker.yml`/`publish-mcpb.yml` `github.repository` guards,
   `desktop-extension/manifest.json` links.
8. **MCP / desktop extension.** Update `display_name` to
   "Gemini Notebook (gemini-notebook-py)" and descriptions. Keep the manifest
   `name: "notebooklm-mcp"` **unchanged** for now — the name is the extension's
   identity in Claude Desktop, and changing it makes installed users' copies
   orphaned rather than upgraded. Ship the identity change at 2.0 only,
   release-noted. The launcher `run_server.py` switches to
   `uvx --from "gemini-notebook-py[mcp]" gemini-notebook-mcp`.
9. **Docker / release assets.** Push images to both `notebooklm-mcp` and
   `gemini-notebook-mcp` repositories (second `DOCKERHUB_IMAGE`-style
   variable); release both `notebooklm-skill.zip` and
   `gemini-notebook-skill.zip` names.

### Phase 2 — 0.9.x bake period

At least one minor-release cycle (target: ~2 months) with both names live.
Watch: PyPI download split between the two dists, bug reports against the
alias package, and whether Google's brand holds. Docs are rewritten to lead
with the new names during this window (mechanical `docs/` sweep, ~1 900
references, old names mentioned once per page as "(formerly NotebookLM)").

### Phase 3 — 1.0.0, the physical flip

1. **Move the tree**: `git mv src/notebooklm src/gemini_notebook`, mechanical
   rename of internal imports (the ~2 000 references). One dedicated PR, no
   functional changes mixed in, so `git blame` damage is a single commit that
   can be listed in `.git-blame-ignore-revs`.
2. **Invert the shim**: `notebooklm` becomes the thin re-export package
   (mirror of the Phase-1 `gemini_notebook` alias, now warning via
   `warn_deprecated(..., removal="2.0")` on first import).
3. **Rename-sensitive config** follows in the same PR: `tool.hatch.build`
   `packages`/`force-include`, `tool.coverage.run` source,
   `per_file_coverage_floors` keys, `tool.mypy` files + per-module overrides,
   `ruff` `known-first-party`, `publish.yml` wheel glob
   (`gemini_notebook_py-*.whl`) and artifact names, `verify-package` /
   public-surface gates, `tests/` imports.
4. `NotebookLMClient` remains exported as a deprecated alias of
   `GeminiNotebookClient` (real subclass-free assignment, warning on
   attribute-free construction is *not* attempted — the alias warns via
   module `__getattr__` on first access).

### Phase 4 — 2.0.0, removal

Old console scripts, `NOTEBOOKLM_*` env fallbacks (except a hard error message
naming the replacement), the `notebooklm` import shim, the `NotebookLMClient`
alias, and `notebooklm-py` shim releases all end. `~/.notebooklm` read
fallback stays (cheap, harmless). Final `notebooklm-py` upload is a shim
pinned `gemini-notebook-py>=2,<3` with a tombstone README.

## Guardrails

- **One resolver rule** (mirrors ADR-0018's "one module, one switch"): CI
  greps that no code outside `_env.py` reads `NOTEBOOKLM_` from `os.environ`
  directly, so the fallback+warning behavior cannot fork.
- **Shim equivalence test**: a unit test asserts
  `dir(gemini_notebook)`-public == `dir(notebooklm)`-public and that
  `gemini_notebook.__version__ is notebooklm.__version__`, so the alias can
  never drift from the real package.
- **Dual-install smoke** in `verify-package.yml`: install `notebooklm-py`
  (shim) and `gemini-notebook-py` into one venv; both imports and all six
  console scripts must work.
- **Quiet gate parity**: the quiet-deprecations env var itself gains a
  `GEMINI_NOTEBOOK_QUIET_DEPRECATIONS` twin via the same resolver — otherwise
  silencing rename warnings would require the deprecated prefix.

## Consequences

- Existing users are never broken before 2.0; every old entry point keeps
  working with a discoverable pointer to its replacement.
- We carry dual publishing and alias surfaces for roughly two release phases —
  accepted cost; the shim dists are near-empty and the alias package is ~50
  lines.
- The old PyPI page, GitHub stars/issues/links, and search ranking are
  preserved (shim README + GitHub redirect) instead of reset.
- If Google renames again before Phase 3, we stop at Phase 1/2: the additive
  aliases are cheap to keep or retarget, and the expensive physical flip has
  not happened. This is the main reason the flip is deferred to 1.0 rather
  than done in 0.9.
- Risk: a user with scripts on another machine sets only `NOTEBOOKLM_HOME`
  pointing at shared state; the precedence order above keeps that working
  indefinitely until 2.0, and `doctor` reports which home/profile source won
  (extends the existing `home_source` diagnostic in `paths.py`).

## Alternatives considered

- **Bare `gemini-notebook` as the canonical dist name.** Cleaner, but loses
  the visual continuity with `notebooklm-py` and looks more official-Google
  than an unofficial client should. Registered as a redirect instead.
- **Hard cutover at 0.9** (rename everything at once, publish only the new
  name). Smallest total diff, but breaks every installed user, MCP config,
  and CI pipeline simultaneously, and contradicts ADR-0018's windowed
  deprecation policy.
- **Never rename; add keywords only.** Preserves all names but cedes the
  searchable identity of the project and confuses new users indefinitely as
  "NotebookLM" disappears from Google's own UI.
- **Physical flip in 0.9.** Rejected for the reason in Consequences: it
  front-loads the most expensive, least reversible step while the new brand
  is weeks old.
