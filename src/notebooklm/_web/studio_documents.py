"""Web workflow binding for the transport-neutral report/video family services."""

from __future__ import annotations

import logging
from types import MappingProxyType
from typing import Any

from .._backend import BackendContractError, BackendError, BackendErrorReason
from .._deadline import RuntimeDeadline
from .._env import get_default_language
from .._notebook_payloads import build_get_notebook_params
from .._operations import Operation
from .._records import (
    GenerationStatusRecord,
    ReportGenerateInput,
    ReportGenerateResult,
    VideoGenerateInput,
    VideoGenerateResult,
)
from .._row_adapters.sources import SourceRow
from ..rpc import RPCMethod, safe_index
from .codec.studio_documents import (
    decode_generation_status,
    encode_report_generation,
    encode_video_generation,
)

notebook_logger = logging.getLogger("notebooklm._notebooks")


class StudioDocumentWebHandlers:
    """Reusable report/video handlers mixed into :class:`WebRpcBackend`."""

    async def _rpc_call(
        self,
        method: RPCMethod,
        params: list[Any],
        *,
        operation: Operation,
        deadline: RuntimeDeadline | None,
        source_path: str = "/",
        allow_null: bool = False,
        _is_retry: bool = False,
        disable_internal_retries: bool = False,
        operation_variant: str | None = None,
        raise_on_null_status: bool = False,
        outcome_unknown_on_expiry: bool = False,
        attempt_timeout: float | None = None,
    ) -> Any:
        """Invoke one native RPC; implemented by the composed web backend."""

        raise NotImplementedError

    async def _video_generate(
        self,
        value: VideoGenerateInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> VideoGenerateResult:
        source_ids = await self._document_source_ids(
            value.notebook_id,
            value.source_ids,
            operation=Operation.ARTIFACT_GENERATE_VIDEO,
            deadline=deadline,
        )
        language = get_default_language() if value.language is None else value.language
        try:
            params = encode_video_generation(value, source_ids=source_ids, language=language)
        except KeyError as exc:
            raise BackendContractError(
                f"unrecognized video option {exc.args[0]!r}",
                operation=Operation.ARTIFACT_GENERATE_VIDEO,
            ) from None
        status = await self._document_generate(
            Operation.ARTIFACT_GENERATE_VIDEO,
            value.notebook_id,
            params,
            null_result_artifact_type="cinematic video" if value.cinematic_route else "video",
            deadline=deadline,
        )
        return VideoGenerateResult(status)

    async def _report_generate(
        self,
        value: ReportGenerateInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> ReportGenerateResult:
        source_ids = await self._document_source_ids(
            value.notebook_id,
            value.source_ids,
            operation=Operation.ARTIFACT_GENERATE_REPORT,
            deadline=deadline,
        )
        language = get_default_language() if value.language is None else value.language
        try:
            params = encode_report_generation(value, source_ids=source_ids, language=language)
        except KeyError as exc:
            raise BackendContractError(
                f"unrecognized report format {exc.args[0]!r}",
                operation=Operation.ARTIFACT_GENERATE_REPORT,
            ) from None
        status = await self._document_generate(
            Operation.ARTIFACT_GENERATE_REPORT,
            value.notebook_id,
            params,
            null_result_artifact_type="report",
            deadline=deadline,
        )
        return ReportGenerateResult(status)

    async def _document_generate(
        self,
        operation: Operation,
        notebook_id: str,
        params: list[Any],
        *,
        null_result_artifact_type: str,
        deadline: RuntimeDeadline | None,
    ) -> GenerationStatusRecord:
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
            raise self._artifact_feature_unavailable(operation, null_result_artifact_type)
        status = decode_generation_status(result)
        if status is None:
            raise self._artifact_feature_unavailable(operation, "artifact")
        return status

    async def _document_source_ids(
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

    @staticmethod
    def _generation_source_ids(notebook_id: str, notebook: object) -> tuple[str, ...]:
        """Preserve the facade's tolerant source-id extraction semantics."""

        if not notebook or not isinstance(notebook, list):
            return ()
        notebook_info = safe_index(
            notebook,
            0,
            method_id=RPCMethod.GET_NOTEBOOK.value,
            source="NotebooksAPI.get_source_ids",
        )
        if not isinstance(notebook_info, list):
            notebook_logger.warning(
                "get_source_ids: notebook_data[0] shape unexpected for %s "
                "(schema drift?). top-type=%s",
                notebook_id,
                type(notebook_info).__name__,
            )
            return ()
        if len(notebook_info) <= 1:
            notebook_logger.warning(
                "get_source_ids: notebook_info has no sources slot for %s (schema drift?). len=%d",
                notebook_id,
                len(notebook_info),
            )
            return ()
        sources = safe_index(
            notebook_info,
            1,
            method_id=RPCMethod.GET_NOTEBOOK.value,
            source="NotebooksAPI.get_source_ids",
        )
        if sources is None:
            return ()
        if not isinstance(sources, list):
            notebook_logger.warning(
                "get_source_ids: notebook_info[1] not list for %s (schema drift?). len=%d",
                notebook_id,
                len(notebook_info),
            )
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

    @staticmethod
    def _artifact_feature_unavailable(
        operation: Operation,
        artifact_type: str,
    ) -> BackendError:
        return BackendError(
            message=f"{artifact_type.replace('_', ' ').capitalize()} generation is unavailable",
            operation=operation,
            diagnostics=MappingProxyType(
                {
                    "artifact_type": artifact_type,
                    "method_id": RPCMethod.CREATE_ARTIFACT.value,
                    "raw_response": None,
                }
            ),
            reason=BackendErrorReason.ARTIFACT_FEATURE_UNAVAILABLE,
        )


__all__ = ["StudioDocumentWebHandlers"]
