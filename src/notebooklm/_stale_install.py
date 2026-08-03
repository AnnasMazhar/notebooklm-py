"""Detect a pre-shim ``notebooklm-py`` sharing this environment (ADR-0028).

``gemini-notebook-py`` ships the ``notebooklm`` import package, and so did every
``notebooklm-py`` release before 0.9.0. pip has no conflict mechanism for two
distributions that claim the same files: installing both silently clobbers the
shared files, and uninstalling *either* deletes files the survivor's RECORD
still lists — leaving a half-package that imports and then fails somewhere
unrelated.

From 0.9.0 on, ``notebooklm-py`` is a **shim**: zero Python files, extras only.
A shim alongside the canonical dist is the expected, healthy state. What is not
healthy is a *pre-shim* ``notebooklm-py`` — one that still ships real files —
sitting next to the canonical dist.

**Detection is by explicit marker, not by version threshold.** The question
"does this dist ship ``notebooklm/`` files?" is answered from its RECORD, which
is what actually determines whether a collision exists. A ``< 0.9.0`` version
compare would encode today's boundary into a permanent runtime check and quietly
become wrong if the shim boundary ever moves; it is kept only as the fallback
for installs whose RECORD was stripped, where no better evidence exists.

**The mitigation is partial by construction.** This check can only run when the
*canonical* dist's ``__init__.py`` is the one that won the file collision — that
is the only case in which this code executes at all. A stale-*last* install
overwrites the canonical files with the old dist's, and the code that then runs
is the old release, which has no such check. That order is covered by
documentation and release notes only; see ADR-0028 step 8.
"""

from __future__ import annotations

import logging
from importlib.metadata import Distribution, PackageNotFoundError

_logger = logging.getLogger(__name__)

#: The dist that turned into a shim at 0.9.0.
_LEGACY_DIST = "notebooklm-py"

#: The dist that is canonical from 0.9.0 on.
_CANONICAL_DIST = "gemini-notebook-py"

#: Lower bound of the first ``notebooklm-py`` release that ships no Python
#: files. Only consulted when RECORD is unavailable — see the module docstring.
#:
#: ``0.9.0a0``, not ``0.9.0``: pre-releases sort BELOW their final version, so
#: a ``0.9.0a1`` shim compared against ``0.9.0`` would be classified pre-shim
#: and warn about a collision on a perfectly healthy install. ``0.9.0a0`` is
#: the lowest possible ``0.9.0`` pre-release, so every 0.9.0 alpha/beta/rc
#: lands on the shim side of the boundary.
_FIRST_SHIM_VERSION = "0.9.0a0"

#: The human-facing boundary, for messages. The comparison uses the
#: pre-release-inclusive bound above.
_FIRST_SHIM_RELEASE = "0.9.0"

#: Any RECORD entry under this prefix means the dist ships real package files.
_PACKAGE_PREFIX = "notebooklm/"

REMEDIATION = "pip uninstall notebooklm-py && pip install --force-reinstall gemini-notebook-py"


def _ships_package_files(dist: Distribution) -> bool | None:
    """Whether ``dist``'s RECORD lists ``notebooklm/`` files.

    ``None`` means "no RECORD to consult" — the caller falls back to a version
    compare rather than treating unknown as either answer.
    """
    try:
        files = dist.files
    except Exception:  # pragma: no cover - corrupt RECORD
        return None
    if not files:
        return None
    try:
        for path in files:
            posix = path.as_posix()
            # RECORD paths are relative to site-packages; a dist-info entry
            # (`notebooklm_py-0.8.0.dist-info/...`) must not count as a package
            # file, hence the trailing-slash prefix rather than a bare
            # `startswith("notebooklm")`.
            if posix.startswith(_PACKAGE_PREFIX):
                return True
    except Exception:  # pragma: no cover - malformed RECORD entries
        return None
    return False


def _is_pre_shim_version(raw: str | None) -> bool:
    """Fallback marker: a version below the first shim release.

    Unparseable or absent versions return ``False`` — this is the *fallback*
    evidence path, and guessing "stale" on no evidence would fire the warning
    on healthy installs.
    """
    if not raw:
        return False
    try:
        from packaging.version import InvalidVersion, Version
    except ImportError:  # pragma: no cover - packaging is a base dependency
        return False
    try:
        return Version(raw) < Version(_FIRST_SHIM_VERSION)
    except InvalidVersion:
        return False


def stale_install_warning() -> str | None:
    """The warning text for a colliding pre-shim install, or ``None`` if healthy.

    Returned rather than emitted so callers (import-time logging here, the CLI
    doctor elsewhere) can present it however suits them, and so tests can assert
    on the decision without capturing log output.
    """
    try:
        legacy = Distribution.from_name(_LEGACY_DIST)
    except PackageNotFoundError:
        return None  # Only the canonical dist is installed — nothing to collide.
    except Exception:  # pragma: no cover - hostile metadata
        return None

    try:
        Distribution.from_name(_CANONICAL_DIST)
    except PackageNotFoundError:
        # A lone `notebooklm-py` is a normal pre-0.9 install, not a collision.
        return None
    except Exception:  # pragma: no cover - hostile metadata
        return None

    ships_files = _ships_package_files(legacy)
    if ships_files is None:
        try:
            ships_files = _is_pre_shim_version(legacy.version)
        except Exception:  # pragma: no cover - corrupt METADATA
            return None
    if not ships_files:
        return None  # The shim: extras-only, no file collision. Healthy.

    return (
        "Both 'notebooklm-py' and 'gemini-notebook-py' are installed, and the "
        "'notebooklm-py' copy still ships its own 'notebooklm' package files. The two "
        "overwrite each other, and uninstalling either one deletes files the other "
        "still needs. 'notebooklm-py' became an extras-only shim in "
        f"{_FIRST_SHIM_RELEASE} (ADR-0028); this environment predates that. Fix it "
        f"with:\n    {REMEDIATION}\n"
        "'--force-reinstall' is required rather than an upgrade: uninstalling the "
        "stale dist deletes the shared files, and a plain upgrade would consider the "
        "already-current install satisfied and never restore them."
    )


def warn_on_stale_install() -> None:
    """Log the collision warning, if any. Never raises — this runs during import."""
    try:
        message = stale_install_warning()
    except Exception:  # pragma: no cover - defensive
        return
    if message:
        _logger.warning("%s", message)
