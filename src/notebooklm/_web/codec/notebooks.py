"""Web notebook response codecs returning transport-neutral records."""

from __future__ import annotations

import logging
import reprlib
from datetime import datetime, timezone
from typing import Any, cast

from ..._records import (
    NotebookChatSessionRecord,
    NotebookChatSettingsRecord,
    NotebookDescriptionRecord,
    NotebookPremiumFeaturesRecord,
    NotebookRecord,
    SuggestedTopicRecord,
)
from ..._row_adapters.chat import unwrap_chat_settings
from ..._row_adapters.notebooks import ProjectRow
from ...exceptions import UnknownRPCMethodError
from ...rpc import RPCMethod, safe_index
from ...rpc.types import ChatGoal, ChatResponseLength

logger = logging.getLogger("notebooklm._types.notebooks")

_METHOD_ID = RPCMethod.LIST_NOTEBOOKS.value
_ROLE_LABELS = {1: "owner", 2: "editor", 3: "viewer"}


def _datetime_from_timestamp(value: object) -> datetime | None:
    try:
        return datetime.fromtimestamp(cast(float, value), tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _timestamp(meta: list[Any] | None, position: int, source: str) -> datetime | None:
    if meta is None or len(meta) <= position:
        return None
    block = safe_index(meta, position, method_id=_METHOD_ID, source=source)
    if not isinstance(block, list) or not block:
        return None
    return _datetime_from_timestamp(safe_index(block, 0, method_id=_METHOD_ID, source=source))


def decode_notebook(data: list[Any], *, include_chat_settings: bool = False) -> NotebookRecord:
    """Decode one ``Project`` row without constructing an exported model."""

    project = ProjectRow(data)
    title_slot = (
        safe_index(data, 0, method_id=_METHOD_ID, source="Notebook.title") if data else None
    )
    title = (title_slot if isinstance(title_slot, str) else "").replace("thought\n", "").strip()
    sources = (
        safe_index(data, 1, method_id=_METHOD_ID, source="Notebook.sources_count")
        if len(data) > 1
        else None
    )
    notebook_id = ""
    if len(data) > 2:
        raw_id = safe_index(data, 2, method_id=_METHOD_ID, source="Notebook.id")
        if isinstance(raw_id, str):
            notebook_id = raw_id
        elif raw_id is not None:
            logger.warning(
                "Notebook row id slot malformed — fabricating empty id "
                "(expected str at data[2], got %s; row=%s)",
                type(raw_id).__name__,
                reprlib.repr(data),
            )

    meta_slot = (
        safe_index(data, 5, method_id=_METHOD_ID, source="Notebook.metadata")
        if len(data) > 5
        else None
    )
    meta = meta_slot if isinstance(meta_slot, list) else None
    role = None
    if meta:
        raw_role = safe_index(meta, 0, method_id=_METHOD_ID, source="Notebook.role")
        if isinstance(raw_role, int) and not isinstance(raw_role, bool):
            role = _ROLE_LABELS.get(raw_role)
        if raw_role is not None and role is None:
            logger.warning(
                "Notebook row userRole slot unmapped — reporting unknown role "
                "(expected 1/2/3 at data[5][0], got %r; row=%s)",
                raw_role,
                reprlib.repr(data),
            )

    premium_flags = project.premium_feature_flags
    premium = NotebookPremiumFeaturesRecord(*premium_flags) if premium_flags is not None else None
    chat_settings = None
    if include_chat_settings:
        try:
            settings = unwrap_chat_settings(data, source="Notebook.chat_settings")
            chat_settings = NotebookChatSettingsRecord(
                goal=ChatGoal(settings.goal_code).name.lower(),
                response_length=ChatResponseLength(settings.response_length_code).name.lower(),
                custom_prompt=settings.custom_prompt,
            )
        except (UnknownRPCMethodError, ValueError):
            logger.warning(
                "Notebook row chat-settings slot could not be decoded — reporting unknown "
                "settings (row=%s)",
                reprlib.repr(data),
            )

    return NotebookRecord(
        id=notebook_id,
        title=title,
        created_at=_timestamp(meta, 8, "Notebook.created_at"),
        sources_count=len(sources) if isinstance(sources, list) else 0,
        is_owner=role in (None, "owner"),
        role=role,
        last_viewed_at=_timestamp(meta, 5, "Notebook.last_viewed_at"),
        emoji=project.emoji,
        premium_features=premium,
        chat_sessions=tuple(
            NotebookChatSessionRecord(session_id) for session_id in project.chat_session_ids
        ),
        chat_settings=chat_settings,
    )


def _decode_summary(outer: Any) -> str:
    if outer is None:
        return ""
    if isinstance(outer, list) and (
        not outer
        or safe_index(
            outer, 0, method_id=RPCMethod.SUMMARIZE.value, source="_notebooks._extract_summary"
        )
        is None
    ):
        return ""
    value = safe_index(
        outer,
        0,
        0,
        method_id=RPCMethod.SUMMARIZE.value,
        source="_notebooks._extract_summary",
    )
    return "" if value is None else str(value)


def _decode_topics(outer: Any) -> tuple[SuggestedTopicRecord, ...]:
    if not isinstance(outer, list) or len(outer) < 2:
        logger.debug("_extract_suggested_topics: Partial description — no outer[1] slot")
        return ()
    container = safe_index(
        outer,
        1,
        method_id=RPCMethod.SUMMARIZE.value,
        source="_notebooks._extract_suggested_topics",
    )
    if not isinstance(container, list) or not container:
        logger.debug(
            "_extract_suggested_topics: Partial description — outer[1] is empty or non-list"
        )
        return ()
    topics = safe_index(
        container,
        0,
        method_id=RPCMethod.SUMMARIZE.value,
        source="_notebooks._extract_suggested_topics",
    )
    if not isinstance(topics, list):
        if topics is not None:
            logger.debug(
                "_extract_suggested_topics: expected list at outer[1][0], got %s",
                type(topics).__name__,
            )
        return ()
    decoded: list[SuggestedTopicRecord] = []
    for index, topic in enumerate(topics):
        if not isinstance(topic, list) or len(topic) < 2:
            logger.debug(
                "_extract_suggested_topics: skipping malformed topic at index %d (type=%s)",
                index,
                type(topic).__name__,
            )
            continue
        question = safe_index(
            topic,
            0,
            method_id=RPCMethod.SUMMARIZE.value,
            source="_notebooks._extract_suggested_topics",
        )
        prompt = safe_index(
            topic,
            1,
            method_id=RPCMethod.SUMMARIZE.value,
            source="_notebooks._extract_suggested_topics",
        )
        decoded.append(
            SuggestedTopicRecord(
                question=str(question) if question else "",
                prompt=str(prompt) if prompt else "",
            )
        )
    return tuple(decoded)


def decode_notebook_description(result: Any) -> NotebookDescriptionRecord:
    """Decode a ``SUMMARIZE`` guide response into a neutral record."""

    outer = (
        safe_index(
            result,
            0,
            method_id=RPCMethod.SUMMARIZE.value,
            source="NotebooksAPI.get_description",
        )
        if isinstance(result, list) and result
        else None
    )
    return NotebookDescriptionRecord(
        summary=_decode_summary(outer),
        suggested_topics=_decode_topics(outer),
    )


__all__ = ["decode_notebook", "decode_notebook_description"]
