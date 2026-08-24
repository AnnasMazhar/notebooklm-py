"""Web payload codecs for report and Video Overview generation."""

from __future__ import annotations

from typing import Any, cast

from ..._artifact.payloads import (
    build_cinematic_video_artifact_params,
    build_report_artifact_params,
    build_video_artifact_params,
)
from ..._records import GenerationStatusRecord, ReportGenerateInput, VideoGenerateInput
from ...exceptions import DecodingError
from ...rpc import ReportFormat, RPCMethod, VideoFormat, VideoStyle, safe_index
from ...rpc.types import artifact_status_to_str

_VIDEO_FORMATS = {
    "explainer": VideoFormat.EXPLAINER,
    "brief": VideoFormat.BRIEF,
    "cinematic": VideoFormat.CINEMATIC,
    "short": VideoFormat.SHORT,
}
_VIDEO_STYLES = {
    "auto_select": VideoStyle.AUTO_SELECT,
    "custom": VideoStyle.CUSTOM,
    "classic": VideoStyle.CLASSIC,
    "whiteboard": VideoStyle.WHITEBOARD,
    "kawaii": VideoStyle.KAWAII,
    "anime": VideoStyle.ANIME,
    "watercolor": VideoStyle.WATERCOLOR,
    "retro_print": VideoStyle.RETRO_PRINT,
    "heritage": VideoStyle.HERITAGE,
    "paper_craft": VideoStyle.PAPER_CRAFT,
}
_REPORT_FORMATS = {value.value: value for value in ReportFormat}


def encode_video_generation(
    value: VideoGenerateInput,
    *,
    source_ids: tuple[str, ...],
    language: str,
) -> list[Any]:
    """Encode neutral video options into the exact CREATE_ARTIFACT payload."""

    video_format = None if value.video_format is None else _VIDEO_FORMATS[value.video_format]
    if value.cinematic_route:
        return build_cinematic_video_artifact_params(
            value.notebook_id,
            list(source_ids),
            language=language,
            instructions=value.instructions,
        )
    video_style = None if value.video_style is None else _VIDEO_STYLES[value.video_style]
    return build_video_artifact_params(
        value.notebook_id,
        list(source_ids),
        language=language,
        instructions=value.instructions,
        video_format=video_format,
        video_style=video_style,
        style_prompt=value.style_prompt,
    )


def encode_report_generation(
    value: ReportGenerateInput,
    *,
    source_ids: tuple[str, ...],
    language: str,
) -> list[Any]:
    """Encode neutral report options into the exact CREATE_ARTIFACT payload."""

    return build_report_artifact_params(
        value.notebook_id,
        list(source_ids),
        report_format=_REPORT_FORMATS[value.report_format],
        language=language,
        custom_prompt=value.custom_prompt,
        extra_instructions=value.extra_instructions,
    )


def decode_generation_status(result: Any) -> GenerationStatusRecord | None:
    """Decode the common CREATE_ARTIFACT task row; ``None`` means a null task id."""

    method_id = RPCMethod.CREATE_ARTIFACT.value
    artifact_id = safe_index(
        result,
        0,
        0,
        method_id=method_id,
        source="_parse_generation_result",
    )
    if artifact_id is None:
        return None
    if not artifact_id:
        raise DecodingError(
            "No artifact id (source=_parse_generation_result)",
            method_id=method_id,
        )
    status_code = safe_index(
        result,
        0,
        4,
        method_id=method_id,
        source="_parse_generation_result",
    )
    status = "pending" if status_code is None else artifact_status_to_str(status_code)
    return GenerationStatusRecord(task_id=cast(str, artifact_id), status=status)


__all__ = ["decode_generation_status", "encode_report_generation", "encode_video_generation"]
