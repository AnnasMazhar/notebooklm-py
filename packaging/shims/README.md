# Shim distributions (ADR-0028)

Two published names forward to the canonical `gemini-notebook-py` distribution:

| Dist | Why it exists |
|---|---|
| `notebooklm-py` | The **former canonical name**. Every install, CI pipeline, `mcp install` config, and shipped desktop extension that predates 0.9.0 refers to it. It keeps resolving, indefinitely. |
| `gemini-notebook` | The bare brand name, registered so nobody else can take it. It is a **first-class install path**, not a parked placeholder. |

Both are generated from the same template, and both must satisfy the same
contract. `tests/unit/test_shim_packaging.py` enforces every clause of it.

## The contract

1. **Zero Python files, zero console scripts.** A shim that shipped its own
   copy of `notebooklm/` would recreate exactly the file collision ADR-0028
   exists to end. Duplicate `bin/` entries are equally unsafe: two dists owning
   the same console-script files means uninstalling either corrupts the other.
2. **Every canonical extra is mirrored**, each pinned to the exact canonical
   version: `notebooklm-py[mcp]` → `gemini-notebook-py[mcp]==<version>`.
   Extras are not inherited — an extra missing here is an install that used to
   work and now silently resolves to fewer packages. The shipped desktop
   extension hardcodes `notebooklm-py[mcp]`, so `[mcp]` in particular must
   never be dropped.
3. **Released in lockstep with every canonical release**, at the identical
   version, by `publish.yml` — never by hand.
4. **Canonical uploads first.** A shim pins `==<version>`, so publishing it
   before the version it pins exists leaves a window where `pip install
   notebooklm-py` resolves to something uninstallable.

## Consequence for isolated tool installers

`pipx install` and `uv tool install` expose only the *requested* distribution's
entry points. Since the shims deliberately declare none, installing a shim that
way yields no commands. This is a real trade-off, accepted in ADR-0028 step 4
over the more dangerous alternative of duplicate script ownership:

- `uvx --from "notebooklm-py[mcp]" notebooklm-mcp` — **works**, and is the path
  baked into already-shipped desktop extensions. `uvx` resolves executables
  from the whole environment, not just the named dist.
- `pipx install notebooklm-py` — needs `--include-deps` to expose the commands.
- New installs should prefer `gemini-notebook-py`, which owns the scripts
  directly.

## Regenerating

`generate.py` writes each shim's `pyproject.toml` from the canonical one, so
extras cannot drift by hand-editing:

```bash
python packaging/shims/generate.py          # rewrite in place
python packaging/shims/generate.py --check  # verify, non-zero on drift (CI)
```
