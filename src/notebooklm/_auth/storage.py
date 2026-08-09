"""Profile persistence for authentication: storage state, its lock, and its writers.

One deep module for the whole ``storage_state.json`` seam. It merges what used to
be three cap-split files — ``storage.py`` (snapshot/CAS merge math + the file-lock
primitive), ``storage_writer.py`` (the canonical writer, ADR-0029) and
``storage_transaction.py`` (the write transaction template, ADR-0031 Stage 3) —
under ADR-0033's sanctioned-merge policy. The split existed only to satisfy the
ADR-0008 module-size cap, and it was re-joined at runtime by function-local
imports in both directions; merging removes those without changing any behaviour.
ADR-0033 PR 4.2 then relocated ``_browser_cookie_filter.py``'s write-time
cookie-domain filter in as well — misfiled under a ``browser_`` name, but write
policy applied by three of the writers here — retiring three more lazy imports.
ADR-0033 PR 5.2 relocated the account-RECORD helpers from ``_auth/account.py``
(section 7b): reading the record, deriving an in-band-shaped one from a legacy
sibling, the detached durable-promotion one-shot, and the sibling scrub. They
are persistence — same document, same lock, driving the writers here directly —
and splitting them from those writers cost a bidirectional function-local
import pair (3 sites there, 5 here) which that relocation retires in full.
``account.py`` keeps the NETWORK identity half (``enumerate_accounts``,
``_probe_authuser``, page-email extraction, the routing-value formatters) and
imports this module at module scope; the edge is now one-way.

The file is organised in labelled sections mirroring the former modules:

1. **Lock compatibility wrappers** — :func:`_file_lock` and blocking
   :func:`_file_lock_exclusive`, routed through the dependency-bottom
   :class:`~notebooklm._auth.storage_lock.StorageLockManager`.
2. **Transaction compatibility aliases** — secure-parent preparation,
   :func:`in_storage_transaction`, and its three lock-unavailable policies now
   have their real definitions in :mod:`notebooklm._auth.profile_store`.
4. **Snapshot types** — the path-aware cookie identity/value tuples and
   :class:`CookieSaveResult`.
5. **CAS + merge math** — snapshotting, the legacy and snapshot/delta merges, and
   :func:`save_cookies_to_storage`, the ADR-0029-pinned monkeypatchable delegate
   seam (``_runtime/lifecycle.py`` late-binds it; ~20 test files patch it).
6. **Writer outcome types** — the value-free enums/records the intent writers
   return.
7. **Write-time cookie-domain compatibility aliases** — the implementation and
   value-free malformed-row diagnostics live in the dependency-bottom
   :mod:`notebooklm._auth.cookie_filter` leaf.
8. **Temporary policy writers and adapters** — profile intents and the
   master-token intent, all routed through typed owners.
9. **Account records** (labelled ``SECTION 7b`` in the source, where it sits
   directly after the two in-band account writers it drives) — the record
   readers, the ``_sanitize_legacy_account_record`` derivation that gives
   ADR-0029 its anti-wrong-account guarantee, the detached durable-promotion
   one-shot with its ``atexit`` drain, and :func:`_drop_legacy_account_key`,
   the sibling ``context.json`` scrub. Relocated from ``_auth/account.py``
   (ADR-0033 PR 5.2).

This module remains the v0.x compatibility and policy façade. The sealed
atomic capability now lives in :mod:`notebooklm._auth.credential_io`, and
:class:`notebooklm._auth.profile_store.ProfileStore` owns profile reads and the
cookie transactions. The compatibility adapters below reach only the
appropriate typed owner.

Intent-shaped API (all synchronous, all serialize on the canonical storage lock,
all write through the typed credential commit spine):

* :func:`merge_cookie_delta` — the compatibility adapter behind
  :func:`save_cookies_to_storage`. It is a **CAS** intent and therefore **fails
  open** on lock unavailability (status quo), delegated to ``ProfileStore``.
* :func:`update_account_metadata` / :func:`clear_in_band_account` — the in-band
  account writers relocated from :mod:`notebooklm._auth.account`. These are
  **full-file RMW** intents: :func:`update_account_metadata` **fails closed**
  (raises :class:`LockUnavailableError`) because failing open could overwrite a
  concurrent CAS delta; :func:`clear_in_band_account` is best-effort cleanup and
  swallows lock unavailability, matching the pre-refactor semantics.
* :func:`replace_from_remint` — the full cookie-replace re-mint persister for the
  BROWSER-CAPTURE arms (L3 headless-launch + interactive + CDP), relocated from
  the bare ``atomic_write_json`` sites in :mod:`notebooklm._auth.browser_capture`.
  The thin adapter delegates filtering, namespace carry/drop, and the bounded
  transaction to :class:`ProfileStore`. **Fails closed** (returns
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
``filelock`` in favour of the project-internal storage lock manager via a
**platform-neutral bounded acquire**: a non-blocking probe plus deadline/jitter
retry (default 90 s), then the
per-intent failure policy above. The store's CAS merge keeps the status-quo
blocking manager acquire (fail-open); :func:`_file_lock_exclusive` remains a
v0.x compatibility alias only. ``StorageLockManager`` owns an
in-process ``threading.Lock`` keyed by the exact raw ``os.fspath(lock_path)``
spelling (ordering: in-process lock -> OS lock); :func:`_file_lock` delegates to
that owner. Threads serialize before the OS lock, while the distinct
``.{name}.rotate.lock`` sentinel is never collapsed into the storage lock.

The fail-closed writers raise :class:`~notebooklm.exceptions.LockUnavailableError`
(public via ``notebooklm.exceptions`` / the ``notebooklm.auth`` facade). It
subclasses :class:`TimeoutError` — itself an :class:`OSError` — exactly mirroring
the ``filelock.Timeout`` MRO it replaces, so callers' existing
``except OSError`` / ``except TimeoutError`` arms (``_auth/recovery.py`` around
``persist_minted_jar``; the CLI login writers around ``write_account_metadata``)
keep catching a lock failure unchanged; only the exception type and the 10 s->90 s
bound differ.

Permission contract (POSIX): bounded writers ensure the parent directory is
``0700`` and typed commits write ``0600`` files. Cookie saves intentionally keep
the manager's raw blocking-lock parent creation without an additional chmod.
On Windows we rely on ``%USERPROFILE%`` ACL inheritance.

Outcome types are **value-free by contract**: :class:`WriteOutcome` may carry
only an enum status — never cookie values, state dicts, jar objects, or caught
exceptions.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import logging
import os
import shutil
import sys
import threading
import warnings
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, NamedTuple, TypeAlias, cast

import httpx
from filelock import FileLock

# The PUBLIC ``atomic_write_json`` remains here for the relocated legacy-account
# scrub only. Credential/profile writes reach the sealed capability through the
# typed wrappers in ``credential_io``.
# the relocated legacy-account scrub :func:`_drop_legacy_account_key` writes the
# SIBLING ``context.json``, not ``storage_state.json``, so it must go through the
# guarded public primitive — the same primitive it used in ``account.py``.
from .._atomic_io import atomic_write_json

# ``LockUnavailableError`` is the public, canonical home for the fail-closed
# lock-failure exception (``notebooklm.exceptions`` — also re-exported on the
# ``notebooklm.auth`` facade). It subclasses ``TimeoutError`` (an ``OSError``),
# exactly mirroring the ``filelock.Timeout`` MRO it replaces, so existing
# ``except OSError`` arms keep catching a lock failure. Re-exported here for the
# writers that raise it.
from ..exceptions import LockUnavailableError
from . import cookie_merge as _cookie_merge
from . import cookies as _auth_cookies
from .cookie_filter import (
    _safe_cookie_shape as _safe_cookie_shape,
)
from .cookie_filter import (
    filter_storage_state_cookies_by_domain_policy as filter_storage_state_cookies_by_domain_policy,
)
from .cookie_types import Cookie, CookieIdentity, CookieJar
from .credential_io import _commit_master_token_json, _commit_profile_json
from .paths import canonical_storage_key, resolve_auth_json_env
from .profile_account import DomainSelection, ProfileAccount
from .profile_document import ProfileDocument
from .profile_store import (
    CookieMergeDisposition,
    ProfileStore,
    RemintWriteRequest,
    ReplaceStatus,
    in_storage_transaction,
    raise_on_lock_unavailable,
    report_on_lock_unavailable,
    skip_on_lock_unavailable,
)
from .profile_store import (
    _ensure_secure_parent_dir as _ensure_secure_parent_dir,
)
from .profile_store import (
    _LockUnavailablePolicy as _LockUnavailablePolicy,
)
from .storage_lock import LockRequest, StorageLockManager

logger = logging.getLogger("notebooklm.auth")

CookieKey: TypeAlias = _auth_cookies.CookieKey
_cookie_is_http_only = _auth_cookies._cookie_is_http_only

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
    "clear_account_metadata",
    "clear_in_band_account",
    "get_account_email_for_storage",
    "get_authuser_for_storage",
    "in_storage_transaction",
    "merge_cookie_delta",
    "persist_minted_jar",
    "promote_legacy_account",
    "raise_on_lock_unavailable",
    "read_account_metadata",
    "read_account_metadata_from_storage_state",
    "replace_from_login",
    "replace_from_remint",
    "report_on_lock_unavailable",
    "resolve_account_identity",
    "save_cookies_to_storage",
    "skip_on_lock_unavailable",
    "snapshot_cookie_jar",
    "update_account_metadata",
    "write_account_metadata",
    "write_master_token",
]


# ==========================================================================
# SECTION 1 — LOCK COMPATIBILITY WRAPPERS
# The lock manager owns process/OS mechanics; these retain the v0.x seams.
# ==========================================================================


_STORAGE_LOCKS = StorageLockManager.process_default()


@contextlib.contextmanager
def _file_lock(lock_path: Path, *, blocking: bool, log_prefix: str) -> Iterator[str]:
    """Delegate one v0.x string-valued acquisition to the shared manager."""
    request = LockRequest(path=lock_path, blocking=blocking, operation=log_prefix)
    with _STORAGE_LOCKS.acquire(request) as state:
        yield state.value


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

    Every ``storage_state.json`` mutator now lives in THIS module and takes
    this sentinel through the shared storage lock manager — the
    account-metadata writers included (ADR-0033 PR 5.2 moved them here). No
    ``filelock.FileLock`` holder of this sentinel remains, so the old
    cross-mechanism POSIX interop is no longer load-bearing; this module's one
    remaining ``filelock`` use targets the *sibling* ``context.json``.

    The lock is per-process: threads within one process aren't serialized —
    that's the intra-process ``threading.Lock`` held by the client. If the
    lock can't be acquired (e.g. NFS where flock semantics vary, read-only
    parent dir, fd exhaustion), the save proceeds anyway; correctness in
    that mode is best-effort and relies on the snapshot/delta CAS guards in
    :func:`_merge_cookies_with_snapshot` alone. The first time this
    fallback fires per process emits a WARNING so operators learn their
    deployment is running without cross-process coordination.
    """
    with _file_lock(lock_path, blocking=True, log_prefix="save_cookies_to_storage") as state:
        if state == "unavailable" and _STORAGE_LOCKS._claim_cookie_warning():
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
    identity = CookieIdentity(key.name, key.domain, key.path)
    return {
        CookieSnapshotKey(variant.name, variant.domain, variant.path)
        for variant in _cookie_merge.equivalent_identities(identity)
    }


def _stored_cookie_snapshot_key(stored_cookie: Any) -> CookieSnapshotKey | None:
    """Build a path-aware snapshot key from a Playwright storage_state cookie."""
    identity = _cookie_merge.stored_cookie_identity(stored_cookie)
    if identity is None:
        return None
    return CookieSnapshotKey(identity.name, identity.domain, identity.path)


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
    # :func:`merge_cookie_delta` (section 5 below). This module-level
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


def _cookie_jar_for_merge(cookie_jar: httpx.Cookies, *, include_none: bool) -> CookieJar:
    """Adapt a live jar without filtering domains or freezing legacy tuples."""
    return CookieJar(
        Cookie(
            name=cookie.name,
            domain=cookie.domain,
            path=cookie.path or "/",
            value=cast(str, cookie.value),
            expires=cookie.expires,
            secure=cast(bool, cookie.secure),
            http_only=_cookie_is_http_only(cookie),
        )
        for cookie in cookie_jar.jar
        if cookie.name and cookie.domain and (include_none or cookie.value is not None)
    )


def _cookie_jar_from_snapshot(snapshot: CookieSnapshot) -> CookieJar:
    return CookieJar(
        Cookie(
            name=key.name,
            domain=key.domain,
            path=key.path,
            value=value.value,
            expires=value.expires,
            secure=value.secure,
            http_only=value.http_only,
        )
        for key, value in snapshot.items()
    )


def _recovery_observation_value(
    observation: RecoveryCookieObservation | None,
) -> _cookie_merge.RecoveryObservation | None:
    if observation is None:
        return None
    return _cookie_merge.RecoveryObservation(
        {
            CookieIdentity(key.name, key.domain, key.path): frozenset(values)
            for key, values in observation.items()
        }
    )


def _install_decided_document(
    storage_data: dict[str, Any], document: ProfileDocument | None
) -> None:
    if document is None:
        return
    decided = document.to_json()
    storage_data.clear()
    storage_data.update(decided)


def _merge_cookies_legacy(cookie_jar: httpx.Cookies, storage_data: dict[str, Any]) -> int:
    """Legacy merge: trust in-memory whenever it differs from disk.

    Vulnerable to the stale-overwrite-fresh race (Appendix A2). Kept only for
    callers that have not yet opted into snapshot semantics. New callers
    must pass ``original_snapshot`` to :func:`save_cookies_to_storage`.

    Returns:
        Number of cookie entries added or modified in ``storage_data``.
    """
    decision = _cookie_merge.decide_legacy_cookie_overlay(
        stored=ProfileDocument.decode(storage_data),
        observation=_cookie_jar_for_merge(cookie_jar, include_none=True),
    )
    _install_decided_document(storage_data, decision.document)
    return decision.updated_rows


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
    decision = _cookie_merge.decide_cookie_merge(
        stored=ProfileDocument.decode(storage_data),
        observation=_cookie_jar_for_merge(cookie_jar, include_none=False),
        baseline=_cookie_jar_from_snapshot(original_snapshot),
        recovery_observation=_recovery_observation_value(recovery_observation),
    )
    for identity, kind in decision._conflicts:
        if kind == "existing":
            logger.debug(
                "Skipped CAS-guarded value update of %s on %s: disk value "
                "differs from snapshot (sibling write preserved)",
                identity.name,
                identity.domain,
            )
        else:
            logger.debug(
                "Skipped CAS-guarded value update of new cookie %s on %s: "
                "disk row already exists (sibling write preserved)",
                identity.name,
                identity.domain,
            )
    _install_decided_document(storage_data, decision.document)
    rejected = frozenset(
        CookieSnapshotKey(identity.name, identity.domain, identity.path)
        for identity in decision.rejected
    )
    return decision.updated_rows, rejected


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
# The temporary v0.x policy bodies for profile and sibling credential writes.
# Profile commits use the typed credential capability; cookie transactions are
# owned by ``ProfileStore`` above this compatibility layer.
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
    quo — the snapshot/delta CAS guards preserve correctness), delegated to
    :class:`ProfileStore`'s blocking cookie transaction. The full signature (incl.
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

    store = ProfileStore(path)
    if original_snapshot is None:
        result = store.merge_legacy_cookie_observation(
            _cookie_jar_for_merge(cookie_jar, include_none=True)
        )
    else:
        result = store.merge_cookie_observation(
            _cookie_jar_for_merge(cookie_jar, include_none=False),
            baseline=_cookie_jar_from_snapshot(original_snapshot),
            recovery_observation=_recovery_observation_value(recovery_observation),
        )

    rejected = frozenset(
        CookieSnapshotKey(identity.name, identity.domain, identity.path)
        for identity in result.rejected
    )
    projected = CookieSaveResult(
        result.disposition in {CookieMergeDisposition.APPLIED, CookieMergeDisposition.NO_CHANGE},
        rejected,
    )
    return _cookie_save_return(projected, return_result=return_result)


# --- In-band account writers (relocated from ``account.py``) ----------------
# The WRITE half of the account record; its readers, the promotion one-shot and
# the sibling scrub follow in section 7b (ADR-0033 PR 5.2).


def update_account_metadata(
    storage_path: Path,
    *,
    authuser: int,
    email: str | None = None,
    only_if_absent: bool = False,
) -> bool:
    """Project the v0.x raw account arguments through the path-owned store.

    ``False`` remains reserved for an ``only_if_absent`` race lost to a
    non-empty in-band record; lock and commit failures still escape.
    """
    return bool(
        ProfileStore(storage_path).update_account(
            ProfileAccount(authuser=authuser, email=email),
            only_if_absent=only_if_absent,
        )
    )


def clear_in_band_account(storage_path: Path) -> None:
    """Project the v0.x best-effort clear through the path-owned store."""
    ProfileStore(storage_path).clear_account()


# ==========================================================================
# SECTION 7b — ACCOUNT RECORDS: READERS, THE LEGACY ONE-SHOT, AND THE SCRUB
# Relocated from ``_auth/account.py`` (ADR-0033 PR 5.2), which kept the NETWORK
# identity half. Everything below is *persistence*: it reads and writes the same
# ``storage_state.json`` document as the writers above, under the same lock, and
# drives them directly — which is why all EIGHT function-local imports the split
# used to need (3 there, 5 here) are now plain same-module references.
# ==========================================================================


_ACCOUNT_CONTEXT_KEY = "account"

# The unified atomic profile-state format embeds account metadata
# inside ``storage_state.json`` under a ``notebooklm`` namespace key, so
# a single ``atomic_write_json`` covers both cookies and account in one
# crash-safe commit. ``version`` is bumped only when the in-band schema
# changes incompatibly — version 1 is the initial shape.
_STORAGE_NAMESPACE_KEY = "notebooklm"
_STORAGE_NAMESPACE_VERSION = 1

# --- Detached one-shot legacy-account promotion (ADR-0033 PR 5.1) ------------
#
# ``read_account_metadata`` is a READ. It is called per RPC on the token-route
# path (``refresh._resolve_token_route_kwargs`` -> ``get_authuser_for_storage``),
# so it must never take the storage WRITE lock. Durable promotion of a
# pre-v0.5.0 sibling record is therefore fired off the read path as a detached
# one-shot: the read derives its answer read-only from the legacy record (see
# :func:`_sanitize_legacy_account_record` — byte-identical to what promotion
# embeds) and returns immediately, while a background worker does the write.
#
# Two pieces of process-global state, both guarded by ONE plain
# ``threading.Lock``:
#
# * ``_PROMOTION_ONCE_PATHS`` — canonical ``storage_path`` strings a promotion
#   has already been scheduled for in this process. This IS the single flight:
#   N concurrent reads of one profile schedule exactly ONE promotion, and a
#   promotion that fails is not retried in-process. Retrying would buy nothing
#   — the read already returns the right record without it — and would put a
#   failing write back on a per-RPC path, which is the whole problem. Unbounded
#   growth is not a concern: real deployments have a handful of profiles.
# * ``_PROMOTION_THREADS`` — the workers still in flight, so tests can join
#   them deterministically (:func:`_drain_promotions_for_tests`). Production
#   never joins; each worker deregisters itself when it finishes.
#
# ``_PROMOTION_LOCK`` is a *scheduling* lock, not a storage lock: it is taken
# only on the legacy-only branch of the read, is held for a set lookup plus a
# ``Thread.start()``, and is never held across file I/O. The in-band fast path
# every per-RPC read walks takes NO lock at all (pinned by
# ``test_auth_account_promotion.py``).
#
# Deliberately ``threading``, not ``asyncio``: ``read_account_metadata`` is a
# synchronous function reached from CLI code with no running event loop as
# often as from ``async`` code, and the work it defers (``filelock`` acquire +
# atomic write) is blocking I/O. ``_auth.single_flight`` is the coalescing core
# for *awaitable* work — it requires a running loop (``asyncio.get_running_loop``
# in ``_claim``) and would leave the CLI entry path uncovered. Using threads
# also keeps this module free of lazily-constructed loop-bound primitives (the
# #1196 class the loop-affinity guard polices).
_PROMOTION_LOCK = threading.Lock()
_PROMOTION_ONCE_PATHS: set[str] = set()
_PROMOTION_THREADS: set[threading.Thread] = set()


def _account_context_path(storage_path: Path) -> Path:
    """Return the context.json path that annotates ``storage_path``.

    Legacy two-file layout: this sibling held ``account`` metadata before
    the unified format embedded it in ``storage_state.json``. Post-migration,
    it keeps CLI context state (``notebook_id``, ``conversation_id``) but no
    longer stores the ``account`` key.
    """
    return storage_path.with_name("context.json")


def _read_in_band_account(storage_path: Path) -> dict[str, Any]:
    """Read account metadata from inside ``storage_state.json``.

    Returns ``{}`` when the namespace key is missing, malformed, or the file
    cannot be read. Callers fall back to the legacy sibling ``context.json``.
    """
    document = ProfileStore(storage_path)._read_account_document()
    if document is None:
        return {}
    return read_account_metadata_from_storage_state(document.to_json())


def read_account_metadata_from_storage_state(storage_state: Any) -> dict[str, Any]:
    """Read in-band account metadata from parsed Playwright storage state."""
    if not isinstance(storage_state, dict):
        return {}
    namespace = storage_state.get(_STORAGE_NAMESPACE_KEY)
    if not isinstance(namespace, dict):
        return {}
    account = namespace.get(_ACCOUNT_CONTEXT_KEY)
    return account if isinstance(account, dict) else {}


def _read_legacy_account(storage_path: Path) -> dict[str, Any]:
    """Read the pre-v0.5.0 sibling ``context.json`` account record.

    Consumed ONLY by :func:`promote_legacy_account` (the one-shot in-band
    migration). Never a standing read path — see ``read_account_metadata``.
    """
    context_path = _account_context_path(storage_path)
    if not context_path.exists():
        return {}
    try:
        data = json.loads(context_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.debug("account metadata read failed at %s: %s", context_path, e)
        return {}
    if not isinstance(data, dict):
        return {}
    account = data.get(_ACCOUNT_CONTEXT_KEY)
    return account if isinstance(account, dict) else {}


def read_account_metadata(storage_path: Path | None) -> dict[str, Any]:
    """Read profile account metadata, self-healing a legacy two-file profile.

    **This is a read. It takes no lock and issues no write.** Per-RPC token
    routing calls it on every request (``_resolve_token_route_kwargs`` ->
    :func:`get_authuser_for_storage`), so the durable half of the legacy
    migration is *detached* from it: see :func:`_schedule_legacy_promotion`.

    Unified layout: account metadata lives inside ``storage_state.json`` under
    the ``notebooklm`` namespace key. This reader never returns a raw
    pass-through of the pre-v0.5.0 sibling ``context.json`` — a standing read
    fallback trusting an unmigrated value is #2103's hazard.

    Instead the three branches are:

    1. **In-band present** — the overwhelming majority of calls, and the only
       one per-RPC routing walks once any profile has been read: one file read,
       one dict lookup, zero locks, zero threads.
    2. **Nothing anywhere** — ``{}``, but only after a SECOND in-band read. A
       promotion committing between this reader's two file samples would
       otherwise leave it holding a pre-embed in-band sample and a post-strip
       sibling sample, neither carrying the binding — ``authuser=0``, i.e.
       #2103 reached by timing rather than staleness. The strip follows the
       embed, so a vanished sibling implies a committed record; the re-read
       finds it. ``TestPromotionRacingTheReader`` spells out the interleaving.
    3. **Legacy-only** — the record is DERIVED read-only, through the very
       function promotion uses to build what it embeds
       (:func:`_sanitize_legacy_account_record`), so the caller sees a genuinely
       in-band-shaped record — never a raw pass-through — whether or not the
       durable write has landed. That write is scheduled once per path, in the
       background; the read does not wait.

    That derivation is the anti-wrong-account contract, and it makes promotion
    timing irrelevant to correctness: one that is slow, contended, or
    permanently failing (read-only dir, full disk) changes nothing observable
    except how long the sibling survives. ``test_auth_account_promotion.py``
    pins the derived record field-by-field against the promoted one.

    The ``account`` object records the Google ``authuser`` index used when the
    profile was authenticated. Profiles from before account-binding shipped
    (and single-Google-account users) have none, and use ``authuser=0``.

    Args:
        storage_path: Path to ``storage_state.json``. ``None`` means the profile
            is loaded from ``NOTEBOOKLM_AUTH_JSON`` — no sibling to promote
            from, so env-auth skips promotion and its record is read from the
            parsed payload by :func:`read_account_metadata_from_storage_state`.

    Returns:
        Parsed metadata dict, or ``{}`` only when no legacy OR in-band record
        exists at all.
    """
    if storage_path is None:
        return {}
    in_band = _read_in_band_account(storage_path)
    if in_band:
        return in_band
    legacy = _read_legacy_account(storage_path)
    if not legacy:
        # Never ``{}`` outright — a promotion may have landed between our two
        # samples. See branch 2.
        return _read_in_band_account(storage_path)
    # Re-read in-band before trusting the legacy record. A concurrent fresh
    # login / account-switch (or this process's own promotion worker) may have
    # committed one while we were reading the sibling, and in-band ALWAYS wins:
    # it is the newer, authoritative binding, and preferring a stale legacy
    # record over it is precisely the wrong-account-routing hazard. Cheap —
    # this branch is only reached on a not-yet-migrated profile.
    in_band_after = _read_in_band_account(storage_path)
    if in_band_after:
        return in_band_after
    _schedule_legacy_promotion(storage_path)
    return _sanitize_legacy_account_record(legacy)


def _schedule_legacy_promotion(storage_path: Path) -> threading.Thread | None:
    """Fire the durable promotion in the background, once per canonical path.

    The caller has already derived its answer read-only, so this exists purely
    to make the migration *durable* (and to scrub the legacy sibling, a privacy
    obligation). Nothing downstream of the read depends on it succeeding, or on
    when it finishes.

    Single-flight: the ``_PROMOTION_ONCE_PATHS`` membership test and the
    insertion happen under one ``_PROMOTION_LOCK`` hold, so N concurrent
    readers of the same profile produce exactly ONE worker. It is a one-shot,
    not a retry loop — a failed promotion is not re-attempted in this process
    (see the state block above for why).

    ``Thread.start()`` runs INSIDE the lock so a concurrent
    :func:`_drain_promotions_for_tests` can never observe a worker that is
    registered but not yet started (``join`` on an unstarted thread raises).
    ``start()`` returns as soon as the worker is bootstrapped, not when it
    finishes, so the reader is not made to wait on the write.

    Returns:
        The worker that was started, or ``None`` when this path had already
        scheduled one (test/diagnostic affordance; production ignores it).
    """
    # Keyed on the CANONICAL path, like every other in-process dedupe in
    # ``_auth`` (the keepalive throttle, the poke-lock registry, the refresh
    # flock): two spellings of one file — relative vs absolute, ``~``-prefixed,
    # or through a symlink — must collapse to one key or the single flight is
    # silently bypassed.
    canonical = str(canonical_storage_key(storage_path))
    with _PROMOTION_LOCK:
        if canonical in _PROMOTION_ONCE_PATHS:
            return None
        _PROMOTION_ONCE_PATHS.add(canonical)
        worker = threading.Thread(
            target=_run_promotion_once,
            args=(storage_path,),
            name="notebooklm-account-promotion",
            daemon=True,
        )
        _PROMOTION_THREADS.add(worker)
        worker.start()
    return worker


def _run_promotion_once(storage_path: Path) -> None:
    """Worker body: promote durably, then deregister.

    :func:`promote_legacy_account` is already best-effort and swallows every
    realistic failure itself. The broad guard here is for the two things it
    cannot promise a *detached* caller: an unexpected exception has no caller
    to surface it to (it would land in ``threading``'s excepthook as a stray
    traceback), and a daemon worker torn down mid-interpreter-shutdown can
    raise from arbitrary places.
    """
    try:
        promote_legacy_account(storage_path)
    except BaseException as e:  # noqa: BLE001 — a detached worker must never escape
        logger.debug("Background legacy account promotion crashed for %s: %s", storage_path, e)
    finally:
        with _PROMOTION_LOCK:
            _PROMOTION_THREADS.discard(threading.current_thread())


def _drain_promotions(timeout: float) -> None:
    """Join every in-flight promotion worker, each bounded by ``timeout``."""
    with _PROMOTION_LOCK:
        workers = list(_PROMOTION_THREADS)
    for worker in workers:
        worker.join(timeout)


def _drain_promotions_for_tests(timeout: float = 30.0) -> None:
    """Join every in-flight promotion worker (test/diagnostic helper).

    No production READ waits on this — the whole point of the one-shot is that
    the durable write never sits on the read path. Tests use it to make the
    durable half observable, and ``tests/conftest.py`` drains + clears the
    process-global state between tests so a worker started by one test cannot
    write into another's ``tmp_path``.
    """
    _drain_promotions(timeout)


#: PER-WORKER bound on the interpreter-exit wait, NOT an aggregate:
#: :func:`_drain_promotions` joins sequentially, so N legacy profiles wait up to
#: N times this. Workers are daemons, so a hung one is killed, not waited on.
_PROMOTION_EXIT_JOIN_SECONDS = 2.0


@atexit.register
def _drain_promotions_at_exit() -> None:
    """Let the detached promotion land before a short-lived process exits.

    Without this the one-shot is effectively dead in the CLI, which is where
    legacy profiles actually live. Measured on the first draft: a real
    ``notebooklm profile list`` against a legacy-only profile migrated it 0
    times out of 6, and ``auth check`` 1 out of 6 — a pure timing race that
    real commands lose, because they read the account binding at the very end
    of their work and the process exits before a daemon worker gets scheduled.
    The READ was correct every time; what silently never happened was the
    durable promotion and, with it, the privacy scrub that removes a stale
    account email from ``context.json`` at rest.

    ``atexit`` handlers run BEFORE daemon threads are torn down, so joining
    here is what makes the write land. It is bounded and best-effort: a
    hard-killed process, ``os._exit`` or a fatal signal skips it, and the next
    read schedules a fresh one-shot — the promotion is idempotent.

    Registration moved modules with the code (ADR-0033 PR 5.2) and that is
    safe by construction, not by luck: the hook is registered at import time of
    whichever module DEFINES :func:`read_account_metadata`, and every entry path
    that can schedule a promotion must first import that module in order to call
    the read at all. The hazard would be an alias — a shim that re-exports the
    read from a module the hook does NOT live in — so there is none.
    """
    _drain_promotions(_PROMOTION_EXIT_JOIN_SECONDS)


def _sanitize_legacy_account_record(legacy: dict[str, Any]) -> dict[str, Any]:
    """Normalize a raw legacy ``context.json[account]`` dict into the exact
    record :func:`promote_legacy_account` embeds in-band.

    This is the anti-wrong-account contract's load-bearing piece, and the
    reason it is ONE function rather than two agreeing implementations:
    :func:`read_account_metadata` returns this on a legacy-only profile
    *before* (and, if promotion never lands, instead of) the durable write, so
    "derived read-only" and "read back after promotion" must be
    indistinguishable field-for-field. Keeping it shared makes them so by
    construction; ``tests/unit/test_auth_account_promotion.py`` proves it over
    a matrix of malformed legacy shapes. Mirrors ``get_authuser_for_storage`` /
    ``get_account_email_for_storage``'s own sanitization rules."""
    raw_authuser = legacy.get("authuser")
    result: dict[str, Any] = {
        "authuser": raw_authuser if type(raw_authuser) is int and raw_authuser >= 0 else 0
    }
    raw_email = legacy.get("email")
    if isinstance(raw_email, str) and raw_email.strip():
        result["email"] = raw_email.strip()
    return result


def promote_legacy_account(storage_path: Path) -> bool:
    """One-shot migration: move a legacy sibling ``account`` record in-band.

    The pre-v0.5.0 two-file layout stored account metadata (``authuser`` /
    ``email``) in the sibling ``context.json``. This helper promotes that
    record into ``storage_state.json`` via the canonical storage writer and
    strips the legacy key.

    **Never called on a read's own thread.** Its three callers are the
    detached one-shot worker :func:`_run_promotion_once` (scheduled by
    :func:`read_account_metadata`), the startup layout migration
    (``migration.py``, which only fires for pre-v0.5.0 two-file profiles), and
    :func:`replace_from_login`'s ``KEEP_ACCOUNT``-with-no-in-band-record
    arm, where promoting instead of scrubbing is what stops
    ``auth import-cookies`` from permanently destroying a legacy profile's only
    copy of its binding. The read's correctness does not depend on any of them:
    it derives the same record read-only (:func:`_sanitize_legacy_account_record`)
    and this function only makes that durable.

    Ordering is crash-safe for the BINDING (never lost), not for the RESIDUE
    (not guaranteed promptly cleaned up). **Two files, two locks, no shared
    critical section** — the window is structural, not an oversight:

    * step 1 embeds into ``storage_state.json`` under the dotted storage
      sentinel ``.storage_state.json.lock`` (:func:`_file_lock`, via
      :func:`update_account_metadata`);
    * step 2 strips ``context.json[account]`` under the *separate*
      ``context.json.lock`` (``filelock.FileLock``, via
      :func:`_drop_legacy_account_key`).

    Nothing spans both, and nothing may: the two files are locked by two
    different sentinels held by two different mechanisms, and widening either
    hold to cover the other step would either invert an existing lock order or
    unify mechanisms (ADR-0033 plan §1/§5 rules both out). The compensation is
    therefore *ordering*, not atomicity: embed-then-strip means a crash in
    between leaves both records present, never neither — the account binding is
    never lost, which is the correctness property this function exists for. The
    reverse order would have a window in which the binding exists nowhere.

    What that compensation does NOT buy is prompt cleanup. The NEXT call does
    NOT reliably take a strip-only branch: :func:`read_account_metadata`'s
    fast path (``if in_band: return in_band``) returns as soon as in-band is
    present and never calls this function again (and the one-shot would not
    schedule a second worker for that path even if it did), so a
    crash-mid-flight residue can survive indefinitely rather than being cleaned
    up on the very next read. This is a privacy nicety, not a correctness gap
    (the authoritative binding is the in-band record, already committed) — it
    is NOT worth a ``context.json`` existence probe on every read's fast path
    to close eagerly. A subsequent :func:`write_account_metadata` /
    :func:`clear_account_metadata` call for the same profile does still strip it
    (both call :func:`_drop_legacy_account_key` unconditionally).

    When the in-band record already exists for another reason — including a
    CONCURRENT fresh login/account-switch that won a race against THIS call
    (see ``only_if_absent`` below) — any stale legacy key IS stripped
    immediately as residue cleanup (privacy: the old account email must not
    live on at rest after a re-login); that path runs through this function,
    not through the fast path above.

    Race-safe: the write is issued with ``only_if_absent=True``, so the
    decision "is in-band still empty" is made under the SAME lock as the
    write, not by a separate unlocked check beforehand. Without that, a
    concurrent fresh login could commit its new record in the gap between
    this function's own (now-removed) unlocked check and its locked write,
    and this call would silently overwrite it with the stale legacy values
    it had already captured — reintroducing the wrong-account hazard this
    whole migration exists to close, via a race instead of a stale read.

    Best-effort by design — it must degrade to the pre-promotion state rather
    than raise, both because ``migration.py`` and :func:`replace_from_login`
    treat it as a completeness step and because its detached worker has no
    caller to report to. Returns ``True`` only when a legacy record was embedded
    by THIS call (``False`` both when there was nothing to promote and when a
    concurrent writer won the race — either way no action was needed from this
    call).

    Never creates ``storage_state.json``: if it doesn't exist yet, promotion
    is skipped (returning ``False``) rather than synthesizing a cookie-less
    file to embed into. Without this guard, a plain READ (``profile list``,
    ``auth check``) on a legacy profile whose cookies had never been captured
    (or had been removed, e.g. by the ``COOKIE_VALIDATION_FAILED`` not-exists
    contract) would itself CREATE a persistent ``storage_state.json`` with no
    cookies at all — and ``_app/profile.py``'s ``authenticated=storage.exists()``
    check runs immediately after calling this (transitively, via
    :func:`read_account_metadata`), so it would flip from correctly reporting
    "not authenticated" to incorrectly reporting "authenticated" for a profile
    with zero cookies, purely as a side effect of having been looked at.

    Args:
        storage_path: Path to ``storage_state.json`` (must be a real file
            path — env-auth profiles have no sibling and are skipped by the
            caller).
    """
    if not storage_path.exists():
        return False
    legacy = _read_legacy_account(storage_path)
    if not legacy:
        return False
    sanitized = _sanitize_legacy_account_record(legacy)
    try:
        # only_if_absent=True: the decision "should this write happen" is made
        # HERE, under the writer's own lock, not by a separate unlocked
        # _read_in_band_account check beforehand — a check-then-act split
        # would let a concurrent fresh login/account-switch land in the gap
        # and then be silently overwritten by these stale legacy values.
        #
        # No deadline override: the usual 90s full-file-RMW deadline applies.
        # It used to be shortened to 2s because this ran INSIDE
        # read_account_metadata, where a 90s lock wait would freeze an event
        # loop mid-"read". It no longer does (ADR-0033 PR 5.1) — the caller is
        # a detached worker with nobody waiting on it, and waiting out real
        # contention is strictly better than giving up, because the one-shot
        # never retries in this process.
        promoted = update_account_metadata(
            storage_path,
            authuser=sanitized["authuser"],
            email=sanitized.get("email"),
            only_if_absent=True,
        )
    except Exception as e:  # noqa: BLE001 — promotion must never raise at its callers
        # Plain WARNING, no per-path throttle: a persistent cause (read-only
        # profile dir, full disk) leaves the profile un-migrated, so an
        # operator needs a default-visible signal rather than one gated behind
        # -v/--debug. The throttle this replaced existed because promotion ran
        # on the per-RPC read path and would otherwise have warned twice per
        # request forever; the one-shot makes that structurally impossible —
        # a read schedules at most ONE promotion per path per process, so this
        # branch can fire at most once per path from the read path (plus at
        # most one each from startup migration and replace_from_login).
        logger.warning("Legacy account promotion failed for %s: %s", storage_path, e)
        return False
    # Reached whether we promoted or lost a race to a concurrent writer —
    # either way in-band now holds a real record, so the legacy residue is
    # safe (and, for privacy, necessary) to scrub. _drop_legacy_account_key
    # already swallows every realistic failure internally (OSError family,
    # including filelock.Timeout — a TimeoutError/OSError subclass — and
    # JSONDecodeError), but this call is NOT inside the try/except above, and
    # read_account_metadata calls this function with no try/except of its
    # own, trusting it never to raise. Wrap defensively anyway: an embed that
    # already committed must not be erased by an unexpected exception in an
    # unrelated cosmetic cleanup step — that would violate this function's
    # own "never break the read" contract for a reason that has nothing to do
    # with the embed's success.
    try:
        _drop_legacy_account_key(storage_path)
    except Exception as e:  # noqa: BLE001 — cosmetic cleanup must not undo a committed embed
        logger.warning("Legacy account context cleanup failed for %s: %s", storage_path, e)
    if promoted:
        logger.info("Promoted legacy account metadata in-band for %s", storage_path)
    return promoted


def get_authuser_for_storage(storage_path: Path | None) -> int:
    """Return the ``authuser`` index recorded for a profile, defaulting to 0.

    Profiles without account metadata (legacy single-account installs and
    fresh logins that never set an authuser) are treated as ``authuser=0``,
    preserving existing behavior.

    Returns:
        Non-negative ``authuser`` index. Malformed values fall back to 0.
    """
    raw = read_account_metadata(storage_path).get("authuser")
    if isinstance(raw, int) and raw >= 0:
        return raw
    return 0


def get_account_email_for_storage(storage_path: Path | None) -> str | None:
    """Return the persisted account email for stable routing, if available."""
    raw = read_account_metadata(storage_path).get("email")
    if isinstance(raw, str):
        email = raw.strip()
        if email:
            return email
    return None


def resolve_account_identity(
    *,
    has_env_auth: bool,
    storage_path: Path | None = None,
    env_auth_storage_state: Any = None,
) -> dict[str, Any]:
    """Resolve the persisted ``{email, authuser}`` identity for a profile.

    Consolidates a sanitization recipe that used to be duplicated verbatim at
    ``cli/auth_runtime.py::get_auth_tokens`` and ``_app/auth_check.py::_account_info``
    (auth cross-boundary ledger shrink, follow-up to #2103): both callers read the
    in-band account record then apply the identical authuser/email cleanup — an
    ``int`` authuser clamped to ``>= 0`` (default 0; ``bool`` excluded since it is
    an ``int`` subclass), and an email stripped-or-``None``.

    The two callers differ only in WHERE the record comes from, not in what they
    do with it: env-var auth carries no profile directory, so the caller must pass
    its own already-parsed ``env_auth_storage_state`` (``_app/`` never reads
    ``os.environ`` directly, and ``cli/auth_runtime.py`` already has the CLI's
    consolidated ``read_env_auth_json()`` payload in hand by the time it gets
    here); file-based auth resolves straight from ``storage_path`` via
    :func:`read_account_metadata`.
    """
    if has_env_auth:
        meta = read_account_metadata_from_storage_state(env_auth_storage_state)
    else:
        meta = read_account_metadata(storage_path)
    raw_email = meta.get("email")
    email = raw_email.strip() if isinstance(raw_email, str) else ""
    raw_authuser = meta.get("authuser")
    authuser = raw_authuser if type(raw_authuser) is int and raw_authuser >= 0 else 0
    return {"email": email or None, "authuser": authuser}


def _drop_legacy_account_key(storage_path: Path) -> None:
    """Scrub the legacy ``account`` key from the sibling ``context.json``.

    Preserves all other CLI context state (``notebook_id``,
    ``conversation_id``, …). Best-effort: a failure here does not abort the
    in-band write. Since the legacy READ path was removed (the reader is
    in-band-only; :func:`promote_legacy_account` owns the migration), this
    survives purely as a privacy scrub — a stale legacy key would leave the
    account email at rest forever with no reader and no writer to remove it.
    Called by :func:`write_account_metadata` / :func:`clear_account_metadata` /
    :func:`promote_legacy_account` / :func:`replace_from_login` (the CLI login
    writer, after its own atomic write).

    This is the one writer in this module that does NOT touch
    ``storage_state.json``: it writes the SIBLING ``context.json``, so it goes
    through the guarded public ``atomic_write_json`` and the sibling
    ``context.json.lock`` (``filelock``) — never the storage sentinel or sealed
    credential commit spine. Its lock mechanism is deliberately NOT
    unified with :func:`_file_lock` (ADR-0033 plan §5: that is a cross-version
    interop change, explicitly deferred).
    """
    context_path = _account_context_path(storage_path)
    if not context_path.exists():
        return
    lock_path = context_path.with_suffix(context_path.suffix + ".lock")
    try:
        with FileLock(str(lock_path), timeout=10.0):
            if not context_path.exists():
                return
            try:
                data = json.loads(context_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                logger.debug("legacy account-key cleanup skipped at %s: %s", context_path, e)
                return
            if not isinstance(data, dict) or _ACCOUNT_CONTEXT_KEY not in data:
                return
            del data[_ACCOUNT_CONTEXT_KEY]
            if data:
                atomic_write_json(context_path, data)
            else:
                context_path.unlink()
    except OSError as e:
        # Best-effort migration; the in-band reader wins.
        logger.debug("legacy account-key cleanup failed at %s: %s", context_path, e)


def write_account_metadata(storage_path: Path, *, authuser: int, email: str | None = None) -> None:
    """Persist account metadata atomically inside ``storage_state.json``.

    The account record lands under the ``notebooklm`` namespace key so the
    (cookies, account) pair commits together via a single
    :func:`atomic_write_json`. An external reader observing the file
    mid-update sees either the fully-old or fully-new commit — never a mix.

    The legacy sibling ``context.json[account]`` is best-effort cleaned up
    after the in-band write succeeds. CLI context state in the same file
    (``notebook_id`` / ``conversation_id``) is preserved.

    This is the ``notebooklm.auth``-exported facade symbol; it keeps its
    raise-on-lock-failure semantics (:func:`update_account_metadata` raises
    :class:`LockUnavailableError` — the documented replacement for the former
    ``filelock.Timeout``).

    Args:
        storage_path: Path to ``storage_state.json``. The file is created
            with empty ``cookies`` / ``origins`` arrays if missing — matching
            the previous semantics of "writing account metadata never fails
            because cookies haven't been written yet."
        authuser: ``authuser`` index used when extracting cookies for this
            profile (0 for the default account).
        email: Optional account email to record alongside the index.
    """
    update_account_metadata(storage_path, authuser=authuser, email=email)

    # Best-effort: drop the legacy account key from sibling context.json so
    # the next reader doesn't see the same data in two places.
    _drop_legacy_account_key(storage_path)


def clear_account_metadata(storage_path: Path | None) -> None:
    """Remove account metadata from both in-band and legacy locations.

    Two documents, two locks, in that order: the in-band record under the
    canonical storage lock (:func:`clear_in_band_account`), then the legacy
    sibling under its own ``context.json.lock``
    (:func:`_drop_legacy_account_key`).
    """
    if storage_path is None:
        return
    # 1. Strip the in-band record from ``storage_state.json``.
    clear_in_band_account(storage_path)
    # 2. Strip the legacy sibling record too (back-compat with old installs).
    _drop_legacy_account_key(storage_path)


# --- Browser-capture re-mint (relocated from ``browser_capture.py``) --------


def replace_from_remint(
    path: Path,
    captured_state: dict[str, Any],
    *,
    carry_account: bool,
    include_domains: set[str] | None = None,
) -> WriteOutcome:
    """Project the compatibility re-mint writer through ``ProfileStore``."""
    source = ProfileDocument.decode(dict(captured_state))
    selection = DomainSelection(
        include_domains=frozenset(include_domains or ()),
        include_optional=False,
    )
    result = ProfileStore(path).replace_from_remint(
        RemintWriteRequest(
            source=source,
            carry_account=carry_account,
            domain_selection=selection,
        )
    )
    if result.status is ReplaceStatus.APPLIED:
        return WriteOutcome(WriteStatus.OK)
    if result.status is ReplaceStatus.LOCK_UNAVAILABLE:
        return WriteOutcome(WriteStatus.LOCK_UNAVAILABLE)
    raise AssertionError("unreachable replace status")


# --- Login / import full-replace -------------------------------------------
# Hoisted from the CLI ``cli/services/login`` and ``cli/_cookie_import``
# writers — the #2086 filter + revalidation moved HERE.


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
       behaviour. As in :func:`replace_from_remint`, this pass is ADR-0029's
       entry-path-independent guarantee — it holds for every login/import caller,
       filtered or not — and being idempotent it does not narrow a caller
       (import) that already pre-filtered with the same opts.
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
    from .cookie_policy import (  # noqa: PLC0415 (deferred; true leaf, no cycle either way)
        MINIMUM_REQUIRED_COOKIES,
        cookie_names_from_storage,
    )

    # Hoisted out of ``_replace`` so the post-lock legacy-account step can read
    # it: that step branches on whether an account key was embedded, and the
    # writer's return value must stay the outcome the template propagates.
    namespace: dict[str, Any] = {}

    def _replace() -> LoginWriteOutcome:
        nonlocal namespace

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
        namespace = {}
        if account is KEEP_ACCOUNT:
            existing_ns = state.get(_STORAGE_NAMESPACE_KEY)
            if isinstance(existing_ns, dict):
                namespace = dict(existing_ns)
        elif isinstance(account, AccountRecord):
            payload: dict[str, Any] = {"authuser": account.authuser}
            if account.email:
                payload["email"] = account.email
            namespace[_ACCOUNT_CONTEXT_KEY] = payload
        # CLEAR_ACCOUNT: leave the account key absent.
        if include_domains:
            namespace["include_domains"] = sorted(include_domains)
        if include_optional:
            namespace["include_optional"] = True
        if namespace:
            namespace.setdefault("version", _STORAGE_NAMESPACE_VERSION)
            filtered[_STORAGE_NAMESPACE_KEY] = namespace

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

        _commit_profile_json(path, filtered)
        return LoginWriteOutcome(LoginWriteStatus.OK, backup_path=backup_path)

    # MUST-KNOW via RETURN VALUE, not exception: ``LoginWriteOutcome`` has a
    # distinct ``LOCK_UNAVAILABLE`` status the CLI already branches on, so
    # ``raise_on_lock_unavailable`` here would be a breaking change.
    outcome: LoginWriteOutcome = in_storage_transaction(
        path,
        _replace,
        log_prefix="replace_from_login",
        on_unavailable=report_on_lock_unavailable(
            LoginWriteOutcome(LoginWriteStatus.LOCK_UNAVAILABLE)
        ),
    )
    # Both non-OK outcomes (lock unavailable, required cookies dropped) wrote
    # nothing, so neither reaches the legacy-account step below.
    if outcome.status is not LoginWriteStatus.OK:
        return outcome

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
    if account is KEEP_ACCOUNT and _ACCOUNT_CONTEXT_KEY not in namespace:
        promote_legacy_account(path)
    else:
        _drop_legacy_account_key(path)
    return outcome


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

    Relocated from ``master_token.persist_minted_jar``, now routed through the
    typed profile commit spine (fsync durability + temp cleanup, closing
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
    from . import (
        master_token as _master_token,
    )  # deferred; no cycle either way (verified)

    def _persist() -> None:
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
            existing_owner = read_account_metadata_from_storage_state(data).get("email")
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
        _commit_profile_json(path, data)

    # MUST-KNOW via exception: the return type is ``None``, so there is no
    # channel to report into. ``raise_on_lock_unavailable`` formats the message
    # this writer raised when it hand-rolled the branch, verbatim; the #2108
    # ownership guard and its write ordering stay inside ``_persist``, untouched.
    in_storage_transaction(
        path,
        _persist,
        log_prefix="persist_minted_jar",
        on_unavailable=raise_on_lock_unavailable("persist_minted_jar"),
    )


def write_master_token(path: Path, *, email: str, master_token: str, android_id: str) -> None:
    """Persist a ``master_token.json`` record at mode 0600 (full-account credential).

    Relocated from ``master_token.write_master_token``, now routed through the
    typed master-token commit spine (atomic + fsync-durable + temp cleanup) and
    guarded by a bounded sibling ``.master_token.json.lock`` — it was previously lockless
    (part of [storage-F5]). RMW intent: **fails closed**.
    """
    from . import (
        master_token as _master_token,
    )  # deferred; no cycle either way (verified)

    payload = {
        "version": _master_token._MASTER_TOKEN_VERSION,
        "email": email,
        "android_id": android_id,
        "master_token": master_token,
    }

    def _write() -> None:
        _commit_master_token_json(path, payload)

    # The transaction template derives the sibling dotted lock for this
    # credential file (distinct from the profile's storage-state lock — a
    # different file) and ensures the parent dir is secure before taking it.
    in_storage_transaction(
        path,
        _write,
        log_prefix="write_master_token",
        on_unavailable=raise_on_lock_unavailable("write_master_token"),
    )
