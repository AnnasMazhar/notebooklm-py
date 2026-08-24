"""Transport-neutral semantic service for the migrated P6.7 Source slice."""

from __future__ import annotations

from pathlib import Path

from ._backend import BackendAdapter
from ._deadline import RuntimeDeadline
from ._records import (
    SOURCE_ADD_DRIVE_DEF,
    SOURCE_ADD_FILE_DEF,
    SOURCE_ADD_TEXT_DEF,
    SOURCE_ADD_URL_BATCH_DEF,
    SOURCE_CHECK_FRESHNESS_DEF,
    SOURCE_DELETE_DEF,
    SOURCE_GET_FULLTEXT_DEF,
    SOURCE_GET_GUIDE_DEF,
    SOURCE_REFRESH_DEF,
    SOURCE_UPDATE_DEF,
    SourceAddDriveInput,
    SourceAddDriveResult,
    SourceAddFileInput,
    SourceAddFileResult,
    SourceAddTextInput,
    SourceAddTextResult,
    SourceAddUrlBatchInput,
    SourceAddUrlBatchResult,
    SourceDeleteInput,
    SourceFileInputKind,
    SourceFreshnessInput,
    SourceFulltextInput,
    SourceFulltextResult,
    SourceGuideInput,
    SourceGuideResult,
    SourceProgressCallback,
    SourceRefreshInput,
    SourceUpdateInput,
    SourceUpdateResult,
)


class SourceService:
    """Invoke typed Source operations without web/RPC/public-model vocabulary."""

    __slots__ = ("_backend",)

    def __init__(self, backend: BackendAdapter) -> None:
        self._backend = backend

    async def add_urls_batch(
        self,
        notebook_id: str,
        urls: tuple[str, ...],
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> SourceAddUrlBatchResult:
        return await self._backend.invoke(
            SOURCE_ADD_URL_BATCH_DEF,
            SourceAddUrlBatchInput(notebook_id, urls),
            deadline=deadline,
        )

    async def add_text(
        self,
        notebook_id: str,
        title: str,
        content: str,
        *,
        wait: bool,
        wait_timeout: float,
        idempotent: bool,
        deadline: RuntimeDeadline | None = None,
    ) -> SourceAddTextResult:
        return await self._backend.invoke(
            SOURCE_ADD_TEXT_DEF,
            SourceAddTextInput(
                notebook_id,
                title,
                content,
                wait=wait,
                wait_timeout=wait_timeout,
                idempotent=idempotent,
            ),
            deadline=deadline,
        )

    async def add_drive(
        self,
        notebook_id: str,
        file_id: str,
        title: str,
        *,
        mime_type: str,
        wait: bool,
        wait_timeout: float,
        deadline: RuntimeDeadline | None = None,
    ) -> SourceAddDriveResult:
        return await self._backend.invoke(
            SOURCE_ADD_DRIVE_DEF,
            SourceAddDriveInput(
                notebook_id,
                file_id,
                title,
                mime_type,
                wait=wait,
                wait_timeout=wait_timeout,
            ),
            deadline=deadline,
        )

    async def add_file(
        self,
        notebook_id: str,
        file_path: str | Path,
        *,
        mime_type: str | None,
        wait: bool,
        wait_timeout: float,
        title: str | None,
        on_progress: SourceProgressCallback | None,
    ) -> SourceAddFileResult:
        return await self._backend.invoke(
            SOURCE_ADD_FILE_DEF,
            SourceAddFileInput(
                notebook_id,
                SourceFileInputKind.LOCAL,
                file_path=file_path,
                mime_type=mime_type,
                title=title,
                wait=wait,
                wait_timeout=wait_timeout,
                on_progress=on_progress,
            ),
            deadline=None,
        )

    async def add_drive_file(
        self,
        notebook_id: str,
        document_id: str,
        *,
        title: str | None,
        wait: bool,
        wait_timeout: float,
    ) -> SourceAddFileResult:
        return await self._backend.invoke(
            SOURCE_ADD_FILE_DEF,
            SourceAddFileInput(
                notebook_id,
                SourceFileInputKind.DRIVE_DOWNLOAD,
                document_id=document_id,
                title=title,
                wait=wait,
                wait_timeout=wait_timeout,
            ),
            deadline=None,
        )

    async def delete(self, notebook_id: str, source_id: str) -> None:
        await self._backend.invoke(
            SOURCE_DELETE_DEF,
            SourceDeleteInput(notebook_id, source_id),
            deadline=None,
        )

    async def update(
        self,
        notebook_id: str,
        source_id: str,
        new_title: str,
        *,
        return_object: bool,
    ) -> SourceUpdateResult:
        return await self._backend.invoke(
            SOURCE_UPDATE_DEF,
            SourceUpdateInput(
                notebook_id,
                source_id,
                new_title,
                return_object=return_object,
            ),
            deadline=None,
        )

    async def refresh(self, notebook_id: str, source_id: str) -> None:
        await self._backend.invoke(
            SOURCE_REFRESH_DEF,
            SourceRefreshInput(notebook_id, source_id),
            deadline=None,
        )

    async def check_freshness(self, notebook_id: str, source_id: str) -> bool:
        result = await self._backend.invoke(
            SOURCE_CHECK_FRESHNESS_DEF,
            SourceFreshnessInput(notebook_id, source_id),
            deadline=None,
        )
        return result.fresh

    async def get_guide(self, notebook_id: str, source_id: str) -> SourceGuideResult:
        return await self._backend.invoke(
            SOURCE_GET_GUIDE_DEF,
            SourceGuideInput(notebook_id, source_id),
            deadline=None,
        )

    async def get_fulltext(
        self,
        notebook_id: str,
        source_id: str,
        *,
        output_format: str,
    ) -> SourceFulltextResult:
        return await self._backend.invoke(
            SOURCE_GET_FULLTEXT_DEF,
            SourceFulltextInput(notebook_id, source_id, output_format),
            deadline=None,
        )


__all__ = ["SourceService"]
