"""Web bindings for the remaining Artifacts compatibility operations."""

from __future__ import annotations

import reprlib
from types import MappingProxyType

from .._artifact.payloads import (
    build_retry_artifact_params,
    build_revise_slide_params,
)
from .._backend import BackendContractError, BackendError, BackendErrorReason
from .._deadline import RuntimeDeadline
from .._operations import Operation
from .._records import (
    ArtifactDeleteInput,
    ArtifactDeleteResult,
    ArtifactDownloadInput,
    ArtifactDownloadResult,
    ArtifactPollInput,
    ArtifactPollResult,
    ArtifactRecord,
    ArtifactRenameInput,
    ArtifactRenameResult,
    ArtifactRetryInput,
    ArtifactRetryResult,
    ArtifactReviseSlideInput,
    ArtifactReviseSlideResult,
)
from .._row_adapters.artifacts import unwrap_artifact_rows
from ..exceptions import DecodingError
from ..rpc import ARTIFACT_STATUS_SUGGESTED_WIRE_NAME, RPCMethod
from .codec.artifacts import (
    decode_artifact_poll,
    decode_artifact_representation,
    decode_interactive_content,
    decode_mind_map_representations,
)
from .codec.studio_documents import decode_generation_status
from .settings_suggestions import SettingsSuggestionWebHandlers


class StudioFacadeWebHandlers(SettingsSuggestionWebHandlers):
    """Management, lifecycle, suggestion, and representation handlers."""

    async def _artifact_catalog_records(
        self,
        notebook_id: str,
        *,
        operation: Operation,
        deadline: RuntimeDeadline | None,
        include_mind_maps: bool,
    ) -> tuple[ArtifactRecord, ...]:
        """Return catalog records from the concrete composed backend."""

        raise NotImplementedError

    async def _artifact_delete(
        self,
        value: ArtifactDeleteInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> ArtifactDeleteResult:
        await self._rpc_call(
            RPCMethod.DELETE_ARTIFACT,
            [[2], value.artifact_id],
            operation=Operation.ARTIFACT_DELETE,
            deadline=deadline,
            source_path=f"/notebook/{value.notebook_id}",
            allow_null=True,
        )
        return ArtifactDeleteResult()

    async def _artifact_rename(
        self,
        value: ArtifactRenameInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> ArtifactRenameResult:
        await self._rpc_call(
            RPCMethod.RENAME_ARTIFACT,
            [[value.artifact_id, value.new_title], [["title"]]],
            operation=Operation.ARTIFACT_RENAME,
            deadline=deadline,
            source_path=f"/notebook/{value.notebook_id}",
            allow_null=True,
        )
        records = await self._artifact_catalog_records(
            value.notebook_id,
            operation=Operation.ARTIFACT_RENAME,
            deadline=deadline,
            include_mind_maps=False,
        )
        artifact = next((item for item in records if item.id == value.artifact_id), None)
        if artifact is None:
            raise BackendError(
                message=f"Artifact not found: {value.artifact_id}",
                operation=Operation.ARTIFACT_RENAME,
                diagnostics=MappingProxyType(
                    {
                        "artifact_id": value.artifact_id,
                        "artifact_type": None,
                        "method_id": RPCMethod.RENAME_ARTIFACT.value,
                        "raw_response": None,
                    }
                ),
                reason=BackendErrorReason.ARTIFACT_NOT_FOUND,
            )
        return ArtifactRenameResult(artifact=artifact)

    async def _artifact_revise_slide(
        self,
        value: ArtifactReviseSlideInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> ArtifactReviseSlideResult:
        result = await self._rpc_call(
            RPCMethod.REVISE_SLIDE,
            build_revise_slide_params(value.artifact_id, value.slide_index, value.prompt),
            operation=Operation.ARTIFACT_REVISE_SLIDE,
            deadline=deadline,
            source_path=f"/notebook/{value.notebook_id}",
            allow_null=True,
            raise_on_null_status=True,
        )
        status = (
            decode_generation_status(result, method_id=RPCMethod.REVISE_SLIDE.value)
            if result is not None
            else None
        )
        if status is None:
            raise self._feature_unavailable(
                Operation.ARTIFACT_REVISE_SLIDE,
                "slide revision",
                RPCMethod.REVISE_SLIDE,
            )
        return ArtifactReviseSlideResult(status)

    async def _artifact_retry(
        self,
        value: ArtifactRetryInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> ArtifactRetryResult:
        result = await self._rpc_call(
            RPCMethod.RETRY_ARTIFACT,
            build_retry_artifact_params(value.artifact_id),
            operation=Operation.ARTIFACT_RETRY,
            deadline=deadline,
            source_path=f"/notebook/{value.notebook_id}",
            allow_null=True,
            raise_on_null_status=True,
        )
        status = (
            decode_generation_status(result, method_id=RPCMethod.RETRY_ARTIFACT.value)
            if result is not None
            else None
        )
        if status is None:
            raise self._feature_unavailable(
                Operation.ARTIFACT_RETRY,
                "retry",
                RPCMethod.RETRY_ARTIFACT,
            )
        return ArtifactRetryResult(status)

    async def _artifact_wait(
        self,
        value: ArtifactPollInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> ArtifactPollResult:
        rows = await self._studio_rows(
            value.notebook_id,
            operation=Operation.ARTIFACT_WAIT,
            deadline=deadline,
            source="WebRpcBackend._artifact_wait",
        )
        return ArtifactPollResult(decode_artifact_poll(rows, value.task_id))

    async def _artifact_download(
        self,
        value: ArtifactDownloadInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> ArtifactDownloadResult:
        if value.action == "catalog":
            rows = await self._studio_rows(
                value.notebook_id,
                operation=Operation.ARTIFACT_DOWNLOAD,
                deadline=deadline,
                source="ArtifactRepresentationService._list_representations",
            )
            return ArtifactDownloadResult(
                representations=tuple(decode_artifact_representation(row) for row in rows)
            )
        if value.action == "mind_maps":
            result = await self._rpc_call(
                RPCMethod.GET_NOTES_AND_MIND_MAPS,
                [value.notebook_id],
                operation=Operation.ARTIFACT_DOWNLOAD,
                deadline=deadline,
                source_path=f"/notebook/{value.notebook_id}",
                allow_null=True,
            )
            return ArtifactDownloadResult(mind_maps=decode_mind_map_representations(result))
        if value.action in {"interactive_html", "mind_map_tree"}:
            if value.artifact_id is None:
                raise BackendContractError(
                    f"artifact.download action {value.action!r} requires artifact_id",
                    operation=Operation.ARTIFACT_DOWNLOAD,
                )
            result = await self._rpc_call(
                RPCMethod.GET_INTERACTIVE_HTML,
                [value.artifact_id],
                operation=Operation.ARTIFACT_DOWNLOAD,
                deadline=deadline,
                source_path=f"/notebook/{value.notebook_id}",
                allow_null=True,
            )
            return ArtifactDownloadResult(
                content=decode_interactive_content(
                    result,
                    tree=value.action == "mind_map_tree",
                )
            )
        raise BackendContractError(
            f"unrecognized artifact.download action {value.action!r}",
            operation=Operation.ARTIFACT_DOWNLOAD,
        )

    async def _studio_rows(
        self,
        notebook_id: str,
        *,
        operation: Operation,
        deadline: RuntimeDeadline | None,
        source: str,
    ) -> list[list[object]]:
        result = await self._rpc_call(
            RPCMethod.LIST_ARTIFACTS,
            [
                [2],
                notebook_id,
                f'NOT artifact.status = "{ARTIFACT_STATUS_SUGGESTED_WIRE_NAME}"',
            ],
            operation=operation,
            deadline=deadline,
            source_path=f"/notebook/{notebook_id}",
            allow_null=True,
        )
        if isinstance(result, list):
            return unwrap_artifact_rows(
                result,
                method_id=RPCMethod.LIST_ARTIFACTS.value,
                source=source,
            )
        if not result:
            return []
        raise DecodingError(
            "Unrecognized LIST_ARTIFACTS payload shape",
            raw_response=reprlib.repr(result),
            method_id=RPCMethod.LIST_ARTIFACTS.value,
        )

    @staticmethod
    def _feature_unavailable(
        operation: Operation,
        artifact_type: str,
        method: RPCMethod,
    ) -> BackendError:
        return BackendError(
            message=f"{artifact_type.capitalize()} generation is unavailable",
            operation=operation,
            diagnostics=MappingProxyType(
                {
                    "artifact_type": artifact_type,
                    "method_id": method.value,
                    "raw_response": None,
                }
            ),
            reason=BackendErrorReason.ARTIFACT_FEATURE_UNAVAILABLE,
        )


__all__ = ["StudioFacadeWebHandlers"]
