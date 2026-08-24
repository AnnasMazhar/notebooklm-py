"""Semantic Studio management, revision, retry, and suggestion services."""

from __future__ import annotations

from .._backend import BackendAdapter
from .._deadline import RuntimeDeadline
from .._records import (
    ARTIFACT_DELETE_DEF,
    ARTIFACT_RENAME_DEF,
    ARTIFACT_RETRY_DEF,
    ARTIFACT_REVISE_SLIDE_DEF,
    ARTIFACT_SUGGEST_REPORTS_DEF,
    ArtifactDeleteInput,
    ArtifactRenameInput,
    ArtifactRenameResult,
    ArtifactRetryInput,
    ArtifactRetryResult,
    ArtifactReviseSlideInput,
    ArtifactReviseSlideResult,
    ArtifactSuggestReportsInput,
    ArtifactSuggestReportsResult,
)


class StudioManagementService:
    """Manage Studio artifacts without exposing web verbs to the facade."""

    __slots__ = ("_backend",)

    def __init__(self, backend: BackendAdapter) -> None:
        self._backend = backend

    async def delete(
        self,
        value: ArtifactDeleteInput,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> None:
        await self._backend.invoke(ARTIFACT_DELETE_DEF, value, deadline=deadline)

    async def rename(
        self,
        value: ArtifactRenameInput,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> ArtifactRenameResult:
        return await self._backend.invoke(ARTIFACT_RENAME_DEF, value, deadline=deadline)

    async def revise_slide(
        self,
        value: ArtifactReviseSlideInput,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> ArtifactReviseSlideResult:
        return await self._backend.invoke(ARTIFACT_REVISE_SLIDE_DEF, value, deadline=deadline)

    async def retry(
        self,
        value: ArtifactRetryInput,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> ArtifactRetryResult:
        return await self._backend.invoke(ARTIFACT_RETRY_DEF, value, deadline=deadline)


class ReportSuggestionService:
    """Obtain report-format suggestions as neutral records."""

    __slots__ = ("_backend",)

    def __init__(self, backend: BackendAdapter) -> None:
        self._backend = backend

    async def suggest(
        self,
        value: ArtifactSuggestReportsInput,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> ArtifactSuggestReportsResult:
        return await self._backend.invoke(ARTIFACT_SUGGEST_REPORTS_DEF, value, deadline=deadline)


__all__ = ["ReportSuggestionService", "StudioManagementService"]
