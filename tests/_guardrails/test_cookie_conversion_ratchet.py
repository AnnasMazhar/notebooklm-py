"""Shrink-only ratchet on the scattered cookie-conversion free functions.

ADR-0031 Stage 1. A cookie has lived in six shapes in this layer, with the
conversions between them as free functions any module could reach for. That is
what let "which cookies are here / is this set usable" get re-derived
independently at a dozen call sites, and it is why the auth layer resisted the
#2139 boundary moves: the coupling had no owner.

:class:`notebooklm._auth.cookie_types.CookieJar` is now that owner. This gate
does **not** force a migration — per this repo's ratchet convention, existing
call sites migrate opportunistically, not in an eager burndown. It blocks the
*next* one: new code converts through ``CookieJar`` instead of adding a
thirteenth bespoke call.

Sanctioned homes (never flagged):

* ``_auth/cookies.py`` — where these functions are defined and compose.
* ``_auth/cookie_types.py`` — the wrapper whose whole job is delegating to them.

Everything else is in :data:`_GRANDFATHERED`, which is **shrink-only**: an entry
whose call has since moved to ``CookieJar`` must be deleted (asserted by
``test_grandfathered_entries_are_all_live``), so the list can only get shorter.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.repo_lint

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "notebooklm"

#: The conversions ``CookieJar`` now owns a typed route for.
_RATCHETED = frozenset(
    {
        "normalize_cookie_map",
        "flatten_cookie_map",
        "extract_cookies_with_domains",
        "convert_rookiepy_cookies_to_storage_state",
    }
)

#: Modules that legitimately call these directly.
_SANCTIONED = frozenset({"_auth/cookies.py", "_auth/cookie_types.py"})

#: (module path relative to ``src/notebooklm``, function) pairs predating the
#: ratchet. SHRINK-ONLY — never add. Each is a candidate for a later stage.
_GRANDFATHERED: frozenset[tuple[str, str]] = frozenset(
    {
        # AuthTokens.__post_init__ widens legacy caller-supplied maps, and the
        # back-compat flat-map property flattens them back. Both retire in
        # ADR-0031 Stage 4, when the dual cookies/cookie_jar fields collapse
        # into a single CookieJar and these become jar methods.
        ("_auth/tokens.py", "normalize_cookie_map"),
        ("_auth/tokens.py", "flatten_cookie_map"),
        # The L3 heal converts rookiepy rows twice (before and after recovery).
        # Retires with the Stage 2 validate/heal split.
        ("_auth/browser_cookie_recovery.py", "convert_rookiepy_cookies_to_storage_state"),
        # The CLI browser-extraction probe path. Its converter choice is pinned
        # to the routability predicates by design (see the module docstring in
        # _auth/psidts_recovery.py), so it moves only with Stage 5's mode split.
        ("cli/services/login/cookie_jar.py", "convert_rookiepy_cookies_to_storage_state"),
        ("cli/services/login/cookie_jar.py", "extract_cookies_with_domains"),
    }
)


def _call_sites() -> set[tuple[str, str]]:
    """Return every ``(module, ratcheted function)`` call site under ``src/``."""
    found: set[tuple[str, str]] = set()
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        rel = path.relative_to(_SRC_ROOT).as_posix()
        if rel in _SANCTIONED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # Matches both ``f(...)`` and ``mod.f(...)`` so aliasing a module
            # (the ``_auth_cookies.f(...)`` idiom used throughout _auth) does
            # not slip past the gate.
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else None
            )
            if name in _RATCHETED:
                found.add((rel, name))
    return found


def test_no_new_bespoke_cookie_conversions() -> None:
    """New code converts through ``CookieJar``, not the raw free functions."""
    new = _call_sites() - _GRANDFATHERED
    assert not new, (
        "New direct call(s) to a ratcheted cookie conversion:\n"
        + "\n".join(f"  {module}: {func}()" for module, func in sorted(new))
        + "\n\nUse notebooklm._auth.cookie_types.CookieJar instead — e.g.\n"
        "  CookieJar.from_storage_state(state).to_domain_map()\n"
        "  CookieJar.from_rookiepy(rows).to_httpx()\n"
        "flatten_cookie_map has NO jar equivalent on purpose: the flat shape is\n"
        "lossy (path collapsed, arbitrary same-tier winner — #369/#2054) and\n"
        "legacy-only. For cookies on the wire use CookieJar.to_httpx().\n"
        "If the call genuinely cannot route through the jar, say why in the PR "
        "and add it to _GRANDFATHERED with that reason."
    )


def test_grandfathered_entries_are_all_live() -> None:
    """The allowlist is shrink-only: a migrated entry must be deleted.

    Without this, the list silently becomes a graveyard and stops describing
    the real remaining debt — the failure mode that makes ratchets rot.
    """
    stale = _GRANDFATHERED - _call_sites()
    assert not stale, (
        "Grandfathered entries whose call no longer exists — delete them:\n"
        + "\n".join(f"  {module}: {func}()" for module, func in sorted(stale))
    )


def test_cookie_jar_is_the_sanctioned_wrapper() -> None:
    """``cookie_types`` really does delegate — the premise of the whole gate.

    If a future edit reimplemented a conversion inline instead of delegating,
    the ratchet would be pointing callers at a second implementation rather
    than at the single owner.
    """
    source = (_SRC_ROOT / "_auth" / "cookie_types.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    delegated = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    missing = {"normalize_cookie_map", "build_cookie_jar"} - delegated
    assert not missing, f"cookie_types.py stopped delegating to: {sorted(missing)}"
