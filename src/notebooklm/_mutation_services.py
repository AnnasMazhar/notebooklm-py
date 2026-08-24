"""Transport-neutral semantic mutation services."""

from __future__ import annotations

from ._backend import BackendAdapter
from ._deadline import RuntimeDeadline
from ._records import SOURCE_ADD_URL_DEF, SourceAddUrlInput, SourceAddUrlResult


class SourceUrlMutationService:
    """Invoke the adapter-owned URL/YouTube registration workflow."""

    __slots__ = ("_backend",)

    def __init__(self, backend: BackendAdapter) -> None:
        self._backend = backend

    async def add_url(
        self,
        notebook_id: str,
        url: str,
        *,
        wait: bool = False,
        wait_timeout: float = 120.0,
        requested_title: str | None = None,
        deadline: RuntimeDeadline | None = None,
    ) -> SourceAddUrlResult:
        """Register one URL through the backend's single shared variant."""
        return await self._backend.invoke(
            SOURCE_ADD_URL_DEF,
            SourceAddUrlInput(
                notebook_id=notebook_id,
                url=url,
                wait=wait,
                wait_timeout=wait_timeout,
                requested_title=requested_title,
            ),
            deadline=deadline,
        )


__all__ = ["SourceUrlMutationService"]
