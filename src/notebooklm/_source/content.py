"""Private source content rendering service."""

from __future__ import annotations

import builtins
import logging
from typing import Any, Literal, Protocol

from .._types.research import SourceGuide
from ..types import SourceFulltext


class SourceContentService(Protocol):
    """High-level content capability consumed by the compatibility renderer."""

    async def get_guide(self, notebook_id: str, source_id: str) -> SourceGuide: ...

    async def get_fulltext(
        self,
        notebook_id: str,
        source_id: str,
        *,
        output_format: Literal["text", "markdown"] = "text",
    ) -> SourceFulltext: ...


class SourceContentRenderer:
    """Render source guide and fulltext content from source RPC responses."""

    def __init__(
        self,
        service: SourceContentService | None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._service = service
        self._logger = logger or logging.getLogger(__name__)

    async def get_guide(self, notebook_id: str, source_id: str) -> SourceGuide:
        """Get AI-generated summary and keywords for a specific source."""
        if self._service is None:
            raise RuntimeError("source content service was not configured")
        return await self._service.get_guide(notebook_id, source_id)

    async def get_fulltext(
        self,
        notebook_id: str,
        source_id: str,
        *,
        output_format: Literal["text", "markdown"] = "text",
    ) -> SourceFulltext:
        """Get the full content of a source."""
        if self._service is None:
            raise RuntimeError("source content service was not configured")
        return await self._service.get_fulltext(
            notebook_id,
            source_id,
            output_format=output_format,
        )

    def extract_all_text(
        self, data: builtins.list[Any], max_depth: int = 100
    ) -> builtins.list[str]:
        """Recursively extract all text strings from nested arrays.

        The **legacy** flat rendering behind :attr:`SourceFulltext.content`.
        It is deliberately frozen: every string in traversal order, structure
        and offsets discarded, so existing callers see byte-identical output.
        The joins its caller applies insert separators the backend's character
        ranges never accounted for, which is why no citation offset can be used
        against the result (#2128). The offset-bearing parse of the same tree is
        :func:`notebooklm._web.codec.documents.decode_structured_document`, surfaced as
        :attr:`SourceFulltext.document`; prefer it for anything that needs to
        know *where* text sits.
        """
        if max_depth <= 0:
            self._logger.warning("Max recursion depth reached in text extraction")
            return []

        texts: builtins.list[str] = []
        for item in data:
            if isinstance(item, str) and len(item) > 0:
                texts.append(item)
            elif isinstance(item, builtins.list):
                texts.extend(self.extract_all_text(item, max_depth - 1))
        return texts


__all__ = ["SourceContentRenderer"]
