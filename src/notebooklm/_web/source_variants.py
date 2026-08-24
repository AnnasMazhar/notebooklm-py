"""Web workflow bindings for the remaining Source variants."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from types import MappingProxyType
from typing import Any, cast
from urllib.parse import urlparse

from .._backend import BackendContractError, BackendError, BackendErrorReason
from .._deadline import RuntimeDeadline
from .._idempotency import _CreateResultKind, _IdempotentCreateResult
from .._operations import Operation
from .._projectors import project_source
from .._records import (
    SourceAddCommitState,
    SourceAddDriveInput,
    SourceAddDriveResult,
    SourceAddFileInput,
    SourceAddFileResult,
    SourceAddTextInput,
    SourceAddTextResult,
    SourceAddTitleState,
    SourceAddUrlBatchInput,
    SourceAddUrlBatchResult,
    SourceAddUrlInput,
    SourceAddUrlReceipt,
    SourceAddUrlResult,
    SourceDeleteInput,
    SourceDeleteResult,
    SourceFileInputKind,
    SourceFreshnessInput,
    SourceFreshnessResult,
    SourceFulltextInput,
    SourceFulltextRecord,
    SourceFulltextResult,
    SourceGetInput,
    SourceGuideInput,
    SourceGuideRecord,
    SourceGuideResult,
    SourceRecord,
    SourceRefreshInput,
    SourceRefreshResult,
    SourceUpdateInput,
    SourceUpdateResult,
    SourceUrlBatchItemRecord,
)
from .._row_adapters.sources import interpret_source_freshness
from .._source.add import SourceAddService, honor_requested_title_if_fresh
from .._source.batch import SourceBatchAddService
from .._source.content import SourceContentRenderer
from .._source.listing import SourceLister
from .._source.polling import SourcePoller
from .._source.upload_payloads import build_rename_source_params
from .._types.sources import _SOURCE_TYPE_CODE_MAP, SourceType
from .._url_utils import is_youtube_url
from ..exceptions import NotebookLMError, SourceNotFoundError
from ..rpc import RPCMethod
from ..rpc.types import drive_source_status_to_str, source_status_to_str
from ..types import Source
from .codec import settings as settings_codec
from .codec.sources import (
    decode_source,
    decode_source_record,
    encode_delete,
    encode_refresh_or_freshness,
)
from .studio_facade import StudioFacadeWebHandlers

source_logger = logging.getLogger("notebooklm").getChild("_sources")


def _source_record(source: Source) -> SourceRecord:
    """Project a public source into its transport-neutral backend record."""
    type_code = source._type_code
    kind = (
        SourceType.UNKNOWN
        if type_code is None
        else _SOURCE_TYPE_CODE_MAP.get(type_code, SourceType.UNKNOWN)
    )
    unrecognized_kind: int | str | None = (
        type_code if type_code is not None and type_code not in _SOURCE_TYPE_CODE_MAP else None
    )
    return SourceRecord(
        id=source.id,
        title=source.title,
        url=source.url,
        kind=kind.value,
        unrecognized_kind=unrecognized_kind,
        kind_present=type_code is not None,
        created_at=source.created_at,
        status=source_status_to_str(source.status),
        drive_document_id=source.drive_document_id,
        drive_status=(
            drive_source_status_to_str(source.drive_status)
            if source.drive_status is not None
            else None
        ),
        download_url=source.download_url,
        viewer_url=source.viewer_url,
        content_mime=source.content_mime,
        word_count=source.word_count,
        revision_id=source.revision_id,
        revision_timestamp=source.revision_timestamp,
        last_modified_at=source.last_modified_at,
    )


class SourceVariantWebHandlers(StudioFacadeWebHandlers):
    """Remaining Source workflows mixed into the composed web backend."""

    _executor: Any
    _source_uploader: Any

    def _source_caller(
        self,
        deadline: RuntimeDeadline | None,
        operation: Operation,
    ) -> Any:
        raise NotImplementedError

    def _capture_public_failure(self, exc: Exception, *, operation: Operation) -> Any:
        raise NotImplementedError

    async def _source_get(
        self,
        value: Any,
        *,
        deadline: RuntimeDeadline | None,
    ) -> Any:
        raise NotImplementedError

    async def _source_add_url(
        self,
        value: SourceAddUrlInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> SourceAddUrlResult:
        """Run the live generic/YouTube URL workflow with optional outer budgeting."""
        caller = self._source_caller(deadline, Operation.SOURCE_ADD_URL)
        adder = SourceAddService()
        lister = SourceLister(cast(Any, caller))
        poller = SourcePoller()

        def extract_youtube_video_id(url: str) -> str | None:
            return adder.extract_youtube_video_id(
                url,
                parse_url=urlparse,
                extract_video_id_from_parsed_url=adder.extract_video_id_from_parsed_url,
                is_valid_video_id=adder.is_valid_video_id,
                logger=source_logger,
            )

        async def wait_until_ready(
            notebook_id: str,
            source_id: str,
            *,
            timeout: float,
        ) -> Source:
            return await poller.wait_until_ready(
                notebook_id,
                source_id,
                timeout=timeout,
                get_source=lister.get,
                sleep=asyncio.sleep,
                monotonic=(deadline.monotonic if deadline is not None else time.monotonic),
                logger=source_logger,
                deadline=deadline,
            )

        async def rename_source(
            notebook_id: str,
            source_id: str,
            new_title: str,
        ) -> Source | None:
            result = await caller.rpc_call(
                RPCMethod.UPDATE_SOURCE,
                build_rename_source_params(source_id, new_title),
                source_path=f"/notebook/{notebook_id}",
                allow_null=True,
            )
            if result:
                return project_source(
                    decode_source(result, method_id=RPCMethod.UPDATE_SOURCE.value)
                )
            source = await lister.get(notebook_id, source_id)
            if source is None:
                raise SourceNotFoundError(
                    source_id,
                    method_id=RPCMethod.UPDATE_SOURCE.value,
                )
            return source

        try:
            create_result = cast(
                _IdempotentCreateResult[Source],
                await adder.add_url(
                    value.notebook_id,
                    value.url,
                    wait=value.wait,
                    wait_timeout=value.wait_timeout,
                    add_youtube_source=lambda notebook_id, url: adder.add_youtube_source(
                        notebook_id,
                        url,
                        rpc=cast(Any, caller),
                    ),
                    add_url_source=lambda notebook_id, url: adder.add_url_source(
                        notebook_id,
                        url,
                        rpc=cast(Any, caller),
                    ),
                    list_sources=lister.list,
                    wait_until_ready=wait_until_ready,
                    extract_youtube_video_id=extract_youtube_video_id,
                    is_youtube_url=is_youtube_url,
                    logger=source_logger,
                    return_result=True,
                ),
            )
        except NotebookLMError as exc:
            outcome_unknown = bool(getattr(exc, "unconfirmed", False))
            receipt = SourceAddUrlReceipt(
                commit_state=(
                    SourceAddCommitState.UNKNOWN if outcome_unknown else SourceAddCommitState.FAILED
                ),
                title_state=SourceAddTitleState.NOT_ATTEMPTED,
                outcome_unknown=outcome_unknown,
            )
            raise BackendError(
                message=str(exc.args[0]) if exc.args else "",
                operation=Operation.SOURCE_ADD_URL,
                outcome_unknown=outcome_unknown,
                diagnostics=MappingProxyType(
                    {
                        "receipt": receipt,
                        "source_add_failure": self._capture_public_failure(
                            exc,
                            operation=Operation.SOURCE_ADD_URL,
                        ),
                    }
                ),
                reason=BackendErrorReason.SOURCE_ADD,
            ) from exc

        source_before_title = create_result.value
        requested_title = value.requested_title
        normalized_title = requested_title.strip() if requested_title is not None else ""
        source = await honor_requested_title_if_fresh(
            rename_source,
            value.notebook_id,
            create_result,
            requested_title,
            source_logger,
            probe_proves_freshness=True,
        )
        if not normalized_title:
            title_state = SourceAddTitleState.NOT_REQUESTED
        elif source_before_title.title == normalized_title:
            title_state = SourceAddTitleState.UNCHANGED
        elif source.title == normalized_title:
            title_state = SourceAddTitleState.RENAMED
        else:
            title_state = SourceAddTitleState.RENAME_FAILED

        return SourceAddUrlResult(
            source=_source_record(source),
            receipt=SourceAddUrlReceipt(
                commit_state=(
                    SourceAddCommitState.CREATED
                    if create_result.kind is _CreateResultKind.CREATED
                    else SourceAddCommitState.RECONCILED
                ),
                title_state=title_state,
            ),
        )

    async def _source_add_url_batch(
        self,
        value: SourceAddUrlBatchInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> SourceAddUrlBatchResult:
        """Run one non-replayed true-batch URL write and preserve positions."""
        caller = self._source_caller(deadline, Operation.SOURCE_ADD_URL_BATCH)
        adder = SourceAddService()
        lister = SourceLister(cast(Any, caller))

        def extract_youtube_video_id(url: str) -> str | None:
            return adder.extract_youtube_video_id(
                url,
                parse_url=urlparse,
                extract_video_id_from_parsed_url=adder.extract_video_id_from_parsed_url,
                is_valid_video_id=adder.is_valid_video_id,
                logger=source_logger,
            )

        outcomes = await SourceBatchAddService().add_urls(
            value.notebook_id,
            value.urls,
            rpc=cast(Any, caller),
            list_sources=lister.list,
            extract_youtube_video_id=extract_youtube_video_id,
            logger=source_logger,
        )
        return SourceAddUrlBatchResult(
            tuple(
                SourceUrlBatchItemRecord(
                    url=item.url,
                    source=(_source_record(item.source) if item.source is not None else None),
                    error=(
                        self._capture_public_failure(
                            item.error,
                            operation=Operation.SOURCE_ADD_URL_BATCH,
                        )
                        if item.error is not None
                        else None
                    ),
                )
                for item in outcomes
            )
        )

    def _source_waiter(
        self,
        caller: Any,
        *,
        deadline: RuntimeDeadline | None,
    ) -> Callable[..., Any]:
        lister = SourceLister(cast(Any, caller))
        poller = SourcePoller()

        async def wait_until_ready(
            notebook_id: str,
            source_id: str,
            *,
            timeout: float,
            **kwargs: Any,
        ) -> Source:
            return await poller.wait_until_ready(
                notebook_id,
                source_id,
                timeout=timeout,
                get_source=lister.get,
                sleep=asyncio.sleep,
                monotonic=(deadline.monotonic if deadline is not None else time.monotonic),
                logger=source_logger,
                deadline=deadline,
                **kwargs,
            )

        return wait_until_ready

    async def _source_add_text(
        self,
        value: SourceAddTextInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> SourceAddTextResult:
        caller = self._source_caller(deadline, Operation.SOURCE_ADD_TEXT)
        source = await SourceAddService().add_text(
            value.notebook_id,
            value.title,
            value.content,
            wait=value.wait,
            wait_timeout=value.wait_timeout,
            idempotent=value.idempotent,
            rpc=cast(Any, caller),
            wait_until_ready=self._source_waiter(caller, deadline=deadline),
            logger=source_logger,
        )
        return SourceAddTextResult(_source_record(source))

    async def _source_add_drive(
        self,
        value: SourceAddDriveInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> SourceAddDriveResult:
        caller = self._source_caller(deadline, Operation.SOURCE_ADD_DRIVE)
        lister = SourceLister(cast(Any, caller))
        adder = SourceAddService()
        result = await adder.add_drive(
            value.notebook_id,
            value.file_id,
            value.title,
            mime_type=value.mime_type,
            wait=value.wait,
            wait_timeout=value.wait_timeout,
            rpc=cast(Any, caller),
            list_sources=lister.list,
            wait_until_ready=self._source_waiter(caller, deadline=deadline),
            logger=source_logger,
            return_result=True,
        )

        async def rename_source(
            notebook_id: str,
            source_id: str,
            new_title: str,
        ) -> Source | None:
            renamed = await caller.rpc_call(
                RPCMethod.UPDATE_SOURCE,
                build_rename_source_params(source_id, new_title),
                source_path=f"/notebook/{notebook_id}",
                allow_null=True,
            )
            if renamed:
                return project_source(
                    decode_source(renamed, method_id=RPCMethod.UPDATE_SOURCE.value)
                )
            return None

        source = await honor_requested_title_if_fresh(
            rename_source,
            value.notebook_id,
            result,
            value.title,
            source_logger,
            probe_proves_freshness=True,
        )
        return SourceAddDriveResult(_source_record(source))

    def _require_source_uploader(self) -> Any:
        if self._source_uploader is None:
            raise BackendContractError(
                "source.add_file requires the composition-root upload pipeline",
                operation=Operation.SOURCE_ADD_FILE,
            )
        return self._source_uploader

    async def _source_add_file(
        self,
        value: SourceAddFileInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> SourceAddFileResult:
        del deadline  # upload/session timeouts retain their existing independent windows
        uploader = self._require_source_uploader()
        if value.kind is SourceFileInputKind.LOCAL:
            if value.file_path is None:
                raise BackendContractError(
                    "local source.add_file input lacks file_path",
                    operation=Operation.SOURCE_ADD_FILE,
                )
            source = await uploader.add_file(
                value.notebook_id,
                value.file_path,
                mime_type=value.mime_type,
                wait=value.wait,
                wait_timeout=value.wait_timeout,
                title=value.title,
                on_progress=value.on_progress,
            )
        else:
            if value.document_id is None:
                raise BackendContractError(
                    "Drive source.add_file input lacks document_id",
                    operation=Operation.SOURCE_ADD_FILE,
                )
            service = uploader.create_drive_import_service()
            async with uploader.get_download_semaphore():
                source = await service.add_drive_file(
                    value.notebook_id,
                    value.document_id,
                    title=value.title,
                    wait=value.wait,
                    wait_timeout=value.wait_timeout,
                )
        return SourceAddFileResult(_source_record(source))

    async def _source_file_limit(self) -> int | None:
        result = await self._rpc_call(
            RPCMethod.GET_USER_SETTINGS,
            settings_codec.encode_get_user_settings(),
            operation=Operation.SOURCE_ADD_FILE,
            deadline=None,
            source_path="/",
        )
        return settings_codec.decode_account_limits(result).source_limit

    async def _source_delete(
        self,
        value: SourceDeleteInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> SourceDeleteResult:
        await self._rpc_call(
            RPCMethod.DELETE_SOURCE,
            encode_delete(value.source_id),
            operation=Operation.SOURCE_DELETE,
            deadline=deadline,
            source_path=f"/notebook/{value.notebook_id}",
            allow_null=True,
        )
        return SourceDeleteResult()

    async def _source_update(
        self,
        value: SourceUpdateInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> SourceUpdateResult:
        payload = await self._rpc_call(
            RPCMethod.UPDATE_SOURCE,
            build_rename_source_params(value.source_id, value.new_title),
            operation=Operation.SOURCE_UPDATE,
            deadline=deadline,
            source_path=f"/notebook/{value.notebook_id}",
            allow_null=True,
        )
        if payload:
            return SourceUpdateResult(
                decode_source_record(payload, method=RPCMethod.UPDATE_SOURCE)
                if value.return_object
                else None
            )

        hydrated = await self._source_get(
            SourceGetInput(value.notebook_id, value.source_id),
            deadline=deadline,
        )
        if hydrated.source is None:
            raise BackendError(
                message=f"Source not found: {value.source_id}",
                operation=Operation.SOURCE_UPDATE,
                diagnostics=MappingProxyType(
                    {
                        "source_id": value.source_id,
                        "method_id": RPCMethod.UPDATE_SOURCE.value,
                        "raw_response": None,
                    }
                ),
                reason=BackendErrorReason.SOURCE_NOT_FOUND,
            )
        return SourceUpdateResult(hydrated.source if value.return_object else None)

    async def _source_refresh(
        self,
        value: SourceRefreshInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> SourceRefreshResult:
        await self._rpc_call(
            RPCMethod.REFRESH_SOURCE,
            encode_refresh_or_freshness(value.source_id),
            operation=Operation.SOURCE_REFRESH,
            deadline=deadline,
            source_path=f"/notebook/{value.notebook_id}",
            allow_null=True,
        )
        return SourceRefreshResult()

    async def _source_check_freshness(
        self,
        value: SourceFreshnessInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> SourceFreshnessResult:
        payload = await self._rpc_call(
            RPCMethod.CHECK_SOURCE_FRESHNESS,
            encode_refresh_or_freshness(value.source_id),
            operation=Operation.SOURCE_CHECK_FRESHNESS,
            deadline=deadline,
            source_path=f"/notebook/{value.notebook_id}",
            allow_null=True,
        )
        return SourceFreshnessResult(interpret_source_freshness(payload))

    async def _source_get_guide(
        self,
        value: SourceGuideInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> SourceGuideResult:
        caller = self._source_caller(deadline, Operation.SOURCE_GET_GUIDE)
        guide = await SourceContentRenderer(cast(Any, caller), logger=source_logger).get_guide(
            value.notebook_id,
            value.source_id,
        )
        return SourceGuideResult(SourceGuideRecord(summary=guide.summary, keywords=guide.keywords))

    async def _source_get_fulltext(
        self,
        value: SourceFulltextInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> SourceFulltextResult:
        caller = self._source_caller(deadline, Operation.SOURCE_GET_FULLTEXT)
        try:
            fulltext = await SourceContentRenderer(
                cast(Any, caller), logger=source_logger
            ).get_fulltext(
                value.notebook_id,
                value.source_id,
                output_format=cast(Any, value.output_format),
            )
        except SourceNotFoundError as exc:
            raise BackendError(
                message=str(exc.args[0]),
                operation=Operation.SOURCE_GET_FULLTEXT,
                diagnostics=MappingProxyType(
                    {
                        "source_id": exc.source_id,
                        "method_id": exc.method_id,
                        "raw_response": exc.raw_response,
                    }
                ),
                reason=BackendErrorReason.SOURCE_NOT_FOUND,
            ) from exc
        type_code = fulltext._type_code
        kind = (
            _SOURCE_TYPE_CODE_MAP.get(type_code, SourceType.UNKNOWN)
            if type_code is not None
            else SourceType.UNKNOWN
        )
        return SourceFulltextResult(
            SourceFulltextRecord(
                source_id=fulltext.source_id,
                title=fulltext.title,
                content=fulltext.content,
                kind=kind.value,
                unrecognized_kind=(
                    type_code
                    if type_code is not None and type_code not in _SOURCE_TYPE_CODE_MAP
                    else None
                ),
                kind_present=type_code is not None,
                url=fulltext.url,
                char_count=fulltext.char_count,
                document=fulltext.document,
            )
        )
