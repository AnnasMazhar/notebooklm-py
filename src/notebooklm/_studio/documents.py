"""Transport-neutral report and Video Overview family behavior."""

from __future__ import annotations

from dataclasses import replace

from .._backend import BackendAdapter
from .._deadline import RuntimeDeadline
from .._records import (
    ARTIFACT_GENERATE_REPORT_DEF,
    ARTIFACT_GENERATE_VIDEO_DEF,
    ArtifactRecord,
    ReportGenerateInput,
    ReportGenerateResult,
    ReportMetadataRecord,
    VideoGenerateInput,
    VideoGenerateResult,
    VideoMetadataRecord,
)
from .catalog import StudioCatalog

_REPORT_FORMATS_BY_KIND = {
    "Briefing Doc": "briefing_doc",
    "Study Guide": "study_guide",
    "Blog Post": "blog_post",
    "Concept Explanation": "concept_explanation",
    "Custom Report": "custom",
}


class DocumentOptionError(ValueError):
    """A report/video option combination violates family behavior."""


class VideoFamilyService:
    """Video generation, discovery, readiness, and representation metadata."""

    __slots__ = ("_backend", "_catalog")

    def __init__(self, backend: BackendAdapter, catalog: StudioCatalog) -> None:
        self._backend = backend
        self._catalog = catalog

    async def generate(
        self,
        value: VideoGenerateInput,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> VideoGenerateResult:
        normalized = self._normalize_options(value)
        return await self._backend.invoke(
            ARTIFACT_GENERATE_VIDEO_DEF,
            normalized,
            deadline=deadline,
        )

    async def list(
        self,
        notebook_id: str,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> tuple[ArtifactRecord, ...]:
        return await self._catalog.list_records(notebook_id, "video", deadline=deadline)

    async def get(
        self,
        notebook_id: str,
        artifact_id: str,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> ArtifactRecord | None:
        record = await self._catalog.get_record(
            notebook_id,
            artifact_id,
            deadline=deadline,
        )
        return record if record is not None and record.family == "video" else None

    @staticmethod
    def metadata(record: ArtifactRecord) -> VideoMetadataRecord:
        """Project media usability without changing lifecycle-terminal semantics."""

        return VideoMetadataRecord(
            artifact_id=record.id,
            lifecycle_status=record.status,
            usable=record.status == "completed" and bool(record.url),
            preferred_url=record.url,
            media_urls=record.media_urls,
            duration_seconds=record.duration_seconds,
            generation_prompt=record.generation_prompt,
            created_at=record.created_at,
        )

    @staticmethod
    def _normalize_options(value: VideoGenerateInput) -> VideoGenerateInput:
        prompt = value.style_prompt.strip() if value.style_prompt is not None else None
        if value.cinematic_route and (
            value.video_format not in {None, "cinematic"} or value.video_style is not None or prompt
        ):
            raise DocumentOptionError(
                "cinematic video route does not accept format, style, or style_prompt overrides"
            )
        if value.video_format == "cinematic" and prompt:
            raise DocumentOptionError("style_prompt is not supported for cinematic videos")
        if value.video_format == "short" and (
            value.video_style not in {None, "auto_select"} or prompt
        ):
            raise DocumentOptionError(
                "video_style and style_prompt are not supported for short videos "
                "(short has a fixed visual style)"
            )
        if value.video_style == "custom" and not prompt:
            raise DocumentOptionError("style_prompt is required when video_style is CUSTOM")
        if prompt and value.video_style != "custom":
            raise DocumentOptionError("style_prompt requires video_style=VideoStyle.CUSTOM")
        return replace(value, style_prompt=prompt)


class ReportFamilyService:
    """Report generation, discovery, readiness, and format metadata."""

    __slots__ = ("_backend", "_catalog")

    def __init__(self, backend: BackendAdapter, catalog: StudioCatalog) -> None:
        self._backend = backend
        self._catalog = catalog

    async def generate(
        self,
        value: ReportGenerateInput,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> ReportGenerateResult:
        return await self._backend.invoke(
            ARTIFACT_GENERATE_REPORT_DEF,
            value,
            deadline=deadline,
        )

    async def list(
        self,
        notebook_id: str,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> tuple[ArtifactRecord, ...]:
        return await self._catalog.list_records(notebook_id, "report", deadline=deadline)

    async def get(
        self,
        notebook_id: str,
        artifact_id: str,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> ArtifactRecord | None:
        record = await self._catalog.get_record(
            notebook_id,
            artifact_id,
            deadline=deadline,
        )
        return record if record is not None and record.family == "report" else None

    @staticmethod
    def metadata(record: ArtifactRecord) -> ReportMetadataRecord:
        """Project report readiness and known/future report-kind identity."""

        return ReportMetadataRecord(
            artifact_id=record.id,
            lifecycle_status=record.status,
            usable=record.status == "completed",
            report_kind=record.report_kind,
            report_format=_REPORT_FORMATS_BY_KIND.get(record.report_kind or ""),
            generation_prompt=record.generation_prompt,
            source_ids=record.source_ids,
            created_at=record.created_at,
        )


__all__ = ["DocumentOptionError", "ReportFamilyService", "VideoFamilyService"]
