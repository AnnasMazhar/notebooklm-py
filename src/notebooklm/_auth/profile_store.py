"""Path-owned profile reads and cookie persistence transactions."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, TypeVar, cast

from ..exceptions import LockUnavailableError
from . import cookie_merge as _cookie_merge
from .cookie_filter import filter_storage_state_cookies_by_domain_policy
from .cookie_merge import RecoveryObservation
from .cookie_types import CookieIdentity, CookieJar
from .credential_io import _commit_profile_json
from .paths import _storage_state_lock_path, canonical_storage_key
from .profile_account import AccountView, DomainSelection, ProfileAccount, StoredSession
from .profile_document import ProfileDocument, _ProfileDocumentStructureError
from .storage_lock import (
    _LOCK_ACQUIRE_DEADLINE_SECONDS,
    LockRequest,
    LockState,
    StorageLockManager,
)

logger = logging.getLogger("notebooklm.auth")

T = TypeVar("T")


class CookieMergeDisposition(Enum):
    APPLIED = "applied"
    NO_CHANGE = "no_change"
    CONFLICT = "conflict"
    HARD_FAILURE = "hard_failure"


@dataclass(frozen=True, slots=True, repr=False)
class CookieMergeResult:
    """Redacted result of one complete cookie persistence transaction."""

    disposition: CookieMergeDisposition
    advances_ordering: bool
    committed: bool | None
    next_baseline: CookieJar | None = field(default=None, repr=False)
    rejected: frozenset[CookieIdentity] = frozenset()

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, CookieMergeDisposition):
            raise TypeError("disposition must be a CookieMergeDisposition")
        if type(self.advances_ordering) is not bool:
            raise TypeError("advances_ordering must be a boolean")
        if self.committed is not None and type(self.committed) is not bool:
            raise TypeError("committed must be a boolean or None")
        try:
            baseline = None if self.next_baseline is None else CookieJar(tuple(self.next_baseline))
            rejected = frozenset(
                CookieIdentity(identity.name, identity.domain, identity.path)
                for identity in self.rejected
            )
        except Exception:
            raise TypeError("cookie merge result fields are invalid") from None

        if self.disposition is CookieMergeDisposition.HARD_FAILURE:
            valid = (
                not self.advances_ordering
                and baseline is None
                and self.committed in (False, None)
                and not rejected
            )
        else:
            valid = self.advances_ordering and baseline is not None and self.committed is not None
            if self.disposition is CookieMergeDisposition.APPLIED:
                valid = valid and self.committed is True and not rejected
            elif self.disposition is CookieMergeDisposition.NO_CHANGE:
                valid = valid and self.committed is False and not rejected
            elif self.disposition is CookieMergeDisposition.CONFLICT:
                valid = valid and bool(rejected)
        if not valid:
            raise ValueError("cookie merge result fields are inconsistent")

        object.__setattr__(self, "next_baseline", baseline)
        object.__setattr__(self, "rejected", rejected)


@dataclass(frozen=True, slots=True, repr=False)
class RemintWriteRequest:
    """Immutable, redacted input for one browser-capture replacement."""

    source: ProfileDocument = field(repr=False)
    carry_account: bool
    domain_selection: DomainSelection = DomainSelection()

    def __post_init__(self) -> None:
        if not isinstance(self.source, ProfileDocument):
            raise TypeError("source must be a ProfileDocument")
        if not isinstance(self.domain_selection, DomainSelection):
            raise TypeError("domain_selection must be a DomainSelection")


class ReplaceStatus(Enum):
    APPLIED = "applied"
    LOCK_UNAVAILABLE = "lock_unavailable"


@dataclass(frozen=True, slots=True)
class ReplaceResult:
    """Value-free result of one full profile replacement."""

    status: ReplaceStatus

    def __post_init__(self) -> None:
        if not isinstance(self.status, ReplaceStatus):
            raise TypeError("status must be a ReplaceStatus")


class _LockUnavailablePolicy(Protocol):
    """What a writer does when the storage lock could not be acquired."""

    def __call__(self, lock_path: Path) -> Any: ...


def raise_on_lock_unavailable(operation: str) -> _LockUnavailablePolicy:
    """Return the existing must-know exception projection."""

    def _policy(lock_path: Path) -> Any:
        raise LockUnavailableError(f"{operation}: storage lock unavailable at {lock_path}")

    return _policy


def report_on_lock_unavailable(outcome: Any) -> _LockUnavailablePolicy:
    """Return the existing must-know outcome projection."""

    def _policy(lock_path: Path) -> Any:
        return outcome

    return _policy


def skip_on_lock_unavailable(message: str) -> _LockUnavailablePolicy:
    """Return the existing best-effort DEBUG projection."""

    def _policy(lock_path: Path) -> Any:
        logger.debug(message, lock_path)
        return None

    return _policy


def _ensure_secure_parent_dir(path: Path) -> None:
    """Create the credential parent and best-effort tighten it on POSIX."""
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        with contextlib.suppress(OSError):
            os.chmod(parent, 0o700)


_STORAGE_LOCKS = StorageLockManager.process_default()


class ProfileStore:
    """Own one raw profile path and its synchronous persistence transactions."""

    def __init__(
        self,
        path: Path,
        *,
        locks: StorageLockManager | None = None,
    ) -> None:
        self._path = path
        self._locks = locks if locks is not None else _STORAGE_LOCKS

    @property
    def path(self) -> Path:
        return self._path

    @property
    def ordering_key(self) -> Path:
        key = canonical_storage_key(self._path)
        if key is None:  # pragma: no cover - a non-None path cannot produce None
            raise AssertionError("profile path must have an ordering key")
        return key

    def read_document(self) -> ProfileDocument:
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        return ProfileDocument.decode(payload)

    def read_session(self) -> StoredSession:
        document = self.read_document()
        return StoredSession(
            cookies=document.cookies(),
            account=document.account_for(AccountView.ROUTE),
            domain_selection=DomainSelection(
                include_domains=document.include_domains(),
                include_optional=document.include_optional(),
            ),
        )

    def replace_from_remint(self, request: RemintWriteRequest) -> ReplaceResult:
        """Replace cookies from a browser re-mint under the bounded profile lock."""

        def _replace() -> ReplaceResult:
            carried_namespace: dict[str, object] | None = None
            if request.carry_account and self.path.exists():
                # Existence/stat failures are not a tolerated destination-read failure.
                try:
                    current = self.read_document().to_json()
                    namespace = current.get("notebooklm")
                    if isinstance(namespace, dict):
                        carried_namespace = namespace
                except UnicodeDecodeError:
                    raise
                except (OSError, json.JSONDecodeError):
                    pass
                except _ProfileDocumentStructureError as exc:
                    if exc.field != "root" or exc.reason != "not_object":
                        raise

            selection = request.domain_selection
            filtered = filter_storage_state_cookies_by_domain_policy(
                cast(dict[str, Any], request.source.to_json()),
                include_optional=selection.include_optional,
                include_domains=(
                    set(selection.include_domains) if selection.include_domains else None
                ),
            )
            if carried_namespace is not None:
                filtered["notebooklm"] = carried_namespace
            _commit_profile_json(self.path, filtered)
            return ReplaceResult(ReplaceStatus.APPLIED)

        return self._under_bounded_lock(
            operation="replace_from_remint",
            body=_replace,
            on_unavailable=report_on_lock_unavailable(
                ReplaceResult(ReplaceStatus.LOCK_UNAVAILABLE)
            ),
        )

    def _read_account_document(self) -> ProfileDocument | None:
        """Read one document using the tolerant in-band account policy."""
        if not self._path.exists():
            return None
        try:
            return self.read_document()
        except UnicodeDecodeError:
            raise
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("in-band account read failed at %s: %s", self._path, exc)
            return None
        except _ProfileDocumentStructureError as exc:
            if exc.field == "root" and exc.reason == "not_object":
                return None
            raise

    def read_account(self) -> ProfileAccount | None:
        """Return the typed routing account when a non-empty raw record exists."""
        document = self._read_account_document()
        if document is None:
            return None
        payload = document.to_json()
        namespace = payload.get("notebooklm")
        account = namespace.get("account") if isinstance(namespace, dict) else None
        if not isinstance(account, dict) or not account:
            return None
        return document.account_for(AccountView.ROUTE)

    def update_account(
        self,
        record: ProfileAccount,
        *,
        only_if_absent: bool = False,
    ) -> bool:
        """Persist one in-band account under the bounded profile lock."""
        account_payload: dict[str, object] = {"authuser": record.authuser}
        if record.email:
            account_payload["email"] = record.email

        def _write() -> bool:
            if not self._path.exists():
                document = ProfileDocument.empty()
            else:
                try:
                    payload = json.loads(self._path.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        f"storage state at {self._path} is corrupted: {exc}"
                    ) from exc
                if not isinstance(payload, dict):
                    raise RuntimeError(
                        f"storage state at {self._path} has unexpected shape: "
                        f"{type(payload).__name__}"
                    )
                document = ProfileDocument.decode(payload)

            payload = document.to_json()
            namespace = payload.get("notebooklm")
            if not isinstance(namespace, dict):
                namespace = {}
            elif only_if_absent:
                current = namespace.get("account")
                if isinstance(current, dict) and current:
                    return False
            namespace["version"] = 1
            namespace["account"] = account_payload
            updated = document.with_namespace(namespace)
            _commit_profile_json(self._path, updated.to_json())
            return True

        return self._under_bounded_lock(
            operation="write_account_metadata",
            body=_write,
            on_unavailable=raise_on_lock_unavailable("write_account_metadata"),
        )

    def clear_account(self) -> None:
        """Remove the in-band account using the best-effort clear policy."""
        if not self._path.exists():
            return

        def _clear() -> None:
            try:
                document = self.read_document()
            except UnicodeDecodeError:
                raise
            except (OSError, json.JSONDecodeError) as exc:
                logger.debug("in-band account clear skipped at %s: %s", self._path, exc)
                return
            except _ProfileDocumentStructureError as exc:
                if exc.field == "root" and exc.reason == "not_object":
                    return
                raise

            payload = document.to_json()
            namespace = payload.get("notebooklm")
            if not isinstance(namespace, dict) or "account" not in namespace:
                return
            del namespace["account"]
            updated = document.with_namespace(None if set(namespace) <= {"version"} else namespace)
            _commit_profile_json(self._path, updated.to_json())

        self._under_bounded_lock(
            operation="clear_account_metadata",
            body=_clear,
            on_unavailable=skip_on_lock_unavailable(
                "in-band account clear skipped: storage lock unavailable at %s"
            ),
        )

    def _under_bounded_lock(
        self,
        *,
        operation: str,
        body: Callable[[], T],
        on_unavailable: _LockUnavailablePolicy,
        deadline_seconds: float = _LOCK_ACQUIRE_DEADLINE_SECONDS,
    ) -> T:
        _ensure_secure_parent_dir(self._path)
        lock_path = _storage_state_lock_path(self._path)
        request = LockRequest(
            path=lock_path,
            blocking=False,
            operation=operation,
            deadline_seconds=deadline_seconds,
        )
        with self._locks.acquire(request) as state:
            if state is not LockState.HELD:
                return on_unavailable(lock_path)
            return body()

    def _under_blocking_cookie_lock(
        self,
        *,
        operation: str,
        body: Callable[[], T],
    ) -> T:
        lock_path = _storage_state_lock_path(self._path)
        request = LockRequest(
            path=lock_path,
            blocking=True,
            operation=operation,
            deadline_seconds=None,
        )
        with self._locks.acquire(request) as state:
            if state is LockState.UNAVAILABLE and self._locks._claim_cookie_warning():
                logger.warning(
                    "Cross-process file lock unavailable at %s; cookie saves will "
                    "proceed without cross-process coordination and rely solely on "
                    "snapshot/delta CAS guards. Common causes: NFS without flock "
                    "support, read-only parent directory, fd exhaustion. (Logged "
                    "once per process.)",
                    lock_path,
                )
            return body()

    def merge_cookie_observation(
        self,
        observation: CookieJar,
        *,
        baseline: CookieJar,
        recovery_observation: RecoveryObservation | None = None,
    ) -> CookieMergeResult:
        def _merge() -> CookieMergeResult:
            document = self._read_cookie_document()
            if isinstance(document, CookieMergeResult):
                return document
            decision = _cookie_merge.decide_cookie_merge(
                stored=document,
                observation=observation,
                baseline=baseline,
                recovery_observation=recovery_observation,
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
            if decision.document is None:
                disposition = (
                    CookieMergeDisposition.CONFLICT
                    if decision.rejected
                    else CookieMergeDisposition.NO_CHANGE
                )
                return CookieMergeResult(
                    disposition,
                    advances_ordering=True,
                    committed=False,
                    next_baseline=decision.next_baseline,
                    rejected=decision.rejected,
                )
            try:
                _commit_profile_json(self._path, decision.document.to_json())
            except Exception as exc:
                logger.warning(
                    "Failed to write updated cookies to %s: %s",
                    self._path,
                    type(exc).__name__,
                )
                return CookieMergeResult(
                    CookieMergeDisposition.HARD_FAILURE,
                    advances_ordering=False,
                    committed=None,
                )
            logger.debug(
                "Successfully synced %d refreshed cookies to %s",
                decision.updated_rows,
                self._path,
            )
            return CookieMergeResult(
                CookieMergeDisposition.CONFLICT
                if decision.rejected
                else CookieMergeDisposition.APPLIED,
                advances_ordering=True,
                committed=True,
                next_baseline=decision.next_baseline,
                rejected=decision.rejected,
            )

        return self._under_blocking_cookie_lock(
            operation="save_cookies_to_storage",
            body=_merge,
        )

    def merge_legacy_cookie_observation(
        self,
        observation: CookieJar,
    ) -> CookieMergeResult:
        def _merge() -> CookieMergeResult:
            document = self._read_cookie_document()
            if isinstance(document, CookieMergeResult):
                return document
            decision = _cookie_merge.decide_legacy_cookie_overlay(
                stored=document,
                observation=observation,
            )
            if decision.document is None:
                return CookieMergeResult(
                    CookieMergeDisposition.NO_CHANGE,
                    advances_ordering=True,
                    committed=False,
                    next_baseline=decision.next_baseline,
                )
            try:
                _commit_profile_json(self._path, decision.document.to_json())
            except Exception as exc:
                logger.warning(
                    "Failed to write updated cookies to %s: %s",
                    self._path,
                    type(exc).__name__,
                )
                return CookieMergeResult(
                    CookieMergeDisposition.HARD_FAILURE,
                    advances_ordering=False,
                    committed=None,
                )
            logger.debug(
                "Successfully synced %d refreshed cookies to %s",
                decision.updated_rows,
                self._path,
            )
            return CookieMergeResult(
                CookieMergeDisposition.APPLIED,
                advances_ordering=True,
                committed=True,
                next_baseline=decision.next_baseline,
            )

        return self._under_blocking_cookie_lock(
            operation="save_cookies_to_storage",
            body=_merge,
        )

    def _read_cookie_document(self) -> ProfileDocument | CookieMergeResult:
        if not self._path.exists():
            logger.debug("Skipping cookie sync: Storage file not found at %s", self._path)
            return CookieMergeResult(
                CookieMergeDisposition.HARD_FAILURE,
                advances_ordering=False,
                committed=False,
            )
        try:
            document = self.read_document()
            document.raw_cookie_rows()
        except _ProfileDocumentStructureError:
            logger.warning(
                "storage_state at %s has an invalid 'cookies' key/payload; "
                "rotated cookies will not be persisted",
                self._path,
            )
            return CookieMergeResult(
                CookieMergeDisposition.HARD_FAILURE,
                advances_ordering=False,
                committed=False,
            )
        except Exception as exc:
            logger.warning(
                "Failed to read storage state for cookie sync: %s",
                type(exc).__name__,
            )
            return CookieMergeResult(
                CookieMergeDisposition.HARD_FAILURE,
                advances_ordering=False,
                committed=False,
            )
        return document


def in_storage_transaction(
    path: Path,
    body: Callable[[], Any],
    *,
    log_prefix: str,
    on_unavailable: _LockUnavailablePolicy,
    deadline_seconds: float = _LOCK_ACQUIRE_DEADLINE_SECONDS,
) -> Any:
    """Run the compatibility body under a path-owned bounded store lock."""
    return ProfileStore(path)._under_bounded_lock(
        operation=log_prefix,
        body=body,
        on_unavailable=on_unavailable,
        deadline_seconds=deadline_seconds,
    )
