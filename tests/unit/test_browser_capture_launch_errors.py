"""Unit tests for :func:`classify_launch_failure` (browser-launch triage).

``launch_persistent_context`` can fail for reasons the user can act on, and
before issue #2004 only the *system-channel* browsers (``chrome`` / ``msedge``)
had a friendly branch — a bundled-Chromium launch failure fell through to a
bare ``raise`` and surfaced as ``Unexpected error: ... This may be a bug``.

The classifier is pure (message in, Rich-markup help or ``None`` out), so every
branch — including the Windows-only ``spawn UNKNOWN`` execution veto, which
cannot be reproduced on Linux/macOS — is testable on any host.
"""

from __future__ import annotations

import pytest

from notebooklm._auth.browser_launch_errors import (
    BUNDLED_CHROMIUM_MISSING_HELP,
    BUNDLED_SPAWN_VETO_HELP,
    CHANNEL_SPAWN_VETO_HELP,
    classify_launch_failure,
)


@pytest.mark.parametrize(
    ("browser", "label", "url_fragment"),
    [
        ("msedge", "Microsoft Edge", "microsoft.com/edge"),
        ("chrome", "Google Chrome", "google.com/chrome"),
    ],
)
def test_channel_browser_not_installed(browser, label, url_fragment):
    help_text = classify_launch_failure(
        browser, f"Executable doesn't exist at /{browser}\nFailed to launch"
    )
    assert help_text is not None
    assert f"{label} not found" in help_text
    assert url_fragment in help_text


def test_channel_browser_unrelated_failure_is_unclassified():
    """A channel failure that is neither "missing" nor a veto must not be mislabelled."""
    assert classify_launch_failure("chrome", "Protocol error: connection lost") is None


def test_bundled_chromium_missing_points_at_playwright_install():
    help_text = classify_launch_failure(
        "chromium", "Executable doesn't exist at /root/.cache/ms-playwright/chromium-1234/chrome"
    )
    assert help_text == BUNDLED_CHROMIUM_MISSING_HELP
    assert "playwright install chromium" in help_text


@pytest.mark.parametrize(
    "error_text",
    [
        "BrowserType.launch_persistent_context: spawn UNKNOWN",
        # Case-insensitive: the classifier lower-cases before matching.
        "browsertype.launch_persistent_context: Spawn Unknown",
    ],
)
def test_bundled_chromium_spawn_unknown_names_the_windows_veto(error_text):
    help_text = classify_launch_failure("chromium", error_text)
    assert help_text == BUNDLED_SPAWN_VETO_HELP
    # Names the cause rather than blaming a missing install...
    assert "spawn UNKNOWN" in help_text
    assert "AppLocker" in help_text
    assert "ms-playwright" in help_text
    # ...offers the different-path system browsers...
    assert "--browser chrome" in help_text
    assert "--browser msedge" in help_text
    # ...points at the ship-a-storage_state.json escape hatch...
    assert "storage_state.json" in help_text
    assert "NOTEBOOKLM_AUTH_JSON" in help_text
    # ...and closes off the wrong guess (headless is the same spawn).
    assert "headless does NOT help" in help_text


def test_bundled_chromium_unknown_failure_is_unclassified():
    """No specific advice → ``None``, so the real exception still propagates."""
    assert classify_launch_failure("chromium", "Timeout 30000ms exceeded") is None


def test_spawn_unknown_on_a_channel_browser_gets_the_channel_variant():
    """Same veto, different remedies: it must not recommend the channel that just failed."""
    help_text = classify_launch_failure("chrome", "spawn UNKNOWN")
    assert help_text == CHANNEL_SPAWN_VETO_HELP
    assert "--browser chrome" not in help_text
    assert "notebooklm login\n" in help_text
    assert "headless does NOT help" in help_text
