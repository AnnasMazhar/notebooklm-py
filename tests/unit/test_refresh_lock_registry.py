"""Tests for the single-flight core that replaced the refresh lock registry.

c-PR2 relocated the per-loop / per-resolved-storage-path refresh coalescing
machinery (``_REFRESH_LOCKS_BY_LOOP`` / ``_get_refresh_lock`` /
``_REFRESH_GENERATIONS`` / the per-loop in-flight future map) into the shared
``notebooklm._auth.single_flight`` core. This suite is the case-for-case
migration of the deleted white-box suite: every pinned GUARANTEE is re-expressed
against the new surface —

* per-loop *lock identity* → per-key *flight claim identity* (one leader per
  ``flight_key`` while in flight; a settled flight is overwritable);
* the path-canonicalization equivalence (relative / symlink representations of
  one file collapse to a single refresh key);
* the ``_REFRESH_GENERATIONS`` cross-loop guard → the process-global per-path
  ``success_epoch`` (bumped on subprocess success only), and the stronger
  cross-loop-coalescing guarantee the shared core now provides: two event loops
  racing the same refresh coalesce onto EXACTLY ONE subprocess.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import httpx
import pytest

from notebooklm import auth as auth_mod
from notebooklm._auth import refresh as _auth_refresh
from notebooklm._auth import single_flight as _single_flight


@pytest.fixture(autouse=True)
def _clear_single_flight_state():
    """Reset the process-global single-flight core between tests."""
    _single_flight._reset_for_tests()
    yield
    _single_flight._reset_for_tests()


class TestFlightClaimIdentity:
    """Per-``flight_key`` claim identity replaces the per-loop lock identity.

    The old contract pinned that ``_get_refresh_lock`` returned the same
    ``asyncio.Lock`` for one (loop, path) and distinct locks for distinct keys.
    The single-flight analogue: while a flight is in flight, every claimant of
    the same ``flight_key`` follows the SAME leader flight; distinct keys get
    distinct flights; a settled flight is overwritable by the next leader.
    """

    def test_second_claim_same_key_follows_leader(self):
        async def _run():
            gate = asyncio.Event()

            async def _leader_body():
                await gate.wait()
                return "done"

            is_leader_1, flight_1 = _single_flight.claim(("k", "p"), _leader_body)
            is_leader_2, flight_2 = _single_flight.claim(("k", "p"), _leader_body)
            # First claim leads; the second (while in flight) follows the SAME
            # flight rather than spawning a second leader.
            assert is_leader_1 is True
            assert is_leader_2 is False
            assert flight_2 is flight_1
            gate.set()
            await _single_flight.await_flight(flight_1)

        asyncio.run(_run())

    def test_distinct_keys_get_distinct_flights(self):
        async def _run():
            gate = asyncio.Event()

            async def _leader_body():
                await gate.wait()

            is_leader_a, flight_a = _single_flight.claim(("path-a", "p"), _leader_body)
            is_leader_b, flight_b = _single_flight.claim(("path-b", "p"), _leader_body)
            assert is_leader_a is True
            assert is_leader_b is True
            assert flight_a is not flight_b
            gate.set()
            await asyncio.gather(
                _single_flight.await_flight(flight_a),
                _single_flight.await_flight(flight_b),
            )

        asyncio.run(_run())

    def test_settled_flight_is_overwritable_by_next_leader(self):
        async def _run():
            calls = 0

            async def _leader_body():
                nonlocal calls
                calls += 1

            is_leader_1, flight_1 = _single_flight.claim(("k", "p"), _leader_body)
            assert is_leader_1 is True
            await _single_flight.await_flight(flight_1)
            # Once settled, a fresh claim becomes a NEW leader (the prior cycle's
            # flight does not pin the slot forever).
            is_leader_2, flight_2 = _single_flight.claim(("k", "p"), _leader_body)
            assert is_leader_2 is True
            assert flight_2 is not flight_1
            await _single_flight.await_flight(flight_2)
            assert calls == 2

        asyncio.run(_run())


class TestSuccessEpoch:
    """``success_epoch`` relocates the ``_REFRESH_GENERATIONS`` semantics."""

    def test_note_success_bumps_per_path_key(self):
        assert _single_flight.read_success_epoch("A") == 0
        _single_flight.note_success("A")
        assert _single_flight.read_success_epoch("A") == 1
        # Keyed per canonical PATH only — a different path is independent.
        assert _single_flight.read_success_epoch("B") == 0
        _single_flight.note_success("A")
        assert _single_flight.read_success_epoch("A") == 2


class TestClaimIfEpochCurrent:
    """Compare-under-exclusion: the epoch compare + the claim are one lock hold."""

    def test_skips_when_epoch_already_advanced(self):
        async def _run():
            ran = False

            async def _body():
                nonlocal ran
                ran = True

            # A sibling already succeeded (epoch 1) before this caller (which
            # captured epoch_before=0) reaches the claim.
            _single_flight.note_success("path")
            claimed = _single_flight.claim_if_epoch_current(
                ("path", "pol"), _body, path_key="path", epoch_before=0
            )
            assert claimed is None, "stale epoch_before must produce a skip signal"
            # No leader task was created, so the factory never runs.
            await asyncio.sleep(0)
            assert ran is False
            assert _single_flight._FLIGHTS == {}

        asyncio.run(_run())

    def test_claims_when_epoch_unchanged(self):
        async def _run():
            ran = False

            async def _body():
                nonlocal ran
                ran = True

            claimed = _single_flight.claim_if_epoch_current(
                ("path", "pol"), _body, path_key="path", epoch_before=0
            )
            assert claimed is not None
            is_leader, flight = claimed
            assert is_leader is True
            await _single_flight.await_flight(flight)
            assert ran is True

        asyncio.run(_run())


class TestDoubleCancelDoesNotDetonateBridge:
    """A SECOND cancellation during the settle window must not cancel the bridge.

    Regression for the un-shielded settle-loop bug: ``asyncio.wrap_future``
    chains cancellation to its source, so a repeated cancel arriving while the
    leader subprocess is still PENDING would call ``bridge.cancel()`` — discarding
    the leader's real result and detonating every sibling follower with a
    spurious ``CancelledError`` (#621). ``await_flight`` shields the settle-loop
    await, so the repeated cancel is absorbed WITHOUT cancelling the bridge.
    """

    def test_second_cancel_while_pending_preserves_bridge_and_siblings(self):
        async def _run():
            leader_gate = asyncio.Event()

            async def _leader_body():
                await leader_gate.wait()
                return "leader-result"

            # Leader claim creates the driving task; a follower shares it.
            is_leader, flight = _single_flight.claim(("k", "p"), _leader_body)
            assert is_leader is True

            follower_result: list[str] = []

            async def _follower():
                is_l2, f2 = _single_flight.claim(("k", "p"), _leader_body)
                assert is_l2 is False and f2 is flight
                follower_result.append(await _single_flight.await_flight(f2))

            follower_task = asyncio.ensure_future(_follower())

            async def _awaiter():
                await _single_flight.await_flight(flight)

            awaiter_task = asyncio.ensure_future(_awaiter())
            await asyncio.sleep(0)  # let both start awaiting the pending bridge

            # Cancel the awaiter TWICE while the bridge is still PENDING (the
            # leader body is blocked on the gate).
            awaiter_task.cancel()
            await asyncio.sleep(0)  # 1st cancel → enters the settle loop
            awaiter_task.cancel()
            await asyncio.sleep(0)  # 2nd cancel → absorbed inside the settle loop

            # The repeated cancel must NOT have cancelled the shared bridge.
            assert not flight.bridge.cancelled(), (
                "second cancel detonated the shared bridge (un-shielded settle loop)"
            )

            # Release the leader; siblings must still receive the REAL result.
            leader_gate.set()
            with pytest.raises(asyncio.CancelledError):
                await awaiter_task
            await follower_task

            assert not flight.bridge.cancelled()
            assert follower_result == ["leader-result"], (
                "a sibling follower must still get the leader's real result, "
                "not a spurious CancelledError"
            )

        asyncio.run(_run())


class TestResolvedPathEquivalence:
    """Two surface representations of one file share a single refresh key.

    ``_fetch_tokens_with_refresh`` canonicalizes via
    :func:`notebooklm._auth.paths.canonical_storage_key` before keying the
    flight / success-epoch, so a symlinked path and the canonical real path (and
    relative vs absolute) flow to the SAME key.
    """

    def test_symlink_and_real_path_share_refresh_key(self, monkeypatch, tmp_path):
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        real_path = real_dir / "storage_state.json"
        real_path.write_text('{"cookies": [], "origins": []}')

        link_dir = tmp_path / "via_link"
        link_dir.symlink_to(real_dir, target_is_directory=True)
        symlinked_path = link_dir / "storage_state.json"

        assert symlinked_path != real_path
        assert symlinked_path.resolve() == real_path.resolve()

        captured_keys: list[str] = []

        async def spy_coalesced(refresh_key, resolved_storage_path, profile):
            captured_keys.append(refresh_key)
            return None

        monkeypatch.setenv(auth_mod.NOTEBOOKLM_REFRESH_CMD_ENV, "dummy")
        monkeypatch.setattr(_auth_refresh, "_coalesced_run_refresh_cmd", spy_coalesced)

        call_phase = {"first": True}

        async def fake_fetch_tokens_with_jar(jar, path, **kwargs):
            if call_phase["first"]:
                call_phase["first"] = False
                raise ValueError("Authentication expired. Run 'notebooklm login'.")
            return "csrf-token", "session-id"

        def fake_build(_p):
            return httpx.Cookies()

        def fake_snapshot(_j):
            return None

        monkeypatch.setattr(_auth_refresh, "_fetch_tokens_with_jar", fake_fetch_tokens_with_jar)
        monkeypatch.setattr(_auth_refresh, "build_httpx_cookies_from_storage", fake_build)
        monkeypatch.setattr(_auth_refresh, "snapshot_cookie_jar", fake_snapshot)

        async def drive(path: Path):
            jar = httpx.Cookies()
            return await auth_mod._fetch_tokens_with_refresh(jar, storage_path=path)

        asyncio.run(drive(symlinked_path))
        call_phase["first"] = True
        asyncio.run(drive(real_path))

        assert len(captured_keys) == 2
        assert captured_keys[0] == captured_keys[1], (
            f"Symlinked and direct paths produced distinct keys: {captured_keys!r}"
        )
        assert captured_keys[0] == str(real_path.resolve())


class TestPromptPopRetention:
    """Settled flights are popped from the registry (prompt-pop retention).

    The old suite pinned that the per-loop WeakKeyDictionary reclaimed closed
    loops. The process-global core instead removes a flight the moment it
    settles, so the registry does not accumulate stale entries across cycles.
    """

    def test_registry_does_not_accumulate_across_cycles(self):
        async def _run():
            async def _leader_body():
                return None

            for _ in range(5):
                _is_leader, flight = _single_flight.claim(("k", "p"), _leader_body)
                await _single_flight.await_flight(flight)
                # Yield so the task done-callbacks (mirror + pop) run.
                await asyncio.sleep(0)

            assert _single_flight._FLIGHTS == {}, (
                "Settled flights must be popped from the process-global registry"
            )

        asyncio.run(_run())


class TestCrossLoopCoalescing:
    def test_two_loops_share_exactly_one_subprocess(self, monkeypatch, tmp_path):
        """Two event loops racing the same refresh coalesce onto ONE subprocess.

        The deleted contract allowed 1–2 subprocess runs because cross-loop
        callers could not signal "in flight" to each other (``asyncio.Future``
        was loop-bound). The single-flight core's process-global registry +
        ``concurrent.futures.Future`` bridge now genuinely coalesces across
        loops: the leader runs the ONE subprocess and the other loop follows it
        via ``asyncio.wrap_future``. So the invariant tightens to EXACTLY ONE
        subprocess, both callers succeed, and the success epoch bumps once.
        """
        storage = tmp_path / "storage_state.json"
        storage.write_text('{"cookies": [], "origins": []}')

        monkeypatch.setenv(auth_mod.NOTEBOOKLM_REFRESH_CMD_ENV, "dummy")

        run_count = 0
        run_count_lock = threading.Lock()
        # Align both threads at the refresh-branch so neither wins the leadership
        # claim before the other has entered.
        fail_barrier = threading.Barrier(2, timeout=5)

        async def fake_run_refresh_cmd(storage_path, profile):
            nonlocal run_count
            with run_count_lock:
                run_count += 1
            await asyncio.sleep(0.01)

        async def fake_fetch_tokens_with_jar(cookie_jar, storage_path, **kwargs):
            if not getattr(cookie_jar, "_refresh_done", False):
                cookie_jar._refresh_done = True
                try:
                    fail_barrier.wait()
                except threading.BrokenBarrierError:
                    pass
                raise ValueError("Authentication expired. Run 'notebooklm login'.")
            return "csrf-token", "session-id"

        def fake_build_httpx_cookies(path):
            return httpx.Cookies()

        def fake_snapshot(jar):
            return None

        monkeypatch.setattr(_auth_refresh, "_run_refresh_cmd", fake_run_refresh_cmd)
        monkeypatch.setattr(_auth_refresh, "_fetch_tokens_with_jar", fake_fetch_tokens_with_jar)
        monkeypatch.setattr(
            _auth_refresh, "build_httpx_cookies_from_storage", fake_build_httpx_cookies
        )
        monkeypatch.setattr(_auth_refresh, "snapshot_cookie_jar", fake_snapshot)

        results: list[BaseException | tuple] = []
        results_lock = threading.Lock()

        def run_in_own_loop():
            async def _work():
                jar = httpx.Cookies()
                return await auth_mod._fetch_tokens_with_refresh(jar, storage_path=storage)

            try:
                res = asyncio.run(_work())
                with results_lock:
                    results.append(res)
            except BaseException as exc:  # noqa: BLE001
                with results_lock:
                    results.append(exc)

        thread_a = threading.Thread(target=run_in_own_loop)
        thread_b = threading.Thread(target=run_in_own_loop)
        thread_a.start()
        thread_b.start()
        thread_a.join(timeout=15)
        thread_b.join(timeout=15)

        assert not thread_a.is_alive() and not thread_b.is_alive(), (
            "Threads failed to terminate; likely a deadlock"
        )
        assert len(results) == 2
        for r in results:
            assert not isinstance(r, BaseException), f"Refresh raised: {r!r}"

        # Cross-loop coalescing: exactly ONE subprocess across both loops.
        assert run_count == 1, (
            f"Expected exactly 1 cross-loop-coalesced refresh invocation, observed "
            f"{run_count}. ``0`` would mean a phantom-skip regression; ``>1`` means "
            "the cross-loop coalescing (process-global flight registry) regressed to "
            "the old per-loop behavior."
        )
        # Success epoch bumped exactly once (subprocess succeeded once).
        assert _single_flight.read_success_epoch(str(storage.expanduser().resolve())) == 1
