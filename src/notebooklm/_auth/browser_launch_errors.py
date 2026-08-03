"""Friendly triage for Playwright browser-launch failures.

Transport-neutral leaf (ADR-0021): no CLI, no Playwright, no I/O. Takes the
resolved browser channel plus the exception text and returns Rich-markup help,
or ``None`` when we have nothing specific to say — in which case the caller
must let the original exception propagate rather than guess.

Split out of ``browser_capture.py`` (ADR-0008 module-size budget) when the
bundled-Chromium branches were added for issue #2004.
"""

from __future__ import annotations

# Browsers launched via Playwright's ``channel`` parameter (system-installed,
# not the bundled Chromium). Maps channel name -> (display label, install URL).
# Used for the --browser option, the launch banner, and the not-installed
# error path. The bundled "chromium" choice is intentionally absent.
CHANNEL_BROWSERS: dict[str, tuple[str, str]] = {
    "msedge": ("Microsoft Edge", "https://www.microsoft.com/edge"),
    "chrome": ("Google Chrome", "https://www.google.com/chrome"),
}

# Launch-failure markers, matched case-insensitively against the exception text
# (Playwright reports these in the message, not via typed exceptions).
#
# The bundled arm keys off the single precise "missing executable" marker: the
# broader list below would mislabel an unrelated launch crash as "run playwright
# install". The channel arm can afford the broad list, because a missing browser
# is the only realistic way a *channel* launch fails.
_EXECUTABLE_MISSING_MARKER = "executable doesn't exist"
_NOT_INSTALLED_MARKERS = (
    _EXECUTABLE_MISSING_MARKER,
    "is not found at",
    "no such file",
    "failed to launch",
)
# libuv's UV_UNKNOWN escaping CreateProcessW: the underlying Win32 error had no
# entry in libuv's translation table. A missing binary maps to ENOENT and a
# plain ACL denial to EACCES, so what actually reaches UNKNOWN is policy- or
# AV-shaped -- ERROR_ACCESS_DISABLED_BY_POLICY (AppLocker / SRP / WDAC) or
# ERROR_VIRUS_INFECTED / ERROR_VIRUS_DELETED (Defender or another endpoint
# agent). Upstream microsoft/playwright#35363, #28307 and #28858 all resolve to
# group policy or endpoint security blocking execution. See issue #2004.
_SPAWN_VETO_MARKER = "spawn unknown"

BUNDLED_CHROMIUM_MISSING_HELP = (
    "[red]Playwright's bundled Chromium is not installed.[/red]\n"
    "Install it with:\n"
    "  python -m playwright install chromium\n"
    "Or use a system browser you already have: notebooklm login --browser chrome"
)

_SPAWN_VETO_HEADER = (
    "[red]The operating system refused to start the browser (spawn UNKNOWN).[/red]\n"
    "The browser is installed, but something on this machine vetoed executing it "
    "-- on Windows, typically AppLocker / WDAC / Software Restriction Policies, or "
    "Microsoft Defender (or another endpoint-security agent) blocking the "
    "executable's directory.\n\nTry:\n"
)
_SPAWN_VETO_FOOTER = (
    "\nRunning headless does NOT help: it launches the same binary the same way, "
    "and the veto happens at process creation, before any window would exist."
)
# "Ship the credentials in" is the last resort on both arms; only the bundled
# arm can also suggest a *different path*, since the system browsers install
# under Program Files rather than %LOCALAPPDATA%\ms-playwright, so a rule
# scoped to the Playwright directory does not cover them.
_SPAWN_VETO_SHIP_STATE_STEP = (
    "Sign in on a machine with a display and copy the resulting\n"
    "     storage_state.json to this one (or set NOTEBOOKLM_AUTH_JSON) -- see the\n"
    "     'Headless server or CI' section of docs/installation.md."
)


def _spawn_veto_help(*steps: str) -> str:
    """Build a spawn-veto message: shared header, numbered ``steps``, shared footer.

    Each step is rendered as ``"  <n>. "`` + its text; a step's own continuation
    lines carry the matching five-space indent.
    """
    numbered_steps = "".join(f"  {number}. {step}\n" for number, step in enumerate(steps, start=1))
    return f"{_SPAWN_VETO_HEADER}{numbered_steps}{_SPAWN_VETO_FOOTER}"


BUNDLED_SPAWN_VETO_HELP = _spawn_veto_help(
    "A system browser, installed under Program Files rather than\n"
    "     %LOCALAPPDATA%\\ms-playwright, so a path-scoped rule does not cover it:\n"
    "       notebooklm login --browser chrome\n"
    "       notebooklm login --browser msedge",
    "Ask IT to allow execution from %LOCALAPPDATA%\\ms-playwright.",
    _SPAWN_VETO_SHIP_STATE_STEP,
)
CHANNEL_SPAWN_VETO_HELP = _spawn_veto_help(
    "Ask IT to allow execution from the browser's install directory.",
    "Try the bundled Chromium instead (a different path, so a path-scoped\n"
    "     rule may not cover it): notebooklm login",
    _SPAWN_VETO_SHIP_STATE_STEP,
)


def classify_launch_failure(browser: str, error: str) -> str | None:
    """Map a browser-launch failure to Rich-markup help text, or ``None``.

    ``browser`` is the resolved channel: a :data:`CHANNEL_BROWSERS` key for a
    system browser, anything else (in practice ``"chromium"``) for Playwright's
    bundled build.
    """
    err = error.lower()
    channel_info = CHANNEL_BROWSERS.get(browser)

    if channel_info is None:
        # Playwright's bundled Chromium.
        if _EXECUTABLE_MISSING_MARKER in err:
            return BUNDLED_CHROMIUM_MISSING_HELP
        if _SPAWN_VETO_MARKER in err:
            return BUNDLED_SPAWN_VETO_HELP
        return None

    if any(marker in err for marker in _NOT_INSTALLED_MARKERS):
        label, install_url = channel_info
        return (
            f"[red]{label} not found.[/red]\n"
            f"Install from: {install_url}\n"
            "Or use the default Chromium browser: notebooklm login"
        )
    if _SPAWN_VETO_MARKER in err:
        return CHANNEL_SPAWN_VETO_HELP
    return None


__all__ = [
    "BUNDLED_CHROMIUM_MISSING_HELP",
    "BUNDLED_SPAWN_VETO_HELP",
    "CHANNEL_BROWSERS",
    "CHANNEL_SPAWN_VETO_HELP",
    "classify_launch_failure",
]
