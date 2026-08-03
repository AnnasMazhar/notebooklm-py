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

Option 3 composes with 1. Options 2 and 4 are the only ones that make the bad
path impossible rather than merely documented; of those, 4 is the only one that
also keeps old-name users receiving updates — which was ADR-0028's reason for
having a shim in the first place.

A fifth option exists and is what the standard procedure assumes: rename the
import package too. ADR-0028 already rejected it at length (v1 alternative:
~2000 in-tree references, the logger namespace as documented API, pickles of
`notebooklm._types.*`, env-var twinning with a credential scrub, and a removal
cliff stranding every config `mcp install` ever wrote). Nothing found here
weakens those objections.

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
