"""Compatibility exports for streamed-chat codec helpers.

Production wire ownership moved to :mod:`notebooklm._web.codec.chat_stream`
in P6.1. This module preserves historical private imports without owning an
RPC binding or a positional request shape.
"""

from __future__ import annotations

from typing import Any

from .._records import ChatHistoryPairRecord
from .._web.codec import chat_stream as _codec
from .._web.codec.chat_stream import (
    AuthSnapshotLike,
    StreamingChatParseResult,
    _ChunkExtraction,
    _extract_chunk_with_parseable,
    _extract_next_turn_content,
    attach_answer_anchors,
    build_streaming_chat_request,
    extract_answer_and_refs_from_chunk,
    extract_text_passages,
    extract_uuid_from_nested,
    parse_citations,
    parse_single_citation,
    raise_if_rate_limited,
)
from ..rpc.decoder import strip_anti_xssi


def parse_streaming_chat_response(response_text: str) -> StreamingChatParseResult:
    """Delegate parsing while retaining the historical monkeypatch seam."""
    previous = _codec.strip_anti_xssi
    _codec.strip_anti_xssi = strip_anti_xssi
    try:
        return _codec.parse_streaming_chat_response(response_text)
    finally:
        _codec.strip_anti_xssi = previous


def project_legacy_conversation_history(
    records: tuple[ChatHistoryPairRecord, ...],
) -> list[list[str | int | None]] | None:
    """Project neutral cached turns for compatibility-only private callers."""
    if not records:
        return None
    return [row for turn in records for row in ([turn.answer, None, 2], [turn.question, None, 1])]


def __getattr__(name: str) -> Any:
    """Expose unlisted private helpers for compatibility-only imports."""
    return getattr(_codec, name)


__all__ = [
    "AuthSnapshotLike",
    "StreamingChatParseResult",
    "_ChunkExtraction",
    "_extract_chunk_with_parseable",
    "_extract_next_turn_content",
    "attach_answer_anchors",
    "build_streaming_chat_request",
    "extract_answer_and_refs_from_chunk",
    "extract_text_passages",
    "extract_uuid_from_nested",
    "parse_citations",
    "parse_single_citation",
    "parse_streaming_chat_response",
    "project_legacy_conversation_history",
    "raise_if_rate_limited",
]
