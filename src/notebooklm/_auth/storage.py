"""Profile persistence for authentication: storage state, its lock, and its writers.

One deep module for the whole ``storage_state.json`` seam. It merges what used to
be three cap-split files — ``storage.py`` (snapshot/CAS merge math + the file-lock
primitive), ``storage_writer.py`` (the canonical writer, ADR-0029) and
``storage_transaction.py`` (the write transaction template, ADR-0031 Stage 3) —
under ADR-0033's sanctioned-merge policy. The split existed only to satisfy the
ADR-0008 module-size cap, and it was re-joined at runtime by function-local
imports in both directions; merging removes those without changing any behaviour.

The file is organised in labelled sections mirroring the former modules:

1. **Lock primitives** — the contention/backoff tuning shared by both bounded
   acquire paths, the per-path in-process lock registry, the OS-level acquire,
   :func:`_file_lock` and the blocking :func:`_file_lock_exclusive`.
2. **Lock acquisition for writers** — the secure-parent-dir prep and the
   platform-neutral bounded :func:`_acquire_storage_lock`. (Lock *path*
   derivation itself stays in :mod:`notebooklm._auth.paths`:
   :func:`_storage_state_lock_path`.)
3. **The storage-write transaction template** — :func:`in_storage_transaction`
   plus the three lock-unavailable policies.
4. **Snapshot types** — the path-aware cookie identity/value tuples and
   :class:`CookieSaveResult`.
5. **CAS + merge math** — snapshotting, the legacy and snapshot/delta merges, and
   :func:`save_cookies_to_storage`, the ADR-0029-pinned monkeypatchable delegate
   seam (``_runtime/lifecycle.py`` late-binds it; ~20 test files patch it).
6. **Writer outcome types** — the value-free enums/records the intent writers
   return.
7. **The intent writers** — the seven sanctioned mutations of
   ``storage_state.json`` and its sibling credential files.

This module is the **single sanctioned home** for mutations of
``storage_state.json``. It is the only module under :mod:`notebooklm._auth`
permitted to import the ``_atomic_io`` write primitives, and it reaches the
module-private bypass under the local alias ``_write_state_unchecked``. The
boundary is enforced by ``tests/_guardrails/test_storage_writer_boundary.py``,
which since ADR-0033 pins it at **function** granularity: an equality-asserted
allowlist of the intent-writer function names permitted to reach the bypass
(a module-granular assertion over a module this size would say almost nothing).

Intent-shaped API (all synchronous, all serialize on the canonical storage lock,
all write via ``_atomic_io``):

* :func:`merge_cookie_delta` — the CAS delta merge behind
  :func:`save_cookies_to_storage`. It is a **CAS** intent and therefore **fails
  open** on lock unavailability (status quo): availability wins, and the
  snapshot/delta CAS guards keep correctness.
* :func:`update_account_metadata` / :func:`clear_in_band_account` — the in-band
  account writers relocated from :mod:`notebooklm._auth.account`. These are
  **full-file RMW** intents: :func:`update_account_metadata` **fails closed**
  (raises :class:`LockUnavailableError`) because failing open could overwrite a
  concurrent CAS delta; :func:`clear_in_band_account` is best-effort cleanup and
  swallows lock unavailability, matching the pre-refactor semantics.
* :func:`replace_from_remint` — the full cookie-replace re-mint persister for the
  BROWSER-CAPTURE arms (L3 headless-launch + interactive + CDP), relocated from
  the bare ``atomic_write_json`` sites in :mod:`notebooklm._auth.browser_capture`.
  Applies the write-time domain filter internally under the lock, then either
  carries the existing ``notebooklm`` account namespace (``carry_account=True`` —
  the unattended profile-launch arm, closing [capture-1]) or drops the stale
  binding (``carry_account=False`` — the interactive arm, whose CLI adapter
  re-establishes it). **Fails closed** (returns
  :class:`WriteOutcome` with ``lock_unavailable``). Closes [capture-2].
* :func:`replace_from_login` — the login/import full-replace, whose write-time
  domain filter and required-cookie revalidation run inside the lock.
  **Fails closed.**
* :func:`persist_minted_jar` — the master-token L4 re-mint persister relocated
  from :mod:`notebooklm._auth.master_token`, routed through ``_atomic_io`` (so it
  gains fsync durability + temp cleanup) while keeping its storage lock and its
  rebind-to-minted-account semantics. b-PR2 adds the write-time domain filter
  here (the L4 unfiltered-persist gap). **Fails closed.**
* :func:`write_master_token` — the ``master_token.json`` writer, now routed
  through ``_atomic_io`` **and** guarded by a bounded sibling lock (it was
  previously lockless). **Fails closed.**

Lock unification (see ADR-0029): the full-file RMW / re-mint intents drop
``filelock`` in favour of the project-internal :func:`_file_lock` primitive
via a **platform-neutral bounded acquire** (:func:`_acquire_storage_lock`):
a non-blocking probe plus deadline/jitter retry (default 90 s), then the
per-intent failure policy above. The CAS merge keeps the status-quo blocking
:func:`_file_lock_exclusive` acquire (fail-open). An in-process ``threading.Lock``
keyed per canonical lock-path (ordering: in-process lock -> OS lock) is added in
:func:`_file_lock` itself so threads within one process serialize before the
OS lock; the distinct ``.{name}.rotate.lock`` sentinel is never collapsed into
the storage lock.

The fail-closed writers raise :class:`~notebooklm.exceptions.LockUnavailableError`
(public via ``notebooklm.exceptions`` / the ``notebooklm.auth`` facade). It
subclasses :class:`TimeoutError` — itself an :class:`OSError` — exactly mirroring
the ``filelock.Timeout`` MRO it replaces, so callers' existing
``except OSError`` / ``except TimeoutError`` arms (``_auth/recovery.py`` around
``persist_minted_jar``; the CLI login writers around ``write_account_metadata``)
keep catching a lock failure unchanged; only the exception type and the 10 s->90 s
bound differ.

Permission contract (POSIX): every writer ensures the parent directory is
``0700`` on creation and the file is ``0600`` (the latter via the atomic write's
default mode). On Windows we rely on ``%USERPROFILE%`` ACL inheritance.

Outcome types are **value-free by contract**: :class:`WriteOutcome` may carry
only an enum status — never cookie values, state dicts, jar objects, or caught
exceptions.
"""

from __future__ import annotations

import contextlib
import errno
import json
import logging
import os
import random
import shutil
import sys
import threading
import time
import warnings
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, NamedTuple, Protocol, TypeAlias

import httpx

# This module is the SOLE sanctioned user of the module-private
# ``_atomic_write_json_unchecked`` bypass: the public ``atomic_write_json``
# rejects ``storage_state.json`` paths (#1215-style runtime guard, b-PR3), and
# the writers below legitimately write them under the canonical dotted lock.
# Bound as ``_write_state_unchecked`` — the former alias spelled it
# ``atomic_write_json``, colliding with the name of the public primitive the
# guard protects (ADR-0033 decision 2). The boundary is enforced at function
# granularity in ``tests/_guardrails/test_storage_writer_boundary.py``.
from .._atomic_io import _atomic_write_json_unchecked as _write_state_unchecked

# ``LockUnavailableError`` is the public, canonical home for the fail-closed
# lock-failure exception (``notebooklm.exceptions`` — also re-exported on the
# ``notebooklm.auth`` facade). It subclasses ``TimeoutError`` (an ``OSError``),
# exactly mirroring the ``filelock.Timeout`` MRO it replaces, so existing
# ``except OSError`` arms keep catching a lock failure. Re-exported here for the
# writers that raise it.
from ..exceptions import LockUnavailableError
from . import cookie_policy as _cookie_policy
from . import cookies as _auth_cookies
from .paths import _storage_state_lock_path, resolve_auth_json_env

logger = logging.getLogger("notebooklm.auth")

CookieKey: TypeAlias = _auth_cookies.CookieKey
_cookie_is_http_only = _auth_cookies._cookie_is_http_only
_cookie_key_variants = _auth_cookies._cookie_key_variants
_cookie_to_storage_state = _auth_cookies._cookie_to_storage_state
_find_cookie_for_storage = _auth_cookies._find_cookie_for_storage
_is_allowed_cookie_domain = _cookie_policy._is_allowed_cookie_domain
# Recovery-target rows: one definition in the ``cookie_policy`` leaf, shared
# with ``psidts_recovery`` (which observes these rows before the RotateCookies
# POST that produces the deltas ``_merge_recovery_target_rows`` below merges).
_RECOVERY_TARGET_COOKIE_NAMES = _cookie_policy._RECOVERY_TARGET_COOKIE_NAMES

__all__ = [
    "CLEAR_ACCOUNT",
    "KEEP_ACCOUNT",
    "AccountRecord",
    "CookieSaveResult",
    "LockUnavailableError",
    "LoginWriteOutcome",
    "LoginWriteStatus",
    "WriteOutcome",
    "WriteStatus",
    "advance_cookie_snapshot_after_save",
    "clear_in_band_account",
    "in_storage_transaction",
    "merge_cookie_delta",
    "persist_minted_jar",
    "raise_on_lock_unavailable",
    "replace_from_login",
    "replace_from_remint",
    "report_on_lock_unavailable",
    "save_cookies_to_storage",
    "skip_on_lock_unavailable",
    "snapshot_cookie_jar",
    "update_account_metadata",
    "write_master_token",
]


# ==========================================================================
# SECTION 1 — LOCK PRIMITIVES
# Contention classification, bounded-acquire tuning, the per-path in-process
# lock registry, the OS-level acquire, and the two file-lock context managers.
# ==========================================================================


# Errnos that a non-blocking lock acquire raises to mean "held elsewhere"
# (contended), NOT "infrastructure broken". EWOULDBLOCK/EAGAIN are the POSIX
# ``flock(LOCK_NB)`` contention signals. ``EACCES`` is here specifically because
# it is the errno Windows ``msvcrt.locking(LK_NBLCK)`` raises under contention —
# POSIX ``flock`` never returns EACCES for contention, and a POSIX *permission*
# failure surfaces earlier at the ``os.open`` step (yielded as "unavailable").
# So do NOT drop EACCES to "fix" it: on Windows that would misclassify real
# contention as an infrastructure failure (fail-open) instead of a skip.
_LOCK_CONTENTION_ERRNOS = {errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES}


# --- Bounded-acquire tuning (single source of truth) ------------------------
#
# Shared by BOTH bounded acquire paths so they honour the same deadline and the
# same jittered exponential backoff:
#   * the blocking Windows ``msvcrt`` retry loop in ``_acquire_os_lock`` below
#     ([storage-F4]: Windows has no blocking-without-internal-timeout primitive,
#     so the blocking path drives ``LK_NBLCK`` probes to this deadline instead
#     of letting ``LK_LOCK`` fail open after its internal ~10x1s), and
#   * :func:`_acquire_storage_lock` (the non-blocking-probe bounded helper that
#     the fail-closed RMW / re-mint writers use), in section 2 below.
# 90 s is a generous worst-case wait that still bounds a crashed/wedged holder.
# See ADR-0029.
_LOCK_ACQUIRE_DEADLINE_SECONDS = 90.0
_LOCK_ACQUIRE_INITIAL_DELAY_SECONDS = 0.01
_LOCK_ACQUIRE_MAX_DELAY_SECONDS = 0.5


def _sleep_backoff(delay: float, deadline: float) -> float | None:
    """Sleep one jittered exponential-backoff step of a bounded-acquire loop.

    The single home for the deadline-check + jitter + sleep + delay-bump
    arithmetic shared by BOTH bounded-acquire loops — the Windows ``msvcrt``
    retry in :func:`_acquire_os_lock` below and
    :func:`_acquire_storage_lock` — so future tuning edits one site
    (b-PR4 review NIT). Behaviour is identical to the two former inline copies:
    equal jitter (``delay + U[0, delay]``) clamped to the remaining budget,
    then ``delay`` doubled and capped at :data:`_LOCK_ACQUIRE_MAX_DELAY_SECONDS`.

    Returns the next ``delay`` to use, or ``None`` when the ``deadline`` has
    already elapsed — the caller must then stop retrying and fall through to
    ``"unavailable"`` (each caller keeps its own site-specific give-up log line).
    """
    now = time.monotonic()
    if now >= deadline:
        return None
    sleep_for = min(delay + random.uniform(0.0, delay), max(0.0, deadline - now))
    time.sleep(sleep_for)
    return min(delay * 2, _LOCK_ACQUIRE_MAX_DELAY_SECONDS)


# In-process lock registry, keyed per canonical lock-path (never global — distinct
# profiles and the rotate sentinel must not couple). Acquired BEFORE the OS lock
# (ordering: in-process lock -> OS lock) so threads within one process serialize
# on a storage sentinel before touching the OS flock, which both bounds Windows
# ``msvcrt`` contention and lets the non-blocking rotate path observe an
# in-process holder as "contended" without an OS round-trip. See ADR-0029.
_INPROCESS_LOCKS: dict[str, threading.Lock] = {}
_INPROCESS_LOCKS_GUARD = threading.Lock()


def _inprocess_lock_for(lock_path: Path) -> threading.Lock:
    """Return the process-wide :class:`threading.Lock` for ``lock_path``."""
    key = os.fspath(lock_path)
    with _INPROCESS_LOCKS_GUARD:
        lock = _INPROCESS_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _INPROCESS_LOCKS[key] = lock
        return lock


def _acquire_os_lock(fd: int, *, blocking: bool, log_prefix: str) -> str:
    """Acquire the OS-level exclusive lock on ``fd``; return the tristate.

    Returns one of ``"held"`` / ``"contended"`` / ``"unavailable"``. The caller
    (:func:`_file_lock`) has already taken the per-path in-process
    :class:`threading.Lock` (ordering: in-process lock -> OS lock), so any
    contention observed here is from **another process**, never another thread in
    this process.

    * **POSIX** — ``flock(LOCK_EX)`` when blocking (a kernel-level wait: unbounded
      but non-spinning, unchanged), ``LOCK_EX | LOCK_NB`` when non-blocking.
    * **Windows** — ``msvcrt`` has no blocking-without-internal-timeout primitive:
      the blocking ``LK_LOCK`` mode gives up after ~10x1s and would fail open
      long before the 90 s deadline ([storage-F4]). So the Windows **blocking**
      path drives a bounded deadline retry over the **non-blocking** ``LK_NBLCK``
      probe using the same jittered exponential backoff as
      :func:`_acquire_storage_lock`, retrying **only** on the
      contention errno and falling through to ``"unavailable"`` when the deadline
      elapses (never ``while True`` without a deadline break). A non-contention
      errno (``EBADF`` etc.) falls through immediately with **no** retry spin.
      Windows non-blocking is a single ``LK_NBLCK`` probe.
    """
    if sys.platform != "win32":
        import fcntl

        op = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        try:
            fcntl.flock(fd, op)
            return "held"
        except OSError as exc:
            if not blocking and exc.errno in _LOCK_CONTENTION_ERRNOS:
                logger.debug("%s: lock contended (%s)", log_prefix, type(exc).__name__)
                return "contended"
            logger.debug("%s: lock op unavailable (%s)", log_prefix, type(exc).__name__)
            return "unavailable"

    import msvcrt

    deadline = time.monotonic() + _LOCK_ACQUIRE_DEADLINE_SECONDS
    delay = _LOCK_ACQUIRE_INITIAL_DELAY_SECONDS
    while True:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return "held"
        except OSError as exc:
            if exc.errno not in _LOCK_CONTENTION_ERRNOS:
                # EBADF and other non-contention errnos: retrying cannot help.
                # Fall through immediately — no spin.
                logger.debug("%s: lock op unavailable (%s)", log_prefix, type(exc).__name__)
                return "unavailable"
            if not blocking:
                # Non-blocking caller: another process holds the byte-range lock
                # (in-process contention was already resolved by the threading
                # lock in _file_lock). Report the skip signal without retrying.
                logger.debug("%s: lock contended (%s)", log_prefix, type(exc).__name__)
                return "contended"
            # Blocking caller under contention: retry the non-blocking probe with
            # jittered exponential backoff until the bounded deadline, then fall
            # through to "unavailable" so the caller applies its per-intent fail
            # policy (CAS fail-open with a one-shot warning).
            next_delay = _sleep_backoff(delay, deadline)
            if next_delay is None:
                logger.debug(
                    "%s: bounded msvcrt lock acquire exceeded %.0fs deadline; giving up",
                    log_prefix,
                    _LOCK_ACQUIRE_DEADLINE_SECONDS,
                )
                return "unavailable"
            delay = next_delay


@contextlib.contextmanager
def _file_lock(lock_path: Path, *, blocking: bool, log_prefix: str) -> Iterator[str]:
    """Cross-process exclusive lock on ``lock_path``.

    Yields one of:
      - ``"held"``  — the lock is held; release it on exit.
      - ``"contended"`` — non-blocking acquire saw the lock held elsewhere
        (by another in-process thread OR another process). Only ever yielded
        when ``blocking=False``.
      - ``"unavailable"`` — lock infrastructure failed (cannot mkdir, cannot
        open the sentinel, NFS without flock support). Caller should
        **fail open** (proceed without coordination) rather than retry forever.

    Wrappers translate this tristate into bool. Distinguishing contention from
    infrastructure failure matters: a non-blocking caller should **skip** on
    contention (someone else is rotating) but **proceed** on infrastructure
    failure (otherwise a read-only auth dir would permanently suppress
    rotation).

    Locking order is **in-process lock -> OS lock**: the per-path
    :class:`threading.Lock` is taken first (blockingly for ``blocking=True``,
    non-blockingly for ``blocking=False`` where a failed acquire maps straight to
    ``"contended"``), then the OS-level flock/``msvcrt`` lock. The in-process
    lock is released last.
    """
    inprocess_lock = _inprocess_lock_for(lock_path)
    if not inprocess_lock.acquire(blocking=blocking):
        # Only reachable with ``blocking=False``: another thread in this process
        # holds the sentinel. Report contention without touching the OS lock.
        logger.debug("%s: in-process lock contended", log_prefix)
        yield "contended"
        return
    try:
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as exc:
            # Read-only directory, permission denied, ENOSPC, etc. Yield
            # "unavailable" so the wrapper can fail open.
            logger.debug(
                "%s: lock file unavailable %s (%s)",
                log_prefix,
                lock_path,
                type(exc).__name__,
            )
            yield "unavailable"
            return
        locked = False
        try:
            # OS-lock acquisition (in-process lock already held above). On Windows
            # the blocking path is a bounded ``LK_NBLCK`` retry to the shared 90 s
            # deadline rather than ``LK_LOCK``'s internal ~10x1s ([storage-F4]).
            state = _acquire_os_lock(fd, blocking=blocking, log_prefix=log_prefix)
            locked = state == "held"
            yield state
        finally:
            if locked:
                try:
                    if sys.platform == "win32":
                        import msvcrt

                        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError as exc:
                    logger.debug(
                        "%s: failed to release file lock (%s)",
                        log_prefix,
                        type(exc).__name__,
                    )
            os.close(fd)
    finally:
        inprocess_lock.release()


# Dedupe contract: best-effort under threads, exactly-once on a single
# event loop. ``_file_lock_exclusive`` below reads ``_FLOCK_UNAVAILABLE_WARNED``
# and sets it to ``True`` in one synchronous block with no intervening
# ``await``, so concurrent coroutines on one loop cannot interleave between
# the check and the set — the warning fires exactly once per process. Under
# genuine OS threads (out of scope per the documented concurrency contract),
# duplicate warnings are possible. We accept that rather than serialize a
# logging side-effect behind a lock for an unsupported configuration.
#
# Note: ``functools.lru_cache`` and ``logging.LoggerAdapter`` do NOT solve
# this — ``lru_cache`` memoizes return values, not the ``logger.warning``
# side-effect; ``LoggerAdapter`` only rewrites records, it does not filter
# duplicates.
_FLOCK_UNAVAILABLE_WARNED = False


@contextlib.contextmanager
def _file_lock_exclusive(lock_path: Path) -> Iterator[None]:
    """Blocking cross-process exclusive lock on ``lock_path``.

    Multiple Python processes that all save to the same ``storage_state.json``
    (e.g. a long-running ``NotebookLMClient(keepalive=...)`` worker plus a
    cron-driven ``notebooklm auth refresh``) would otherwise race on the read-
    merge-write cycle and lose updates. The lock is held on a sentinel file
    sibling to the storage file (``.storage_state.json.lock``, derived by
    :func:`notebooklm._auth.paths._storage_state_lock_path`), since locking the
    storage file itself would interfere with the atomic temp-rename below.

    ``_auth/account.py`` holds this *same* sentinel via ``filelock.FileLock``
    when it writes account metadata into ``storage_state.json``. The two
    mechanisms interoperate because ``filelock.FileLock`` also uses
    ``fcntl.flock`` on POSIX, so an exclusive hold from either side blocks the
    other — that cross-mechanism compatibility is what lets cookie saves and
    account-metadata writes serialize on one file.

    The lock is per-process: threads within one process aren't serialized —
    that's the intra-process ``threading.Lock`` held by the client. If the
    lock can't be acquired (e.g. NFS where flock semantics vary, read-only
    parent dir, fd exhaustion), the save proceeds anyway; correctness in
    that mode is best-effort and relies on the snapshot/delta CAS guards in
    :func:`_merge_cookies_with_snapshot` alone. The first time this
    fallback fires per process emits a WARNING so operators learn their
    deployment is running without cross-process coordination.
    """
    global _FLOCK_UNAVAILABLE_WARNED
    with _file_lock(lock_path, blocking=True, log_prefix="save_cookies_to_storage") as state:
        if state == "unavailable" and not _FLOCK_UNAVAILABLE_WARNED:
            _FLOCK_UNAVAILABLE_WARNED = True
            logger.warning(
                "Cross-process file lock unavailable at %s; cookie saves will "
                "proceed without cross-process coordination and rely solely on "
                "snapshot/delta CAS guards. Common causes: NFS without flock "
                "support, read-only parent directory, fd exhaustion. (Logged "
                "once per process.)",
                lock_path,
            )
        yield


# ==========================================================================
# SECTION 2 — LOCK ACQUISITION FOR THE WRITERS
# Secure-parent-dir prep + the platform-neutral bounded acquire the full-file
# RMW / re-mint intents use. Lock PATH derivation lives in ``paths.py``
# (``_storage_state_lock_path``) and is unchanged.
# ==========================================================================


def _ensure_secure_parent_dir(path: Path) -> None:
    """Ensure ``path.parent`` exists and is ``0700`` on POSIX.

    Closes the master-token path's mode-less ``mkdir(parents=True)`` gap. The
    chmod is applied UNCONDITIONALLY (not only when this call creates the dir),
    restoring the pre-refactor self-heal that ``cli/services/login/cookie_writes.py``
    performed after every successful write: a credentials directory loosened by a
    backup / restore / sync tool (e.g. to 0755) is re-tightened to 0700 on the
    next login / refresh, so session-cookie files never sit under a
    world-traversable parent. Windows is skipped (POSIX modes are a no-op there
    and can confuse ACL inheritance from ``%USERPROFILE%``).
    """
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        with contextlib.suppress(OSError):
            os.chmod(parent, 0o700)


@contextlib.contextmanager
def _acquire_storage_lock(
    lock_path: Path,
    *,
    log_prefix: str,
    deadline_seconds: float = _LOCK_ACQUIRE_DEADLINE_SECONDS,
) -> Iterator[str]:
    """Platform-neutral **bounded** exclusive acquire of a storage sentinel lock.

    Non-blocking probe (via :func:`_file_lock` with ``blocking=False``, which takes
    the per-path in-process ``threading.Lock`` before the OS lock) plus a
    deadline/jitter retry loop. Yields one of:

    * ``"held"`` — the lock is held; released when the ``with`` block exits.
    * ``"unavailable"`` — the deadline elapsed under contention, or the lock
      infrastructure failed (read-only dir, NFS without flock, fd exhaustion).

    The caller maps ``"unavailable"`` to its per-intent policy: fail-open
    callers proceed, fail-closed callers raise :class:`LockUnavailableError`.
    """
    deadline = time.monotonic() + deadline_seconds
    delay = _LOCK_ACQUIRE_INITIAL_DELAY_SECONDS
    while True:
        with _file_lock(lock_path, blocking=False, log_prefix=log_prefix) as state:
            if state == "held":
                yield "held"
                return
            if state == "unavailable":
                # Infrastructure failure — no amount of retrying will help.
                yield "unavailable"
                return
            # state == "contended": another holder (thread or process) has it.
        # Jittered exponential backoff (shared with ``_acquire_os_lock``'s
        # Windows retry via :func:`_sleep_backoff` — one tuning site).
        next_delay = _sleep_backoff(delay, deadline)
        if next_delay is None:
            logger.debug(
                "%s: bounded storage-lock acquire exceeded %.0fs deadline; giving up",
                log_prefix,
                deadline_seconds,
            )
            yield "unavailable"
            return
        delay = next_delay


# ==========================================================================
# SECTION 3 — THE STORAGE-WRITE TRANSACTION TEMPLATE (ADR-0031 Stage 3)
# ``in_storage_transaction`` owns the four-step preamble every writer used to
# hand-roll; the not-held policy is a parameter because it genuinely differs.
# ==========================================================================


# Six of this module's writers each hand-rolled the same four-step preamble —
# secure the parent dir, derive the sentinel lock path, take the bounded lock,
# and branch on whether it was held. **Three of those six route through this
# template today**; the remaining three are pinned in the shrink-only ratchet
# ``tests/_guardrails/test_storage_transaction_ratchet.py`` and convert in a later
# pass. Only the last step differs, and it differs in three genuinely incompatible
# ways, so the policy is a parameter rather than a decision baked into the
# template: a version that picked one behavior would be a silent semantic change
# in a credential-write path.
#
# ``merge_cookie_delta`` deliberately does NOT use this. It takes the BLOCKING
# ``_file_lock_exclusive`` rather than the bounded acquire, and skips the
# parent-dir prep because it only ever updates a file that already exists. Its
# lock semantics are a different operation, not a variant of this one.


#
# THE POLICIES — two intents, three constructors
# ----------------------------------------------
# There are only TWO intents here, and it is worth stating which is which,
# because the surface count of three invites the wrong mental model:
#
#   MUST-KNOW  the write mattered; a caller that proceeds as though it happened
#              is wrong. Five writers. A master token that was not persisted
#              means the mint was wasted; account metadata that was not written
#              means routing silently targets the wrong Google account; a login
#              that was not persisted means the user believes they are signed in.
#
#   TOLERABLE  the write was cleanup; missing it degrades gracefully. One writer.
#
# MUST-KNOW has *two constructors* only because the writers' return channels
# differ in what they can express — not because the intent differs:
#
#   ``-> None``            no channel at all                      -> raise
#   ``-> bool``            ``False`` already means "deliberately   -> raise
#                          skipped (only_if_absent)", so reusing
#                          it would conflate *chose not to* with
#                          *could not*
#   ``-> WriteOutcome``    a rich enum with room for a distinct    -> report
#   ``-> LoginWriteOutcome`` LOCK_UNAVAILABLE status
#
# Each choice is locally forced. The inconsistency lives one level up, in
# writers that do morally identical things having different return types.
# Unifying that means giving every MUST-KNOW writer a rich outcome type, which
# is a breaking change for callers that today catch ``OSError``/``TimeoutError``
# around ``persist_minted_jar`` and ``update_account_metadata`` — a deprecation
# runway, not a refactor stage. Tracked in ADR-0031.


class _LockUnavailablePolicy(Protocol):
    """What a writer does when the storage lock could not be acquired."""

    def __call__(self, lock_path: Path) -> Any: ...


def raise_on_lock_unavailable(operation: str) -> _LockUnavailablePolicy:
    """MUST-KNOW, via exception — for writers with no usable return channel.

    Used where the return type is ``None`` (``persist_minted_jar``,
    ``write_master_token``) or a ``bool`` whose ``False`` already carries a
    different meaning (``update_account_metadata``).
    """

    def _policy(lock_path: Path) -> Any:
        raise LockUnavailableError(f"{operation}: storage lock unavailable at {lock_path}")

    return _policy


def report_on_lock_unavailable(outcome: Any) -> _LockUnavailablePolicy:
    """MUST-KNOW, via return value — for writers with a rich outcome type.

    Same intent as :func:`raise_on_lock_unavailable`; different mechanism only
    because the caller has somewhere unambiguous to put it. The two full-replace
    writers have their OWN outcome types (:class:`WriteOutcome` vs
    :class:`LoginWriteOutcome`), so the value comes from the caller.

    .. note::
       This has **no caller yet** — ``replace_from_login`` and
       ``replace_from_remint`` are the only writers whose return type can carry
       a distinct lock-unavailable status, and both are still unconverted. That
       is pinned rather than merely noted: the ratchet asserts zero callers
       while they are unconverted, and at least one once they are, so this
       helper cannot quietly outlive its reason to exist.
    """

    def _policy(lock_path: Path) -> Any:
        return outcome

    return _policy


def skip_on_lock_unavailable(message: str) -> _LockUnavailablePolicy:
    """TOLERABLE — log at DEBUG and do nothing.

    Args:
        message: a logging format string with **exactly one** ``%s``, which
            receives the lock path. A message with no placeholder (or more than
            one) raises inside ``logging``, which swallows it and prints to
            stderr instead of logging — an unpleasant failure to trace back,
            since it surfaces nowhere near this call.

    The only genuinely different intent, and it has exactly one user today:
    ``clear_in_band_account``. Its justification is functional — a missed clear
    leaves the legacy reader still able to resolve the account record.

    .. note::
       That justification is narrower than the operation's motive. Clearing the
       in-band account is **privacy**-motivated ("a stale key must not leave the
       account email at rest" — see ``auth.py``), and a swallowed failure leaves
       precisely that email on disk until the next successful write. Functional
       degradation is graceful; the privacy miss is silent. Rare — it needs 90 s
       of lock contention or a lock-infrastructure failure — but the swallow is
       justified on a different axis than the one that matters most here.
       Flagged in ADR-0031 rather than changed unilaterally, since promoting it
       to MUST-KNOW would make a best-effort cleanup able to fail a caller.
    """

    def _policy(lock_path: Path) -> Any:
        logger.debug(message, lock_path)
        return None

    return _policy


def in_storage_transaction(
    path: Path,
    body: Callable[[], Any],
    *,
    log_prefix: str,
    on_unavailable: _LockUnavailablePolicy,
    deadline_seconds: float = _LOCK_ACQUIRE_DEADLINE_SECONDS,
) -> Any:
    """Run ``body()`` under the bounded storage lock for ``path``.

    Owns the four steps every writer repeated: secure-parent-dir prep, lock-path
    derivation, the bounded acquire, and the not-held branch. ``body`` returns
    the writer's own return value, so an early ``return`` inside it (the
    ``only_if_absent`` short-circuit, for instance) propagates unchanged.

    The lock is held for the whole of ``body``, including its atomic write
    — the read-decide-write sequence must not be re-entered by a concurrent
    writer partway through.
    """
    # Before ADR-0033's persistence merge this reached ``_acquire_storage_lock``
    # and ``_ensure_secure_parent_dir`` through a function-local import back into
    # ``storage_writer`` (the module this template was split out of). Both now
    # live in this module, so the cycle-breaking lazy import is gone.
    _ensure_secure_parent_dir(path)
    lock_path = _storage_state_lock_path(path)
    with _acquire_storage_lock(
        lock_path, log_prefix=log_prefix, deadline_seconds=deadline_seconds
    ) as state:
        if state != "held":
            return on_unavailable(lock_path)
        return body()


# ==========================================================================
# SECTION 4 — SNAPSHOT TYPES
# Path-aware cookie identity/value tuples and the detailed save result.
# ==========================================================================


class CookieSnapshotKey(NamedTuple):
    """Path-aware cookie identity used by the snapshot/delta save machinery.

    RFC 6265 treats ``path`` as part of cookie identity: two cookies with the
    same ``(name, domain)`` but different paths are distinct entries. The
    snapshot/delta path widens the legacy ``(name, domain)`` key (still used
    elsewhere for back-compat — see ``CookieKey``) to ``(name, domain, path)``
    so that path-scoped cookies (e.g. ``OSID`` on a per-product path) survive
    a load → save round trip and so that a sibling-process write to a
    different-path variant of the same name is not silently overwritten.
    """

    name: str
    domain: str
    path: str


class CookieSnapshotValue(NamedTuple):
    """Snapshot value tuple: ``(value, expires, secure, http_only)``.

    Widened from a bare ``str`` so that a ``Set-Cookie`` which keeps the same
    value but renews ``expires`` (or flips ``secure`` / ``httpOnly``) still
    registers as a delta. The legacy save path compared ``expires`` directly
    and would write the new expiry through; the snapshot path previously
    keyed on value alone and silently dropped attribute-only refreshes.
    """

    value: str
    expires: int | None
    secure: bool
    http_only: bool


CookieSnapshot: TypeAlias = dict[CookieSnapshotKey, CookieSnapshotValue]
# ``None`` is a private observation marker for a pre-existing target row whose
# value was empty, missing, or non-string.  It lets recovery replace an unusable
# row while still treating a newly-written non-empty sibling as a CAS conflict.
RecoveryCookieObservation: TypeAlias = dict[CookieSnapshotKey, frozenset[str | None]]


@dataclass(frozen=True)
class CookieSaveResult:
    """Detailed result for callers that need to maintain a save baseline."""

    ok: bool
    cas_rejected_keys: frozenset[CookieSnapshotKey] = frozenset()


# ==========================================================================
# SECTION 5 — CAS + MERGE MATH (and the pinned delegate seam)
# Snapshotting, baseline advancement, the legacy and snapshot/delta merges, and
# ``save_cookies_to_storage`` — the ADR-0029-pinned monkeypatchable delegate.
# ==========================================================================


def snapshot_cookie_jar(cookie_jar: httpx.Cookies) -> CookieSnapshot:
    """Capture an open-time snapshot of an httpx cookie jar.

    Snapshots are the input to the dirty-flag/delta merge in
    :func:`save_cookies_to_storage`: at save time, only cookies whose
    in-memory value differs from the snapshot — plus cookies absent from
    the jar but present in the snapshot (deletions) — are propagated to
    disk. Cookies the in-process code never touched are left to whatever
    a sibling process may have written (closes the Appendix A2
    stale-overwrite-fresh hazard).

    The key shape is path-aware ``(name, domain, path)`` (also closes
    the Appendix A2 path-collapse hazard). Cookies with no name or no domain
    are skipped — the storage format requires both.

    Args:
        cookie_jar: The httpx.Cookies object to snapshot.

    Returns:
        Mapping of ``CookieSnapshotKey -> CookieSnapshotValue`` capturing
        each cookie's value and the attributes the storage_state schema
        persists (``expires``, ``secure``, ``httpOnly``).
    """
    return {
        CookieSnapshotKey(cookie.name, cookie.domain, cookie.path or "/"): CookieSnapshotValue(
            value=cookie.value,
            expires=cookie.expires,
            secure=bool(cookie.secure),
            http_only=_cookie_is_http_only(cookie),
        )
        for cookie in cookie_jar.jar
        if cookie.name and cookie.domain and cookie.value is not None
    }


def _cookie_snapshot_key_variants(key: CookieSnapshotKey) -> set[CookieSnapshotKey]:
    """Return equivalent host/domain snapshot keys for leading-dot domains.

    Mirrors :func:`_cookie_key_variants` but preserves the path component so
    storage entries on the same path match snapshot entries regardless of
    whether ``http.cookiejar`` normalized the domain to a leading dot.
    """
    variants = {key}
    if key.domain.startswith("."):
        variants.add(CookieSnapshotKey(key.name, key.domain[1:], key.path))
    else:
        variants.add(CookieSnapshotKey(key.name, f".{key.domain}", key.path))
    return variants


def _stored_cookie_snapshot_key(stored_cookie: Any) -> CookieSnapshotKey | None:
    """Build a path-aware snapshot key from a Playwright storage_state cookie."""
    if not isinstance(stored_cookie, dict):
        return None
    name = stored_cookie.get("name")
    domain = stored_cookie.get("domain", "")
    if not isinstance(name, str) or not name:
        return None
    if not isinstance(domain, str) or not domain:
        return None
    raw_path = stored_cookie.get("path")
    if raw_path is not None and not isinstance(raw_path, str):
        return None
    path = raw_path or "/"
    return CookieSnapshotKey(name, domain, path)


def advance_cookie_snapshot_after_save(
    original_snapshot: CookieSnapshot | None,
    post_save_snapshot: CookieSnapshot,
    cas_rejected_keys: frozenset[CookieSnapshotKey],
) -> CookieSnapshot | None:
    """Advance save baseline for successful keys while preserving rejected ones.

    A save can partially succeed: one cookie delta may write through while a
    sibling-process CAS conflict rejects another. Advancing the whole baseline
    would lose the rejected delta; keeping the whole old baseline would replay
    already-written deltas and wedge future saves. This helper advances every
    key to ``post_save_snapshot`` except the CAS-rejected keys, which retain
    their old baseline value or absence. Rejected keys are matched through
    leading-dot variants because the merge path can reject a normalized variant
    of the key captured in ``original_snapshot``.
    """
    if original_snapshot is None:
        return None

    advanced = dict(post_save_snapshot)
    for key in cas_rejected_keys:
        original_key = next(
            (
                variant
                for variant in _cookie_snapshot_key_variants(key)
                if variant in original_snapshot
            ),
            None,
        )
        for variant in _cookie_snapshot_key_variants(key):
            advanced.pop(variant, None)
        if original_key is not None:
            advanced[original_key] = original_snapshot[original_key]
    return advanced


def _cookie_save_return(
    result: CookieSaveResult, *, return_result: bool
) -> bool | CookieSaveResult:
    """Return either the detailed save result or its public bool projection."""
    return result if return_result else result.ok


def save_cookies_to_storage(
    cookie_jar: httpx.Cookies,
    path: Path | None = None,
    *,
    original_snapshot: CookieSnapshot | None = None,
    recovery_observation: RecoveryCookieObservation | None = None,
    return_result: bool = False,
) -> bool | CookieSaveResult:
    """Save an updated httpx.Cookies jar back to Playwright storage_state.json.

    This ensures that when Google issues short-lived token refreshes (e.g.
    during 302 redirects to accounts.google.com), those updated cookies are
    serialized back to disk so the session remains valid across CLI invocations.

    If auth was loaded from an environment variable (no file), this is a no-op.

    Cross-process safety: the read-merge-write cycle is wrapped in an OS-level
    file lock (``.storage_state.json.lock``) so concurrent writers from
    different Python processes (e.g. an in-process ``NotebookLMClient`` keepalive
    plus a cron-driven ``notebooklm auth refresh``) serialize cleanly rather
    than tearing or losing updates.

    Two merge modes:

    - **Legacy (``original_snapshot=None``)**: every in-memory cookie whose
      value differs from disk wins. Vulnerable to the stale-overwrite-fresh
      race documented in ``docs/auth-cookie-lifecycle.md`` Appendix A2 and emits a
      ``RuntimeWarning`` safety advisory about that race (this is a permanent
      back-compat shim, not a scheduled deprecation, so the advisory is a
      ``RuntimeWarning`` and is not silenced by ``NOTEBOOKLM_QUIET_DEPRECATIONS``).
      Kept only as a public-API back-compat shim for callers outside this repo;
      every first-party caller passes ``original_snapshot``.
    - **Snapshot/delta (``original_snapshot`` provided)**: only cookies
      whose in-memory persisted tuple differs from the snapshot are written, and
      cookies present in the snapshot but no longer in the jar are
      deleted from disk. Cookies the in-process code never touched are
      left untouched on disk so a sibling-process write survives.
      Path-aware ``(name, domain, path)`` keys are used here (also closes
      the Appendix A2 path-collapse hazard).

    Args:
        cookie_jar: The httpx.Cookies object containing the latest cookies.
        path: Path to storage_state.json. If None, cookie sync is skipped.
        original_snapshot: Open-time snapshot from
            :func:`snapshot_cookie_jar`. When provided, only deltas and
            deletions relative to the snapshot are persisted.
        return_result: Internal escape hatch for callers that need CAS-rejected
            keys to maintain a per-cookie baseline. Public callers should use
            the default bool return.

    Returns:
        ``True`` if the disk state now reflects the caller's intent (write
        succeeded, was a successful no-op, or the call was a deliberate skip
        because auth was loaded from an env var). ``False`` if an I/O error
        prevented the save or a CAS guard preserved a sibling-process write.
        With ``return_result=True``, callers can inspect CAS-rejected keys and
        advance their baseline for the keys that did write through.
    """
    if original_snapshot is None and path is not None:
        # NOT a deprecation: the original_snapshot=None form is a *permanent*
        # public-API back-compat shim (docs/auth-cookie-lifecycle.md Appendix A2),
        # not a scheduled removal — every in-tree caller already passes a
        # snapshot. The warning is a runtime safety advisory about the
        # stale-overwrite-fresh race that path is vulnerable to, so it is a
        # RuntimeWarning, not a DeprecationWarning. It is therefore outside
        # ADR-0018's scope: no NOTEBOOKLM_QUIET_DEPRECATIONS gate, no removal
        # version, and emitted directly here rather than via warn_deprecated.
        # Emitted on THIS delegate (not the relocated merge body) so
        # ``stacklevel=2`` still points at the caller.
        warnings.warn(
            "save_cookies_to_storage called without original_snapshot; the "
            "legacy full-merge path is vulnerable to the stale-overwrite-fresh "
            "race (docs/auth-cookie-lifecycle.md Appendix A2). Pass an original_snapshot "
            "captured via snapshot_cookie_jar() at jar-open time.",
            RuntimeWarning,
            stacklevel=2,
        )

    # Canonical patch seam: the CAS delta merge body lives in
    # :func:`merge_cookie_delta` (section 7 below). This module-level
    # ``save_cookies_to_storage`` symbol stays here as the monkeypatchable
    # delegate (~18 test files patch it; ``_runtime/lifecycle.py`` late-binds it).
    # Before ADR-0033's persistence merge the delegate reached the body through a
    # function-local ``from . import storage_writer``; it is now a same-module call.
    return merge_cookie_delta(
        cookie_jar,
        path,
        original_snapshot=original_snapshot,
        recovery_observation=recovery_observation,
        return_result=return_result,
    )


def _preserved_same_site(stored_cookie: dict[str, Any], fresh_state: dict[str, Any]) -> str:
    """Keep a stored ``sameSite`` instead of the merge default that erases it.

    ``http.cookiejar.Cookie`` carries no SameSite attribute, so
    :func:`_cookie_to_storage_state` can only emit the ``"None"`` default. Writing
    that back over a row captured with ``"Lax"``/``"Strict"`` would downgrade it on
    every rotation, quietly undoing the attribute preservation the capture and
    rookiepy converters perform.
    """
    stored = stored_cookie.get("sameSite")
    if stored in {"Strict", "Lax", "None"}:
        return str(stored)
    return str(fresh_state["sameSite"])


def _merge_cookies_legacy(cookie_jar: httpx.Cookies, storage_data: dict[str, Any]) -> int:
    """Legacy merge: trust in-memory whenever it differs from disk.

    Vulnerable to the stale-overwrite-fresh race (Appendix A2). Kept only for
    callers that have not yet opted into snapshot semantics. New callers
    must pass ``original_snapshot`` to :func:`save_cookies_to_storage`.

    Returns:
        Number of cookie entries added or modified in ``storage_data``.
    """
    cookies_by_key: dict[CookieKey, Any] = {
        (cookie.name, cookie.domain, cookie.path or "/"): cookie
        for cookie in cookie_jar.jar
        if cookie.name and cookie.domain and _is_allowed_cookie_domain(cookie.domain)
    }

    updated_count = 0
    stored_keys: set[CookieKey] = set()
    for stored_cookie in storage_data["cookies"]:
        if not isinstance(stored_cookie, dict):
            continue
        name = stored_cookie.get("name")
        domain = stored_cookie.get("domain", "")
        if not isinstance(name, str) or not name or not isinstance(domain, str) or not domain:
            continue

        stored_key = _stored_cookie_snapshot_key(stored_cookie)
        if stored_key is None:
            continue
        key: CookieKey = stored_key
        stored_keys.update(_cookie_key_variants(key))
        refreshed_cookie = _find_cookie_for_storage(cookies_by_key, key, stored_cookie.get("value"))
        if refreshed_cookie is None:
            continue

        fresh_state = _cookie_to_storage_state(refreshed_cookie)
        new_expires = fresh_state["expires"]
        changed = (
            stored_cookie.get("value") != refreshed_cookie.value
            or stored_cookie.get("expires") != new_expires
        )
        if changed:
            stored_cookie["value"] = refreshed_cookie.value
            stored_cookie["expires"] = new_expires
            # Normalize present-but-empty ``"path": ""`` to ``"/"`` so the row
            # we write matches the path normalization used to build the
            # identity key one block up (and used by every loader). Without
            # the trailing ``or "/"`` an on-disk row with ``"path": ""`` would
            # survive across save cycles while every other code path treats
            # it as ``"/"``.
            stored_cookie["path"] = refreshed_cookie.path or stored_cookie.get("path") or "/"
            stored_cookie["secure"] = refreshed_cookie.secure
            stored_cookie["httpOnly"] = _cookie_is_http_only(refreshed_cookie)
            stored_cookie["sameSite"] = _preserved_same_site(stored_cookie, fresh_state)
            updated_count += 1

    for key, cookie in cookies_by_key.items():
        if key in stored_keys:
            continue
        storage_data["cookies"].append(_cookie_to_storage_state(cookie))
        updated_count += 1

    return updated_count


def _merge_recovery_target_rows(
    storage_cookies: list[Any],
    deltas: dict[CookieSnapshotKey, Any],
    observation: RecoveryCookieObservation | None,
) -> tuple[list[Any], int, set[CookieSnapshotKey], set[CookieSnapshotKey]]:
    """Collapse observed recovery targets while preserving sibling conflicts."""
    if observation is None:
        return storage_cookies, 0, set(), set()

    replacements: dict[int, dict[str, Any]] = {}
    removals: set[int] = set()
    appends: list[dict[str, Any]] = []
    handled: set[CookieSnapshotKey] = set()
    cas_rejected: set[CookieSnapshotKey] = set()
    updated_count = 0

    for delta_key, cookie in deltas.items():
        if delta_key.name not in _RECOVERY_TARGET_COOKIE_NAMES:
            continue

        variants = _cookie_snapshot_key_variants(delta_key)
        observed_values: set[str | None] = set()
        for variant in variants:
            observed_values.update(observation.get(variant, frozenset()))
        if not observed_values:
            # No target row was observed before the POST. Let the ordinary
            # snapshot/CAS path decide whether a same-key sibling appeared.
            continue

        row_indices: list[int] = []
        for index, stored_cookie in enumerate(storage_cookies):
            stored_key = _stored_cookie_snapshot_key(stored_cookie)
            if stored_key is not None and variants & _cookie_snapshot_key_variants(stored_key):
                row_indices.append(index)

        fresh_state = _cookie_to_storage_state(cookie)
        replaceable: list[int] = []
        conflicts: list[int] = []
        for index in row_indices:
            stored_cookie = storage_cookies[index]
            stored_value = stored_cookie.get("value") if isinstance(stored_cookie, dict) else None
            stored_value_is_unusable = not isinstance(stored_value, str) or not stored_value
            observed_unusable = None in observed_values
            if (
                stored_value == cookie.value
                or stored_value in observed_values
                or (stored_value_is_unusable and observed_unusable)
            ):
                replaceable.append(index)
            else:
                conflicts.append(index)

        if conflicts:
            # This is the recovery-specific CAS rejection. The sibling rows
            # remain byte-for-byte intact; no stale recovery value may clobber
            # a value that did not exist when the POST started.
            #
            # Deliberately whole-key, even in the mixed case where another row
            # for this identity *was* replaced below: the key is reported as
            # rejected, so ``advance_cookie_snapshot_after_save`` leaves the
            # baseline where it is. A conflicting row is still on disk and the
            # loaders pick a winner among duplicates, so we cannot claim the
            # identity now reads as the value we wrote. Advancing on a partial
            # write would retire a delta that never fully landed.
            cas_rejected.add(delta_key)

        if replaceable:
            winner = replaceable[0]
            # Same ``sameSite`` preservation the ordinary merges apply: only the
            # cookie's *value* and expiry are being refreshed by the rotation,
            # and ``fresh_state`` can only carry the ``"None"`` default, so
            # taking it wholesale would downgrade a captured ``Lax``/``Strict``
            # on the one path recovery owns.
            stored_winner = storage_cookies[winner]
            replacements[winner] = {
                **fresh_state,
                "sameSite": _preserved_same_site(
                    stored_winner if isinstance(stored_winner, dict) else {}, fresh_state
                ),
            }
            removals.update(replaceable[1:])
            updated_count += 1 + len(replaceable[1:])
            handled.add(delta_key)
        elif not row_indices:
            appends.append(fresh_state)
            updated_count += 1
            handled.add(delta_key)
        elif conflicts:
            # Preserve an unobserved sibling exactly. The ordinary new-cookie
            # CAS path would likewise decline to append over an existing row.
            handled.add(delta_key)

    merged: list[Any] = []
    for index, stored_cookie in enumerate(storage_cookies):
        if index in removals:
            continue
        merged.append(replacements.get(index, stored_cookie))
    merged.extend(appends)
    return merged, updated_count, cas_rejected, handled


def _merge_cookies_with_snapshot(
    cookie_jar: httpx.Cookies,
    storage_data: dict[str, Any],
    original_snapshot: CookieSnapshot,
    *,
    recovery_observation: RecoveryCookieObservation | None = None,
) -> tuple[int, frozenset[CookieSnapshotKey]]:
    """Snapshot/delta merge: write only what this process actually changed.

    Closes the Appendix A2 stale-overwrite-fresh and path-collapse hazards:

    - **Deltas (CAS-guarded for keys in the snapshot)**: cookies in the
      jar whose snapshot tuple (``value, expires, secure, http_only``)
      differs from ``original_snapshot`` are written to disk **only if**
      the on-disk value still matches the snapshot value. If disk has
      rotated since open time, a sibling process has written it; we
      preserve their write rather than clobber it with our local
      rotation. New cookies acquired during the session are written only
      when no same-key storage row exists yet; an existing row means a
      sibling acquired the same cookie first. Comparing the full snapshot
      tuple keeps attribute-only refreshes (same value, new ``expires``)
      flowing to disk, but CAS remains value-only because attribute-only
      sibling drift is routine session metadata and should not wedge later
      value rotations.
    - **Deletions (CAS-guarded)**: a key present in the snapshot but
      absent from the jar is dropped from disk **only if** the on-disk
      value still matches the snapshot value — symmetric with the
      value-update CAS above. An ``Max-Age=0`` that evicted our
      locally-expired copy must not erase the sibling's freshly-issued
      replacement.
    - **Untouched**: cookies in the jar whose tuple matches the snapshot
      are not written, so a sibling-process write to the same key
      survives. Cookies on disk that are not in the snapshot are also
      left alone (they belong to a sibling process or another path).

    Args:
        cookie_jar: Current in-memory cookie jar.
        storage_data: Mutable storage_state.json dict (modified in place).
        original_snapshot: Open-time snapshot of the same jar.

    Returns:
        Tuple of ``(updated_count, cas_rejected_keys)``:

        - ``updated_count``: cookie entries added, modified, or removed
          (drives whether the temp-write step runs).
        - ``cas_rejected_keys``: keys whose CAS check rejected a delta or
          deletion. Caller uses this to advance the baseline only for keys
          that were actually written or already matched.
    """
    current_snapshot = snapshot_cookie_jar(cookie_jar)

    # Path-aware index of jar cookies for delta application. Restricting to
    # _is_allowed_cookie_domain matches the legacy save's allowlist gate so
    # this PR doesn't inadvertently widen the persisted-domain set.
    # Filter ``cookie.value is not None`` to mirror ``snapshot_cookie_jar``: a
    # value-less cookie is treated as a deletion (absent from this index, absent
    # from ``current_snapshot``) rather than a delta that would write ``null``
    # to disk.
    cookies_by_snapshot_key = {
        CookieSnapshotKey(cookie.name, cookie.domain, cookie.path or "/"): cookie
        for cookie in cookie_jar.jar
        if (
            cookie.name
            and cookie.domain
            and cookie.value is not None
            and _is_allowed_cookie_domain(cookie.domain)
        )
    }

    deltas = {
        snapshot_key: cookie
        for snapshot_key, cookie in cookies_by_snapshot_key.items()
        if original_snapshot.get(snapshot_key) != current_snapshot.get(snapshot_key)
    }

    deletion_candidates: set[CookieSnapshotKey] = {
        snapshot_key
        for snapshot_key in original_snapshot
        if snapshot_key not in current_snapshot
        # Only delete cookies the merge would otherwise be allowed to write.
        # Snapshot may include sibling-product domains the allowlist filters
        # out at write time; treating those as deletions would silently drop
        # disk entries we never persisted to begin with.
        and _is_allowed_cookie_domain(snapshot_key.domain)
    }

    updated_count = 0
    cas_rejected_keys: set[CookieSnapshotKey] = set()

    recovery_rows, recovery_updated, recovery_rejected, recovery_handled = (
        _merge_recovery_target_rows(storage_data["cookies"], deltas, recovery_observation)
    )
    updated_count += recovery_updated
    cas_rejected_keys.update(recovery_rejected)
    storage_data["cookies"] = recovery_rows
    merge_deltas = {key: cookie for key, cookie in deltas.items() if key not in recovery_handled}

    # Apply deltas + deletions to the existing storage entries in place.
    new_cookies: list[dict[str, Any]] = []
    matched_delta_keys: set[CookieSnapshotKey] = set(recovery_handled)
    for stored_cookie in storage_data["cookies"]:
        stored_key = _stored_cookie_snapshot_key(stored_cookie)
        if stored_key is None:
            new_cookies.append(stored_cookie)
            continue

        # Find the delta (or deletion) that maps to this stored entry.
        # Match leading-dot domain variants so e.g. snapshot
        # ``.accounts.google.com`` lines up with stored ``accounts.google.com``.
        # A delta wins over a deletion: if the same stored entry matches
        # both (which can happen when httpx normalized one variant), we
        # prefer to update rather than drop, because dropping would lose
        # the rotation we just applied.
        matched_delta_cookie = None
        matched_delta_key: CookieSnapshotKey | None = None
        for variant in _cookie_snapshot_key_variants(stored_key):
            if variant in merge_deltas:
                matched_delta_cookie = merge_deltas[variant]
                matched_delta_key = variant
                break

        if matched_delta_cookie is not None:
            if matched_delta_key is None:  # pragma: no cover - loop invariant
                raise RuntimeError("matched_delta_cookie set without matched_delta_key")
            # CAS-guard for value updates: if our snapshot had this key in any
            # leading-dot variant and disk's current value differs from the
            # snapshot value, a sibling process has rewritten the row between
            # our open and our save. Preserve their write rather than clobber,
            # unless disk has already converged to our current value; in that
            # case the save intent is satisfied and the caller may advance its
            # baseline.
            # Variant-aware lookup mirrors the delta match above: if the snapshot
            # was keyed on ``accounts.google.com`` but the matched delta key is
            # the leading-dot variant, a plain ``.get(matched_delta_key)`` would
            # miss the entry and silently bypass the CAS.
            snapshot_entry = next(
                (
                    original_snapshot[variant]
                    for variant in _cookie_snapshot_key_variants(matched_delta_key)
                    if variant in original_snapshot
                ),
                None,
            )
            stored_value = stored_cookie.get("value")
            if (
                snapshot_entry is not None
                and stored_value != snapshot_entry.value
                and stored_value != matched_delta_cookie.value
            ):
                logger.debug(
                    "Skipped CAS-guarded value update of %s on %s: disk value "
                    "differs from snapshot (sibling write preserved)",
                    matched_delta_key.name,
                    matched_delta_key.domain,
                )
                cas_rejected_keys.add(matched_delta_key)
                matched_delta_keys.add(matched_delta_key)
                new_cookies.append(stored_cookie)
                continue
            if snapshot_entry is None and stored_value != matched_delta_cookie.value:
                logger.debug(
                    "Skipped CAS-guarded value update of new cookie %s on %s: "
                    "disk row already exists (sibling write preserved)",
                    matched_delta_key.name,
                    matched_delta_key.domain,
                )
                cas_rejected_keys.add(matched_delta_key)
                matched_delta_keys.add(matched_delta_key)
                new_cookies.append(stored_cookie)
                continue
            fresh_state = _cookie_to_storage_state(matched_delta_cookie)
            stored_cookie["value"] = matched_delta_cookie.value
            stored_cookie["expires"] = fresh_state["expires"]
            # Mirror :func:`_merge_cookies_legacy`: ``or "/"`` normalizes a
            # present-but-empty ``"path": ""`` so the written row matches the
            # path normalization used by the identity key and every loader.
            stored_cookie["path"] = matched_delta_cookie.path or stored_cookie.get("path") or "/"
            stored_cookie["secure"] = matched_delta_cookie.secure
            stored_cookie["httpOnly"] = _cookie_is_http_only(matched_delta_cookie)
            stored_cookie["sameSite"] = _preserved_same_site(stored_cookie, fresh_state)
            matched_delta_keys.add(matched_delta_key)
            updated_count += 1
            new_cookies.append(stored_cookie)
            continue

        deletion_match = next(
            (
                variant
                for variant in _cookie_snapshot_key_variants(stored_key)
                if variant in deletion_candidates
            ),
            None,
        )
        if deletion_match is not None:
            # CAS-guard: only drop the disk row if its value still matches
            # what we observed at snapshot time. A sibling process may have
            # rewritten this key between our open and our save; clobbering
            # their fresh value with our local eviction would resurrect the
            # exact stale-overwrite-fresh hazard the snapshot path exists
            # to close (just inverted — deletion-of-fresh instead of
            # value-write-of-stale).
            snapshot_value = original_snapshot[deletion_match].value
            if stored_cookie.get("value") == snapshot_value:
                updated_count += 1
                continue  # drop the entry from disk
            cas_rejected_keys.add(deletion_match)

        new_cookies.append(stored_cookie)

    # Append delta cookies that didn't match any existing storage entry
    # (genuinely new cookies acquired during the session).
    for snapshot_key, cookie in merge_deltas.items():
        if snapshot_key in matched_delta_keys:
            continue
        new_cookies.append(_cookie_to_storage_state(cookie))
        updated_count += 1

    storage_data["cookies"] = new_cookies
    return updated_count, frozenset(cas_rejected_keys)


# ==========================================================================
# SECTION 6 — WRITER OUTCOME TYPES
# Value-free status enums and records the intent writers return.
# ==========================================================================


class WriteStatus(Enum):
    """Closed-enum status for a full-file / RMW storage write."""

    OK = "ok"
    LOCK_UNAVAILABLE = "lock_unavailable"


@dataclass(frozen=True)
class WriteOutcome:
    """Value-free outcome for full-replace / RMW storage writers.

    Carries only an enum status — never cookie values, jars, state dicts, or
    caught exceptions — so it is always safe to ``repr``/log.
    """

    status: WriteStatus

    @property
    def ok(self) -> bool:
        return self.status is WriteStatus.OK

    @property
    def lock_unavailable(self) -> bool:
        return self.status is WriteStatus.LOCK_UNAVAILABLE


# ---------------------------------------------------------------------------
# Account-metadata sentinel for the login/import full-replace intent
# ---------------------------------------------------------------------------


class _AccountAction(Enum):
    """Sentinel actions for :func:`replace_from_login`'s ``account`` param."""

    KEEP = "keep"
    CLEAR = "clear"


#: Leave the account binding untouched — carry whatever the input state holds
#: (import-cookies has none, so the result carries none). The default.
KEEP_ACCOUNT = _AccountAction.KEEP
#: Drop any stale account binding (the refresh default-account login branch —
#: the user may have re-logged into a different Google account).
CLEAR_ACCOUNT = _AccountAction.CLEAR


@dataclass(frozen=True)
class AccountRecord:
    """An explicit account binding to embed in the ``notebooklm`` namespace.

    ``authuser`` is the internal Google account index; ``email`` is the stable
    routing identity (optional). Passed as ``replace_from_login(account=...)`` to
    embed the binding in the SAME atomic write as the cookies (replacing the
    former separate ``write_account_metadata`` step, which had its own lock and a
    partial-failure window).
    """

    authuser: int
    email: str | None = None


# The ``account`` argument sentinel: KEEP_ACCOUNT | CLEAR_ACCOUNT | AccountRecord.
AccountArg = _AccountAction | AccountRecord


class LoginWriteStatus(Enum):
    """Closed-enum status for a login/import full-replace storage write."""

    OK = "ok"
    LOCK_UNAVAILABLE = "lock_unavailable"
    REQUIRED_COOKIES_DROPPED = "required_cookies_dropped"


@dataclass(frozen=True)
class LoginWriteOutcome:
    """Value-free outcome for :func:`replace_from_login`.

    Carries only an enum status, cookie **names** (keys, never values), and a
    filesystem path — never cookie values, jars, state dicts, or caught
    exceptions — so it is always safe to ``repr``/log.

    * ``missing_required`` — names of ``MINIMUM_REQUIRED_COOKIES`` that the
      write-time domain filter dropped (only set on ``REQUIRED_COOKIES_DROPPED``).
    * ``present_names`` — names surviving the filter, so the CLI can build the
      same ``missing_cookies_hint`` #2086 produced without re-reading disk.
    * ``backup_path`` — path of the ``.bak`` copy taken inside the lock for the
      import flavour (``None`` when no backup was taken).
    """

    status: LoginWriteStatus
    missing_required: tuple[str, ...] = ()
    present_names: tuple[str, ...] = ()
    backup_path: Path | None = None

    @property
    def ok(self) -> bool:
        return self.status is LoginWriteStatus.OK

    @property
    def lock_unavailable(self) -> bool:
        return self.status is LoginWriteStatus.LOCK_UNAVAILABLE

    @property
    def required_cookies_dropped(self) -> bool:
        return self.status is LoginWriteStatus.REQUIRED_COOKIES_DROPPED


# ==========================================================================
# SECTION 7 — THE INTENT WRITERS
# The seven sanctioned mutations of ``storage_state.json`` and its sibling
# credential files. These are the only functions permitted to reach
# ``_write_state_unchecked`` (equality-asserted in test_storage_writer_boundary).
# ==========================================================================


# --- CAS delta merge (behind ``save_cookies_to_storage``) -------------------


def merge_cookie_delta(
    cookie_jar: httpx.Cookies,
    path: Path | None = None,
    *,
    original_snapshot: CookieSnapshot | None = None,
    recovery_observation: RecoveryCookieObservation | None = None,
    return_result: bool = False,
) -> bool | CookieSaveResult:
    """CAS snapshot/delta merge of ``cookie_jar`` into ``storage_state.json``.

    Relocated verbatim (behaviour-preserving) from
    ``save_cookies_to_storage``; that function remains the public,
    monkeypatchable delegate seam. The ``original_snapshot=None`` legacy-warning
    branch stays on the delegate so its ``stacklevel`` still points at the
    caller.

    This is a **CAS** intent: on lock unavailability it **fails open** (status
    quo — the snapshot/delta CAS guards preserve correctness), driven by
    :func:`_file_lock_exclusive`. The full signature (incl.
    ``recovery_observation``) and the :class:`CookieSaveResult` return with
    ``cas_rejected_keys`` are load-bearing for the PSIDTS-recovery and
    cookie-persistence baseline callers.
    """
    if path is None and resolve_auth_json_env() is not None:
        logger.debug("Skipping cookie sync: Auth loaded from NOTEBOOKLM_AUTH_JSON env var")
        return _cookie_save_return(CookieSaveResult(True), return_result=return_result)

    if path is None:
        logger.debug("Skipping cookie sync: No storage file path available")
        return _cookie_save_return(CookieSaveResult(True), return_result=return_result)

    lock_path = _storage_state_lock_path(path)
    with _file_lock_exclusive(lock_path):
        if not path.exists():
            logger.debug("Skipping cookie sync: Storage file not found at %s", path)
            return _cookie_save_return(CookieSaveResult(False), return_result=return_result)

        try:
            storage_data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(
                "Failed to read storage state for cookie sync: %s",
                type(e).__name__,
            )
            return _cookie_save_return(CookieSaveResult(False), return_result=return_result)

        cookies = storage_data.get("cookies") if isinstance(storage_data, dict) else None
        if not isinstance(cookies, list):
            logger.warning(
                "storage_state at %s has an invalid 'cookies' key/payload; "
                "rotated cookies will not be persisted",
                path,
            )
            return _cookie_save_return(CookieSaveResult(False), return_result=return_result)

        if original_snapshot is None:
            updated_count = _merge_cookies_legacy(cookie_jar, storage_data)
            cas_rejected_keys: frozenset[Any] = frozenset()
        else:
            updated_count, cas_rejected_keys = _merge_cookies_with_snapshot(
                cookie_jar,
                storage_data,
                original_snapshot,
                recovery_observation=recovery_observation,
            )

        if updated_count == 0:
            # A CAS rejection with no other successful work means disk does
            # not reflect our intent; the caller must not advance baseline.
            return _cookie_save_return(
                CookieSaveResult(not cas_rejected_keys, cas_rejected_keys),
                return_result=return_result,
            )

        try:
            _write_state_unchecked(path, storage_data)
            logger.debug("Successfully synced %d refreshed cookies to %s", updated_count, path)
            # Even on a successful disk write, if any CAS arm rejected work,
            # disk diverges from ``post`` for at least one key — caller must
            # not advance baseline.
            return _cookie_save_return(
                CookieSaveResult(not cas_rejected_keys, cas_rejected_keys),
                return_result=return_result,
            )
        except Exception as e:
            logger.warning(
                "Failed to write updated cookies to %s: %s",
                path,
                type(e).__name__,
            )
            return _cookie_save_return(CookieSaveResult(False), return_result=return_result)


# --- In-band account writers (relocated from ``account.py``) ----------------


def update_account_metadata(
    storage_path: Path,
    *,
    authuser: int,
    email: str | None = None,
    only_if_absent: bool = False,
    deadline_seconds: float = _LOCK_ACQUIRE_DEADLINE_SECONDS,
) -> bool:
    """Persist account metadata atomically inside ``storage_state.json``.

    Relocated from ``account.write_account_metadata`` (the in-band write only —
    the sibling ``context.json`` cleanup ``_drop_legacy_account_key`` stays in
    ``account.py``). Full-file RMW intent: **fails closed**, raising
    :class:`LockUnavailableError` on lock unavailability.

    ``only_if_absent`` closes a check-then-act race in
    :func:`account.promote_legacy_account`: that caller reads the legacy
    record and checks whether an in-band record is already present — both
    OUTSIDE this function's lock — before deciding to call this function at
    all. Without a re-check taken under the SAME lock as the write, a
    concurrent fresh login/account-switch (``write_account_metadata``,
    ``replace_from_login``) landing in that unlocked gap would commit its new
    record first, and this call would then unconditionally overwrite it with
    the stale legacy values the caller captured before the gap — silently
    re-clobbering a just-completed account switch with a promotion nobody
    asked to happen. ``write_account_metadata`` (an intentional overwrite —
    the whole point of a real login) always passes ``only_if_absent=False``
    (the default); only the promotion caller opts in.

    ``deadline_seconds`` lets an opportunistic caller bound how long it will
    contend for the lock. The default (90s, matching every other full-file
    RMW intent) is right for an intentional login. It is WRONG for
    :func:`account.promote_legacy_account`: that function now runs inside
    :func:`account.read_account_metadata`, which many callers — including
    ``async`` code paths (``client.get_account_email``, token-route
    resolution) — call assuming a fast, lock-free read. Blocking one of those
    for up to 90s on lock contention would freeze the event loop for far
    longer than the "read" it thinks it's doing. Promotion is best-effort by
    design (a failed promotion falls back to the legacy record, never breaks
    the read — see ``promote_legacy_account``), so a short deadline that gives
    up fast and takes that fallback is strictly the right trade-off; only the
    promotion caller passes a short one.

    Returns:
        ``True`` if a write happened; ``False`` if ``only_if_absent`` was set
        and an in-band record was already present under the lock (no-op —
        the caller's stale values were correctly discarded).
    """
    from . import account as _account  # lazy: avoid the account<->writer cycle

    account_payload: dict[str, Any] = {"authuser": authuser}
    if email:
        account_payload["email"] = email

    def _write() -> bool:
        data = _account._load_storage_state_for_write(storage_path)
        namespace = data.get(_account._STORAGE_NAMESPACE_KEY)
        if not isinstance(namespace, dict):
            namespace = {}
        elif only_if_absent and isinstance(namespace.get(_account._ACCOUNT_CONTEXT_KEY), dict):
            return False
        namespace["version"] = _account._STORAGE_NAMESPACE_VERSION
        namespace[_account._ACCOUNT_CONTEXT_KEY] = account_payload
        data[_account._STORAGE_NAMESPACE_KEY] = namespace
        _write_state_unchecked(storage_path, data)
        return True

    # MUST-KNOW via exception: the ``bool`` return already spends ``False`` on
    # the ``only_if_absent`` no-op above, so it cannot also carry "could not
    # acquire" without conflating *chose not to* with *could not*.
    return bool(
        in_storage_transaction(
            storage_path,
            _write,
            log_prefix="write_account_metadata",
            on_unavailable=raise_on_lock_unavailable("write_account_metadata"),
            deadline_seconds=deadline_seconds,
        )
    )


def clear_in_band_account(storage_path: Path) -> None:
    """Remove the ``notebooklm.account`` key from ``storage_state.json``.

    Relocated from ``account._clear_in_band_account``. Best-effort cleanup:
    swallows lock unavailability and read/parse errors, matching the
    pre-refactor semantics (the reader falls back to the legacy record). No-op if
    the file is missing, unreadable, or carries no in-band record.
    """
    from . import account as _account  # lazy: avoid the account<->writer cycle

    if not storage_path.exists():
        return

    def _clear() -> None:
        try:
            data = json.loads(storage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.debug("in-band account clear skipped at %s: %s", storage_path, e)
            return
        if not isinstance(data, dict):
            return
        namespace = data.get(_account._STORAGE_NAMESPACE_KEY)
        if not isinstance(namespace, dict) or _account._ACCOUNT_CONTEXT_KEY not in namespace:
            return
        del namespace[_account._ACCOUNT_CONTEXT_KEY]
        if set(namespace.keys()) <= {"version"}:
            del data[_account._STORAGE_NAMESPACE_KEY]
        else:
            data[_account._STORAGE_NAMESPACE_KEY] = namespace
        _write_state_unchecked(storage_path, data)

    # TOLERABLE: the same failure mode the old filelock OSError arm swallowed.
    # See the caveat on ``skip_on_lock_unavailable`` — the functional argument
    # (the legacy reader still resolves the record) is narrower than this
    # operation's privacy motive, and that tension is tracked in ADR-0031.
    in_storage_transaction(
        storage_path,
        _clear,
        log_prefix="clear_account_metadata",
        on_unavailable=skip_on_lock_unavailable(
            "in-band account clear skipped: storage lock unavailable at %s"
        ),
    )


# --- Browser-capture re-mint (relocated from ``browser_capture.py``) --------


def replace_from_remint(
    path: Path,
    captured_state: dict[str, Any],
    *,
    carry_account: bool,
    include_domains: set[str] | None = None,
) -> WriteOutcome:
    """Full cookie replace for a browser-capture re-mint, under the storage lock.

    The single sanctioned persist for the :mod:`notebooklm._auth.browser_capture`
    arms (interactive login, L3 headless-launch re-auth, CDP re-auth). Replaces
    ``storage_state.json``'s cookies with ``captured_state`` — a re-mint is a
    brand-new session, so cookies are *replaced*, never merged. Full-file replace
    intent: **fails closed**, returning ``WriteOutcome(lock_unavailable)`` on lock
    unavailability so the capture caller can surface/retry rather than race a
    concurrent keepalive write ([capture-2]).

    Everything below happens **inside** the canonical storage lock:

    * The write-time domain filter
      (:func:`filter_storage_state_cookies_by_domain_policy`) is applied so
      sibling-product cookies never reach disk. ``include_domains`` carries the
      interactive ``--include-domains`` opt-in through unchanged; the default
      policy preserves trusted Google roots (``*.googleusercontent.com`` / Drive
      etc.), matching main's preserve-trusted-roots behavior. The filter is
      idempotent, so a caller that pre-filtered with the same ``include_domains``
      is not narrowed further.
    * Account namespace handling branches on ``carry_account``:

      - ``carry_account=True`` (unattended profile-launch arm): the existing
        ``notebooklm`` namespace is read from the current file and CARRIED OVER
        into the new state, so an in-place re-mint against our own profile no
        longer destroys the account binding ([capture-1]).
      - ``carry_account=False`` (interactive arm, and the CDP no-resolve
        fallback): the stale binding is DROPPED — the user may have signed into a
        different account. On the INTERACTIVE login arm the CLI adapter's
        ``repair_playwright_account_metadata`` re-establishes it immediately
        after the write. On the library / mid-RPC CDP arm there is NO such
        repair, so it lands on the authuser=0 default (repair happens only via
        CLI ``auth refresh``); carrying a stale index blindly would instead
        relocate [capture-1], so authuser=0 is the deliberate safe fallback.

    CDP arm caveat: CDP attaches to the operator's daily Chrome, whose account
    set may not match the stored binding. The CALLER re-resolves the stored email
    against the captured jar (any network lookup happens OUTSIDE this held lock)
    and passes the verdict as ``carry_account``; on no-resolve it passes
    ``carry_account=False`` rather than carry a possibly-misrouting index.

    Args:
        path: Destination ``storage_state.json``.
        captured_state: The (already healed) captured storage-state dict.
        carry_account: Whether to carry the existing account namespace forward.
        include_domains: Optional ``--include-domains`` opt-in labels, applied by
            the internal filter (mirrors the capture caller's filter call).

    Returns:
        :class:`WriteOutcome` — ``ok`` on success, ``lock_unavailable`` if the
        bounded storage-lock acquire timed out / the lock infra failed.
    """
    from . import account as _account  # lazy: avoid the account<->writer cycle
    from ._browser_cookie_filter import (  # noqa: PLC0415 (deferred; true leaf, no cycle either way)
        filter_storage_state_cookies_by_domain_policy,
    )

    _ensure_secure_parent_dir(path)
    lock_path = _storage_state_lock_path(path)
    with _acquire_storage_lock(lock_path, log_prefix="replace_from_remint") as state:
        if state != "held":
            return WriteOutcome(WriteStatus.LOCK_UNAVAILABLE)

        # Carry the existing account namespace BEFORE overwriting (read under the
        # same lock so it can't tear against a concurrent writer).
        carried_namespace: dict[str, Any] | None = None
        if carry_account and path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = None
            if isinstance(existing, dict):
                namespace = existing.get(_account._STORAGE_NAMESPACE_KEY)
                if isinstance(namespace, dict):
                    carried_namespace = namespace

        # Write-time domain filter (preserve-trusted-roots). Returns a fresh
        # ``{"cookies": [...], "origins": []}`` — the captured browser state
        # never carries our ``notebooklm`` namespace, so it is only (re)attached
        # from the carried value below.
        filtered = filter_storage_state_cookies_by_domain_policy(
            dict(captured_state), include_domains=include_domains
        )
        if carried_namespace is not None:
            filtered[_account._STORAGE_NAMESPACE_KEY] = carried_namespace
        _write_state_unchecked(path, filtered)
    return WriteOutcome(WriteStatus.OK)


# --- Login / import full-replace (hoisted from the CLI login/import writers) -


def replace_from_login(
    path: Path,
    state: dict[str, Any],
    *,
    include_domains: set[str] | None,
    include_optional: bool = False,
    account: AccountArg = KEEP_ACCOUNT,
    backup: bool = False,
    io_policy: object | None = None,
) -> LoginWriteOutcome:
    """Full cookie replace for the CLI login / import flows, under the storage lock.

    The single sanctioned persist for ``notebooklm login --browser-cookies``,
    ``notebooklm auth refresh --browser-cookies``, and ``notebooklm auth
    import-cookies``. Replaces ``storage_state.json``'s cookies with ``state`` —
    a login is a brand-new session, so cookies are *replaced*, never merged.
    Everything below happens **inside** the canonical storage lock; the writer
    **fails closed** (``LoginWriteOutcome(lock_unavailable)``) so a caller can
    surface/retry rather than race a concurrent keepalive write.

    Under the lock, in order:

    1. **Write-time domain filter.** ``state``'s cookies are run through
       :func:`filter_storage_state_cookies_by_domain_policy` (hoisted from the
       #2086 CLI call sites) so sibling-product cookies never reach disk.
       ``include_domains`` / ``include_optional`` carry the CLI opt-ins through;
       the default policy preserves trusted Google roots
       (``*.googleusercontent.com`` / Drive) — main's preserve-trusted-roots
       behaviour. The filter is idempotent, so a caller (import) that pre-filtered
       with the same opts is not narrowed further.
    2. **Post-filter required-cookie revalidation.** ``MINIMUM_REQUIRED_COOKIES``
       is re-checked on the FILTERED names. If a required cookie's only copy sat
       on a now-dropped domain, the writer returns
       ``LoginWriteOutcome(required_cookies_dropped, ...)`` and writes NOTHING —
       preserving #2086's contract (the CLI maps this to
       ``CookieValidationFailure(code="COOKIE_VALIDATION_FAILED")`` + ``io.fail(1)``
       + ``not storage_path.exists()``). Both ``missing_required`` and
       ``present_names`` are value-free cookie NAMES.
    3. **Account metadata**, embedded in the same atomic write via the ``account``
       sentinel:

       - :data:`KEEP_ACCOUNT` (default; the import flavour) — carry whatever
         ``state`` already holds in the ``notebooklm`` namespace (import has none,
         so the result carries none). No account key is synthesised.
       - :data:`CLEAR_ACCOUNT` (the refresh default-account login branch) — no
         account binding is written, so stale routing cannot survive.
       - :class:`AccountRecord` (the targeted login branches) — the
         ``{authuser, email}`` binding is embedded, replacing the former separate
         ``write_account_metadata`` step (one atomic write, no partial-failure
         window).
    4. **Opt-in recording.** The resolved ``include_domains`` (and
       ``include_optional``) are recorded in the ``notebooklm`` namespace so a
       future merge-gate narrowing can consult per-profile opt-ins (plan §b.5);
       additive — old readers ignore unknown namespace keys.
    5. **Import backup.** When ``backup=True`` (the import flavour), a pre-overwrite
       ``.bak`` copy of any existing target is taken INSIDE the lock (0600 on
       POSIX) so it cannot race a concurrent keepalive write; its path is returned
       in the outcome.

    Args:
        path: Destination ``storage_state.json``.
        state: The captured / coerced storage-state dict to persist.
        include_domains: ``--include-domains`` opt-in labels (or ``None``).
        include_optional: Persist all optional sibling-product domains (the
            import-cookies flavour).
        account: Account-metadata action (see above).
        backup: Take a pre-overwrite ``.bak`` backup inside the lock (import).
        io_policy: Reserved for a future per-intent lock/IO policy override;
            currently unused (accepted for forward-compatible call sites).

    Returns:
        :class:`LoginWriteOutcome`.
    """
    del io_policy  # reserved; see docstring
    from . import account as _account  # lazy: avoid the account<->writer cycle
    from ._browser_cookie_filter import (  # noqa: PLC0415 (deferred; true leaf, no cycle either way)
        filter_storage_state_cookies_by_domain_policy,
    )
    from .cookie_policy import (  # noqa: PLC0415 (deferred; true leaf, no cycle either way)
        MINIMUM_REQUIRED_COOKIES,
        cookie_names_from_storage,
    )

    _ensure_secure_parent_dir(path)
    lock_path = _storage_state_lock_path(path)
    with _acquire_storage_lock(lock_path, log_prefix="replace_from_login") as lock_state:
        if lock_state != "held":
            return LoginWriteOutcome(LoginWriteStatus.LOCK_UNAVAILABLE)

        # (1) Write-time domain filter (preserve-trusted-roots). Returns a fresh
        # ``{"cookies": [...], "origins": []}`` — the browser/import state never
        # carries our ``notebooklm`` namespace.
        filtered = filter_storage_state_cookies_by_domain_policy(
            dict(state), include_optional=include_optional, include_domains=include_domains
        )

        # (2) Post-filter required-cookie revalidation on the FILTERED names.
        present = cookie_names_from_storage(filtered)
        missing_required = tuple(sorted(MINIMUM_REQUIRED_COOKIES.difference(present)))
        if missing_required:
            # Count-only breadcrumb — never cookie names or values.
            logger.debug(
                "replace_from_login: %d required cookie(s) dropped by the write-time "
                "domain policy for %s; writing nothing",
                len(missing_required),
                path,
            )
            return LoginWriteOutcome(
                LoginWriteStatus.REQUIRED_COOKIES_DROPPED,
                missing_required=missing_required,
                present_names=tuple(sorted(present)),
            )

        # (3) + (4) Build the ``notebooklm`` namespace (account + opt-ins).
        namespace: dict[str, Any] = {}
        if account is KEEP_ACCOUNT:
            existing_ns = state.get(_account._STORAGE_NAMESPACE_KEY)
            if isinstance(existing_ns, dict):
                namespace = dict(existing_ns)
        elif isinstance(account, AccountRecord):
            payload: dict[str, Any] = {"authuser": account.authuser}
            if account.email:
                payload["email"] = account.email
            namespace[_account._ACCOUNT_CONTEXT_KEY] = payload
        # CLEAR_ACCOUNT: leave the account key absent.
        if include_domains:
            namespace["include_domains"] = sorted(include_domains)
        if include_optional:
            namespace["include_optional"] = True
        if namespace:
            namespace.setdefault("version", _account._STORAGE_NAMESPACE_VERSION)
            filtered[_account._STORAGE_NAMESPACE_KEY] = namespace

        # (5) Import backup, inside the lock, before overwriting.
        backup_path: Path | None = None
        if backup and path.exists():
            candidate = path.with_name(path.name + ".bak")
            shutil.copy2(path, candidate)
            # ``copy2`` preserves the SOURCE mode; force 0600 so a backup of a
            # legacy/world-readable storage_state never leaks credentials at rest.
            if sys.platform != "win32":
                with contextlib.suppress(OSError):
                    os.chmod(candidate, 0o600)
            backup_path = candidate

        _write_state_unchecked(path, filtered)
    # Outside the storage lock (its own sibling ``.lock``, matching
    # ``write_account_metadata``'s ordering): the in-band write just committed
    # (or explicitly cleared) the account binding.
    #
    # KEEP_ACCOUNT (the import-cookies default) with NO existing in-band record
    # to carry is NOT an intentional "no account" decision — it means the caller
    # (typically a fresh browser/import jar with no ``notebooklm`` namespace of
    # its own) never considered the account question at all. Scrubbing the
    # legacy sibling unconditionally in that case PERMANENTLY DESTROYS a
    # pre-v0.5.0 profile's only copy of its account binding: nothing was
    # embedded in-band, and the legacy record is gone from disk with no reader
    # left to find it (verified: `auth import-cookies` on such a profile drops
    # authuser 3 -> 0 irrecoverably). Promote instead — it embeds a legacy
    # record if one exists (then scrubs it) and is a safe no-op otherwise.
    #
    # An EXPLICIT decision — CLEAR_ACCOUNT, or an AccountRecord that did embed —
    # must still scrub directly: CLEAR_ACCOUNT means the caller deliberately
    # wants no account bound, and promoting there would resurrect a binding
    # that was just intentionally cleared.
    if account is KEEP_ACCOUNT and _account._ACCOUNT_CONTEXT_KEY not in namespace:
        _account.promote_legacy_account(path)
    else:
        _account._drop_legacy_account_key(path)
    return LoginWriteOutcome(LoginWriteStatus.OK, backup_path=backup_path)


# --- Master-token writers (relocated from ``master_token.py``) --------------


def persist_minted_jar(
    path: Path,
    jar: httpx.Cookies,
    *,
    email: str | None,
    force: bool = False,
    refuse_unknown_owner: bool = True,
) -> None:
    """Replace the cookies in ``storage_state.json`` with a freshly-minted jar.

    Relocated from ``master_token.persist_minted_jar``, now routed through
    :func:`_write_state_unchecked` (fsync durability + temp cleanup, closing
    [storage-F5]) while keeping the storage lock it already held and its
    rebind-to-minted-account namespace semantics. Old cookies are *replaced*, not
    merged — a re-mint is a brand-new session. Full-file replace intent:
    **fails closed**.

    b-PR2 additionally applies the write-time domain filter
    (:func:`filter_storage_state_cookies_by_domain_policy`, default policy —
    preserve-trusted-roots) to the minted cookies before they reach disk, closing
    the L4 unfiltered-persist gap. The rebind to the minted account
    (``authuser=0`` + the minted ``email``) is unaffected: the filter only
    narrows the cookie rows, never the account namespace.

    #2103 PR-2 D6: the authoritative account-ownership guard lives HERE, under
    the storage-write lock this function already holds — not only in
    :func:`notebooklm._auth.master_token.assert_account_writable`'s pre-mint
    advisory check, which cannot see a caller that mints and persists directly
    (the documented low-level recipe does exactly that) and cannot close the
    TOCTOU window between a pre-check and this write. Existing storage bound to
    a DIFFERENT recorded email than ``email`` always raises
    :class:`notebooklm._auth.master_token.MasterTokenError` unless ``force``.
    No existing storage: proceeds unconditionally (nothing to protect yet).

    ``refuse_unknown_owner`` (default ``True``) additionally refuses existing
    storage with NO recorded owner at all, unless ``force``. Callers re-minting
    from a master token ALREADY paired with this exact ``storage_path`` (the
    L4 recovery rung, the no-prompt operator re-mint —
    :func:`notebooklm._auth.master_token.remint_from_stored_token`) pass
    ``refuse_unknown_owner=False``: that pairing was already trusted when the
    token was first bootstrapped for this profile, so an account-less
    profile (never bound to an ``--account``, e.g. a cookie-only
    ``import-cookies`` profile — empirically the COMMON case, not the "rare"
    one D6 originally assumed) must not lose mid-session self-recovery. A
    caller *selecting* an account for the first time
    (:func:`notebooklm._auth.master_token.bootstrap_from_oauth_token`) keeps
    the default: minting into an existing, unrecorded-owner profile is
    exactly the ambiguous case worth refusing without an explicit ``force``.
    """
    from . import account as _account  # noqa: PLC0415 (avoid the account<->writer cycle)
    from . import master_token as _master_token  # lazy: avoid import cycle
    from ._browser_cookie_filter import (  # noqa: PLC0415 (deferred; true leaf, no cycle either way)
        filter_storage_state_cookies_by_domain_policy,
    )

    _ensure_secure_parent_dir(path)
    lock_path = _storage_state_lock_path(path)
    with _acquire_storage_lock(lock_path, log_prefix="persist_minted_jar") as state:
        if state != "held":
            raise LockUnavailableError(
                f"persist_minted_jar: storage lock unavailable at {lock_path}"
            )
        data: dict[str, Any] = {}
        existed = path.exists()
        if existed:
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                data = loaded if isinstance(loaded, dict) else {}
            except json.JSONDecodeError:
                data = {}
        if existed and force:
            logger.debug("persist_minted_jar: force=True bypasses the account-ownership guard.")
        elif existed:
            existing_owner = _account.read_account_metadata_from_storage_state(data).get("email")
            existing_owner = existing_owner.strip() if isinstance(existing_owner, str) else None
            if not existing_owner:
                if refuse_unknown_owner:
                    raise _master_token.MasterTokenError(
                        "This profile has no recorded account owner; refusing to overwrite "
                        "its session with a freshly minted one without force=True."
                    )
                logger.debug(
                    "persist_minted_jar: existing storage has no recorded owner; proceeding "
                    "with refuse_unknown_owner=False (re-mint from a token already paired "
                    "with this storage_path, not a fresh account selection)."
                )
            elif existing_owner.casefold() != (email or "").casefold():
                raise _master_token.MasterTokenError(
                    f"This profile already belongs to {existing_owner}, but the mint is "
                    f"for {email or '(no account)'}. Minting here would overwrite "
                    f"{existing_owner}'s session and master token. Pass force=True to "
                    "overwrite this profile intentionally."
                )
        # Apply the write-time domain filter to the minted jar (L4 gap): the
        # minted cookies were previously persisted raw. Default policy — trusted
        # Google roots are preserved (main's preserve-trusted-roots behavior).
        minted_state = _master_token.storage_state_from_jar(jar)
        filtered_minted = filter_storage_state_cookies_by_domain_policy(minted_state)
        data["cookies"] = filtered_minted["cookies"]
        data.setdefault("origins", [])
        ns_raw = data.get("notebooklm")
        ns: dict[str, Any] = ns_raw if isinstance(ns_raw, dict) else {}
        ns["version"] = 1
        ns["account"] = {"authuser": 0, **({"email": email} if email else {})}
        data["notebooklm"] = ns
        _write_state_unchecked(path, data)


def write_master_token(path: Path, *, email: str, master_token: str, android_id: str) -> None:
    """Persist a ``master_token.json`` record at mode 0600 (full-account credential).

    Relocated from ``master_token.write_master_token``, now routed through
    :func:`_write_state_unchecked` (atomic + fsync-durable + temp cleanup) and guarded
    by a bounded sibling ``.master_token.json.lock`` — it was previously lockless
    (part of [storage-F5]). RMW intent: **fails closed**.
    """
    from . import master_token as _master_token  # lazy: avoid import cycle

    payload = {
        "version": _master_token._MASTER_TOKEN_VERSION,
        "email": email,
        "android_id": android_id,
        "master_token": master_token,
    }

    def _write() -> None:
        _write_state_unchecked(path, payload)

    # The transaction template derives the sibling dotted lock for this
    # credential file (distinct from the profile's storage-state lock — a
    # different file) and ensures the parent dir is secure before taking it.
    in_storage_transaction(
        path,
        _write,
        log_prefix="write_master_token",
        on_unavailable=raise_on_lock_unavailable("write_master_token"),
    )
