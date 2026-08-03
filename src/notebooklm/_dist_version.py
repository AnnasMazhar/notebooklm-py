"""Resolve ``__version__`` from the distribution that actually supplied our files.

Background (ADR-0028): the import package ``notebooklm`` is permanent, but the
distribution that ships it was renamed ``notebooklm-py`` → ``gemini-notebook-py``
at 0.9.0. Both names can be installed into one environment — the old dist
lingering from a pre-0.9 install alongside the new canonical one — and, because
they ship *the same files*, both ``RECORD`` manifests list
``notebooklm/__init__.py``. A plain ``version("notebooklm-py")`` would report the
wrong dist's version in that state, and ``packages_distributions()`` /
``Distribution.files`` path matching cannot break the tie either: path
*membership* is claimed by both, while only one of them actually wrote the bytes
Python just imported.

So ownership is decided by **content**:

1. Collect the installed candidates among :data:`_CANDIDATE_DISTS` whose RECORD
   claims ``notebooklm/__init__.py``.
2. Exactly one → that is the answer, no hashing needed (the overwhelmingly
   common case: a normal single install).
3. More than one → hash the bytes of the ``__init__.py`` we actually imported
   and compare against each candidate's RECORD hash. Accept the result only
   when **exactly one** candidate matches.
4. Ambiguous (identical file contents, missing or unreadable RECORD hashes) →
   fall back to the version stamped into the wheel at build time by
   ``hatch_build.py``, which travels *with the files* and so cannot be confused
   by a second dist's metadata.
5. Nothing resolves → ``0.0.0.dev0``, the long-standing not-installed marker.

What this deliberately never does is pick by distribution-name order. Preferring
whichever name sorts first, or hardcoding "new name wins", is precisely the
wrong-version failure mode this module exists to prevent: in a stale-last
install the *old* dist's files are the ones on disk, and reporting the new
dist's version would misdescribe the code that is running.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from importlib.metadata import Distribution, PackageNotFoundError
from pathlib import Path

_logger = logging.getLogger(__name__)

#: Every distribution name that has ever shipped the ``notebooklm`` package.
#: Order carries no meaning and must not be used to break ties — it exists only
#: to bound the metadata scan.
_CANDIDATE_DISTS = ("gemini-notebook-py", "notebooklm-py")

#: RECORD path of the module whose bytes decide ownership.
_OWNED_FILE = "notebooklm/__init__.py"

_FALLBACK_VERSION = "0.0.0.dev0"


def _stamped_version() -> str | None:
    """The version baked into the wheel by ``hatch_build.py``, if present.

    Lives inside the package, so it describes the files on disk rather than any
    ``.dist-info`` directory — which is exactly what makes it the right
    tiebreaker when two dists' metadata both claim those files.
    """
    try:
        from ._commit import VERSION
    except ImportError:
        return None
    return VERSION or None


def _record_hash_matches(entry: object, data: bytes) -> bool:
    """True if ``data`` hashes to the RECORD entry's declared digest.

    RECORD stores ``<algorithm>=<urlsafe-base64 digest, padding stripped>`` per
    PEP 376/427. ``importlib.metadata`` exposes that on the *path* object as a
    ``.hash`` attribute holding a ``FileHash`` with ``.mode`` (the algorithm)
    and ``.value`` — it is not on the path itself. ``.hash`` is ``None`` when
    RECORD omitted a digest for the entry, which counts as unverifiable, not as
    a match.
    """
    file_hash = getattr(entry, "hash", None)
    mode = getattr(file_hash, "mode", None)
    value = getattr(file_hash, "value", None)
    if not mode or not value:
        return False
    try:
        digest = hashlib.new(mode, data).digest()
    except (ValueError, TypeError):
        # An algorithm this interpreter does not implement (or a FIPS build
        # that refuses it) is an ambiguity, not a match.
        return False
    encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return encoded == value


def _claimed_record_entry(dist: Distribution) -> object | None:
    """The RECORD entry for :data:`_OWNED_FILE`, or ``None`` if unclaimed."""
    try:
        files = dist.files
    except Exception:  # pragma: no cover - corrupt/unreadable RECORD
        # Reading metadata must never propagate: a version string is cosmetic,
        # but this runs during ``import notebooklm``.
        return None
    if not files:
        # No RECORD at all (some system packagers strip it). The dist cannot
        # be shown to own the file, so it does not become a candidate.
        return None
    try:
        for path in files:
            if path.as_posix() == _OWNED_FILE:
                return path
    except Exception:  # pragma: no cover - malformed RECORD entries
        return None
    return None


def _candidates() -> list[tuple[Distribution, object]]:
    """Installed dists claiming :data:`_OWNED_FILE`, paired with their entry."""
    found: list[tuple[Distribution, object]] = []
    for name in _CANDIDATE_DISTS:
        try:
            dist = Distribution.from_name(name)
        except PackageNotFoundError:
            continue
        except Exception:  # pragma: no cover - hostile/corrupt metadata
            # Version reporting must never be the reason an import fails.
            continue
        entry = _claimed_record_entry(dist)
        if entry is not None:
            found.append((dist, entry))
    return found


def _imported_bytes() -> bytes | None:
    """Raw bytes of the ``notebooklm/__init__.py`` this process imported."""
    try:
        return (Path(__file__).resolve().parent / "__init__.py").read_bytes()
    except OSError:
        return None


def _dist_version(dist: Distribution) -> str | None:
    try:
        return dist.version or None
    except Exception:  # pragma: no cover - corrupt METADATA
        return None


def _dist_name(dist: Distribution) -> str:
    """Distribution name for diagnostics only — never for tie-breaking."""
    try:
        return dist.metadata["Name"] or "?"
    except Exception:  # pragma: no cover - corrupt METADATA
        return "?"


def resolve_version() -> str:
    """Best-supported version for the ``notebooklm`` files actually imported.

    Never raises: a version string is cosmetic, and failing to compute one must
    not take down ``import notebooklm``.
    """
    candidates = _candidates()

    if len(candidates) == 1:
        version = _dist_version(candidates[0][0])
        if version:
            return version

    if len(candidates) > 1:
        data = _imported_bytes()
        matches = (
            [(dist, entry) for dist, entry in candidates if _record_hash_matches(entry, data)]
            if data is not None
            else []
        )
        if len(matches) == 1:
            version = _dist_version(matches[0][0])
            if version:
                _logger.debug(
                    "Multiple distributions ship 'notebooklm'; resolved ownership by "
                    "file hash to %r (version %s).",
                    matches[0][0].metadata["Name"],
                    version,
                )
                return version
        # Ambiguous: identical bytes across dists, or hashes we could not read
        # or verify. The build stamp travels with the files, so it describes
        # the code on disk even when the metadata cannot.
        stamped = _stamped_version()
        if stamped:
            _logger.debug(
                "Distribution ownership of 'notebooklm' is ambiguous (%d candidates, "
                "%d hash matches); using the build-stamped version %s.",
                len(candidates),
                len(matches),
                stamped,
            )
            return stamped
        _logger.warning(
            "Multiple distributions (%s) ship the 'notebooklm' package and none could "
            "be identified as the owner of the imported files. Reporting %s. This "
            "environment has a stale install: run "
            "`pip uninstall notebooklm-py && pip install --force-reinstall gemini-notebook-py`.",
            ", ".join(sorted(_dist_name(d) for d, _ in candidates)),
            _FALLBACK_VERSION,
        )
        return _FALLBACK_VERSION

    # No candidate claimed the file: running from a source checkout, a zipapp,
    # or an install whose RECORD was stripped. Prefer the build stamp, then a
    # bare metadata version — but only when exactly one of our dist names is
    # installed, so this path cannot become dist-name-ordering ambiguity
    # resolution by the back door.
    stamped = _stamped_version()
    if stamped:
        return stamped
    installed = []
    for name in _CANDIDATE_DISTS:
        try:
            installed.append(Distribution.from_name(name))
        except Exception:
            continue
    if len(installed) == 1:
        version = _dist_version(installed[0])
        if version:
            return version
    _logger.debug(
        "No installed distribution provides the 'notebooklm' package; using fallback "
        "version %r. This is normal when running from a source checkout.",
        _FALLBACK_VERSION,
    )
    return _FALLBACK_VERSION
