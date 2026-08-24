"""Unit tests for the private notebook metadata service."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from notebooklm._notebook_metadata import NotebookMetadataService
from notebooklm.types import Notebook, NotebookMetadata, Source, SourceType


@pytest.mark.asyncio
async def test_metadata_service_uses_injected_lister_and_builds_source_summaries() -> None:
    get_notebook = AsyncMock(
        return_value=Notebook(id="nb_123", title="Architecture", sources_count=2)
    )
    source_lister = MagicMock()
    source_lister.list = AsyncMock(
        return_value=[
            Source(
                id="src_web",
                title="Architecture Notes",
                url="https://example.com/notes",
                _type_code=5,  # SourceType.WEB_PAGE
            ),
            Source(id="src_pdf", title="Design Paper", _type_code=3),  # SourceType.PDF
        ]
    )
    service = NotebookMetadataService(get_notebook, source_lister)

    metadata = await service.get_metadata("nb_123")

    assert isinstance(metadata, NotebookMetadata)
    assert metadata.notebook == Notebook(id="nb_123", title="Architecture", sources_count=2)
    assert [source.kind for source in metadata.sources] == [
        SourceType.WEB_PAGE,
        SourceType.PDF,
    ]
    assert metadata.sources[0].title == "Architecture Notes"
    assert metadata.sources[0].url == "https://example.com/notes"
    get_notebook.assert_awaited_once_with("nb_123")
    source_lister.list.assert_awaited_once_with("nb_123")


@pytest.mark.asyncio
async def test_metadata_service_fetches_notebook_and_sources_concurrently() -> None:
    get_started = asyncio.Event()
    list_started = asyncio.Event()
    release = asyncio.Event()

    async def get_notebook(notebook_id: str) -> Notebook:
        assert notebook_id == "nb_123"
        get_started.set()
        await list_started.wait()
        await release.wait()
        return Notebook(id="nb_123", title="Concurrent", sources_count=1)

    async def list_sources(notebook_id: str) -> list[Source]:
        assert notebook_id == "nb_123"
        list_started.set()
        await get_started.wait()
        await release.wait()
        return [Source(id="src_1", title="Paper", _type_code=3)]  # SourceType.PDF

    source_lister = MagicMock()
    source_lister.list = AsyncMock(side_effect=list_sources)
    service = NotebookMetadataService(get_notebook, source_lister)

    metadata_task = asyncio.create_task(service.get_metadata("nb_123"))
    await asyncio.wait_for(get_started.wait(), timeout=1)
    await asyncio.wait_for(list_started.wait(), timeout=1)
    assert not metadata_task.done()

    release.set()
    metadata = await metadata_task

    assert metadata.notebook.title == "Concurrent"
    assert metadata.sources[0].kind == SourceType.PDF


@pytest.mark.asyncio
async def test_metadata_service_preserves_empty_source_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    get_notebook = AsyncMock(return_value=Notebook(id="nb_123", title="Sparse", sources_count=2))
    source_lister = MagicMock()
    source_lister.list = AsyncMock(return_value=[])
    service = NotebookMetadataService(get_notebook, source_lister)

    with caplog.at_level(logging.WARNING, logger="notebooklm._notebooks"):
        metadata = await service.get_metadata("nb_123")

    assert metadata.sources == []
    assert "Notebook nb_123 reports 2 sources but listing returned empty" in caplog.text


@pytest.mark.asyncio
async def test_metadata_service_propagates_notebook_lookup_errors() -> None:
    error = RuntimeError("notebook lookup failed")
    get_notebook = AsyncMock(side_effect=error)
    source_lister = MagicMock()
    source_lister.list = AsyncMock(return_value=[Source(id="src_1")])
    service = NotebookMetadataService(get_notebook, source_lister)

    with pytest.raises(RuntimeError, match="notebook lookup failed"):
        await service.get_metadata("nb_123")

    get_notebook.assert_awaited_once_with("nb_123")
    source_lister.list.assert_awaited_once_with("nb_123")


@pytest.mark.asyncio
async def test_metadata_service_propagates_source_listing_errors() -> None:
    get_notebook = AsyncMock(return_value=Notebook(id="nb_123", title="Notebook"))
    source_lister = MagicMock()
    source_lister.list = AsyncMock(side_effect=RuntimeError("source listing failed"))
    service = NotebookMetadataService(get_notebook, source_lister)

    with pytest.raises(RuntimeError, match="source listing failed"):
        await service.get_metadata("nb_123")

    get_notebook.assert_awaited_once_with("nb_123")
    source_lister.list.assert_awaited_once_with("nb_123")
