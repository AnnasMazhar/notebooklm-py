"""Neutral projection of the retained streamed-Chat parser result."""

from __future__ import annotations

from typing import Any

from .._records import (
    ChatNextStepRecord,
    ChatReferenceRecord,
    ChatStreamAnswerRecord,
    ChatTurnKeyRecord,
)


def _reference_record(reference: Any) -> ChatReferenceRecord:
    return ChatReferenceRecord(
        source_id=reference.source_id,
        citation_number=reference.citation_number,
        cited_text=reference.cited_text,
        start_char=reference.start_char,
        end_char=reference.end_char,
        chunk_id=reference.chunk_id,
        passage_id=reference.passage_id,
        score=reference.score,
        fragment_start_char=reference.fragment_start_char,
        fragment_end_char=reference.fragment_end_char,
        answer_anchor_start=reference.answer_anchor_start,
        answer_anchor_end=reference.answer_anchor_end,
    )


def project_streaming_chat_result(parsed: Any) -> ChatStreamAnswerRecord:
    """Project a retained parser result and discard its unreliable stream id."""
    turn_key = parsed.turn_key
    return ChatStreamAnswerRecord(
        answer=parsed.answer,
        references=tuple(_reference_record(reference) for reference in parsed.references),
        answer_document=parsed.answer_document,
        turn_key=(
            ChatTurnKeyRecord(turn_key.session_id, turn_key.turn_id, turn_key.turn_code)
            if turn_key is not None
            else None
        ),
        next_steps=tuple(
            ChatNextStepRecord(item.question, item.type_code) for item in parsed.next_steps
        ),
    )


__all__ = ["project_streaming_chat_result"]
