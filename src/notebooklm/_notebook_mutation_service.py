"""Transport-neutral semantic service for the P2.2 notebook mutation slice."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._backend import BackendAdapter
from ._deadline import RuntimeDeadline
from ._projectors import project_notebook
from ._records import (
    NOTEBOOK_CREATE_DEF,
    NOTEBOOK_DELETE_DEF,
    NOTEBOOK_UPDATE_DEF,
    NotebookCreateInput,
    NotebookDeleteInput,
    NotebookUpdateInput,
)
from .exceptions import ValidationError

if TYPE_CHECKING:
    from .types import Notebook


class NotebookMutationService:
    """Validate notebook mutations and invoke their typed backend operations."""

    __slots__ = ("_backend",)

    def __init__(self, backend: BackendAdapter) -> None:
        self._backend = backend

    async def create(
        self,
        title: str,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> Notebook:
        result = await self._backend.invoke(
            NOTEBOOK_CREATE_DEF,
            NotebookCreateInput(title),
            deadline=deadline,
        )
        return project_notebook(result.notebook)

    async def update(
        self,
        notebook_id: str,
        *,
        title: str | None = None,
        emoji: str | None = None,
        deadline: RuntimeDeadline | None = None,
    ) -> Notebook:
        if title is None and emoji is None:
            raise ValidationError("At least one of title or emoji must be provided")
        result = await self._backend.invoke(
            NOTEBOOK_UPDATE_DEF,
            NotebookUpdateInput(notebook_id, title=title, emoji=emoji),
            deadline=deadline,
        )
        return project_notebook(result.notebook)

    async def update_title(
        self,
        notebook_id: str,
        title: str,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> Notebook:
        """Compatibility convenience over the generic property mutation."""
        return await self.update(notebook_id, title=title, deadline=deadline)

    async def delete(
        self,
        notebook_id: str,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> None:
        await self._backend.invoke(
            NOTEBOOK_DELETE_DEF,
            NotebookDeleteInput(notebook_id),
            deadline=deadline,
        )


__all__ = ["NotebookMutationService"]
