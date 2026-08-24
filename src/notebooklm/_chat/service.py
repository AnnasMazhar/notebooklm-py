"""Transport-neutral semantic service for the six chat operations."""

from __future__ import annotations

from .._backend import BackendAdapter
from .._deadline import RuntimeDeadline
from .._records import (
    CHAT_ASK_DEF,
    CHAT_CONFIGURE_DEF,
    CHAT_DELETE_HISTORY_DEF,
    CHAT_GET_CONVERSATION_DEF,
    CHAT_GET_HISTORY_DEF,
    CHAT_SAVE_NOTE_DEF,
    ChatAskInput,
    ChatAskResultRecord,
    ChatConfigureInput,
    ChatConfigureResult,
    ChatDeleteHistoryInput,
    ChatGetConversationInput,
    ChatGetHistoryInput,
    ChatGetHistoryResult,
    ChatSaveNoteInput,
    ChatSaveNoteResult,
)


class ChatService:
    """Invoke typed chat operations without naming web RPC or transport vocabulary."""

    __slots__ = ("_backend",)

    def __init__(self, backend: BackendAdapter) -> None:
        self._backend = backend

    async def ask(
        self,
        value: ChatAskInput,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> ChatAskResultRecord:
        return await self._backend.invoke(CHAT_ASK_DEF, value, deadline=deadline)

    async def get_conversation_id(
        self,
        notebook_id: str,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> str | None:
        result = await self._backend.invoke(
            CHAT_GET_CONVERSATION_DEF,
            ChatGetConversationInput(notebook_id),
            deadline=deadline,
        )
        return result.conversation_id

    async def get_history(
        self,
        notebook_id: str,
        conversation_id: str,
        *,
        limit: int = 2,
        deadline: RuntimeDeadline | None = None,
    ) -> ChatGetHistoryResult:
        return await self._backend.invoke(
            CHAT_GET_HISTORY_DEF,
            ChatGetHistoryInput(notebook_id, conversation_id, limit),
            deadline=deadline,
        )

    async def delete_history(
        self,
        notebook_id: str,
        conversation_id: str,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> None:
        await self._backend.invoke(
            CHAT_DELETE_HISTORY_DEF,
            ChatDeleteHistoryInput(notebook_id, conversation_id),
            deadline=deadline,
        )

    async def configure(
        self,
        value: ChatConfigureInput,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> ChatConfigureResult:
        return await self._backend.invoke(CHAT_CONFIGURE_DEF, value, deadline=deadline)

    async def save_note(
        self,
        value: ChatSaveNoteInput,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> ChatSaveNoteResult:
        return await self._backend.invoke(CHAT_SAVE_NOTE_DEF, value, deadline=deadline)


__all__ = ["ChatService"]
