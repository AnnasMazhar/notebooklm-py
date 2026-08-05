"""Validation bridge for Playwright-captured browser state."""

from __future__ import annotations

from typing import Any

from . import cookies as _auth_cookies
from . import psidts_recovery as _psidts_recovery


def validate_captured_state_with_recovery(state: dict[str, Any]) -> dict[str, Any]:
    """Validate captured rows without losing Playwright-only attributes.

    The in-memory recovery helper consumes rookiepy-shaped rows, so the bridge
    adapts only the ``httpOnly`` spelling.  The cookie converter preserves an
    existing Playwright ``sameSite`` value; newly minted recovery cookies use
    its safe default.  Synthetic projection helpers remain side-effect-free,
    while real browser capture still validates before persistence.
    """
    rookiepy_rows: list[dict[str, Any]] = []
    for entry in _auth_cookies._sanitized_auth_entries(state):
        rookiepy_entry = dict(entry)
        rookiepy_entry["http_only"] = bool(entry.get("httpOnly", False))
        rookiepy_rows.append(rookiepy_entry)

    validated_state, error = _psidts_recovery.validate_with_recovery(rookiepy_rows)
    if error is not None:
        raise error
    return {
        "cookies": validated_state["cookies"],
        "origins": list(state.get("origins", [])),
    }
