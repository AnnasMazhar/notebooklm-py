"""Standalone pre-P7 baseline for public constructor-option routing.

P7 replaces the mutable web-runtime holders, so these tests pin each public
``NotebookLMClient.__init__`` option to its current effective owner without a
brittle whole-``vars()`` snapshot.  Coverage is intentionally split as follows:

* this module owns constructor-to-consumer routing, validation, semaphore
  selection, and callback backpressure;
* ``test_client_keepalive.py`` retains keepalive task/rotation lifecycle detail;
* ``test_semantic_p7_observability_baseline.py`` retains the per-RPC metrics
  and telemetry emission matrix.
"""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from notebooklm._middleware.retry import RetryMiddleware
from notebooklm._middleware.semaphore import SemaphoreMiddleware
from notebooklm._runtime.config import DEFAULT_MAX_CONCURRENT_UPLOADS
from notebooklm.auth import AuthTokens
from notebooklm.client import NotebookLMClient
from notebooklm.types import ConnectionLimits, RpcTelemetryEvent, Source


def _auth() -> AuthTokens:
    return AuthTokens(
        cookies={"SID": "option-routing-sid"},
        csrf_token="option-routing-csrf",
        session_id="option-routing-session",
    )


def _middleware(client: NotebookLMClient, kind: type[Any]) -> Any:
    return next(item for item in client._composed.middlewares if isinstance(item, kind))


def test_constructor_options_route_to_current_effective_consumers(tmp_path: Path) -> None:
    """Every P7-listed public kwarg reaches its current runtime consumer."""
    storage_path = tmp_path / "profile.json"
    limits = ConnectionLimits(
        max_connections=40,
        max_keepalive_connections=17,
        keepalive_expiry=12.5,
    )
    upload_timeout = httpx.Timeout(connect=7.0, read=101.0, write=13.0, pool=17.0)
    telemetry_calls: list[RpcTelemetryEvent] = []
    saver_calls: list[tuple[object, object]] = []

    def on_rpc_event(event: RpcTelemetryEvent) -> None:
        telemetry_calls.append(event)

    def cookie_saver(jar: object, path: object, **_kwargs: object) -> None:
        saver_calls.append((jar, path))

    async def cookie_rotator(_client: object, _path: object) -> None:
        return None

    original_auth = _auth()
    client = NotebookLMClient(
        original_auth,
        timeout=37.0,
        storage_path=storage_path,
        keepalive=5.0,
        keepalive_min_interval=11.0,
        rate_limit_max_retries=6,
        server_error_max_retries=7,
        limits=limits,
        max_concurrent_uploads=2,
        max_concurrent_rpcs=9,
        upload_timeout=upload_timeout,
        on_rpc_event=on_rpc_event,
        cookie_saver=cookie_saver,
        cookie_rotator=cookie_rotator,
        chat_timeout=73.0,
        chat_response_max_bytes=8192,
        import_research_timeout=83.0,
    )

    lifecycle = client._collaborators.lifecycle

    # timeout -> lifecycle, executor budget, and ResearchAPI base window.
    assert lifecycle._timeout == 37.0
    assert client._rpc_executor._timeout_provider() == 37.0
    assert client.research._base_timeout == 37.0

    # storage_path -> a client-local AuthTokens copy, persistence owners, and
    # ArtifactsAPI's trusted remote-download client. The caller's AuthTokens is
    # not mutated when it is reused for another client.
    assert original_auth.storage_path is None
    assert client.auth is not original_auth
    assert client.auth.storage_path == storage_path
    assert lifecycle._keepalive_storage_path == storage_path.resolve()
    assert lifecycle._cookie_persistence_path == storage_path.resolve()
    assert client.artifacts._downloads._remote._storage_path == storage_path

    # keepalive + keepalive_min_interval -> the already-clamped lifecycle
    # interval. Task behavior remains in test_client_keepalive.py.
    assert lifecycle._keepalive_interval == 11.0

    # Retry options -> the live chain-host values consumed by RetryMiddleware.
    retry = _middleware(client, RetryMiddleware)
    assert client._composed.chain_host._rate_limit_max_retries == 6
    assert client._composed.chain_host._server_error_max_retries == 7
    assert retry._resolve_rate_limit_max() == 6
    assert retry._resolve_server_error_max() == 7

    # Connection limits are owned by lifecycle.open(), which passes this exact
    # neutral value object to Kernel.open().
    assert lifecycle._limits is limits

    # Upload knobs reach both the compatibility API attributes and the one
    # uploader instance through which SourcesAPI.add_file dispatches.
    assert client.sources._uploader is client._source_uploader
    assert client._backend._source_uploader is client._source_uploader
    assert client.sources._upload_timeout is upload_timeout
    assert client.sources._max_concurrent_uploads == 2
    assert client._source_uploader._upload_timeout is upload_timeout
    assert client._source_uploader._max_concurrent_uploads == 2
    assert client._source_uploader._resolve_upload_timeout(httpx.Timeout(1.0)) is upload_timeout
    assert client._source_uploader.get_upload_semaphore()._value == 2

    # RPC concurrency reaches the holder and the live SemaphoreMiddleware
    # factory; the lazily materialized semaphore has the configured capacity.
    semaphore_middleware = _middleware(client, SemaphoreMiddleware)
    assert client._composed.max_concurrent_rpcs == 9
    assert semaphore_middleware._semaphore_factory.__self__ is client._composed
    assert client._composed.get_rpc_semaphore()._value == 9

    # Callback/cookie seams retain identity at their single runtime owners.
    assert client._collaborators.metrics._on_rpc_event is on_rpc_event
    assert lifecycle._cookie_saver is cookie_saver
    assert lifecycle._cookie_rotator is cookie_rotator
    assert telemetry_calls == []
    assert saver_calls == []

    # Chat options reach the WebRpcBackend stream binding; research import's
    # attempt window remains independently owned by ResearchAPI.
    assert client._backend._chat_timeout == 73.0
    assert client._backend._chat_response_max_bytes == 8192
    assert client.research._import_research_timeout == 83.0


@pytest.mark.asyncio
async def test_upload_options_reach_the_public_sources_api_route(tmp_path: Path) -> None:
    """The public facade reaches the constructor-owned uploader via the backend."""
    upload_timeout = httpx.Timeout(47.0)
    client = NotebookLMClient(
        _auth(),
        upload_timeout=upload_timeout,
        max_concurrent_uploads=3,
    )
    uploaded = Source(id="uploaded-source", title="report.txt")
    add_file = AsyncMock(return_value=uploaded)
    client._source_uploader.add_file = add_file
    path = tmp_path / "report.txt"

    result = await client.sources.add_file(
        "notebook-id",
        path,
        mime_type="text/plain",
        wait=True,
        wait_timeout=41.0,
        title="Report",
    )

    assert result == uploaded
    assert client._source_uploader._resolve_upload_timeout(httpx.Timeout(1.0)) is upload_timeout
    assert client._source_uploader.get_upload_semaphore()._value == 3
    add_file.assert_awaited_once_with(
        "notebook-id",
        path,
        mime_type="text/plain",
        wait=True,
        wait_timeout=41.0,
        title="Report",
        on_progress=None,
    )


@pytest.mark.parametrize("bad_value", [0, -1])
def test_max_concurrent_uploads_validation_is_constructor_owned(bad_value: int) -> None:
    with pytest.raises(
        ValueError,
        match=rf"max_concurrent_uploads must be >= 1, got {bad_value}",
    ):
        NotebookLMClient(_auth(), max_concurrent_uploads=bad_value)


def test_none_upload_limit_normalizes_only_at_the_uploader_owner() -> None:
    client = NotebookLMClient(_auth(), max_concurrent_uploads=None)

    # SourcesAPI retains the public constructor input for compatibility, while
    # its injected uploader owns the effective normalized limit.
    assert client.sources._max_concurrent_uploads is None
    assert client.sources._uploader is client._source_uploader
    assert client._source_uploader._max_concurrent_uploads == DEFAULT_MAX_CONCURRENT_UPLOADS
    assert client._source_uploader.get_upload_semaphore()._value == DEFAULT_MAX_CONCURRENT_UPLOADS


@pytest.mark.parametrize("bad_value", [0, -1])
def test_max_concurrent_rpcs_validation_is_constructor_owned(bad_value: int) -> None:
    with pytest.raises(
        ValueError,
        match=rf"max_concurrent_rpcs must be >= 1, got {bad_value}",
    ):
        NotebookLMClient(_auth(), max_concurrent_rpcs=bad_value)


def test_rpc_limit_cross_validates_against_connection_pool() -> None:
    with pytest.raises(
        ValueError,
        match=r"max_concurrent_rpcs must be <= limits\.max_connections",
    ):
        NotebookLMClient(
            _auth(),
            limits=ConnectionLimits(max_connections=3),
            max_concurrent_rpcs=4,
        )


def test_none_rpc_limit_routes_to_the_unbounded_semaphore_path() -> None:
    client = NotebookLMClient(_auth(), max_concurrent_rpcs=None)
    semaphore_middleware = _middleware(client, SemaphoreMiddleware)

    assert client._composed.max_concurrent_rpcs is None
    assert isinstance(client._composed.get_rpc_semaphore(), type(nullcontext()))
    assert semaphore_middleware._semaphore_factory.__self__ is client._composed


@pytest.mark.asyncio
async def test_on_rpc_event_callback_is_owned_and_awaited() -> None:
    """The configured callback applies backpressure at the metrics owner."""
    entered = asyncio.Event()
    release = asyncio.Event()
    received: list[RpcTelemetryEvent] = []

    async def callback(event: RpcTelemetryEvent) -> None:
        entered.set()
        await release.wait()
        received.append(event)

    client = NotebookLMClient(_auth(), on_rpc_event=callback)
    event = RpcTelemetryEvent(
        method="option-routing",
        status="success",
        elapsed_seconds=0.25,
        request_id="request-1",
    )

    emission = asyncio.create_task(client._collaborators.metrics.emit_rpc_event(event))
    await asyncio.wait_for(entered.wait(), timeout=1.0)
    await asyncio.sleep(0)
    assert not emission.done()

    release.set()
    await emission
    assert received == [event]


@pytest.mark.parametrize("field", ["chat_timeout", "import_research_timeout"])
@pytest.mark.parametrize("bad_value", [0.0, -1.0, float("inf")])
def test_per_operation_timeout_validation_stays_at_construction(
    field: str,
    bad_value: float,
) -> None:
    kwargs = {field: bad_value}
    with pytest.raises(ValueError, match=field):
        NotebookLMClient(_auth(), **kwargs)


def test_chat_response_size_validation_stays_at_construction() -> None:
    with pytest.raises(ValueError, match="chat_response_max_bytes must be >= 1"):
        NotebookLMClient(_auth(), chat_response_max_bytes=0)
