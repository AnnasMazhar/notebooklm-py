"""Web workflow bindings for Audio, Quiz/Flashcards, and visual Studio families."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from .._artifact.payloads import (
    build_audio_artifact_params,
    build_flashcards_artifact_params,
    build_infographic_artifact_params,
    build_quiz_artifact_params,
    build_slide_deck_artifact_params,
)
from .._backend import BackendContractError
from .._deadline import RuntimeDeadline
from .._env import get_default_language
from .._notebook_payloads import build_get_notebook_params
from .._operations import Operation
from .._records import (
    AudioGenerateInput,
    AudioGenerateResult,
    GenerationStatusRecord,
    InfographicGenerateInput,
    InteractiveGenerateInput,
    InteractiveGenerateResult,
    SlideDeckGenerateInput,
    VisualGenerateResult,
)
from .._row_adapters.sources import SourceRow
from ..exceptions import DecodingError
from ..rpc import (
    AudioFormat,
    AudioLength,
    InfographicDetail,
    InfographicOrientation,
    InfographicStyle,
    QuizDifficulty,
    QuizQuantity,
    RPCMethod,
    SlideDeckFormat,
    SlideDeckLength,
    safe_index,
)
from ..rpc.types import artifact_status_to_str
from .studio_documents import StudioDocumentWebHandlers

_AUDIO_FORMATS = {
    "deep_dive": AudioFormat.DEEP_DIVE,
    "brief": AudioFormat.BRIEF,
    "critique": AudioFormat.CRITIQUE,
    "debate": AudioFormat.DEBATE,
}
_AUDIO_LENGTHS = {
    "short": AudioLength.SHORT,
    "default": AudioLength.DEFAULT,
    "long": AudioLength.LONG,
}
_QUIZ_QUANTITIES = {member.name.lower(): member for member in QuizQuantity}
_QUIZ_DIFFICULTIES = {member.name.lower(): member for member in QuizDifficulty}
_InteractiveOptionT = TypeVar("_InteractiveOptionT", QuizQuantity, QuizDifficulty)
_INFOGRAPHIC_ORIENTATIONS = {
    "landscape": InfographicOrientation.LANDSCAPE,
    "portrait": InfographicOrientation.PORTRAIT,
    "square": InfographicOrientation.SQUARE,
}
_INFOGRAPHIC_DETAILS = {
    "concise": InfographicDetail.CONCISE,
    "standard": InfographicDetail.STANDARD,
    "detailed": InfographicDetail.DETAILED,
}
_INFOGRAPHIC_STYLES = {
    "auto_select": InfographicStyle.AUTO_SELECT,
    "sketch_note": InfographicStyle.SKETCH_NOTE,
    "professional": InfographicStyle.PROFESSIONAL,
    "bento_grid": InfographicStyle.BENTO_GRID,
    "editorial": InfographicStyle.EDITORIAL,
    "instructional": InfographicStyle.INSTRUCTIONAL,
    "bricks": InfographicStyle.BRICKS,
    "clay": InfographicStyle.CLAY,
    "anime": InfographicStyle.ANIME,
    "kawaii": InfographicStyle.KAWAII,
    "scientific": InfographicStyle.SCIENTIFIC,
}
_SLIDE_DECK_FORMATS = {
    "detailed_deck": SlideDeckFormat.DETAILED_DECK,
    "presenter_slides": SlideDeckFormat.PRESENTER_SLIDES,
}
_SLIDE_DECK_LENGTHS = {
    "default": SlideDeckLength.DEFAULT,
    "short": SlideDeckLength.SHORT,
}


class StudioMediaWebHandlers(StudioDocumentWebHandlers):
    """Reusable generation handlers mixed into :class:`WebRpcBackend`."""

    async def _audio_generate(
        self,
        value: AudioGenerateInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> AudioGenerateResult:
        if value.audio_format is not None and value.audio_format not in _AUDIO_FORMATS:
            raise BackendContractError(
                f"unrecognized audio format {value.audio_format!r}",
                operation=Operation.ARTIFACT_GENERATE_AUDIO,
            )
        if value.audio_length is not None and value.audio_length not in _AUDIO_LENGTHS:
            raise BackendContractError(
                f"unrecognized audio length {value.audio_length!r}",
                operation=Operation.ARTIFACT_GENERATE_AUDIO,
            )
        source_ids = value.source_ids
        if source_ids is None:
            notebook = await self._rpc_call(
                RPCMethod.GET_NOTEBOOK,
                build_get_notebook_params(value.notebook_id),
                operation=Operation.ARTIFACT_GENERATE_AUDIO,
                deadline=deadline,
                source_path=f"/notebook/{value.notebook_id}",
            )
            source_ids = self._audio_source_ids(notebook)

        result = await self._rpc_call(
            RPCMethod.CREATE_ARTIFACT,
            build_audio_artifact_params(
                value.notebook_id,
                list(source_ids),
                language=(get_default_language() if value.language is None else value.language),
                instructions=value.instructions,
                audio_format=(
                    None if value.audio_format is None else _AUDIO_FORMATS[value.audio_format]
                ),
                audio_length=(
                    None if value.audio_length is None else _AUDIO_LENGTHS[value.audio_length]
                ),
            ),
            operation=Operation.ARTIFACT_GENERATE_AUDIO,
            deadline=deadline,
            source_path=f"/notebook/{value.notebook_id}",
            allow_null=True,
            operation_variant=None,
            raise_on_null_status=True,
        )
        if result is None:
            raise self._artifact_feature_unavailable(Operation.ARTIFACT_GENERATE_AUDIO, "audio")
        status = self._generation_status(result, Operation.ARTIFACT_GENERATE_AUDIO)
        return AudioGenerateResult(status)

    @staticmethod
    def _audio_source_ids(notebook: object) -> tuple[str, ...]:
        """Preserve the facade's tolerant source-id extraction semantics."""

        if not notebook or not isinstance(notebook, list):
            return ()
        notebook_info = safe_index(
            notebook,
            0,
            method_id=RPCMethod.GET_NOTEBOOK.value,
            source="NotebooksAPI.get_source_ids",
        )
        if not isinstance(notebook_info, list) or len(notebook_info) <= 1:
            return ()
        sources = safe_index(
            notebook_info,
            1,
            method_id=RPCMethod.GET_NOTEBOOK.value,
            source="NotebooksAPI.get_source_ids",
        )
        if not isinstance(sources, list):
            return ()
        result: list[str] = []
        for source in sources:
            if isinstance(source, list) and source:
                source_id = SourceRow.from_entry(
                    source,
                    method_id=RPCMethod.GET_NOTEBOOK.value,
                ).id
                if source_id:
                    result.append(source_id)
        return tuple(result)

    async def _quiz_generate(
        self,
        value: InteractiveGenerateInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> InteractiveGenerateResult:
        return await self._interactive_generate(
            value,
            operation=Operation.ARTIFACT_GENERATE_QUIZ,
            family="quiz",
            deadline=deadline,
        )

    async def _flashcards_generate(
        self,
        value: InteractiveGenerateInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> InteractiveGenerateResult:
        return await self._interactive_generate(
            value,
            operation=Operation.ARTIFACT_GENERATE_FLASHCARDS,
            family="flashcards",
            deadline=deadline,
        )

    async def _interactive_generate(
        self,
        value: InteractiveGenerateInput,
        *,
        operation: Operation,
        family: Literal["quiz", "flashcards"],
        deadline: RuntimeDeadline | None,
    ) -> InteractiveGenerateResult:
        quantity = self._interactive_option(
            value.quantity,
            _QUIZ_QUANTITIES,
            parameter="quantity",
            operation=operation,
        )
        difficulty = self._interactive_option(
            value.difficulty,
            _QUIZ_DIFFICULTIES,
            parameter="difficulty",
            operation=operation,
        )
        source_ids = value.source_ids
        if source_ids is None:
            notebook = await self._rpc_call(
                RPCMethod.GET_NOTEBOOK,
                build_get_notebook_params(value.notebook_id),
                operation=operation,
                deadline=deadline,
                source_path=f"/notebook/{value.notebook_id}",
            )
            source_ids = self._generation_source_ids(value.notebook_id, notebook)

        builder = (
            build_quiz_artifact_params if family == "quiz" else build_flashcards_artifact_params
        )
        result = await self._rpc_call(
            RPCMethod.CREATE_ARTIFACT,
            builder(
                value.notebook_id,
                list(source_ids),
                instructions=value.instructions,
                quantity=quantity,
                difficulty=difficulty,
            ),
            operation=operation,
            deadline=deadline,
            source_path=f"/notebook/{value.notebook_id}",
            allow_null=True,
            operation_variant=None,
            raise_on_null_status=True,
        )
        if result is None:
            raise self._artifact_feature_unavailable(operation, family)
        return InteractiveGenerateResult(self._generation_status(result, operation))

    @staticmethod
    def _interactive_option(
        value: str | None,
        options: Mapping[str, _InteractiveOptionT],
        *,
        parameter: str,
        operation: Operation,
    ) -> _InteractiveOptionT | None:
        if value is None:
            return None
        option = options.get(value)
        if option is None:
            raise BackendContractError(
                f"unrecognized interactive {parameter} {value!r}",
                operation=operation,
            )
        return option

    async def _infographic_generate(
        self,
        value: InfographicGenerateInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> VisualGenerateResult:
        orientation = self._visual_option(
            value.orientation,
            _INFOGRAPHIC_ORIENTATIONS,
            parameter="orientation",
            operation=Operation.ARTIFACT_GENERATE_INFOGRAPHIC,
        )
        detail_level = self._visual_option(
            value.detail_level,
            _INFOGRAPHIC_DETAILS,
            parameter="detail level",
            operation=Operation.ARTIFACT_GENERATE_INFOGRAPHIC,
        )
        style = self._visual_option(
            value.style,
            _INFOGRAPHIC_STYLES,
            parameter="style",
            operation=Operation.ARTIFACT_GENERATE_INFOGRAPHIC,
        )
        source_ids = await self._visual_source_selection(
            value.notebook_id,
            value.source_ids,
            operation=Operation.ARTIFACT_GENERATE_INFOGRAPHIC,
            deadline=deadline,
        )
        params = build_infographic_artifact_params(
            value.notebook_id,
            list(source_ids),
            language=(get_default_language() if value.language is None else value.language),
            instructions=value.instructions,
            orientation=cast(InfographicOrientation | None, orientation),
            detail_level=cast(InfographicDetail | None, detail_level),
            style=cast(InfographicStyle | None, style),
        )
        return await self._visual_generate(
            value.notebook_id,
            params,
            operation=Operation.ARTIFACT_GENERATE_INFOGRAPHIC,
            artifact_type="infographic",
            deadline=deadline,
        )

    async def _slide_deck_generate(
        self,
        value: SlideDeckGenerateInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> VisualGenerateResult:
        slide_format = self._visual_option(
            value.slide_format,
            _SLIDE_DECK_FORMATS,
            parameter="format",
            operation=Operation.ARTIFACT_GENERATE_SLIDE_DECK,
        )
        slide_length = self._visual_option(
            value.slide_length,
            _SLIDE_DECK_LENGTHS,
            parameter="length",
            operation=Operation.ARTIFACT_GENERATE_SLIDE_DECK,
        )
        source_ids = await self._visual_source_selection(
            value.notebook_id,
            value.source_ids,
            operation=Operation.ARTIFACT_GENERATE_SLIDE_DECK,
            deadline=deadline,
        )
        params = build_slide_deck_artifact_params(
            value.notebook_id,
            list(source_ids),
            language=(get_default_language() if value.language is None else value.language),
            instructions=value.instructions,
            slide_format=cast(SlideDeckFormat | None, slide_format),
            slide_length=cast(SlideDeckLength | None, slide_length),
        )
        return await self._visual_generate(
            value.notebook_id,
            params,
            operation=Operation.ARTIFACT_GENERATE_SLIDE_DECK,
            artifact_type="slide deck",
            deadline=deadline,
        )

    @staticmethod
    def _visual_option(
        value: str | None,
        options: Mapping[str, object],
        *,
        parameter: str,
        operation: Operation,
    ) -> object | None:
        if value is None:
            return None
        option = options.get(value)
        if option is None:
            raise BackendContractError(
                f"unrecognized visual {parameter} {value!r}",
                operation=operation,
            )
        return option

    async def _visual_source_selection(
        self,
        notebook_id: str,
        source_ids: tuple[str, ...] | None,
        *,
        operation: Operation,
        deadline: RuntimeDeadline | None,
    ) -> tuple[str, ...]:
        if source_ids is not None:
            return source_ids
        notebook = await self._rpc_call(
            RPCMethod.GET_NOTEBOOK,
            build_get_notebook_params(notebook_id),
            operation=operation,
            deadline=deadline,
            source_path=f"/notebook/{notebook_id}",
        )
        return self._generation_source_ids(notebook_id, notebook)

    async def _visual_generate(
        self,
        notebook_id: str,
        params: list[Any],
        *,
        operation: Operation,
        artifact_type: str,
        deadline: RuntimeDeadline | None,
    ) -> VisualGenerateResult:
        result = await self._rpc_call(
            RPCMethod.CREATE_ARTIFACT,
            params,
            operation=operation,
            deadline=deadline,
            source_path=f"/notebook/{notebook_id}",
            allow_null=True,
            operation_variant=None,
            raise_on_null_status=True,
        )
        if result is None:
            raise self._artifact_feature_unavailable(operation, artifact_type)
        return VisualGenerateResult(self._generation_status(result, operation))

    def _generation_status(self, result: object, operation: Operation) -> GenerationStatusRecord:
        method_id = RPCMethod.CREATE_ARTIFACT.value
        artifact_id = safe_index(
            result,
            0,
            0,
            method_id=method_id,
            source="_parse_generation_result",
        )
        if artifact_id is None:
            raise self._artifact_feature_unavailable(operation, "artifact")
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


__all__ = ["StudioMediaWebHandlers"]
