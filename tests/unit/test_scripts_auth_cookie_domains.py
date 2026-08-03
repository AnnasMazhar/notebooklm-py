"""Cookie-domain preservation guardrail for the two maintenance scripts (#2019).

``scripts/check_rpc_health.py`` and ``scripts/capture_rpc_registry.py`` both
authenticate against live Google hosts, and both used to load credentials
through the *flat* ``load_auth_from_storage()`` mapping, which drops every
cookie's domain. Under ``NOTEBOOKLM_AUTH_JSON`` (how the nightly workflows run)
there is no storage file to recover the domains from, so:

* ``check_rpc_health`` fed the flat dict to ``fetch_tokens``, whose
  ``build_cookie_jar`` fallback re-pinned **every** cookie to ``.google.com``;
* ``capture_rpc_registry`` handed the flat dict straight to
  ``httpx.get(cookies=...)``, which is domain-less by construction.

Host-scoped cookies (``OSID`` / ``__Secure-OSID`` on the NotebookLM host,
``LSID`` on ``accounts.google.com``) were therefore broadcast to every
``*.google.com`` host. After Google's ``notebook.google.com`` cutover the
sign-in redirect chain mints a fresh host ``OSID``; the stale broadcast copy
collides with it and the chain dead-ends on
``accounts.google.com/CookieMismatch``, which the scripts then misreported as
"Authentication expired or invalid".

The live rpc-health workflow cannot gate a PR, so these offline tests pin the
contract instead: the jar each script authenticates with must mirror the
``storage_state`` domains exactly, and a host-scoped cookie must never ride
along to a host it was not scoped to.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from notebooklm._env import get_base_url
from scripts import capture_rpc_registry, check_rpc_health

_ACCOUNTS_HOST = "accounts.google.com"
_SIGN_IN_URL = f"https://{_ACCOUNTS_HOST}/ServiceLogin"

_HOMEPAGE_HTML = (
    '<html><script>window.WIZ_global_data={"SNlM0e":"csrf_ok","FdrFJe":"sess_ok"};</script></html>'
)


def _notebook_host() -> str:
    """The NotebookLM host, read from the library rather than pinned literally.

    Resolved at call time (``get_base_url()`` is env-overridable) so the fixture
    host and the mocked URLs can never disagree, and so this file keeps working
    through the Gemini Notebook rebrand (ADR-0028).
    """
    return httpx.URL(get_base_url()).host


def _cookie_entry(name: str, value: str, domain: str) -> dict[str, Any]:
    return {
        "name": name,
        "value": value,
        "domain": domain,
        "path": "/",
        "expires": -1,
        "httpOnly": True,
        "secure": True,
        "sameSite": "None",
    }


def _storage_state() -> dict[str, Any]:
    """Storage state spanning three hosts, mirroring a real logged-in profile.

    ``SID`` + ``__Secure-1PSIDTS`` are the ``MINIMUM_REQUIRED_COOKIES`` the auth
    validators demand; ``OSID`` and ``LSID`` are the host-scoped pair the #2019
    regression broadcast domain-wide.
    """
    return {
        "cookies": [
            _cookie_entry("SID", "v-sid", ".google.com"),
            _cookie_entry("__Secure-1PSIDTS", "v-psidts", ".google.com"),
            _cookie_entry("OSID", "v-osid", _notebook_host()),
            _cookie_entry("LSID", "v-lsid", _ACCOUNTS_HOST),
        ],
        "origins": [],
    }


def _name_domain_pairs(storage_state: dict[str, Any]) -> set[tuple[str, str]]:
    return {(entry["name"], entry["domain"]) for entry in storage_state["cookies"]}


def _jar_pairs(jar: httpx.Cookies) -> set[tuple[str, str]]:
    return {(cookie.name, cookie.domain) for cookie in jar.jar}


def _cookie_names(header: str) -> set[str]:
    return {pair.split("=", 1)[0].strip() for pair in header.split(";") if pair.strip()}


def _cookie_names_sent_to(httpx_mock: HTTPXMock, host: str) -> list[set[str]]:
    """Cookie names carried by each recorded request that reached ``host``."""
    return [
        _cookie_names(request.headers.get("cookie", ""))
        for request in httpx_mock.get_requests()
        if request.url.host == host
    ]


async def test_check_rpc_health_auth_preserves_cookie_domains(
    monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
) -> None:
    """``check_rpc_health.load_auth`` must build a domain-preserving jar.

    Drives the real auth path against a mocked NotebookLM → sign-in →
    NotebookLM redirect chain (the shape of the post-cutover hop that broke the
    nightly canary) and asserts both halves of the contract: the resulting jar
    mirrors ``storage_state``, and the ``accounts.google.com`` hop carries only
    the cookies actually scoped to it.

    Fails against the pre-#2019 ``load_auth_from_storage`` + ``fetch_tokens``
    pairing, which collapsed all four cookies onto ``.google.com``.
    """
    storage_state = _storage_state()
    monkeypatch.setenv("NOTEBOOKLM_AUTH_JSON", json.dumps(storage_state))

    homepage_url = f"{get_base_url()}/"
    httpx_mock.add_response(url=homepage_url, status_code=302, headers={"Location": _SIGN_IN_URL})
    httpx_mock.add_response(url=_SIGN_IN_URL, status_code=302, headers={"Location": homepage_url})
    httpx_mock.add_response(url=homepage_url, status_code=200, html=_HOMEPAGE_HTML)

    # ``resolve_storage_path()`` returns None under NOTEBOOKLM_AUTH_JSON, which
    # is exactly the CI configuration that exposed the bug.
    storage_path = check_rpc_health.resolve_storage_path()
    assert storage_path is None
    auth = await check_rpc_health.load_auth(storage_path)

    assert auth.csrf_token == "csrf_ok"
    assert auth.session_id == "sess_ok"

    # 1. Jar-level: every cookie kept its original domain.
    assert auth.cookie_jar is not None
    assert _jar_pairs(auth.cookie_jar) == _name_domain_pairs(storage_state)

    # 2. Wire-level: the sign-in hop must not receive the NotebookLM-host OSID.
    sent_to_accounts = _cookie_names_sent_to(httpx_mock, _ACCOUNTS_HOST)
    assert sent_to_accounts, "expected at least one accounts.google.com hop"
    for names in sent_to_accounts:
        assert "OSID" not in names, (
            "the NotebookLM-host OSID must not be broadcast to "
            f"{_ACCOUNTS_HOST} (issue #2019); sent: {sorted(names)}"
        )
        # Positive control: the cookies that *are* scoped there still ride along,
        # so the assertion above cannot pass by dropping cookies wholesale.
        assert {"SID", "LSID"} <= names

    # 3. Positive control on the other side: the NotebookLM host still gets OSID.
    sent_to_notebook = _cookie_names_sent_to(httpx_mock, _notebook_host())
    assert sent_to_notebook
    assert all("OSID" in names for names in sent_to_notebook)


def test_capture_rpc_registry_sends_domain_scoped_cookie_jar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``fetch_bundle`` must hand ``httpx`` a jar, not a domain-less dict.

    ``httpx.get(cookies={...})`` attaches a plain mapping to *every* request in
    the redirect chain regardless of host — the same broadcast the health-check
    fix removes. Fails against the pre-#2019 ``load_auth_from_storage()`` call,
    which returned exactly that mapping.
    """
    storage_state = _storage_state()
    monkeypatch.setenv("NOTEBOOKLM_AUTH_JSON", json.dumps(storage_state))

    bundle_url = (
        f"https://www.gstatic.com/_/mss/{capture_rpc_registry._APP}/_/js/k=boq.en.abc123.js"
    )
    captured: list[tuple[str, Any]] = []

    def _fake_get(url: str, **kwargs: Any) -> httpx.Response:
        captured.append((url, kwargs.get("cookies")))
        request = httpx.Request("GET", url)
        if url == bundle_url:
            return httpx.Response(
                200,
                text="/* bundle chunk */",
                headers={"content-type": "text/javascript"},
                request=request,
            )
        return httpx.Response(200, text=f'<script src="{bundle_url}"></script>', request=request)

    monkeypatch.setattr(httpx, "get", _fake_get)

    assert capture_rpc_registry.fetch_bundle() == "/* bundle chunk */"

    homepage_url, homepage_cookies = captured[0]
    assert homepage_url.startswith(get_base_url())
    assert isinstance(homepage_cookies, httpx.Cookies), (
        "the authenticated homepage read must use a domain-preserving "
        "httpx.Cookies jar, not a flat name->value dict (issue #2019)"
    )
    assert _jar_pairs(homepage_cookies) == _name_domain_pairs(storage_state)

    # The gstatic chunk read stays unauthenticated — it is a public CDN.
    assert captured[1] == (bundle_url, None)
