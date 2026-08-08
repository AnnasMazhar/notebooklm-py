"""Filter captured browser cookies before auth storage is persisted.

The Playwright login and headless re-auth arms capture a complete browser
storage state; the rookiepy/Firefox CLI writers persist an extracted jar. All
of them funnel through this leaf, which applies the shared cookie-domain
policy and removes malformed or exact-identity duplicate rows without
depending on Playwright or the browser-capture lifecycle.
"""

from __future__ import annotations

import logging
from typing import Any

from . import cookie_semantics as _cookie_semantics
from .cookie_policy import _is_trusted_google_cookie_domain, build_cookie_domain_allowlist

# The documented auth logger (core-F10), not ``__name__``: this module's
# dropped-cookie / malformed-row warnings must reach the same
# ``notebooklm.auth`` namespace users already configure for auth diagnostics
# (ADR-0016), rather than the private ``notebooklm._auth._browser_cookie_filter``
# child that no operator subscribes to.
logger = logging.getLogger("notebooklm.auth")


def _safe_cookie_shape(cookie: dict[str, Any]) -> str:
    """A VALUE-FREE structural summary of a cookie dict, safe to log.

    Returns the sorted key set plus the Python type of each field — but NEVER
    any field *value*. A cookie ``value`` is a live credential (and, on the CDP
    arm, comes straight from the operator's running browser), so the
    malformed-row warnings must not echo the row. Example output:
    ``keys=['domain', 'name', 'value'] types={domain: int, name: str, value: str}``.

    Iterates ``items()`` (sorted by the string form of each key) rather than
    re-subscripting by a stringified key, so a malformed cookie with a non-str
    key (e.g. an ``int``) cannot raise ``KeyError`` here — this helper exists to
    describe malformed rows, so it must itself never choke on one.
    """
    sorted_items = sorted(cookie.items(), key=lambda item: str(item[0]))
    keys = [str(k) for k, _ in sorted_items]
    types = ", ".join(f"{k}: {type(v).__name__}" for k, v in sorted_items)
    return f"keys={keys} types={{{types}}}"


#: ``CookieRowError.field`` -> the bounded warning this module emits for it.
#: The *checks* live in :func:`notebooklm._auth.cookie_semantics.sanitize_cookie_entry`
#: (the one row-shape predicate); only the failure mode is local. Every message
#: takes exactly one ``%s`` — the value-free shape from :func:`_safe_cookie_shape`.
_MALFORMED_ROW_WARNINGS: dict[str, str] = {
    "name": "Skipping storage_state cookie with missing/empty/non-str name (%s)",
    "domain": "Skipping storage_state cookie with non-str domain (%s)",
    "path": "Skipping storage_state cookie with non-str path (%s)",
    "expires": "Skipping storage_state cookie with unusable expires (%s)",
}


def _report_malformed_row(cookie: Any, exc: _cookie_semantics.CookieRowError) -> None:
    """Log one bounded, value-free warning for a row the predicate rejected.

    ``exc.field == "row"`` means the entry is not a dict at all, so
    :func:`_safe_cookie_shape` cannot describe it — log the Python type instead.
    Never log the row itself: a cookie ``value`` is a live credential and, on the
    CDP arm, comes straight from the operator's running browser.

    An absent or empty-string ``domain`` is dropped **silently**: such a row is
    never on the allowlist, so it was dropped without a warning before the shared
    predicate started rejecting it up front, and a warning here would be new
    noise on every domain-less row a browser exports.
    """
    if exc.field == "row":
        logger.warning(
            "Skipping malformed storage_state cookie entry (not a dict): type=%s",
            type(cookie).__name__,
        )
        return
    if exc.field == "domain" and isinstance(cookie.get("domain", ""), str):
        return
    message = _MALFORMED_ROW_WARNINGS.get(exc.field, "Skipping malformed storage_state cookie (%s)")
    logger.warning(message, _safe_cookie_shape(cookie))


def filter_storage_state_cookies_by_domain_policy(
    state: dict[str, Any],
    *,
    include_optional: bool = False,
    include_domains: set[str] | None = None,
) -> dict[str, Any]:
    """Filter a Playwright ``storage_state`` dict to the configured cookie-domain policy.

    The Playwright login flow captures every cookie the browser context holds.
    Without this filter, unrelated non-Google cookies and origin storage from
    the user's browser context can leak into the persisted
    ``storage_state.json`` and inflate the blast radius. This applies the
    shared allowlist (:func:`build_cookie_domain_allowlist`, the same set the
    rookiepy extraction request is built from) at write time; the
    rookiepy/Firefox persist path (``_write_extracted_cookies`` /
    ``_login_with_browser_cookies``) runs this same filter before its atomic
    write — the Firefox extractor suffix-matches dot-prefixed domains, so
    extraction-time narrowing alone is not enough — so both login paths
    produce equivalent on-disk state. Distinct optional roots remain opt-in
    via ``--include-domains=...``.
    Exact allowlist entries use leading-dot/no-dot equivalence
    (``http.cookiejar`` may normalize either). In addition, trusted Google
    roots use boundary-aware suffix matching. This compatibility-first rule
    preserves unknown ``*.google.com``, ``*.googleusercontent.com``, and
    regional Google subdomains until they can be narrowed with live-flow
    evidence, while still rejecting lookalikes such as ``evilgoogle.com``.

    Two hardening behaviors (#1513) ride on top of the allowlist:

    * **Malformed rows are skipped, not raised.** rookiepy / Playwright can
      emit malformed rows; a non-dict entry, a cookie whose ``domain`` is not
      a str, or a cookie whose ``name`` is not a non-empty str (all malformed
      under Playwright's own ``storage_state`` schema) is dropped with one
      bounded ``logger.warning`` per row instead of crashing the whole persist.
      The warning logs only a **value-free shape** (:func:`_safe_cookie_shape`:
      the row's keys + per-field types) — never the row itself — so a cookie
      ``value`` (a live credential, and for the CDP arm one that comes straight
      from the operator's running browser) cannot leak into the logs.
    * **Exact-identity duplicate dedup.** Rows are keyed by their full
      RFC 6265 identity ``(name, domain, path)`` (path normalized via
      ``or "/"``, matching every loader). For exact-identity duplicates —
      where only metadata such as ``value`` / ``expires`` / flags can differ —
      the **last occurrence in input order wins** and replaces the earlier row
      in place, kept whole (fields are never merged). This mirrors the
      persistence-merge rule in
      :func:`notebooklm._auth.storage.save_cookies_to_storage`, where the
      newer observation overwrites the stored row for the same
      ``(name, domain, path)`` key.

      Same-name rows on *different* domains or paths are deliberately ALL
      kept: cross-domain same-name resolution is a **load-time** concern (the
      flat loaders :func:`notebooklm._auth.cookies.extract_cookies_from_storage`
      / :func:`notebooklm._auth.cookies.flatten_cookie_map` rank by
      ``_auth_domain_priority``). Deduping by bare name at write time would
      starve the ``(name, domain, path)``-keyed runtime loader
      (:func:`notebooklm._auth.cookies.build_httpx_cookies_from_storage`),
      which legitimately holds e.g. the per-product ``OSID`` cookie on
      ``notebooklm.google.com`` and ``myaccount.google.com`` as distinct
      jar entries.

    Args:
        state: Playwright ``storage_state`` dict (``BrowserContext.storage_state()``).
        include_optional: When ``True``, opt in to every label in
            :data:`notebooklm._auth.cookie_policy.OPTIONAL_COOKIE_DOMAINS_BY_LABEL`.
        include_domains: Optional-domain labels to opt in (``"all"`` = every
            label). Mirrors the rookiepy path semantics.

    Returns:
        A new ``storage_state`` dict with ``cookies`` filtered and ``origins``
        cleared. Origin localStorage / IndexedDB is not used for cookie auth
        and must not bypass the domain policy. The input dict is not mutated.
    """
    allowed_list = build_cookie_domain_allowlist(
        include_optional=include_optional, include_domains=include_domains
    )
    allowed: frozenset[str] = frozenset(allowed_list)
    allowed_stripped: frozenset[str] = frozenset(d.lstrip(".").lower() for d in allowed_list)

    def _is_allowed(domain: str) -> bool:
        normalized = domain[1:] if domain.startswith(".") else domain
        return (
            domain in allowed
            or normalized.lower() in allowed_stripped
            or _is_trusted_google_cookie_domain(domain)
        )

    filtered_cookies: list[dict[str, Any]] = []
    index_by_identity: dict[tuple[str, str, Any], int] = {}

    for cookie in state.get("cookies", []):
        # ONE row-shape predicate, shared with every loader (ADR-0033 PR 2.1).
        # It rejects a non-dict entry, a missing/empty/non-str ``name`` or
        # ``domain``, a present-but-non-str ``path`` (which would slip past the
        # ``or "/"`` normalization below and later crash http.cookiejar/httpx
        # path matching), and an ``expires`` that cannot be normalized — the
        # last one because every loader that rebuilds the row goes through
        # ``int(float(expires))`` inside ``http.cookiejar.Cookie``, so dropping
        # it at capture time keeps the persisted state loadable instead of
        # deferring the failure to the first authed call (#2061).
        #
        # ``check_value=False``: this filter is domain policy, not a request
        # jar. It has never inspected ``value`` and must not start — a row's
        # value is a credential it only ever copies through.
        try:
            normalized = _cookie_semantics.sanitize_cookie_entry(cookie, check_value=False)
        except _cookie_semantics.CookieRowError as exc:
            _report_malformed_row(cookie, exc)
            continue
        name = normalized["name"]
        domain = normalized["domain"]
        if not _is_allowed(domain):
            continue

        # Full RFC 6265 identity. The predicate's ``path or "/"`` normalization
        # mirrors the loaders and the save_cookies_to_storage merge key, so an
        # empty-path twin can't survive as a phantom duplicate row.
        identity = (name, domain, normalized["path"])
        existing = index_by_identity.get(identity)
        if existing is None:
            index_by_identity[identity] = len(filtered_cookies)
            filtered_cookies.append(cookie)
        else:
            # Exact-identity duplicate: the later observation wins whole,
            # replacing the earlier row in place — mirroring the
            # save_cookies_to_storage merge, where the newer observation
            # overwrites the stored row for the same (name, domain, path) key.
            logger.debug(
                "Cookie %s: exact-identity duplicate on (%s, %s); keeping later observation",
                name,
                domain,
                identity[2],
            )
            filtered_cookies[existing] = cookie

    return {
        "cookies": filtered_cookies,
        "origins": [],
    }


__all__ = ["filter_storage_state_cookies_by_domain_policy"]
