"""Live assertion that real traffic reaches the configured app host.

Three layers already pin the host offline, and none of them can fail the way
that matters:

* ``tests/_guardrails/test_public_surface_manifest.py`` pins
  ``DEFAULT_BASE_URL`` to the rebrand host and asserts ``get_base_url()``
  agrees — but that is the *constant*, not the socket.
* ``tests/_guardrails/test_app_host_literals_centralized.py`` forbids a second
  source of truth in ``src/notebooklm/`` — but a path that reads the right
  constant can still be handed to the wrong client.
* VCR replay is structurally incapable of covering this: ``tests/vcr_config.py``
  matches on ``host``, so a cassette replays whatever host it was recorded
  against, and the corpus deliberately retains pre-rebrand entries (#2067).

So the whole offline suite can be green while every live request goes somewhere
else. That gap is what this module closes: it watches the requests a *real*
call actually issues, built through the public constructor the e2e ``client``
fixture uses, so it exercises the path users take rather than a test shell.

Asserted against ``get_base_url()`` rather than a literal, so a run using the
documented ``NOTEBOOKLM_BASE_URL`` rollback lever (ADR-0028) still passes — the
question here is "does traffic follow the configuration", and the guardrail
above is what pins what the configuration defaults to. When nothing is
configured, this additionally asserts the default resolves to the rebrand host,
so the CI run makes the unconditional statement too.
"""

from __future__ import annotations

import os

import httpx
import pytest

from notebooklm._env import PERSONAL_BASE_HOST, get_base_url

from .conftest import requires_auth

#: Google's cookie-rotation endpoint. Not app traffic, and legitimately a
#: different host — ``RotateCookies`` is accounts-scoped by construction.
_ACCOUNTS_HOST = "accounts.google.com"


class _RequestLog:
    """Records ``(host, path, status, location)`` for every request issued."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, str, int, str | None]] = []

    def app_entries(self) -> list[tuple[str, str, int, str | None]]:
        """Entries excluding the accounts-scoped cookie rotation."""
        return [e for e in self.entries if e[0] != _ACCOUNTS_HOST]

    def hosts(self) -> set[str]:
        return {e[0] for e in self.entries}


@pytest.fixture
def request_log(monkeypatch: pytest.MonkeyPatch) -> _RequestLog:
    """Record every HTTP request the client under test issues.

    Wraps ``httpx.AsyncClient.send`` rather than injecting a client factory:
    ``async_client_factory`` is deliberately kept off the public constructor
    (see ``client.py``), and routing this test around that constructor would
    stop it testing the thing it exists to test. Wrapping the transport instead
    observes whatever the shipped construction path actually built.
    """
    log = _RequestLog()
    original = httpx.AsyncClient.send

    async def recording_send(self: httpx.AsyncClient, request: httpx.Request, **kwargs: object):
        response = await original(self, request, **kwargs)
        log.entries.append(
            (
                request.url.host,
                request.url.path,
                response.status_code,
                response.headers.get("location"),
            )
        )
        return response

    monkeypatch.setattr(httpx.AsyncClient, "send", recording_send)
    return log


@requires_auth
@pytest.mark.readonly
class TestAppHost:
    """Both tests issue a single ``notebooks.list()`` and mutate nothing.

    Marked ``readonly`` so the release-branch lane (``-m "readonly and not
    variants"`` in ``nightly.yml``) carries the host assertion too — a release
    build aimed at the wrong host is exactly the case worth catching.
    """

    @pytest.mark.asyncio
    async def test_rpc_traffic_reaches_the_configured_host(self, client, request_log):
        """A real RPC lands on the configured app host, with no redirect hop.

        The redirect assertion carries as much weight as the host one. Being
        *bounced* to the right host and *addressing* it are different states:
        the first means the client is aimed at the wrong host and the service is
        covering for it, which is precisely the condition that would rot
        unnoticed until the bounce stops.
        """
        await client.notebooks.list()

        expected = httpx.URL(get_base_url()).host
        app = request_log.app_entries()
        assert app, "no app requests were observed — the recording fixture did not engage"

        wrong_host = [e for e in app if e[0] != expected]
        assert not wrong_host, f"requests left the configured host {expected}: {wrong_host}"

        redirected = [e for e in app if 300 <= e[2] < 400]
        assert not redirected, (
            f"the app host answered with a redirect, so the client is aimed at the "
            f"wrong host and is being bounced: {redirected}"
        )

        assert any("batchexecute" in e[1] for e in app), (
            f"no batchexecute request was observed; paths seen: {[e[1] for e in app]}"
        )

    @pytest.mark.asyncio
    async def test_unconfigured_runs_reach_the_rebrand_host(self, client, request_log):
        """With no override set, that configured host is the rebrand host.

        Skips rather than fails when ``NOTEBOOKLM_BASE_URL`` is set: pointing a
        run at the legacy host is a supported configuration (ADR-0028), and a
        test that failed for it would punish the rollback lever for working.
        """
        if os.environ.get("NOTEBOOKLM_BASE_URL", "").strip():
            pytest.skip("NOTEBOOKLM_BASE_URL is set — this asserts the *unconfigured* default")

        await client.notebooks.list()

        assert httpx.URL(get_base_url()).host == PERSONAL_BASE_HOST
        assert request_log.hosts() - {_ACCOUNTS_HOST} == {PERSONAL_BASE_HOST}
