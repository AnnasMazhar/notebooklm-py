"""Shared, non-test support for focused ``RpcExecutor`` tests."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from notebooklm._request_types import AuthSnapshot
from notebooklm._rpc_executor import RpcExecutor


def _ok_response(text: str = "raw") -> httpx.Response:
    return httpx.Response(
        200,
        text=text,
        request=httpx.Request("POST", "https://example.test/rpc"),
    )


class _Owner:
    """Stub satisfying the executor's four injected collaborator protocols."""

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        refresh_callback: Callable[[], Awaitable[Any]] | None = None,
        refresh_retry_delay: float = 0.0,
    ):
        self._timeout = timeout
        self._refresh_callback = refresh_callback
        self._refresh_retry_delay = refresh_retry_delay
        self.perform_calls: list[dict[str, Any]] = []
        self.refresh_calls = 0
        self.metric_increments: list[dict[str, int | float]] = []
        self.response = _ok_response()
        self.snapshot = AuthSnapshot(
            csrf_token="CSRF_SNAPSHOT",
            session_id="SID_SNAPSHOT",
            authuser=1,
            account_email="user@example.test",
        )
        self._kernel = self

    def get_http_client(self) -> object:
        return object()

    def increment(self, **increments: int | float) -> None:
        self.metric_increments.append(increments)

    async def perform_authed_post(
        self,
        *,
        build_request,
        log_label: str,
        disable_internal_retries: bool = False,
        rpc_method: str | None = None,
        refresh_budget: Any = None,
        retry_deadline: Any = None,
        read_timeout: float | None = None,
    ) -> httpx.Response:
        url, body, headers = build_request(self.snapshot)
        self.perform_calls.append(
            {
                "log_label": log_label,
                "disable_internal_retries": disable_internal_retries,
                "url": url,
                "body": body,
                "headers": headers,
                "refresh_budget": refresh_budget,
                "retry_deadline": retry_deadline,
                "read_timeout": read_timeout,
            }
        )
        return self.response

    async def await_refresh(self) -> None:
        self.refresh_calls += 1


def _executor(
    owner: _Owner,
    *,
    decode_response: Callable[..., Any] | None = None,
    is_auth_error: Callable[[Exception], bool] | None = None,
    sleep: Callable[[float], Awaitable[Any]] | None = None,
) -> RpcExecutor:
    async def _no_sleep(_: float) -> None:
        return None

    def _decode(
        _: str, rpc_id: str, *, allow_null: bool = False, raise_on_null_status: bool = False
    ) -> dict[str, Any]:
        return {"rpc_id": rpc_id, "allow_null": allow_null}

    return RpcExecutor(
        assert_open=owner.get_http_client,
        transport=owner,  # type: ignore[arg-type]
        refresh=owner.await_refresh,
        metrics=owner,  # type: ignore[arg-type]
        decode_response=decode_response or _decode,
        is_auth_error=is_auth_error or (lambda exc: False),
        sleep=sleep or _no_sleep,
        timeout_provider=lambda: owner._timeout,
        refresh_callback_enabled_provider=lambda: owner._refresh_callback is not None,
        refresh_retry_delay_provider=lambda: owner._refresh_retry_delay,
    )


__all__ = ["_executor", "_ok_response", "_Owner"]
