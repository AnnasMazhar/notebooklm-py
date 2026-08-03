#!/usr/bin/env bash
# Claim the ADR-0028 PyPI names. ONE-OFF: run once, then never again.
#
# PyPI has no reservation mechanism — a name is held by the first successful
# upload and by nothing else. This builds the placeholder distributions, checks
# each name is still free, uploads them CANONICAL FIRST, and verifies the claim
# landed and installs something that actually works.
#
# Usage:
#   export TWINE_PASSWORD=pypi-<account-scoped-token>   # see below
#   packaging/placeholders/claim.sh                     # upload for real
#   packaging/placeholders/claim.sh --dry-run           # build + check only
#   packaging/placeholders/claim.sh --testpypi          # rehearse credentials
#
# A project-scoped token cannot exist before its project does, so the FIRST
# upload needs an ACCOUNT-scoped token. Delete it immediately afterwards and
# switch each new project to Trusted Publishing — see
# docs/plans/2026-08-03-pypi-name-claim.md step 4.
#
# Names are independent: if one is already taken, the others are still claimed.
# The only unrecoverable mistake is uploading at the wrong VERSION, because
# PyPI burns version numbers permanently even after a delete or yank.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../.." && pwd)"
DIST="${HERE}/dist"

# Canonical FIRST — it is the name the rename actually depends on; the other
# two are defensive. If this is interrupted, the important one is already done.
NAMES=(gemini-notebook-py gemini-notebook notebooklm)

DRY_RUN=0
REPO_ARGS=()
INDEX_HOST="pypi.org"
for arg in "$@"; do
  case "$arg" in
    --dry-run)  DRY_RUN=1 ;;
    --testpypi) REPO_ARGS=(--repository testpypi); INDEX_HOST="test.pypi.org" ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

# --- preflight -------------------------------------------------------------

say "Checking availability on ${INDEX_HOST}"
AVAILABLE=()
for name in "${NAMES[@]}"; do
  code="$(curl -s -o /dev/null -w '%{http_code}' "https://${INDEX_HOST}/pypi/${name}/json")"
  if [ "$code" = "404" ]; then
    printf '  %-22s FREE\n' "$name"
    AVAILABLE+=("$name")
  else
    printf '  %-22s ALREADY REGISTERED (HTTP %s) — skipping\n' "$name" "$code"
  fi
done

if [ "${#AVAILABLE[@]}" -eq 0 ]; then
  echo
  echo "Nothing to claim. If these are already ours this script has been run;" >&2
  echo "if not, see the contingency section of the plan." >&2
  exit 1
fi

# --- build -----------------------------------------------------------------

say "Building placeholders"
rm -rf "$DIST"

# Prefer `uv build` when available; it needs nothing pre-installed. Fall back
# to `python -m build`, checking for it up front rather than failing halfway
# through with an import error.
if command -v uv >/dev/null 2>&1; then
  build_one() { uv build --wheel --sdist -o "$DIST" "${HERE}/$1" >/dev/null; }
elif python -c "import build" 2>/dev/null; then
  # Build from INSIDE the package directory. Running this from the repo root
  # fails: the root carries a `build/` directory (hatch_build.py's output), and
  # a local directory of that name shadows the `build` module for `python -m`.
  build_one() { ( cd "${HERE}/$1" && python -m build --outdir "$DIST" . >/dev/null ); }
else
  echo "Need either 'uv' or the 'build' module. Install one:" >&2
  echo "  python -m pip install build twine" >&2
  exit 2
fi

for name in "${AVAILABLE[@]}"; do
  build_one "$name"
  echo "  built ${name}"
done

say "twine check"
if ! python -c "import twine" 2>/dev/null; then
  echo "  twine not importable — install it: python -m pip install twine" >&2
  exit 2
fi
python -m twine check "$DIST"/*

# Guard the one unrecoverable mistake: a version we might later want for real.
say "Verifying placeholder versions"
for f in "$DIST"/*.tar.gz; do
  base="$(basename "$f")"
  case "$base" in
    *-0.0.1.tar.gz) echo "  OK  $base" ;;
    *) echo "  REFUSING: $base is not version 0.0.1." >&2
       echo "  PyPI burns version numbers permanently; 0.9.0 is the real release." >&2
       exit 1 ;;
  esac
done

if [ "$DRY_RUN" -eq 1 ]; then
  say "Dry run — nothing uploaded"
  echo "Artifacts in ${DIST}"
  exit 0
fi

# --- upload ----------------------------------------------------------------

if [ -z "${TWINE_PASSWORD:-}" ]; then
  echo "TWINE_PASSWORD is not set — export an account-scoped PyPI token first." >&2
  exit 2
fi
export TWINE_USERNAME="${TWINE_USERNAME:-__token__}"

for name in "${AVAILABLE[@]}"; do
  underscored="${name//-/_}"
  say "Uploading ${name}"
  python -m twine upload "${REPO_ARGS[@]}" "$DIST/${underscored}-0.0.1"*
done

# --- verify ----------------------------------------------------------------

say "Verifying the claim landed"
# Check the SIMPLE index, not the JSON API. The JSON API can keep serving 404
# for minutes after a successful upload, which reads as "the claim failed" when
# the package is in fact already installable. The simple index is also the path
# pip actually uses, so it is the honest thing to assert.
for name in "${AVAILABLE[@]}"; do
  sleep 2  # brief propagation allowance
  code="$(curl -s -o /dev/null -w '%{http_code}' -H 'Cache-Control: no-cache' \
          "https://${INDEX_HOST}/simple/${name}/")"
  if [ "$code" = "200" ]; then
    printf '  %-22s CLAIMED (simple index HTTP 200)\n' "$name"
  else
    printf '  %-22s simple index HTTP %s — re-check in a minute\n' "$name" "$code"
  fi
done

if [ "$INDEX_HOST" = "pypi.org" ]; then
  say "Verifying the canonical name installs a WORKING client"
  # A placeholder that installs nothing is exactly what PEP 541 targets, so
  # this assertion is part of the claim being defensible — not a nicety.
  tmpvenv="$(mktemp -d)/venv"
  python -m venv "$tmpvenv"
  "$tmpvenv/bin/pip" install -q --no-cache-dir gemini-notebook-py==0.0.1
  "$tmpvenv/bin/python" -c "import notebooklm; print('  import notebooklm OK, version', notebooklm.__version__)"
fi

cat <<'NEXT'

== Done. Two things to do RIGHT NOW, in this sitting:

  1. Add a Trusted Publisher to each new project (and notebooklm-py):
       Owner:       teng-lin
       Repository:  notebooklm-py      (current name; rename is deferred)
       Workflow:    publish.yml
       Environment: release            (must match, or the OIDC claim fails)

  2. DELETE the account-scoped API token you just used.

  Later, after 0.9.0 ships — see the plan, step 7. These differ:
    - YANK    gemini-notebook-py 0.0.1 and gemini-notebook 0.0.1
    - REFRESH bare notebooklm (publish 0.0.2 -> gemini-notebook-py>=0.9).
      It is outside the lockstep matrix, so nothing supersedes it; left
      alone it serves 0.8.x forever. No CI catches this one.
NEXT
