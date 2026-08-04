"""Internal environment/default resolvers for NotebookLM runtime behavior.

Centralises lookup of environment variables that influence the live behavior
of the client. Keeping these here avoids scattering ``os.environ.get`` calls
across the codebase and gives each override a single, documented entry point.

This is an implementation module. Public configuration imports stay on
``notebooklm.config``, which deliberately re-exports only the supported subset
of endpoint/language helpers from here.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime
from urllib.parse import urlparse

DEFAULT_BASE_URL = "https://notebooklm.google.com"
PERSONAL_BASE_HOST = "notebooklm.google.com"
ENTERPRISE_BASE_HOST = "notebooklm.cloud.google.com"

# Alias host the personal app is also served from after Google's "Gemini
# Notebook" rebrand. It answers "did a response come from the personal app?" and,
# since it is part of :data:`PERSONAL_APP_HOSTS` below, it is also selectable via
# ``NOTEBOOKLM_BASE_URL``. Selecting it is *not* a documented configuration: a
# live probe (issue #1977) reached ``batchexecute`` on both personal hosts, so the
# endpoint is dual-served — but this repository's cassettes have never exercised
# rebrand-host RPC, because the allowlist rejected the host until now. It stays
# out of ``docs/configuration.md`` while the default is the legacy host, not
# because it is unproven. It is accepted here so the login/landing and
# upload-host seams that must cope with both personal hosts can be exercised.
#
# Must stay a direct string literal: ``tests/_guardrails/
# test_app_host_literals_centralized.py`` reads it out of this module by AST.
PERSONAL_APP_ALIAS_HOST = "notebook.google.com"

# Both hosts the personal app is served from. Built from the two literal-valued
# constants above -- never derive it from ``PERSONAL_BASE_HOST`` alone, which
# collapses it to a one-element set and silently un-fixes #2015/#2020/#2038.
PERSONAL_APP_HOSTS = frozenset({PERSONAL_BASE_HOST, PERSONAL_APP_ALIAS_HOST})

_ALLOWED_BASE_HOSTS = PERSONAL_APP_HOSTS | {ENTERPRISE_BASE_HOST}


def get_base_url() -> str:
    """Return the configured NotebookLM base URL.

    ``NOTEBOOKLM_BASE_URL`` is constrained to known Google-owned NotebookLM hosts
    because the value is used for authenticated requests.
    """
    configured = os.environ.get("NOTEBOOKLM_BASE_URL")
    raw = (configured.strip() if configured is not None else DEFAULT_BASE_URL).rstrip("/")
    if not raw:
        raw = DEFAULT_BASE_URL
    parsed = urlparse(raw)
    path = parsed.path.rstrip("/")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("NOTEBOOKLM_BASE_URL has an invalid port") from exc
    host = parsed.hostname
    if (
        parsed.scheme != "https"
        or host is None
        or host not in _ALLOWED_BASE_HOSTS
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or path
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        allowed = ", ".join(sorted(_ALLOWED_BASE_HOSTS))
        raise ValueError(f"NOTEBOOKLM_BASE_URL must use https and one of: {allowed}")
    return f"https://{host}"


def get_base_host() -> str:
    """Return the configured NotebookLM host."""
    return urlparse(get_base_url()).hostname or PERSONAL_BASE_HOST


# The frontend build label sent as ``bl`` on the chat streaming endpoint.
#
# Re-captured live on 2026-08-04 from the app shell, which served
# ``…_20260802.02_p0`` from BOTH personal hosts (identical bytes — this is not a
# host-specific value). The previous pin, ``…_20260301.03_p0``, had gone five
# months without a re-check (#2073).
#
# Measured on the same day, the server does NOT validate this value: the live
# streaming endpoint returned a complete, cited answer for the pinned label, for
# the served label, AND for a fabricated ``…_19700101.00_p0``. So a stale pin is
# not degrading today — the risk is that it is accepted silently right up until
# it isn't, which is why :data:`BUILD_LABEL_STALE_AFTER_DAYS` exists and the
# nightly canary's build-label lane (``scripts/check_rpc_health.py``) watches the
# gap instead of leaving this constant unattended.
DEFAULT_BL = "boq_labs-tailwind-frontend_20260802.02_p0"

# How far behind the served label this pin may fall before the canary calls it
# stale. Wide on purpose: Google ships a new build roughly weekly, so a tighter
# window would alarm continuously and teach the operator to ignore the lane. The
# lane compares LABEL DATES, never the wall clock, so the verdict depends only on
# what was served.
BUILD_LABEL_STALE_AFTER_DAYS = 90

# Shape of the build label the app shell advertises: ``boq_labs-tailwind-frontend``
# followed by a ``YYYYMMDD.NN`` build stamp and a ``_pN`` patch suffix. Used to
# read the served label out of the shell HTML and to date it; a guardrail test
# asserts :data:`DEFAULT_BL` itself matches, so a malformed bump cannot land.
BUILD_LABEL_RE = re.compile(r"boq_labs-tailwind-frontend_(\d{8})\.\d{2}_p\d+")


def extract_build_label(html: str) -> str | None:
    """Return the newest build label named in an app-shell response, if any.

    ``None`` means the text carried no recognizable label — a sign-in
    interstitial, an error page, or a shell whose label format changed. Callers
    must treat that as "no observation", never as "the label is empty".

    A healthy shell names exactly one label (measured 2026-08-04). Should it ever
    name several, the newest wins: the question this answers is "what is the
    freshest build Google is serving?", which is what :data:`DEFAULT_BL` is
    supposed to be tracking. ``max`` over the whole label is that ordering — the
    prefix is fixed and every field after it is zero-padded fixed width, so
    lexicographic order is build order.
    """
    labels = [match.group(0) for match in BUILD_LABEL_RE.finditer(html)]
    return max(labels) if labels else None


def build_label_date(label: str) -> date | None:
    """Return the build date encoded in ``label``, or ``None`` if unreadable.

    Unreadable covers both a label that does not match :data:`BUILD_LABEL_RE`
    and one whose eight digits are not a real calendar date. Either way the
    caller has no basis for a staleness verdict and must not invent one.

    ``strptime`` rather than ``date.fromisoformat``: the compact ``YYYYMMDD``
    form only became valid ISO input in 3.11, and this package supports 3.10.
    """
    match = BUILD_LABEL_RE.search(label)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def build_label_days_behind(pinned: str, served: str) -> int | None:
    """Return how many days ``pinned`` trails ``served``, or ``None`` if undatable.

    Negative means the pin is *ahead* of what was served (a cohort served an
    older shell, say) — unusual, but not stale, and the caller must not read the
    magnitude as staleness.
    """
    pinned_date = build_label_date(pinned)
    served_date = build_label_date(served)
    if pinned_date is None or served_date is None:
        return None
    return (served_date - pinned_date).days


def get_default_bl() -> str:
    """Return the NotebookLM ``bl`` (build label) URL parameter value.

    Reads the ``NOTEBOOKLM_BL`` environment variable; surrounding whitespace
    is stripped. Unset, empty, or whitespace-only values fall back to
    :data:`DEFAULT_BL`.

    The ``bl`` parameter is sent on the chat streaming endpoint
    (``ChatAPI.ask``) and pins the frontend build the request is associated
    with. Override via ``NOTEBOOKLM_BL`` when chasing a regression tied to
    a specific build snapshot.

    An override does not change what the canary reports: the build-label lane
    always compares the *committed* :data:`DEFAULT_BL` against what the app shell
    serves, because that constant is what ships to every user.
    """
    raw = os.environ.get("NOTEBOOKLM_BL", "") or ""
    return raw.strip() or DEFAULT_BL


def get_default_language() -> str:
    """Return the user's preferred interface language.

    Reads the ``NOTEBOOKLM_HL`` environment variable. Surrounding whitespace
    is stripped; unset, empty, or whitespace-only values fall back to ``"en"``.

    This value is threaded into two places:

    * The ``hl`` URL query parameter on every batchexecute RPC call
      (``RpcExecutor.build_url`` and
      ``_chat.wire.build_streaming_chat_request``).
    * Language-aware ``ArtifactsAPI.generate_*`` calls when callers pass
      ``language=None`` to opt in to environment/default resolution. Omitting
      ``language`` in the public Python API keeps the historical ``"en"``
      artifact-language default.
    """
    raw = os.environ.get("NOTEBOOKLM_HL", "") or ""
    return raw.strip() or "en"
