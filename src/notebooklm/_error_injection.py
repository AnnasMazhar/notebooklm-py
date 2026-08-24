"""Synthetic-error configuration guard for VCR tests.

``NOTEBOOKLM_VCR_RECORD_ERRORS`` selects the ``429`` / ``5xx`` /
``expired_csrf`` response shape used by the VCR live/cassette recording seam in
``tests/vcr_config.py``.  The former production
``ErrorInjectionMiddleware`` was permanently pass-through in installed
clients and was removed before the P7 runtime collapse.  Semantic service
tests now register neutral failures on ``tests._fixtures.RecordingBackend``;
no test-only response substitution remains in the production HTTP chain.

Public surface kept:

- :func:`_get_error_injection_mode` — env-var → mode normalization.
- :func:`_refuse_synthetic_error_outside_test_context` — client
  construction (``NotebookLMClient.__init__``) calls this so a leaked
  deploy env raises ``RuntimeError`` instead of silently carrying test-only
  recording configuration. The guard fires only when
  ``PYTEST_CURRENT_TEST`` is unset (pytest sets it for every test).
- :data:`ERROR_INJECT_ENV_VAR` — env-var name (canonical string).
"""

from __future__ import annotations

__all__ = [
    "ERROR_INJECT_ENV_VAR",
    "_get_error_injection_mode",
    "_refuse_synthetic_error_outside_test_context",
]

import logging
import os

from ._runtime.config import CORE_LOGGER_NAME

# Logger name pinned via :data:`CORE_LOGGER_NAME` so log filters in
# tests — e.g. ``caplog.at_level(..., logger=CORE_LOGGER_NAME)`` — keep
# matching. Client collaborators share the same historical name.
logger = logging.getLogger(CORE_LOGGER_NAME)


ERROR_INJECT_ENV_VAR = "NOTEBOOKLM_VCR_RECORD_ERRORS"


def _get_error_injection_mode() -> str | None:
    """Return the synthetic-error mode from ``NOTEBOOKLM_VCR_RECORD_ERRORS``.

    Returns ``None`` when the env var is unset, empty, or carries an
    unrecognized value (we deliberately fail open rather than crash a
    cassette-recording run on a typo — the unit tests catch the typo path,
    and the VCR config validates the value separately).

    Returning a non-``None`` mode does not affect production requests. The
    value is consumed by the test-suite VCR recording seam, while semantic
    tests inject neutral failures through the recording fake backend. Issue #1005 closed
    the prior dynamic-load attack surface where a leaked env var triggered an
    ``importlib`` walk of ``tests/cassette_patterns.py``.

    The valid-mode set is hardcoded here (rather than imported from
    ``tests.cassette_patterns``) so production import time never reaches into
    the test tree. The same set is mirrored in
    ``tests.cassette_patterns.VALID_ERROR_MODES`` and the
    ``synthetic_error`` marker validator in ``tests/conftest.py``; the
    duplication is intentional and bounded — adding a fourth mode requires
    updating all three sites, which the unit tests in ``tests/unit/
    test_vcr_config.py`` will surface immediately.
    """
    raw = os.environ.get(ERROR_INJECT_ENV_VAR, "").strip()
    if not raw:
        return None
    # Case-fold-normalize so callers can use ``"5XX"`` / ``"429"`` / etc.
    # (``casefold`` over ``lower`` for the project's Unicode-aware
    # case-insensitive comparison rule; ASCII-identical here — #1268).
    normalized = raw.casefold()
    valid = {"429", "5xx", "expired_csrf"}
    if normalized not in valid:
        return None
    return normalized


def _refuse_synthetic_error_outside_test_context() -> None:
    """Refuse client instantiation when the test-only env var leaks.

    ``NOTEBOOKLM_VCR_RECORD_ERRORS`` is documented as test-only. Production
    runtime has no response-injection stage, so the env var cannot substitute
    production responses; this guard still rejects leaked cassette-recording
    config before the client starts.

    The guard fires only when:

    1. :func:`_get_error_injection_mode` returns a non-``None`` mode (so an
       empty / unrecognized env-var value still allows production startup),
       AND
    2. ``PYTEST_CURRENT_TEST`` is unset (pytest sets this for the lifetime
       of every test, including the ``@pytest.mark.synthetic_error`` fixture
       path that *does* legitimately set the env var).

    On refusal we log at WARNING with the env-var name and raise
    ``RuntimeError`` with the same env-var name so an operator can grep
    deploy configs and unset the offending variable.
    """
    mode = _get_error_injection_mode()
    if mode is None:
        return
    if os.environ.get("PYTEST_CURRENT_TEST"):
        # Legitimate pytest run — the ``@pytest.mark.synthetic_error``
        # fixture sets the env var inside a test context. Allow.
        return
    message = (
        f"{ERROR_INJECT_ENV_VAR}={mode!r} is set but no pytest context was "
        f"detected (PYTEST_CURRENT_TEST unset). This env var is test-only — "
        f"it selects synthetic VCR response shapes and must not be set in "
        f"production. Unset {ERROR_INJECT_ENV_VAR} to restore normal "
        f"behavior, or run under pytest if synthetic-error recording is "
        f"intended."
    )
    logger.warning(message)
    raise RuntimeError(message)
