# RELEASE BLOCKER: `pip install -U notebooklm-py` breaks the install at 0.9.0

**Found:** 2026-08-03, while testing whether to publish a `notebooklm-py`
pre-release. **Affects:** the ADR-0028 0.9.0 final release, not just the alpha.
**Installer-specific:** reproduced with **pip 26.0.1**; **uv 0.11.7 is not
affected.**

## What happens

Starting from an ordinary `notebooklm-py 0.8.0` install, upgrading through the
shim leaves a **gutted package**:

```console
$ pip install notebooklm-py==0.8.0        # the world today
$ pip install -U notebooklm-py            # what everyone will run at 0.9.0
$ python -c "import notebooklm; print(notebooklm.__version__)"
AttributeError: module 'notebooklm' has no attribute '__version__'
$ notebooklm --version
bash: notebooklm: command not found
```

`pip list` looks *correct* — both `notebooklm-py 0.9.0a1` and
`gemini-notebook-py 0.9.0a1` are present at the right versions. Only the files
are missing.

## Mechanism

1. pip resolves the upgrade to `notebooklm-py` (shim) + `gemini-notebook-py`
   (canonical, a new dependency).
2. pip **installs `gemini-notebook-py` first** — writing 633 `notebooklm/`
   files and a RECORD listing them.
3. pip **then uninstalls `notebooklm-py 0.8.0`**, deleting every path in
   *0.8.0's* RECORD — which names 631 of the files just written.
4. The only survivors are files that did not exist in 0.8.0 (`_dist_version.py`,
   `_stale_install.py`).

Because `notebooklm/__init__.py` is deleted, `notebooklm` silently degrades to
an **implicit namespace package**: `import notebooklm` still *succeeds*, so the
failure surfaces later as confusing `AttributeError`s rather than an
`ImportError` at the point of breakage.

This is ADR-0028 constraint 3 ("uninstalling either corrupts the survivor"), but
the ADR treats it as a *co-installation* hazard requiring a warning. It does not
anticipate that the **ordinary upgrade command triggers it**.

## Why the existing collision warning does not catch it

`_stale_install` asks whether the *legacy* dist still ships `notebooklm/` files.
Here the legacy dist is the shim, which correctly ships none — so the check
returns `None` (healthy). The damage is to the **canonical** dist's files, which
that check never inspects. And once `__init__.py` is gone, our code cannot run
at all, so no in-package check can fire.

## Measured behaviour

| Scenario | Installer | Result |
|---|---|---|
| `pip install -U notebooklm-py` from 0.8.0 | pip 26.0.1 | **BROKEN** |
| `pip install --force-reinstall -U notebooklm-py` from 0.8.0 | pip 26.0.1 | **BROKEN** — the obvious mitigation does *not* help |
| `pip uninstall notebooklm-py` → `pip install gemini-notebook-py` | pip 26.0.1 | clean |
| `pip install --force-reinstall gemini-notebook-py` *from the broken state* | pip 26.0.1 | **repairs fully** |
| `uv pip install -U notebooklm-py` from 0.8.0 | uv 0.11.7 | clean — unaffected |

Two consequences worth stating plainly:

- **`--force-reinstall` is a cure, not a preventative.** It repairs a gutted
  environment but does not stop the upgrade from gutting it. The README and
  ADR currently present it as the remedy without distinguishing the two.
- **uv users are fine.** The blast radius is pip users upgrading via the old
  name — still the majority.

## Why the standard rename procedure does not cover us

The usual PyPI rename (publish under the new name; turn the old project into a
wrapper depending on it) has a step we deliberately skip: *"update your import
package statements and folder names"*. That step is what makes the wrapper safe
— the new project ships **different files**, so uninstalling the old dist
removes only its own.

`sklearn` → `scikit-learn` is the standard example working correctly: the
`sklearn` wrapper never shipped `sklearn/` files itself, so there was nothing to
collide. Our old dist *did* ship `notebooklm/`, and ADR-0028 keeps that import
name permanently — so both dists claim the same paths. **The file overlap, not
the wrapper, is the defect.** Any option below is really a choice about how to
remove the overlap.

## Options (maintainer decision, not yet taken)

1. **Ship the shim anyway, document the dance.** Release notes and README tell
   pip users to `pip uninstall notebooklm-py && pip install gemini-notebook-py`.
   Weak: the failure is silent, and the people who need the instruction are
   exactly the ones running `-U` without reading release notes.
2. **Do not publish a `notebooklm-py` shim at all.** Freeze the dist at 0.8.x.
   `pip install -U notebooklm-py` then reports "already satisfied" — users go
   stale, but are never broken, and the failure mode is visible (no new
   features) rather than silent corruption. Trades ADR-0028's
   "old-name users are never broken" for "old-name users never move
   automatically". Contradicts the ADR's stated shim mechanism and needs an ADR
   amendment.
3. **Publish the shim only after a 0.8.x release that warns loudly** on import
   that the next upgrade requires an uninstall-first. Gets a message to users
   *before* they break, using the one release channel they still receive.

4. **Invert the wrapper.** Keep `notebooklm-py` as the real, file-shipping
   distribution forever, and make **`gemini-notebook-py` the wrapper** that
   depends on it. Only one dist ever ships `notebooklm/`, so the overlapping
   RECORD that causes this bug cannot occur — safe *by construction*, not by
   documentation. Verified against the live 0.0.1 placeholders, which already
   have exactly this shape: installing `gemini-notebook-py` then running
   `pip install -U notebooklm-py` leaves a healthy environment.

   Keeps everything ADR-0028 actually protects: `import notebooklm` permanent,
   every existing install and config untouched, both names installable, the new
   name present on PyPI with its own README and keywords for discovery.

   What it gives up is the ADR's stated goal that the *new* name be canonical:
   downloads, release history, and PyPI's own ranking stay on `notebooklm-py`,
   and `gemini-notebook-py`'s project page is a pointer rather than the
   package. Discoverability is largely preserved (the name exists and installs
   the right thing); *identity* is not transferred.

   Also needs an ADR amendment, and reverses the dist/import mismatch direction
   (`pip install notebooklm-py`, `import notebooklm` — no mismatch at all,
   which is arguably simpler than the `bs4` precedent the ADR invokes).

6. **Hard-fail shim** (from independent review). Publish `notebooklm-py 0.9.0`
   shipping **only** a `notebooklm/__init__.py` that raises `ImportError` with
   migration instructions, and **no dependency on the canonical dist**. Because
   it is a plain single-package upgrade, pip uninstalls 0.8.0 and then writes
   this file, so it survives. **Verified:** `pip install -U notebooklm-py` from
   0.8.0 yields a clean, actionable `ImportError` naming the fix, instead of a
   namespace package with missing attributes.

   This does not keep old-name users working — it deliberately breaks them,
   *loudly and at a moment they control* (immediately after they ran an
   upgrade), rather than silently. Compared with option 2 it trades "silently
   stale until Google changes an RPC id" for "clearly broken right now, with
   the fix in the message". It is the honest way to get identity transfer;
   option 1 is not.

   **Constraint (reasoned, not measured):** the hard-fail shim must NOT depend
   on `gemini-notebook-py`. With a dependency, pip installs the canonical dist
   first and writes the shim's `__init__.py` last — overwriting the real one
   and breaking even a correctly resolved install. An attempt to test this
   directly hit `ResolutionImpossible` because the published placeholders'
   `<0.9` cap blocks the circular pairing, which is that cap working as
   intended.

Option 3 composes with 1. Options 2, 4 and 6 are the only ones that avoid
*silent* corruption; of those, **4 is the only one where nothing breaks at
all** — and it is the only one that keeps old-name users receiving updates,
which was ADR-0028's reason for having a shim in the first place.

### Confirmed: there is no packaging-level escape

**Two independent reviews** agree no mechanism exists to keep
`gemini-notebook-py` canonical *and* avoid the overlap:

- pip implements no `Conflicts` / `Replaces` semantics. `Provides-Dist` /
  `Obsoletes-Dist` exist as metadata but are **not** pip replacement semantics.
- PEP 610 concerns source *provenance*, not file ownership.
- Neither `dependency_links` nor requirement ordering establishes *uninstall*
  ordering.
- RECORD manipulation would be non-conforming — and still could not alter the
  RECORD already installed on a user's machine.
- Wheels are static archives; there are no install-time hooks to restore files
  or amend an uninstall.
- PEP 420 namespace packages help only after redesigning the package into
  genuinely disjoint portions.

A re-export shim (new dist ships `gemini_notebook/` plus a thin
`notebooklm/`) is viable **only** if the legacy distribution becomes the sole
owner of `notebooklm/` — which is option 4 wearing a different hat, plus an
import-compatibility migration. It is not a drop-in fix.

So the option space above really is closed.

### Independent verdicts

Three independent analyses — this one and two external reviews — converged on
**option 4**. Neither reviewer proposed keeping the ADR's current design.

One added the explicit rider: *if* the new name must own the implementation,
then do **not** ship the current shim at all — require an explicit
uninstall-first migration, with import-time warnings as supplemental mitigation
only, never as the mechanism.

### Test-coverage gap this exposes

The 0.9.0 acceptance matrix in ADR-0028 — and the `verify-package.yml`
implementation of it on this branch — covers **co-installation** (install the
stale dist, then the canonical one) but **not the upgrade transaction** that
actually breaks: `pip install -U <old-name>` from the previous release. That is
why the defect survived the acceptance matrix.

Add as a required row, independent of which option is chosen:

> **Upgrade-from-previous-release.** Starting from the latest published release
> installed under **each** published name, run the ordinary upgrade command for
> that name and assert a working `import notebooklm` plus a working CLI.

And a warning about *how* to assert it: **`pip list` and `pip check` cannot
detect this failure.** Both report a healthy environment while the package
files are missing — `pip list` showed correct names and versions throughout.
Assertions must exercise real imports and console scripts, and ideally inspect
wheel payloads and installed RECORDs directly.

### Still unchecked

- **Other installers/lockfile tools:** Poetry, PDM, Pipenv, `pip-tools`. uv is
  clean and pip is broken; the rest are unknown and matter for CI users.
- **System packagers (conda, APT/DNF/Arch):** these enforce file ownership
  strictly and would likely refuse the co-install outright rather than corrupt
  it — a different, louder failure worth knowing about if anyone repackages us.
- **The bare `notebooklm` dist:** harmless today (it ships no files), but once
  its floor is refreshed to depend on `gemini-notebook-py`, it becomes a third
  participant in the same transaction. Re-check the upgrade matrix then.

A fifth option exists and is what the standard procedure assumes: rename the
import package too. ADR-0028 already rejected it at length (v1 alternative:
~2000 in-tree references, the logger namespace as documented API, pickles of
`notebooklm._types.*`, env-var twinning with a credential scrub, and a removal
cliff stranding every config `mcp install` ever wrote). Nothing found here
weakens those objections.

## Analysis

### The failure is reproducible — but the ordering is not a guarantee

pip installs `gemini-notebook-py` before upgrading `notebooklm-py` under both
of its apparent ordering heuristics: dependency order (the canonical dist is a
dependency of the shim) and alphabetical order (`g` < `n`). They agree, and it
reproduced on every run.

**Correction (from review):** that ordering is *observed behaviour of the tested
pip* (26.0.1), **not a portable guarantee**. pip does not specify install/
uninstall ordering across distributions, and it may differ by pip version or
invocation. This does not make the situation better — it makes it *less
predictable*: we cannot promise the failure always happens, and equally cannot
promise any version avoids it. The underlying defect is unconditional: pip
performs no cross-distribution file-ownership tracking, so uninstall consults
only the removed distribution's own RECORD and will happily delete files another
distribution just wrote.

The consequence for ADR-0028 is blunt: its claim that **"old-name users are
never broken" is false.**

### The shim is self-defeating

The shim exists for exactly one reason: to keep old-name users receiving
updates. Under pip, **delivering that update is precisely what corrupts them.**
The mechanism and the purpose are the same act. That makes option 1 ("ship it
and document the workaround") not merely risky but incoherent — the users who
would need the workaround are, by construction, the ones who ran the command
without reading release notes.

### The option space is closed

The corruption needs three things at once:

1. two distributions that both ship `notebooklm/` files, **and**
2. an upgrade transaction that installs one while uninstalling the other, **and**
3. pip's install-before-uninstall ordering.

We cannot change (3). So every viable option removes (1) or (2), and there are
exactly three ways to do that — which is why the option list is closed rather
than merely long:

| Remove | How | Option |
|---|---|---|
| (1), by the *new* dist not shipping the files | old dist stays the real one; new name is a wrapper | **4** |
| (1), by the *old* dist not shipping the files | rename the import package so paths differ | 5 |
| (2), by never issuing an upgrade | freeze `notebooklm-py` at 0.8.x | 2 |

Option 3 is a mitigation on top of 1, not a fourth way out.

### The ADR mis-classifies the distribution name

ADR-0028 sorts surfaces into *discoverability* (PyPI dist name, repo, docs) and
*operational plumbing* (import package, env vars, config home, MCP identities),
and argues plumbing must never be renamed because "the plumbing writes its name
into places we cannot patch after the fact."

The distribution name is in **both** buckets. It is discoverability for someone
choosing a package — and plumbing for everyone who already has one, because it
is written into `requirements.txt`, `pyproject.toml`, `poetry.lock`,
`uv.lock`, Dockerfiles, and CI configs across every downstream user. Those are
exactly "places we cannot patch after the fact." Constraint 2 of the ADR applies
to the dist name by the ADR's own reasoning; it was simply not applied there.

Option 4 splits the two roles cleanly: the plumbing keeps its stable name, and
the brand gets a wrapper that is cheap to publish and cheap to abandon.

### Option 4 is the ADR's own principle, applied consistently

ADR-0028 closes with: *"the import name that never chases brands is the only one
that cannot go stale twice."* That argument does not depend on anything specific
to import names — it is an argument about **which layer should absorb rebrands**.
Option 4 answers "the wrapper layer", and gets the same robustness for the dist
name: if Google renames again, publish another thin wrapper and leave the real
package alone.

### Option 4 deletes machinery rather than adding it

Most of what this branch built exists to *manage* the overlap:

- `_dist_version.py` — content-hash ownership resolution, needed only because
  two dists' RECORDs can claim the same `__init__.py`;
- `_stale_install.py` — the collision detector (which, note, does not even fire
  for this bug);
- the lockstep three-dist release matrix, the shim generator, the extras-parity
  gate, the dual-install acceptance rows in `verify-package.yml`.

Under option 4 none of that is required: one dist ships the files, so ownership
is unambiguous and collisions are impossible. Complexity that exists solely to
contain a self-inflicted hazard is a strong signal the hazard should not be
taken on.

### Cost, honestly stated

Option 4 gives up what ADR-0028 most wanted: **transfer of canonical identity.**
Downloads, release history, and PyPI's own ranking stay on `notebooklm-py`, and
`gemini-notebook-py`'s project page becomes a pointer. New users still find and
install the new name; the project's *identity* on PyPI remains the retired
brand — permanently, the same bargain the ADR already accepted for the import
name.

Option 2's cost is worse than it looks **for this project specifically**: the
client breaks whenever Google changes undocumented RPC method IDs (the repo's
own stated #1 breakage class). A user frozen on 0.8.x does not merely miss
features — their install stops working against the live service, with no update
path they have any reason to look for.

### Recommendation

**Option 4**, unless transferring canonical identity is worth shipping a known
silent-corruption path to pip users. It is the only option that keeps
old-name users receiving updates (the shim's whole purpose) while making the
corruption impossible by construction, and it removes machinery instead of
adding it.

If identity transfer is judged essential, the honest choice is **option 5** —
the full standard rename, import package included — not option 1. Option 1 buys
identity with a defect; option 5 buys it with work.

### Not urgent any more

Claiming the three PyPI names removed the time pressure that motivated a fast
alpha. Nothing is at risk while this is decided. The `0.9.0a1` tag as currently
prepared assumes ADR-0028's design (real code under `gemini-notebook-py`), so
**it should not be pushed until this is settled** — under option 4 the canonical
dist would be a wrapper instead.

## Reproducing

```bash
V=$(mktemp -d)/v && python3 -m venv "$V"
"$V/bin/pip" install -q "notebooklm-py==0.8.0"
"$V/bin/pip" install -q --pre -U --find-links <dist-dir> notebooklm-py
"$V/bin/python" -c "import notebooklm; print(notebooklm.__version__)"
```

`<dist-dir>` needs `gemini_notebook_py-*.whl` and `notebooklm_py-*.whl` built
from this branch (`uv build --wheel -o D .` and
`uv build --wheel -o D packaging/shims/notebooklm-py`).
