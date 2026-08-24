"""Construction-time seams for middleware-pipeline behavior.

These tests deliberately vary retry, refresh, and terminal behavior only at
client construction. They do not mutate the live middleware holder, which keeps
the suite independent of the pre-P7 runtime graph.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from notebooklm._middleware.core import NextCall, RpcRequest, RpcResponse
from notebooklm._request_types import AuthSnapshot
from notebooklm.auth import AuthTokens
from notebooklm.client import NotebookLMClient
from tests._helpers.client_factory import build_client_shell_for_tests
from tests.unit.conftest import install_post_as_stream


@pytest.fixture(autouse=True)
def _no_backoff_jitter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(random, "uniform", lambda a, b: 0.0)


def _make_core(
    *,
    refresh_callback: Callable[[], Any] | None = None,
    rate_limit_max_retries: int = 0,
    server_error_max_retries: int = 0,
    refresh_retry_delay: float = 0.0,
    authed_post_terminal: NextCall | None = None,
) -> NotebookLMClient:
    auth = AuthTokens(
        csrf_token="CSRF",
        session_id="SID",
        cookies={"SID": "sid_cookie"},
    )
    return build_client_shell_for_tests(
        auth=auth,
        refresh_callback=refresh_callback,
        refresh_retry_delay=refresh_retry_delay,
        rate_limit_max_retries=rate_limit_max_retries,
        server_error_max_retries=server_error_max_retries,
        authed_post_terminal=authed_post_terminal,
    )


def _ok_response(text: str = "OK") -> httpx.Response:
    return httpx.Response(
        200,
        text=text,
        request=httpx.Request("POST", "https://example.test/x"),
    )


def _status_error(code: int, *, retry_after: str | None = None) -> httpx.HTTPStatusError:
    headers = {"retry-after": retry_after} if retry_after else {}
    request = httpx.Request("POST", "https://example.test/x")
    response = httpx.Response(code, request=request, headers=headers)
    return httpx.HTTPStatusError(f"HTTP {code}", request=request, response=response)


@pytest.mark.asyncio
async def test_constructor_retry_budget_steers_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A client configured with one retry performs exactly one 429 retry."""

    core = _make_core(rate_limit_max_retries=1)
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    def build(snapshot: AuthSnapshot) -> tuple[str, str, dict[str, str]]:
        return "https://example.test/x", "payload", {}

    call_count = 0

    async def fake_post(*args: Any, **kwargs: Any) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise _status_error(429, retry_after="1")
        return _ok_response()

    await core.__aenter__()
    try:
        install_post_as_stream(
            monkeypatch,
            core._backend._kernel.get_http_client(),
            fake_post,
        )
        response = await core._backend._runtime._transport.perform_authed_post(
            build_request=build,
            log_label="test-constructor-retry-budget",
        )
    finally:
        await core.close()

    assert response.status_code == 200
    assert call_count == 2
    assert sleeps == [1]


@pytest.mark.asyncio
async def test_constructor_refresh_callback_is_the_refresh_leaf() -> None:
    calls: list[None] = []
    auth = AuthTokens(csrf_token="CSRF", session_id="SID", cookies={"SID": "cookie"})

    async def fake_refresh() -> AuthTokens:
        calls.append(None)
        return auth

    core = build_client_shell_for_tests(auth=auth, refresh_callback=fake_refresh)

    await core._backend._auth_coord.await_refresh()
    await core._backend._auth_coord.await_refresh()

    assert calls == [None, None]


@pytest.mark.asyncio
async def test_constructor_terminal_leaf_steers_real_middleware_pipeline() -> None:
    captured: list[RpcRequest] = []

    async def fake_terminal(request: RpcRequest) -> RpcResponse:
        captured.append(request)
        return RpcResponse(response=_ok_response("fake-terminal"), state=request.state)

    core = _make_core(authed_post_terminal=fake_terminal)
    await core.__aenter__()
    try:

        def build(snapshot: AuthSnapshot) -> tuple[str, str, dict[str, str]]:
            return "https://example.test/x", "payload", {"X-Test": "yes"}

        response = await core._backend._runtime._transport.perform_authed_post(
            build_request=build,
            log_label="test-terminal-leaf",
        )
    finally:
        await core.close()

    assert response.status_code == 200
    assert response.text == "fake-terminal"
    assert len(captured) == 1
    assert captured[0].url == "https://example.test/x"


def test_constructor_retry_values_reach_runtime_configuration() -> None:
    core = _make_core(
        rate_limit_max_retries=7,
        server_error_max_retries=11,
        refresh_retry_delay=0.5,
    )

    assert core._backend._chain_host._rate_limit_max_retries == 7
    assert core._backend._chain_host._server_error_max_retries == 11
    assert core._backend._chain_host._refresh_retry_delay == 0.5
