"""Unit tests for tolerating failed navigations during the login wait (#2257).

``page.wait_for_url`` rejects on **any** failed main-frame navigation, not only
one heading for the awaited URL — Playwright's ``expect_navigation`` predicate
returns True for every event carrying an ``error``. So one cancelled navigation
anywhere in Google's sign-in chain used to raise out of the five-minute wait and
reach the CLI's generic arm as "Unexpected error: net::ERR_ABORTED; maybe frame
was detached?" plus exit 2 — *discarding a sign-in the user had already
completed*, because the browser could be sitting on the accepted host at the
moment we gave up.

The regression that matters most is :func:`test_landing_after_a_failed_navigation_is_detected`:
a failed navigation followed by a successful landing must capture, not crash.

These tests pin the tolerance without widening it: a dead browser
(``TargetClosed``) and unrelated Playwright errors must still propagate to the
caller's existing routing, and the caller's overall timeout must still be
honoured rather than restarted on every tolerated failure.
"""

from __future__ import annotations

import logging
from typing import Any, NoReturn
from unittest.mock import MagicMock

import pytest

from notebooklm._auth.browser_capture import (
    INSTANT_FAILURE_SECONDS,
    MAX_TOLERATED_NAVIGATION_FAILURES,
    TARGET_CLOSED_ERROR,
    is_navigation_failure,
    is_navigation_interrupted_error,
    is_navigation_race,
    navigation_error_code,
    wait_for_login_landing,
)

pytest.importorskip("playwright")

CAPTURE_LOGGER = "notebooklm._auth.browser_capture"

LANDED = "https://notebooklm.google.com/"
SIGNING_IN = "https://accounts.google.com/signin/v2/challenge"

# The literal Playwright emits when a pending main-frame document request is
# cancelled (``frames.js`` appends the suffix when ``canceled`` is set). Raised
# bare — ``expect_navigation`` builds this Error inside its own waiter task, so
# it never picks up the ``Page.goto:`` api-name prefix a goto failure carries.
ABORTED = "net::ERR_ABORTED; maybe frame was detached?"


@pytest.fixture(autouse=True)
def _default_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the accept set to the personal hosts for every test in this module."""
    monkeypatch.delenv("NOTEBOOKLM_BASE_URL", raising=False)


def _playwright_error(message: str) -> Exception:
    from playwright.sync_api import Error as PlaywrightError

    return PlaywrightError(message)


class _FakePage:
    """Page stand-in whose ``wait_for_url`` replays a scripted outcome list.

    Each entry is either an exception to raise or a URL to "land" on. Records
    the timeout it was handed per call so budget behaviour can be asserted.
    """

    def __init__(self, outcomes: list[Any], *, url: str = SIGNING_IN) -> None:
        self._outcomes = list(outcomes)
        self.url = url
        self.timeouts: list[float] = []

    def wait_for_url(self, _matcher: Any, *, wait_until: str, timeout: float) -> None:
        self.timeouts.append(timeout)
        if not self._outcomes:
            raise AssertionError("wait_for_url called more times than the test scripted")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        self.url = outcome


class _RecordingIO:
    """Captures the user-facing emissions without a Rich console.

    Implements the full ``BrowserCaptureIO`` protocol, ``run_async`` included:
    a double that is narrower than the real thing can hide a call the
    production code legitimately makes.
    """

    def __init__(self) -> None:
        self.messages: list[str] = []

    def emit(self, *args: Any, **kwargs: Any) -> None:
        self.messages.append(args[0] if args else "")

    def fail(self, code: int) -> NoReturn:  # pragma: no cover - not reached here
        raise AssertionError(f"io.fail({code}) is not part of the wait contract")

    def run_async(self, coro: Any) -> Any:  # pragma: no cover - not reached here
        raise AssertionError("run_async is not part of the wait contract")


# ---------------------------------------------------------------------------
# Classifiers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (ABORTED, "net::ERR_ABORTED"),
        ("net::ERR_NAME_NOT_RESOLVED", "net::ERR_NAME_NOT_RESOLVED"),
        (
            "Page.goto: net::ERR_ABORTED at https://accounts.google.com/x?f.sid=S",
            "net::ERR_ABORTED",
        ),
        ("Navigation interrupted by another one", None),
        ("some unrelated failure", None),
    ],
)
def test_navigation_error_code_extracts_the_code_only(message: str, expected: str | None) -> None:
    assert navigation_error_code(message) == expected


def test_navigation_error_code_never_returns_the_url() -> None:
    """The extracted code is what gets logged, so it must not carry the URL.

    Playwright embeds credential-bearing URLs in navigation errors, and ``-vv``
    output is exactly what the issue template asks users to paste in public.
    """
    code = navigation_error_code(
        "Page.goto: net::ERR_ABORTED at https://accounts.google.com/o/oauth2/GRANT?f.sid=SECRET"
    )
    assert code == "net::ERR_ABORTED"
    assert "SECRET" not in (code or "")
    assert "GRANT" not in (code or "")


@pytest.mark.parametrize(
    "message",
    [
        ABORTED,
        "net::ERR_NAME_NOT_RESOLVED",
        "net::ERR_CONNECTION_RESET",
        "Navigation interrupted by another one",
        "navigation interrupted",
    ],
)
def test_navigation_failures_are_recognised(message: str) -> None:
    assert is_navigation_failure(message) is True


@pytest.mark.parametrize("message", [TARGET_CLOSED_ERROR, "Execution context was destroyed"])
def test_non_navigation_errors_are_not_recognised(message: str) -> None:
    """A dead browser is not a retryable navigation — it owns a different remedy."""
    assert is_navigation_failure(message) is False


def test_target_closed_wins_even_when_a_net_code_is_present() -> None:
    """Ordering matters: a closed target must never be mistaken for a retry."""
    assert is_navigation_failure(f"{TARGET_CLOSED_ERROR} (net::ERR_ABORTED)") is False


def test_prose_interruption_matcher_still_answers_its_own_question() -> None:
    """The #214/#322 predicate stays narrow; the new ones are supersets of it."""
    assert is_navigation_interrupted_error("Navigation interrupted by another one") is True
    assert is_navigation_interrupted_error(ABORTED) is False
    assert is_navigation_failure(ABORTED) is True
    assert is_navigation_race(ABORTED) is True


@pytest.mark.parametrize(
    ("message", "race", "failure"),
    [
        (ABORTED, True, True),
        ("Navigation interrupted by another one", True, True),
        # The half of the family that must NOT be swallowed where we navigate:
        # a refused connection earns connection_error_help, and an invalid URL
        # is a configuration fault that has to fail fast.
        ("net::ERR_CONNECTION_REFUSED", False, True),
        ("net::ERR_INVALID_URL", False, True),
        ("net::ERR_NAME_NOT_RESOLVED", False, True),
        ("net::ERR_SOMETHING_ELSE while loading", False, True),
        # Chromium aborts with prose and no net:: code when a beforeunload
        # dialog is dismissed (crPage.js -> frameAbortedNavigation). Broad only:
        # where WE navigate, a cancelled goto means it did not happen.
        ("navigation cancelled by beforeunload dialog", False, True),
        (TARGET_CLOSED_ERROR, False, False),
    ],
)
def test_the_two_predicates_disagree_by_who_issued_the_navigation(
    message: str, race: bool, failure: bool
) -> None:
    """Same error, different verdict, depending on the call site.

    The wait watches a human navigate, so any failed hop is noise. The retry
    loop and cookie forcing issue the navigation themselves, so only a
    superseded one may be ignored — swallowing a refused connection there would
    turn a real error into a silent hang.
    """
    assert is_navigation_race(message) is race
    assert is_navigation_failure(message) is failure


# ---------------------------------------------------------------------------
# The wait itself
# ---------------------------------------------------------------------------


def test_beforeunload_cancellation_is_tolerated_by_the_wait() -> None:
    """A federated IdP with a beforeunload handler must not kill the wait.

    This message carries no ``net::`` code, so the code-extraction path misses
    it entirely; without an explicit marker it propagated and exited 2 while a
    perfectly usable login page was still open.
    """
    page = _FakePage([_playwright_error("navigation cancelled by beforeunload dialog"), LANDED])
    assert wait_for_login_landing(page, timeout_s=300) == 1


def test_clean_landing_tolerates_nothing() -> None:
    page = _FakePage([LANDED])
    assert wait_for_login_landing(page, timeout_s=300) == 0


def test_failed_navigation_is_tolerated_and_the_wait_resumes() -> None:
    """One cancelled hop must not end a login the human is still completing."""
    page = _FakePage([_playwright_error(ABORTED), LANDED])
    assert wait_for_login_landing(page, timeout_s=300) == 1
    assert len(page.timeouts) == 2


def test_landing_after_a_failed_navigation_is_detected() -> None:
    """THE regression: the wait rejected *after* the browser already landed.

    Playwright rejects the wait on a failed navigation even when a previous hop
    already reached the accepted host. Before #2257 that raised, exit 2, and
    wrote no ``storage_state.json`` — throwing away a completed sign-in. The
    accept predicate, not the exception, decides whether we are done.
    """
    page = _FakePage([_playwright_error(ABORTED)], url=SIGNING_IN)

    def _land_then_fail(_matcher: Any, *, wait_until: str, timeout: float) -> None:
        page.timeouts.append(timeout)
        page.url = LANDED  # the human finished; the later hop aborted
        raise _playwright_error(ABORTED)

    page.wait_for_url = _land_then_fail  # type: ignore[method-assign]
    assert wait_for_login_landing(page, timeout_s=300) == 0


@pytest.mark.parametrize(
    "code",
    ["net::ERR_NAME_NOT_RESOLVED", "net::ERR_INTERNET_DISCONNECTED", "net::ERR_NETWORK_CHANGED"],
)
def test_transient_network_faults_are_tolerated_too(code: str) -> None:
    """Not an ERR_ABORTED bug: any failed navigation killed the wait.

    A DNS blip or VPN flap while the user types their password is the same
    defect wearing different text.
    """
    page = _FakePage([_playwright_error(code), LANDED])
    assert wait_for_login_landing(page, timeout_s=300) == 1


def test_target_closed_still_propagates() -> None:
    """A dead browser must reach the caller's BROWSER_CLOSED_HELP routing."""
    from playwright.sync_api import Error as PlaywrightError

    page = _FakePage([_playwright_error(TARGET_CLOSED_ERROR)])
    with pytest.raises(PlaywrightError, match="has been closed"):
        wait_for_login_landing(page, timeout_s=300)


def test_unrelated_playwright_errors_still_propagate() -> None:
    """Tolerance is scoped to navigation failures, not to Playwright at large."""
    from playwright.sync_api import Error as PlaywrightError

    page = _FakePage([_playwright_error("Protocol error: something structural")])
    with pytest.raises(PlaywrightError, match="Protocol error"):
        wait_for_login_landing(page, timeout_s=300)


def test_timeout_propagates_for_the_callers_own_handler() -> None:
    """The caller owns the "Login not detected" message; do not swallow it."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    page = _FakePage([PlaywrightTimeout("Timeout 300000ms exceeded.")])
    with pytest.raises(PlaywrightTimeout):
        wait_for_login_landing(page, timeout_s=300)


def test_the_budget_shrinks_and_is_never_restarted() -> None:
    """Tolerating a failure must not hand the user a fresh five minutes each time.

    An unbounded reset would turn a page that fails in a loop into a wait that
    never ends.
    """
    page = _FakePage([_playwright_error(ABORTED), _playwright_error(ABORTED), LANDED])
    wait_for_login_landing(page, timeout_s=300)
    assert page.timeouts == sorted(page.timeouts, reverse=True)
    assert page.timeouts[0] <= 300 * 1000
    assert page.timeouts[-1] < page.timeouts[0]


def test_a_page_failing_in_a_loop_is_bounded_not_spun_on() -> None:
    """A page that fails INSTANTLY defeats the deadline as a bound.

    ``wait_for_url`` blocks between real navigations, so it is tempting to
    assume the page paces the loop. An error page that reloads itself returns
    the rejection with no delay, and tolerating forever would burn the whole
    timeout at full tilt. The cap stops it and surfaces the last error.
    """
    from playwright.sync_api import Error as PlaywrightError

    page = _FakePage([_playwright_error(ABORTED)] * 500)
    with pytest.raises(PlaywrightError, match="ERR_ABORTED"):
        wait_for_login_landing(page, timeout_s=300)
    assert len(page.timeouts) == MAX_TOLERATED_NAVIGATION_FAILURES + 1


def test_giving_up_explains_itself_before_re_raising() -> None:
    """The cap must not hand the user a bare "please report a bug".

    Re-raising is deliberate — an unclassified Playwright error has to stay
    visible — but a captive portal looping the sign-in is not a defect in this
    tool, so the cause and a browser-free way out are printed first.
    """
    from playwright.sync_api import Error as PlaywrightError

    page = _FakePage([_playwright_error("net::ERR_NAME_NOT_RESOLVED")] * 500)
    io = _RecordingIO()
    with pytest.raises(PlaywrightError):
        wait_for_login_landing(page, timeout_s=300, io=io)

    assert any("could not complete a navigation" in m for m in io.messages)
    guidance = next(m for m in io.messages if "could not complete a navigation" in m)
    assert "net::ERR_NAME_NOT_RESOLVED" in guidance
    assert "--browser-cookies" in guidance
    # No URL material, same rule as the DEBUG trace.
    assert "http" not in guidance


def test_slow_failures_reset_the_streak_and_never_trip_the_cap() -> None:
    """The cap counts CONSECUTIVE IMMEDIATE failures, not failures overall.

    A cumulative counter would abort a long, flaky-but-honest sign-in: with
    `--browser-timeout 1800`, 21 failures spread over half an hour would kill a
    login the human was still completing — the exact class of bug this whole
    function exists to fix. Only a page failing with no delay is pathological.
    """
    import time as _time

    slow = _playwright_error(ABORTED)
    calls = {"n": 0}

    class _SlowFailingPage(_FakePage):
        def wait_for_url(self, _matcher: Any, *, wait_until: str, timeout: float) -> None:
            self.timeouts.append(timeout)
            calls["n"] += 1
            if calls["n"] > MAX_TOLERATED_NAVIGATION_FAILURES + 5:
                self.url = LANDED
                return
            # Each failure takes real time, so the page is pacing us.
            _time.sleep(INSTANT_FAILURE_SECONDS * 1.4)
            raise slow

    page = _SlowFailingPage([])
    tolerated = wait_for_login_landing(page, timeout_s=300)
    assert tolerated == MAX_TOLERATED_NAVIGATION_FAILURES + 5, "slow failures must not trip the cap"


def test_landing_at_the_buzzer_beats_the_timeout() -> None:
    """Playwright's timeout races the navigated event; losing by a hair is real.

    If the browser is on the accepted host, report success rather than
    'Login not detected' — the same rule the navigation-failure arm follows.
    """
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    page = _FakePage([])

    def _timeout_but_landed(_matcher: Any, *, wait_until: str, timeout: float) -> None:
        page.timeouts.append(timeout)
        page.url = LANDED
        raise PlaywrightTimeout("Timeout 300000ms exceeded.")

    page.wait_for_url = _timeout_but_landed  # type: ignore[method-assign]
    assert wait_for_login_landing(page, timeout_s=300) == 0


def test_timeout_without_landing_still_propagates() -> None:
    """The re-check must not swallow a genuine timeout."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    page = _FakePage([PlaywrightTimeout("Timeout 300000ms exceeded.")], url=SIGNING_IN)
    with pytest.raises(PlaywrightTimeout):
        wait_for_login_landing(page, timeout_s=300)


def test_the_notice_names_the_error_code() -> None:
    """A policy block must be diagnosable, not a vague 'interrupted'."""
    page = _FakePage([_playwright_error("net::ERR_BLOCKED_BY_ADMINISTRATOR"), LANDED])
    io = _RecordingIO()
    wait_for_login_landing(page, timeout_s=300, io=io)
    assert "net::ERR_BLOCKED_BY_ADMINISTRATOR" in io.messages[0]
    assert "http" not in io.messages[0]


def test_the_cap_leaves_room_for_an_ordinary_racy_sign_in() -> None:
    """A handful of aborts is normal; the cap must not clip a real login."""
    page = _FakePage([_playwright_error(ABORTED)] * 5 + [LANDED])
    assert wait_for_login_landing(page, timeout_s=300) == 5


def test_an_exhausted_budget_raises_rather_than_looping() -> None:
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    page = _FakePage([_playwright_error(ABORTED)] * 50)
    with pytest.raises(PlaywrightTimeout):
        wait_for_login_landing(page, timeout_s=0)
    # The deadline is checked before the first wait, so nothing was attempted.
    assert page.timeouts == []


def test_the_user_is_told_once_not_once_per_failure() -> None:
    """Silence would look like a hang; per-failure noise would bury the prompt."""
    page = _FakePage([_playwright_error(ABORTED), _playwright_error(ABORTED), LANDED])
    io = _RecordingIO()
    assert wait_for_login_landing(page, timeout_s=300, io=io) == 2
    assert len(io.messages) == 1
    assert "still waiting" in io.messages[0]


def test_a_clean_login_stays_silent() -> None:
    page = _FakePage([LANDED])
    io = _RecordingIO()
    wait_for_login_landing(page, timeout_s=300, io=io)
    assert io.messages == []


def test_the_tolerated_hop_is_traced_with_its_code_and_no_url(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fills the ``-vv`` blind spot without reopening the credential-leak one.

    ``log_observed_navigations`` hooks ``framenavigated``, which Playwright only
    emits for events carrying **no** error — so the failing hop, the one worth
    seeing, was exactly the one the trace could never show.
    """
    caplog.set_level(logging.DEBUG, logger=CAPTURE_LOGGER)
    page = _FakePage(
        [
            _playwright_error(
                "net::ERR_ABORTED at https://accounts.google.com/signin?f.sid=SECRET_SID"
            ),
            LANDED,
        ]
    )
    wait_for_login_landing(page, timeout_s=300)

    tolerated = [m for m in caplog.messages if "tolerated a failed navigation" in m]
    assert len(tolerated) == 1
    assert "net::ERR_ABORTED" in tolerated[0]
    assert "SECRET_SID" not in tolerated[0]
    assert "signin" not in tolerated[0]


def test_an_unreadable_page_url_does_not_mask_the_wait(caplog: pytest.LogCaptureFixture) -> None:
    """A dying page must not turn a diagnostic read into an unhandled traceback.

    ``page.url`` can raise once the page is going away, and it is read twice per
    tolerated failure — once to test the accept predicate, once for the trace.
    Neither read may escape.
    """
    caplog.set_level(logging.DEBUG, logger=CAPTURE_LOGGER)

    class _UnreadableUrlPage(_FakePage):
        @property  # type: ignore[misc]
        def url(self) -> str:
            raise RuntimeError("page is gone")

        @url.setter
        def url(self, value: str) -> None:
            self._landed = value

    page = _UnreadableUrlPage([_playwright_error(ABORTED), LANDED])
    # An unreadable URL cannot match the accept predicate, so the wait resumes
    # rather than falsely reporting a landing.
    assert wait_for_login_landing(page, timeout_s=300) == 1
    assert any("could not read the page URL" in m for m in caplog.messages)


def test_matcher_and_lifecycle_passed_through_unchanged() -> None:
    """``commit`` is load-bearing: the SPA never fires ``load`` (#1697)."""
    seen: dict[str, Any] = {}

    class _Recorder(_FakePage):
        def wait_for_url(self, matcher: Any, *, wait_until: str, timeout: float) -> None:
            seen["matcher"] = matcher
            seen["wait_until"] = wait_until
            self.url = LANDED

    wait_for_login_landing(_Recorder([LANDED]), timeout_s=300)
    assert seen["wait_until"] == "commit"
    assert seen["matcher"]("https://notebooklm.google.com/") is True
    assert seen["matcher"]("https://accounts.google.com/") is False


def test_page_object_is_never_replaced() -> None:
    """The caller keeps using this page afterwards (cookie forcing, HTML capture)."""
    page = _FakePage([_playwright_error(ABORTED), LANDED])
    sentinel = MagicMock()
    page.marker = sentinel  # type: ignore[attr-defined]
    wait_for_login_landing(page, timeout_s=300)
    assert page.marker is sentinel  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# End to end through the capture core
# ---------------------------------------------------------------------------


class _EndToEndIO:
    def __init__(self) -> None:
        self.emitted: list[Any] = []

    def emit(self, *args: Any, **kwargs: Any) -> None:
        self.emitted.append(args)

    def fail(self, code: int) -> Any:
        raise AssertionError(f"unexpected io.fail({code})")

    def run_async(self, coro: Any) -> Any:  # pragma: no cover - unused here
        raise AssertionError("run_async not used by the neutral core")


class _FakeSyncPlaywright:
    def __init__(self, playwright: Any) -> None:
        self._playwright = playwright

    def __enter__(self) -> Any:
        return self._playwright

    def __exit__(self, *exc: Any) -> bool:
        return False


@pytest.mark.requires_playwright
def test_interactive_login_survives_an_aborted_navigation(tmp_path: Any) -> None:
    """The reported failure, end to end: it must capture instead of exiting 2.

    Before #2257 the ``net::ERR_ABORTED`` below propagated out of
    ``run_browser_capture`` to the CLI's generic handler ("Unexpected error …
    please report a bug", exit 2) and no ``storage_state.json`` was written —
    even though the browser reached the accepted host immediately afterwards.
    """
    from pathlib import Path
    from unittest.mock import patch

    from notebooklm._auth.browser_capture import BrowserCapturePlan, run_browser_capture

    profile = Path(tmp_path) / "browser_profile"
    profile.mkdir()
    storage = Path(tmp_path) / "storage_state.json"

    page = MagicMock()
    page.url = SIGNING_IN
    page.content.return_value = "<html></html>"
    page.on.side_effect = lambda event, handler: None

    calls = {"n": 0}

    def _abort_then_land(*_args: Any, **_kwargs: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise _playwright_error(ABORTED)
        page.url = LANDED

    page.wait_for_url.side_effect = _abort_then_land

    context = MagicMock()
    context.pages = [page]
    context.storage_state.return_value = {
        "cookies": [
            {"name": "SID", "value": "sid", "domain": ".google.com", "path": "/"},
            {
                "name": "__Secure-1PSIDTS",
                "value": "psidts",
                "domain": ".google.com",
                "path": "/",
            },
        ],
        "origins": [],
    }
    playwright = MagicMock()
    playwright.chromium.launch_persistent_context.return_value = context

    io = _EndToEndIO()
    with patch(
        "playwright.sync_api.sync_playwright",
        side_effect=lambda: _FakeSyncPlaywright(playwright),
    ):
        run_browser_capture(
            BrowserCapturePlan(
                browser="chromium",
                browser_profile=profile,
                storage_path=storage,
            ),
            io,
            headless=False,
            interactive=True,
        )

    assert calls["n"] == 2, "the wait must have been re-armed after the aborted hop"
    assert storage.exists(), "the completed sign-in must be persisted, not discarded"
    flattened = " ".join(str(a) for args in io.emitted for a in args)
    assert "Login detected" in flattened


@pytest.mark.requires_playwright
def test_headless_reauth_never_trusts_a_stale_url_after_failed_navigations() -> None:
    """A cancelled goto must not let headless re-auth persist unvalidated cookies.

    The interactive arm may fall through to its landing check after repeated
    aborted navigations, because a human is still signing in and the wait
    re-reads the URL. The headless arm has no such recovery: with nothing
    committed, ``page.url`` is whatever a restored tab happened to show. If that
    is already a NotebookLM URL, every check passes and re-auth reports success
    while writing cookies it never validated.
    """
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from unittest.mock import patch

    from playwright.sync_api import Error as PlaywrightError

    from notebooklm._auth.browser_capture import BrowserCapturePlan, run_browser_capture

    with TemporaryDirectory() as tmp:
        profile = Path(tmp) / "browser_profile"
        profile.mkdir()
        storage = Path(tmp) / "storage_state.json"

        page = MagicMock()
        # A restored tab already showing the app host — the trap.
        page.url = LANDED
        page.content.return_value = "<html></html>"
        page.goto.side_effect = _playwright_error(ABORTED)

        context = MagicMock()
        context.pages = [page]
        context.storage_state.return_value = {"cookies": [], "origins": []}
        playwright = MagicMock()
        playwright.chromium.launch_persistent_context.return_value = context

        with (
            patch(
                "playwright.sync_api.sync_playwright",
                side_effect=lambda: _FakeSyncPlaywright(playwright),
            ),
            pytest.raises(PlaywrightError),
        ):
            run_browser_capture(
                BrowserCapturePlan(
                    browser="chromium",
                    browser_profile=profile,
                    storage_path=storage,
                ),
                _EndToEndIO(),
                headless=True,
                interactive=False,
            )

        assert not storage.exists(), (
            "headless re-auth must not persist cookies when no navigation committed"
        )
