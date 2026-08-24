"""Web workflow bindings for fast/deep Research and imports."""

from __future__ import annotations

from types import MappingProxyType

from .._backend import BackendError, BackendErrorReason
from .._deadline import RuntimeDeadline
from .._operations import Operation
from .._records import (
    ResearchCancelInput,
    ResearchCancelResult,
    ResearchImportInput,
    ResearchImportResult,
    ResearchMode,
    ResearchPollInput,
    ResearchPollResult,
    ResearchStartInput,
    ResearchStartResult,
)
from ..exceptions import AuthError, NetworkError, RateLimitError, RPCError, ServerError
from ..rpc import RPCMethod
from .codec.research import (
    decode_imported_sources,
    decode_research_start,
    decode_research_tasks,
    encode_research_cancel_params,
    encode_research_import_params,
    encode_research_poll_params,
    encode_research_start_params,
)
from .sharing import SharingWebHandlers


def _is_deep_start_null_result_error(exc: RPCError) -> bool:
    """Whether a deep-start RPCError is the decoder's null-payload frame."""

    method_id = RPCMethod.START_DEEP_RESEARCH.value
    null_result_markers = ("rejected this request", "returned an empty result")
    return (
        exc.method_id == method_id
        and method_id in exc.found_ids
        and any(marker in str(exc).lower() for marker in null_result_markers)
    )


class ResearchWebHandlers(SharingWebHandlers):
    """Reusable Research handlers mixed into the web backend."""

    def _translate_error(self, operation: Operation, error: RPCError) -> BackendError:
        """Translate one native error; implemented by the composed backend."""

        raise NotImplementedError

    async def _research_start(
        self,
        value: ResearchStartInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> ResearchStartResult:
        method = (
            RPCMethod.START_FAST_RESEARCH
            if value.mode is ResearchMode.FAST
            else RPCMethod.START_DEEP_RESEARCH
        )
        try:
            result = await self._rpc_call(
                method,
                encode_research_start_params(
                    value.notebook_id,
                    value.query,
                    value.search_source,
                    value.mode,
                ),
                operation=Operation.RESEARCH_START,
                deadline=deadline,
                source_path=f"/notebook/{value.notebook_id}",
            )
        except (AuthError, RateLimitError, ServerError, NetworkError):
            raise
        except RPCError as exc:
            if value.mode is ResearchMode.DEEP and _is_deep_start_null_result_error(exc):
                raise self._research_start_unavailable_error(value, exc) from exc
            raise
        return decode_research_start(result, method_id=method.value)

    def _research_start_unavailable_error(
        self,
        value: ResearchStartInput,
        error: RPCError,
    ) -> BackendError:
        original = self._translate_error(Operation.RESEARCH_START, error)
        return BackendError(
            message="research start returned no run",
            operation=Operation.RESEARCH_START,
            diagnostics=MappingProxyType(
                {
                    "notebook_id": value.notebook_id,
                    "mode": value.mode.value,
                    "original_message": original.message,
                    "original_reason": (
                        original.reason.value if original.reason is not None else None
                    ),
                    "original_diagnostics": dict(original.diagnostics or {}),
                }
            ),
            reason=BackendErrorReason.RESEARCH_START_UNAVAILABLE,
        )

    async def _research_poll(
        self,
        value: ResearchPollInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> ResearchPollResult:
        result = await self._rpc_call(
            RPCMethod.POLL_RESEARCH,
            encode_research_poll_params(value.notebook_id),
            operation=Operation.RESEARCH_POLL,
            deadline=deadline,
            source_path=f"/notebook/{value.notebook_id}",
        )
        return ResearchPollResult(tasks=decode_research_tasks(result))

    async def _research_cancel(
        self,
        value: ResearchCancelInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> ResearchCancelResult:
        await self._rpc_call(
            RPCMethod.CANCEL_RESEARCH,
            encode_research_cancel_params(value.run_id),
            operation=Operation.RESEARCH_CANCEL,
            deadline=deadline,
            source_path=f"/notebook/{value.notebook_id}",
        )
        return ResearchCancelResult()

    async def _research_import(
        self,
        value: ResearchImportInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> ResearchImportResult:
        result = await self._rpc_call(
            RPCMethod.IMPORT_RESEARCH,
            encode_research_import_params(value.notebook_id, value.task_id, value.entries),
            operation=Operation.RESEARCH_IMPORT,
            deadline=deadline,
            source_path=f"/notebook/{value.notebook_id}",
            attempt_timeout=value.attempt_timeout,
        )
        return ResearchImportResult(imported=decode_imported_sources(result))


__all__ = ["ResearchWebHandlers"]
