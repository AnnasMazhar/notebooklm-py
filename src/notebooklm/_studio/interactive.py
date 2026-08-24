"""Transport-neutral Quiz and Flashcards family behavior."""

from __future__ import annotations

from typing import Literal

from .._backend import BackendAdapter
from .._deadline import RuntimeDeadline
from .._records import (
    ARTIFACT_GENERATE_FLASHCARDS_DEF,
    ARTIFACT_GENERATE_QUIZ_DEF,
    ArtifactRecord,
    InteractiveGenerateInput,
    InteractiveGenerateResult,
    InteractiveMetadataRecord,
)
from .catalog import StudioCatalog


class InteractiveFamilyService:
    """Quiz/flashcard generation, discovery, and usable-readiness metadata."""

    __slots__ = ("_backend", "_catalog")

    def __init__(self, backend: BackendAdapter, catalog: StudioCatalog) -> None:
        self._backend = backend
        self._catalog = catalog

    async def generate_quiz(
        self,
        value: InteractiveGenerateInput,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> InteractiveGenerateResult:
        return await self._backend.invoke(
            ARTIFACT_GENERATE_QUIZ_DEF,
            value,
            deadline=deadline,
        )

    async def generate_flashcards(
        self,
        value: InteractiveGenerateInput,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> InteractiveGenerateResult:
        return await self._backend.invoke(
            ARTIFACT_GENERATE_FLASHCARDS_DEF,
            value,
            deadline=deadline,
        )

    async def list_quizzes(
        self,
        notebook_id: str,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> tuple[ArtifactRecord, ...]:
        return await self._catalog.list_records(notebook_id, "quiz", deadline=deadline)

    async def list_flashcards(
        self,
        notebook_id: str,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> tuple[ArtifactRecord, ...]:
        return await self._catalog.list_records(notebook_id, "flashcards", deadline=deadline)

    async def get(
        self,
        notebook_id: str,
        artifact_id: str,
        family: Literal["quiz", "flashcards"],
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> ArtifactRecord | None:
        if family not in {"quiz", "flashcards"}:
            raise ValueError("interactive family must be 'quiz' or 'flashcards'")
        record = await self._catalog.get_record(
            notebook_id,
            artifact_id,
            deadline=deadline,
        )
        return record if record is not None and record.family == family else None

    @staticmethod
    def metadata(record: ArtifactRecord) -> InteractiveMetadataRecord:
        """Preserve family user-state while separating usable from terminal."""

        if record.family not in {"quiz", "flashcards"}:
            raise ValueError("interactive metadata requires a quiz or flashcards record")
        return InteractiveMetadataRecord(
            artifact_id=record.id,
            family=record.family,
            lifecycle_status=record.status,
            usable=record.status == "completed",
            generation_prompt=record.generation_prompt,
            user_state=record.user_state,
        )


__all__ = ["InteractiveFamilyService"]
