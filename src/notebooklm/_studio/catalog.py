"""Semantic Studio catalog over the neutral backend port."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .._backend import BackendAdapter
from .._deadline import RuntimeDeadline
from .._projectors import project_artifact
from .._records import (
    ARTIFACT_GET_DEF,
    ARTIFACT_LIST_DEF,
    ArtifactGetInput,
    ArtifactListInput,
    ArtifactRecord,
)
from .classifiers import matches_artifact_family

if TYPE_CHECKING:
    from ..types import Artifact


class StudioCatalog:
    """List and select complete heterogeneous Studio records."""

    __slots__ = ("_backend",)

    def __init__(self, backend: BackendAdapter) -> None:
        self._backend = backend

    async def list(
        self,
        notebook_id: str,
        family: str | None = None,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> list[Artifact]:
        records = await self.list_records(notebook_id, family, deadline=deadline)
        return [project_artifact(record) for record in records]

    async def list_records(
        self,
        notebook_id: str,
        family: str | None = None,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> tuple[ArtifactRecord, ...]:
        """Return complete neutral catalog records for family services."""

        result = await self._backend.invoke(
            ARTIFACT_LIST_DEF,
            ArtifactListInput(notebook_id, family),
            deadline=deadline,
        )
        return tuple(
            record for record in result.artifacts if matches_artifact_family(record, family)
        )

    async def get_record(
        self,
        notebook_id: str,
        artifact_id: str,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> ArtifactRecord | None:
        """Return one complete neutral catalog record, if present."""

        result = await self._backend.invoke(
            ARTIFACT_GET_DEF,
            ArtifactGetInput(notebook_id, artifact_id),
            deadline=deadline,
        )
        return result.artifact

    async def get_or_none(
        self,
        notebook_id: str,
        artifact_id: str,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> Artifact | None:
        record = await self.get_record(notebook_id, artifact_id, deadline=deadline)
        return None if record is None else project_artifact(record)


__all__ = ["StudioCatalog"]
