"""Focused regression coverage for idempotent-create provenance (#1988)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from notebooklm._idempotency import (
    _CreateResultKind,
    _IdempotentCreateResult,
    idempotent_create,
)
from notebooklm._records import (
    SOURCE_ADD_URL_DEF,
    SourceAddCommitState,
    SourceAddTitleState,
    SourceAddUrlReceipt,
    SourceAddUrlResult,
    SourceRecord,
)
from notebooklm._source.add import SourceAddService
from notebooklm._sources import SourcesAPI
from notebooklm.exceptions import NetworkError
from notebooklm.types import Source
from tests._fixtures.recording_backend import RecordingBackend


@pytest.mark.asyncio
async def test_idempotent_create_marks_fresh_create() -> None:
    result = await idempotent_create(
        AsyncMock(return_value="created"), AsyncMock(return_value=None)
    )
    assert result == _IdempotentCreateResult("created", _CreateResultKind.CREATED)


@pytest.mark.asyncio
async def test_idempotent_create_marks_probe_match_without_retry() -> None:
    create = AsyncMock(side_effect=NetworkError("lost response"))
    probe = AsyncMock(return_value="existing")

    result = await idempotent_create(create, probe)

    assert result.value == "existing"
    assert result.kind is _CreateResultKind.PROBED
    create.assert_awaited_once()
    probe.assert_awaited_once()


@pytest.mark.asyncio
async def test_idempotent_create_retries_after_probe_miss() -> None:
    create = AsyncMock(side_effect=[NetworkError("first"), "second"])
    probe = AsyncMock(return_value=None)

    result = await idempotent_create(create, probe)

    assert result.value == "second"
    assert result.kind is _CreateResultKind.CREATED
    assert create.await_count == 2
    assert probe.await_count == 1


@pytest.mark.asyncio
async def test_idempotent_create_reraises_last_exception_by_identity() -> None:
    error = NetworkError("still unavailable")
    create = AsyncMock(side_effect=[NetworkError("first"), error])

    with pytest.raises(NetworkError) as raised:
        await idempotent_create(create, AsyncMock(return_value=None))

    assert raised.value is error


@pytest.mark.asyncio
async def test_probed_url_result_preserves_one_facade_wait() -> None:
    """A ``PROBED`` ``add_url`` result receives exactly one facade wait.

    #1988 skipped the rename for every ``PROBED`` result because a probe match
    could not be proven to be the caller's own — renaming a stranger's source
    would be a surprise. #2204 filters ``add_url`` probe matches against a
    baseline captured before the create, so a match now *is* provably fresh and
    the requested title must win (the same flip #2113 made for ``add_drive``).

    The semantic handler never polls. The neutral request carries ``wait`` only
    so title application can be deferred; the public facade owns the one wait.
    """
    backend = RecordingBackend()
    backend.set_result(
        SOURCE_ADD_URL_DEF,
        SourceAddUrlResult(
            SourceRecord(
                id="existing",
                title="Retitle me",
                url="https://example.test",
            ),
            SourceAddUrlReceipt(
                SourceAddCommitState.RECONCILED,
                SourceAddTitleState.RENAMED,
            ),
        ),
    )
    api = SourcesAPI(MagicMock(), uploader=MagicMock(), _backend=backend)
    api.wait_until_ready = AsyncMock(  # type: ignore[method-assign]
        return_value=Source(id="existing", title="Retitle me")
    )

    result = await api.add_url(
        "nb",
        "https://example.test",
        title="Retitle me",
        wait=True,
    )

    assert result.title == "Retitle me"
    assert len(backend.invocations) == 1
    invocation = backend.invocations[0]
    assert invocation.operation is SOURCE_ADD_URL_DEF.key
    assert invocation.value.wait is True
    assert invocation.value.requested_title == "Retitle me"
    api.wait_until_ready.assert_awaited_once_with("nb", "existing", timeout=120.0)


@pytest.mark.asyncio
async def test_private_service_default_return_remains_source() -> None:
    service = SourceAddService()
    source = Source(id="fresh", title="Upstream")

    result = await service.add_url(
        "nb",
        "https://example.test",
        add_youtube_source=AsyncMock(),
        add_url_source=AsyncMock(return_value=source),
        list_sources=AsyncMock(return_value=[]),
        wait_until_ready=AsyncMock(),
        extract_youtube_video_id=MagicMock(return_value=None),
        is_youtube_url=MagicMock(return_value=False),
        logger=MagicMock(),
    )

    assert isinstance(result, Source)
    assert result.id == source.id


@pytest.mark.asyncio
async def test_private_service_preserves_probe_provenance_through_wait() -> None:
    service = SourceAddService()
    existing = Source(id="existing", title="Upstream", url="https://example.test")
    ready = Source(id="existing", title="Ready", url=existing.url)

    result = await service.add_url(
        "nb",
        existing.url,
        wait=True,
        add_youtube_source=AsyncMock(),
        add_url_source=AsyncMock(side_effect=NetworkError("lost response")),
        # Baseline (empty) then probe: the source must be absent before the
        # create for the probe to claim it as this call's own (#2204).
        list_sources=AsyncMock(side_effect=[[], [existing]]),
        wait_until_ready=AsyncMock(return_value=ready),
        extract_youtube_video_id=MagicMock(return_value=None),
        is_youtube_url=MagicMock(return_value=False),
        logger=MagicMock(),
        return_result=True,
    )

    assert isinstance(result, _IdempotentCreateResult)
    assert result.kind is _CreateResultKind.PROBED
    assert result.value is ready


@pytest.mark.asyncio
async def test_idempotent_create_aborts_when_the_probe_raises() -> None:
    """A probe that raises stops the loop — no further create attempt (#2220).

    Pinned at the wrapper's own level, not only through the four in-tree probes:
    this is the seam a fifth ``PROBE_THEN_CREATE`` path would rely on, and the
    contract it depends on is that ``None`` is the *only* way to authorize
    another create.
    """
    create = AsyncMock(side_effect=NetworkError("lost response"))
    probe_error = RuntimeError("probe cannot answer")

    with pytest.raises(RuntimeError) as exc_info:
        await idempotent_create(create, AsyncMock(side_effect=probe_error), max_attempts=3)

    assert exc_info.value is probe_error
    # One attempt, despite max_attempts=3: the probe never said "no match".
    assert create.await_count == 1
    # The transport failure that made the probe run is still reachable.
    assert isinstance(probe_error.__context__, NetworkError)
