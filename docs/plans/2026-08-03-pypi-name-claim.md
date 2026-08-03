# Plan: claim the ADR-0028 PyPI names

**Status:** ready to execute · **Owner:** maintainer (needs PyPI credentials)
**Blocks:** ADR-0028 Phase 1 (the 0.9.0 rename release)

## Why now, not at 0.9.0

PyPI has **no reservation mechanism**. A name is held by the first successful
upload and by nothing else — not an account, not an issue, not a pending
Trusted Publisher. Until something is uploaded, all three names are takeable by
anyone.

Two facts make this urgent rather than routine:

1. **ADR-0028 is merged and public** (PR #2035, 2026-08-03). The rename plan,
   including the exact target names, is readable by anyone browsing the repo.
2. **Verified free as of 2026-08-03**: `gemini-notebook-py`, `gemini-notebook`,
   and bare `notebooklm` all return 404 from the PyPI JSON API. `notebooklm-py`
   is ours already.

The claim is a ~15-minute manual task. It does not depend on the repo rename,
on 0.9.0, or on anything else in the ADR.

## Constraints that shape the approach

| Constraint | Consequence |
|---|---|
| **Version numbers are burned permanently.** Deleting or yanking a release does not free that version for re-upload. | Placeholders sit at `0.0.1`, a version we will never want for a real release. Never upload a placeholder at `0.9.0`. |
| **PEP 541 targets empty squats.** An unused placeholder invites a takeover claim. | Each placeholder installs a *working* client (`notebooklm-py>=0.8,<0.9`), so the name is genuinely in use. |
| **"Gemini" is an actively defended mark.** | `gemini-notebook-py` is canonical; bare `gemini-notebook` is defensive only. Nothing may depend on holding the bare name. |
| **Names normalize (PEP 503).** | Claiming `gemini-notebook-py` also blocks `gemini_notebook_py` / `Gemini.Notebook.Py`. It does **not** block `gemininotebook`. |
| **A project-scoped API token cannot exist before its project does.** | The first upload needs an **account-scoped** token, narrowed immediately afterwards. |
| **`publish.yml` validates tag == pyproject version.** | It cannot perform these out-of-band uploads. They are one-off manual `twine` uploads. |

## Artifacts (built and verified)

Built from `<scratchpad>/placeholders/`, all six files `twine check` clean:

```
gemini_notebook_py-0.0.1{-py3-none-any.whl,.tar.gz}
gemini_notebook-0.0.1{-py3-none-any.whl,.tar.gz}
notebooklm-0.0.1{-py3-none-any.whl,.tar.gz}
```

Verified end-to-end: installing `gemini-notebook-py==0.0.1` pulls
`notebooklm-py 0.8.0`, `import notebooklm` works, and `notebooklm --version`
runs.

Each placeholder pins `notebooklm-py>=0.8,<0.9`. **The `<0.9` cap is
load-bearing**, not tidiness: from 0.9.0 the `notebooklm-py` dist becomes a shim
depending on `gemini-notebook-py==0.9.0`, so without the cap a resolver could
pair a placeholder with that shim and cycle.

---

## Step 1 — Pre-flight (2 min)

```bash
# Confirm still free immediately before uploading — this is a race.
for n in gemini-notebook-py gemini-notebook notebooklm; do
  printf '%-20s %s\n' "$n" "$(curl -s -o /dev/null -w '%{http_code}' https://pypi.org/pypi/$n/json)"
done   # expect 404 404 404
```

- Confirm **2FA is enabled** on the PyPI account (required for uploads).
- Create an **account-scoped** API token: pypi.org → Account settings → API
  tokens → "Entire account". Copy it; it is shown once.

## Step 2 — Claim, canonical first (5 min)

```bash
cd <scratchpad>/placeholders
python -m pip install --upgrade twine
export TWINE_USERNAME=__token__
export TWINE_PASSWORD=pypi-<account-scoped-token>

twine upload dist/gemini_notebook_py-0.0.1*   # ← the one that matters
twine upload dist/gemini_notebook-0.0.1*
twine upload dist/notebooklm-0.0.1*
```

**Order is deliberate.** `gemini-notebook-py` is the name the rename actually
depends on; the other two are defensive. If you are interrupted after one
upload, the important one is done.

*Optional 2-minute de-risk:* if unsure the twine setup works, upload
`gemini_notebook_py` to TestPyPI first (`--repository testpypi`). TestPyPI is a
separate namespace — it claims nothing on PyPI — but it proves the credential
path before burning the real `0.0.1`.

## Step 3 — Verify the claim (2 min)

```bash
for n in gemini-notebook-py gemini-notebook notebooklm; do
  curl -s https://pypi.org/pypi/$n/json | python3 -c "import sys,json;d=json.load(sys.stdin)['info'];print(d['name'],d['version'])"
done

# The claim is only defensible if it installs something real:
python -m venv /tmp/claimcheck && /tmp/claimcheck/bin/pip install -q gemini-notebook-py
/tmp/claimcheck/bin/python -c "import notebooklm; print(notebooklm.__version__)"
```

## Step 4 — Lock down the credential (5 min)

Do this in the same sitting; an account-scoped token left lying around is the
main new risk this plan introduces.

For **each** of the three new projects, plus the existing `notebooklm-py`:

1. Project → Manage → Publishing → **Add a trusted publisher** (GitHub):
   - Owner: `teng-lin`
   - Repository: `notebooklm-py` *(current name — the repo rename is deferred)*
   - Workflow: `publish.yml`
   - Environment: `release` *(must match `publish.yml`'s `environment:` key, or
     the OIDC claim is rejected)*
2. Delete the account-scoped token from Account settings.

Because the projects now exist, this is the ordinary "add a publisher" flow —
no pending-publisher handling needed.

> If the repo is renamed later, **every** trusted publisher must be re-pointed.
> GitHub's post-rename redirect does not apply to OIDC claims, and publishing
> breaks until re-registered. That is ADR-0028 Phase 1 step 1 and is tracked
> separately.

## Step 5 — Nothing to maintain until 0.9.0

The placeholders need no upkeep. They pin `notebooklm-py>=0.8,<0.9`, so ongoing
0.8.x patch releases are picked up automatically and no 0.9.x can be resolved
into them.

## Step 6 — At the 0.9.0 release

`publish.yml` already handles the canonical dist and both shims from one tag
(implemented on `v0.9.x`). Before tagging, run a **TestPyPI rehearsal** to prove
all three publishers work — that is the ADR's stated gate, and it is much
cheaper than discovering a bad publisher mid-release.

## Step 7 — After 0.9.0 ships (cleanup)

Two different actions — do not conflate them:

- **Yank** `gemini-notebook-py 0.0.1` and `gemini-notebook 0.0.1`. Real 0.9.0
  releases supersede them, and their stale `<0.9` pin should never be resolved
  again. Yanking keeps exact pins working while hiding them from resolution.
- **Refresh, do not yank, bare `notebooklm`.** It is deliberately *not* in the
  lockstep release matrix, so nothing supersedes it — left alone it would keep
  serving 0.8.x forever. Publish `notebooklm 0.0.2` depending on
  `gemini-notebook-py>=0.9`, and give it a one-time install smoke.

  This is the easiest step to forget: it is the only one with no CI enforcing
  it and no release artifact that fails without it.

---

## Contingencies

**A name is taken before we upload.** The ADR's documented fallback stands:
keep `notebooklm-py` canonical. Everything continues to work — the rename is
discoverability, not function. Losing bare `gemini-notebook` costs nothing.
Losing `gemini-notebook-py` means either picking a variant
(`gemini-notebook-client` is unregistered as of 2026-08-03) or abandoning the
dist rename; that is a maintainer call, not an automatic one.

**A PEP 541 or trademark challenge lands on a `gemini-*` name.** Surrender the
bare `gemini-notebook` without contest — it is defensive only. If the challenge
reaches `gemini-notebook-py`, fall back to `notebooklm-py` as canonical; the
shim machinery already works in both directions, so the blast radius is a
release, not the codebase.

**An upload partly fails.** Each name is independent. Re-run the failed one.
The only unrecoverable mistake is uploading at the wrong *version* — `0.0.1`
would then be burned, and the next placeholder would need `0.0.2`.

## Definition of done

- [ ] All three names return HTTP 200 from the PyPI JSON API and are owned by us
- [ ] `pip install gemini-notebook-py` in a clean venv yields a working `import notebooklm`
- [ ] Trusted Publishing configured on all four projects against `publish.yml` / `release`
- [ ] Account-scoped API token deleted
- [ ] Step 7 recorded somewhere durable — it fires months later, with no CI to catch it
