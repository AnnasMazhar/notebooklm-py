"""Private artifact download service implementation."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .._auth.cookies import load_httpx_cookies
from .._mind_maps_api import extract_interactive_tree_leaf
from .._row_adapters.artifacts import unwrap_artifact_rows
from .._row_adapters.notes import NoteRow
from .._studio.downloads import _DOWNLOAD_WRITER_QUEUE_SIZE as _DOWNLOAD_WRITER_QUEUE_SIZE
from .._studio.downloads import (
    DownloadResult,
    StudioDownloadClient,
)
from .._studio.downloads import (
    _await_writer_exit as _await_writer_exit,
)
from .._studio.downloads import (
    _download_display_host as _download_display_host,
)
from .._studio.downloads import (
    _is_trusted_download_host as _is_trusted_download_host,
)
from .._studio.downloads import (
    _make_download_client as _make_download_client,
)
from .._studio.serialization import StudioSerializationClient
from ..exceptions import DecodingError, UnknownRPCMethodError, ValidationError
from ..rpc import (
    ARTIFACT_STATUS_SUGGESTED_WIRE_NAME,
    ArtifactTypeCode,
    RPCMethod,
    safe_index,
)
from ..types import (
    Artifact,
    ArtifactDownloadError,
    ArtifactNotFoundError,
    ArtifactNotReadyError,
    ArtifactParseError,
    ArtifactType,
)
from .formatters import _extract_app_data, _format_interactive_content, _parse_data_table

if TYPE_CHECKING:
    from .._mind_map import NoteBackedMindMapService
    from .._row_adapters.artifacts import ArtifactRow
    from .._runtime.contracts import RpcCaller
    from .listing import ArtifactListingService

logger = logging.getLogger(__name__)

# ``_PREFETCH_NOTE`` — referenced by the per-method docstrings below. Each
# ``download_<x>`` accepts an optional pre-fetched list (``artifacts_data`` raw
# studio rows / ``artifacts`` typed list / ``mind_maps`` note-backed rows). When
# supplied — the ``_app`` executor lists once to select the target and threads
# what it already fetched — the method skips its own otherwise-redundant second
# ``LIST_ARTIFACTS`` / ``GET_NOTES_AND_MIND_MAPS`` RPC; ``None`` re-lists as before
# (issue #1488).


def _load_httpx_cookies(storage_path: Any) -> Any:
    return load_httpx_cookies(path=storage_path)


class ArtifactDownloadService:
    """Download operations extracted from :class:`ArtifactsAPI`."""

    def __init__(
        self,
        *,
        rpc: RpcCaller,
        listing: ArtifactListingService,
        mind_maps: NoteBackedMindMapService,
        storage_path: Path | None = None,
        cookie_loader: Callable[[Any], Any] = _load_httpx_cookies,
    ) -> None:
        self._rpc = rpc
        self._listing = listing
        self._mind_maps = mind_maps
        self._storage_path, self._cookie_loader = storage_path, cookie_loader
        self._remote = StudioDownloadClient(
            storage_path=storage_path,
            cookie_loader=cookie_loader,
        )
        self._serialization = StudioSerializationClient()

    async def _list_raw(self, notebook_id: str) -> list[Any]:
        """List raw artifacts for the not-yet-migrated download families."""
        result = await self._rpc.rpc_call(
            RPCMethod.LIST_ARTIFACTS,
            [
                [2],
                notebook_id,
                f'NOT artifact.status = "{ARTIFACT_STATUS_SUGGESTED_WIRE_NAME}"',
            ],
            source_path=f"/notebook/{notebook_id}",
            allow_null=True,
        )
        if isinstance(result, list):
            return unwrap_artifact_rows(
                result,
                method_id=RPCMethod.LIST_ARTIFACTS.value,
                source="ArtifactDownloadService._list_raw",
            )
        if not result:
            return []
        raise DecodingError(
            "Unrecognized LIST_ARTIFACTS payload shape",
            raw_response=repr(result),
            method_id=RPCMethod.LIST_ARTIFACTS.value,
        )

    async def _list_mind_maps(self, notebook_id: str) -> list[Any]:
        """List mind-map artifacts through the injected mind-map service."""
        return await self._mind_maps.list_mind_maps(notebook_id)

    async def _list_artifacts(
        self,
        notebook_id: str,
        artifact_type: ArtifactType,
    ) -> list[Artifact]:
        """List typed artifacts using the download service's patchable seams."""
        return await self._listing.list_artifacts(
            notebook_id,
            artifact_type,
            list_raw=self._list_raw,
            list_mind_maps=self._list_mind_maps,
        )

    def _select_artifact(
        self,
        candidates: list[Any],
        artifact_id: str | None,
        type_name: str,
        no_result_error_key: str,
        *,
        type_code: ArtifactTypeCode,
    ) -> ArtifactRow:
        """Select one completed artifact candidate as an adapter row."""
        return self._listing.select_completed_artifact_row(
            candidates,
            artifact_id,
            type_name,
            no_result_error_key,
            type_code=type_code,
        )

    async def _get_artifact_content(self, notebook_id: str, artifact_id: str) -> str | None:
        """Fetch interactive artifact HTML through the runtime RPC seam.

        ``GET_INTERACTIVE_HTML`` is the live generic ``GetArtifact`` getter; here
        we read the HTML body at ``[0][9][0]`` (quiz / flashcard content).
        """
        result = await self._rpc.rpc_call(
            RPCMethod.GET_INTERACTIVE_HTML,
            [artifact_id],
            source_path=f"/notebook/{notebook_id}",
            allow_null=True,
        )
        if result is None:
            return None
        return safe_index(
            result,
            0,
            9,
            0,
            method_id=RPCMethod.GET_INTERACTIVE_HTML.value,
            source="_artifact_downloads._get_artifact_content",
        )

    async def _get_interactive_mind_map_tree(
        self, notebook_id: str, artifact_id: str
    ) -> str | None:
        """Fetch the interactive mind-map JSON tree string.

        The interactive (studio-artifact) mind map exposes its ``{"name",
        "children"}`` node tree at ``[0][9][3]`` of the ``GET_INTERACTIVE_HTML``
        response (vs the HTML body at ``[0][9][0]``). Returns the raw JSON
        string, or ``None`` when the response is empty / not yet populated.
        """
        result = await self._rpc.rpc_call(
            RPCMethod.GET_INTERACTIVE_HTML,
            [artifact_id],
            source_path=f"/notebook/{notebook_id}",
            allow_null=True,
        )
        # ``extract_interactive_tree_leaf`` re-raises ``UnknownRPCMethodError``
        # on genuine ``[0][9]`` shape drift (failing loud like the sibling HTML
        # accessor ``_get_artifact_content``) while tolerating an absent ``[3]``
        # leaf as the legitimate "tree not populated yet" window (issue #1270).
        tree_json = extract_interactive_tree_leaf(
            result, source="_artifact_downloads._get_interactive_mind_map_tree"
        )
        return tree_json if isinstance(tree_json, str) else None

    async def download_audio(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
        *,
        artifacts_data: list[Any] | None = None,
    ) -> str:
        """Download an Audio Overview to a file (``artifacts_data``: see ``_PREFETCH_NOTE``)."""
        if artifacts_data is None:
            artifacts_data = await self._list_raw(notebook_id)

        audio_art = self._select_artifact(
            artifacts_data,
            artifact_id,
            "Audio",
            "audio",
            type_code=ArtifactTypeCode.AUDIO,
        )

        try:
            url = audio_art.audio_url
        except UnknownRPCMethodError as e:
            raise ArtifactParseError(
                "audio",
                artifact_id=artifact_id,
                details=f"Failed to parse structure: {e}",
                cause=e,
            ) from e
        if not url:
            raise ArtifactParseError(
                "audio",
                artifact_id=artifact_id,
                details="Could not extract download URL from artifact metadata",
            )

        return await self.download_url(url, output_path)

    async def download_video(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
        *,
        artifacts_data: list[Any] | None = None,
    ) -> str:
        """Download a Video Overview to a file (``artifacts_data``: see ``_PREFETCH_NOTE``)."""
        if artifacts_data is None:
            artifacts_data = await self._list_raw(notebook_id)

        # Note: distinct error keys preserved — specific-ID miss raises
        # "video" (from type_name="Video"); empty-list raises
        # "video_overview" (from type_name_lower).
        video_art = self._select_artifact(
            artifacts_data,
            artifact_id,
            "Video",
            "video_overview",
            type_code=ArtifactTypeCode.VIDEO,
        )

        try:
            url = video_art.video_url
        except UnknownRPCMethodError as e:
            raise ArtifactParseError(
                "video_artifact",
                artifact_id=artifact_id,
                details=f"Failed to parse structure: {e}",
                cause=e,
            ) from e
        if not url:
            raise ArtifactParseError(
                "video_artifact",
                artifact_id=artifact_id,
                details="Could not extract download URL from artifact metadata",
            )

        return await self.download_url(url, output_path)

    async def download_infographic(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
        *,
        artifacts_data: list[Any] | None = None,
    ) -> str:
        """Download an Infographic to a file (``artifacts_data``: see ``_PREFETCH_NOTE``)."""
        if artifacts_data is None:
            artifacts_data = await self._list_raw(notebook_id)

        info_art = self._select_artifact(
            artifacts_data,
            artifact_id,
            "Infographic",
            "infographic",
            type_code=ArtifactTypeCode.INFOGRAPHIC,
        )

        try:
            url = info_art.infographic_url
            if not url:
                raise ArtifactParseError(
                    "infographic",
                    artifact_id=artifact_id,
                    details="Could not find metadata",
                )
            return await self.download_url(url, output_path)

        except (IndexError, TypeError, UnknownRPCMethodError) as e:
            raise ArtifactParseError(
                "infographic",
                artifact_id=artifact_id,
                details=f"Failed to parse structure: {e}",
                cause=e,
            ) from e

    async def download_slide_deck(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
        output_format: str = "pdf",
        *,
        artifacts_data: list[Any] | None = None,
    ) -> str:
        """Download a slide deck as PDF or PPTX (``artifacts_data``: see ``_PREFETCH_NOTE``)."""
        if output_format not in ("pdf", "pptx"):
            raise ValidationError(f"Invalid format '{output_format}'. Must be 'pdf' or 'pptx'.")

        if artifacts_data is None:
            artifacts_data = await self._list_raw(notebook_id)

        slide_art = self._select_artifact(
            artifacts_data,
            artifact_id,
            "Slide deck",
            "slide_deck",
            type_code=ArtifactTypeCode.SLIDE_DECK,
        )

        try:
            if output_format == "pptx":
                url = slide_art.slide_deck_pptx_url
                if not url:
                    raise ArtifactDownloadError(
                        "slide_deck", details="PPTX URL not available in artifact data"
                    )
            else:
                url = slide_art.slide_deck_pdf_url
                if not url:
                    raise ArtifactDownloadError(
                        "slide_deck",
                        details=f"Could not find {output_format.upper()} download URL",
                    )

        except UnknownRPCMethodError as e:
            raise ArtifactParseError(
                "slide_deck",
                artifact_id=artifact_id,
                details=f"Failed to parse structure: {e}",
                cause=e,
            ) from e

        return await self.download_url(url, output_path)

    async def download_interactive_artifact(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None,
        output_format: str,
        artifact_type: str,
        *,
        artifacts: list[Artifact] | None = None,
    ) -> str:
        """Download quiz or flashcard artifact.

        ``artifacts`` is the optional pre-fetched *typed* list of the matching
        ``list_type`` (see ``_PREFETCH_NOTE``); this method still filters it to
        completed entries and id-matches within it.
        """
        valid_formats = ("json", "markdown", "html")
        if output_format not in valid_formats:
            raise ValidationError(
                f"Invalid output_format: {output_format!r}. Use one of: {', '.join(valid_formats)}"
            )

        is_quiz = artifact_type == "quiz"
        default_title = "Untitled Quiz" if is_quiz else "Untitled Flashcards"
        list_type = ArtifactType.QUIZ if is_quiz else ArtifactType.FLASHCARDS

        if artifacts is None:
            artifacts = await self._list_artifacts(notebook_id, list_type)
        completed = [a for a in artifacts if a.is_completed]
        if not completed:
            raise ArtifactNotReadyError(artifact_type)

        completed.sort(key=lambda a: a.created_at.timestamp() if a.created_at else 0, reverse=True)

        if artifact_id:
            artifact = next((a for a in completed if a.id == artifact_id), None)
            if not artifact:
                raise ArtifactNotFoundError(artifact_id, artifact_type=artifact_type)
        else:
            artifact, *_ = (
                completed  # typed Artifact list head (newest-first); unpack avoids name[int]
            )

        html_content = await self._get_artifact_content(notebook_id, artifact.id)
        if not html_content:
            raise ArtifactDownloadError(artifact_type, details="Failed to fetch content")

        try:
            app_data = _extract_app_data(html_content)
        except json.JSONDecodeError as e:
            raise ArtifactParseError(
                artifact_type, details=f"Failed to parse content: {e}", cause=e
            ) from e

        title = artifact.title or default_title
        content = _format_interactive_content(app_data, title, output_format, html_content, is_quiz)

        await self._serialization.write_text(output_path, content)
        return output_path

    async def download_report(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
        *,
        artifacts_data: list[Any] | None = None,
    ) -> str:
        """Download a report artifact as markdown (``artifacts_data``: see ``_PREFETCH_NOTE``)."""
        if artifacts_data is None:
            artifacts_data = await self._list_raw(notebook_id)

        report_art = self._select_artifact(
            artifacts_data,
            artifact_id,
            "Report",
            "report",
            type_code=ArtifactTypeCode.REPORT,
        )

        try:
            markdown_content = report_art.report_markdown

            if not isinstance(markdown_content, str):
                raise ArtifactParseError(
                    "report_content",
                    artifact_id=artifact_id,
                    details="Invalid structure",
                )

            return await self._serialization.write_text(output_path, markdown_content)

        except (IndexError, TypeError, UnknownRPCMethodError) as e:
            raise ArtifactParseError(
                "report",
                artifact_id=artifact_id,
                details=f"Failed to parse structure: {e}",
                cause=e,
            ) from e

    async def download_mind_map(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
        *,
        mind_maps: list[Any] | None = None,
        artifacts_data: list[Any] | None = None,
    ) -> str:
        """Download a mind map as JSON (note-backed or interactive kind).

        ``mind_maps`` (note-backed rows) and ``artifacts_data`` (raw studio rows,
        used only by the interactive-mind-map branch) are optional pre-fetched
        lists; see ``_PREFETCH_NOTE``. Each is fetched on demand when ``None``.
        """
        mind_maps_service = self._mind_maps

        # Fetch the note-backed list first: it is the primary backing for this
        # method, so an explicit id that resolves here (the happy path) avoids
        # the extra _list_raw artifact-collection network call entirely.
        if mind_maps is None:
            mind_maps = await mind_maps_service.list_mind_maps(notebook_id)

        # The JSON tree string to write — sourced from the note content for
        # note-backed maps, or from GET_INTERACTIVE_HTML for interactive ones.
        json_string: str | None = None

        if artifact_id:
            # Read the row id through the ``NoteRow`` adapter seam rather than a
            # raw ``mm[0]`` index so a numeric / non-str id is ``str``-coerced
            # consistently with the rest of the mind-map path (issue #1270) and
            # any future row-shape change is absorbed in one place.
            mind_map = next((mm for mm in mind_maps if NoteRow(mm).id == artifact_id), None)
            if mind_map is not None:
                json_string = mind_maps_service.extract_content(mind_map)
            else:
                # The id is not a note-backed mind map. Interactive
                # (studio-artifact) mind maps live in the artifact collection,
                # not the note-backed list — fetch the tree there so both kinds
                # download to the same JSON shape (issue #1256). Reuse the
                # caller-provided ``artifacts_data`` when present to avoid a
                # redundant second ``LIST_ARTIFACTS``.
                if artifacts_data is None:
                    artifacts_data = await self._list_raw(notebook_id)
                interactive = False
                for row in artifacts_data:
                    if not isinstance(row, list):
                        continue
                    artifact = Artifact.from_api_response(row)
                    if artifact.id == artifact_id and artifact.is_interactive_mind_map:
                        interactive = True
                        break
                if interactive:
                    json_string = await self._get_interactive_mind_map_tree(
                        notebook_id, artifact_id
                    )
                    if json_string is None:
                        # Found the interactive artifact but its tree is not yet
                        # readable (generation still settling).
                        raise ArtifactNotReadyError("mind_map")
                elif not mind_maps:
                    # Not interactive either: preserve the prior error precedence
                    # — an empty note-backed list reads as "not ready", a
                    # populated list with no matching id reads as "not found".
                    raise ArtifactNotReadyError("mind_map")
                else:
                    raise ArtifactNotFoundError(artifact_id, artifact_type="mind_map")
        else:
            # No explicit id: the first note-backed mind map (if any) is used.
            if not mind_maps:
                raise ArtifactNotReadyError("mind_map")
            json_string = mind_maps_service.extract_content(next(iter(mind_maps)))

        try:
            if json_string is None:
                raise ArtifactParseError("mind_map_content", details="Invalid structure")

            return await self._serialization.write_json_string(output_path, json_string)

        except (IndexError, TypeError, json.JSONDecodeError) as e:
            raise ArtifactParseError(
                "mind_map", details=f"Failed to parse structure: {e}", cause=e
            ) from e

    async def download_data_table(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
        *,
        artifacts_data: list[Any] | None = None,
    ) -> str:
        """Download a data table as CSV (``artifacts_data``: see ``_PREFETCH_NOTE``)."""
        if artifacts_data is None:
            artifacts_data = await self._list_raw(notebook_id)

        table_art = self._select_artifact(
            artifacts_data,
            artifact_id,
            "Data table",
            # Unified to "data_table" so both empty-list and explicit-id-miss
            # paths raise ArtifactNotReadyError with the same artifact_type key.
            "data_table",
            type_code=ArtifactTypeCode.DATA_TABLE,
        )

        try:
            raw_data = table_art.data_table_raw_payload
            headers, rows = _parse_data_table(raw_data)

            return await self._serialization.write_csv(output_path, headers, rows)

        except (IndexError, TypeError, ValueError, UnknownRPCMethodError) as e:
            raise ArtifactParseError(
                "data_table",
                artifact_id=artifact_id,
                details=f"Failed to parse structure: {e}",
                cause=e,
            ) from e

    async def download_quiz(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
        output_format: str = "json",
        *,
        artifacts: list[Artifact] | None = None,
    ) -> str:
        """Download quiz questions."""
        return await self.download_interactive_artifact(
            notebook_id, output_path, artifact_id, output_format, "quiz", artifacts=artifacts
        )

    async def download_flashcards(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
        output_format: str = "json",
        *,
        artifacts: list[Artifact] | None = None,
    ) -> str:
        """Download flashcard deck."""
        return await self.download_interactive_artifact(
            notebook_id, output_path, artifact_id, output_format, "flashcards", artifacts=artifacts
        )

    async def download_urls_batch(self, urls_and_paths: list[tuple[str, str]]) -> DownloadResult:
        """Download multiple files through the trusted representation client."""
        return await self._remote.download_batch(urls_and_paths)

    async def download_url(self, url: str, output_path: str) -> str:
        """Download one representation through the trusted byte client."""
        return await self._remote.download(url, output_path)
