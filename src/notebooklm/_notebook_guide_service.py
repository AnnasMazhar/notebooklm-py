"""Transport-neutral semantic service for generated notebook guides."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._backend import BackendAdapter
from ._deadline import RuntimeDeadline
from ._projectors import project_notebook_description
from ._records import (
    NOTEBOOK_DESCRIBE_DEF,
    NOTEBOOK_SUMMARIZE_DEF,
    NotebookGuideInput,
)

if TYPE_CHECKING:
    from .types import NotebookDescription


class NotebookGuideService:
    """Generate notebook guides and preserve each public projection."""

    __slots__ = ("_backend",)

    def __init__(self, backend: BackendAdapter) -> None:
        self._backend = backend

    async def get_summary(
        self,
        notebook_id: str,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> str:
        result = await self._backend.invoke(
            NOTEBOOK_SUMMARIZE_DEF,
            NotebookGuideInput(notebook_id),
            deadline=deadline,
        )
        return result.description.summary

    async def get_description(
        self,
        notebook_id: str,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> NotebookDescription:
        result = await self._backend.invoke(
            NOTEBOOK_DESCRIBE_DEF,
            NotebookGuideInput(notebook_id),
            deadline=deadline,
        )
        return project_notebook_description(result.description)


__all__ = ["NotebookGuideService"]
