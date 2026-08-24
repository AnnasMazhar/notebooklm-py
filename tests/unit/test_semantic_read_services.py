"""Focused tests for transport-neutral read services and public projections."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from notebooklm._deadline import RuntimeDeadline
from notebooklm._operations import Operation
from notebooklm._projectors import project_notebook, project_source
from notebooklm._read_services import NotebookReadService, SourceReadService
from notebooklm._records import (
    NOTEBOOK_GET_DEF,
    NOTEBOOK_LIST_DEF,
    SOURCE_GET_DEF,
    SOURCE_LIST_DEF,
    NotebookChatSessionRecord,
    NotebookChatSettingsRecord,
    NotebookGetInput,
    NotebookGetResult,
    NotebookListInput,
    NotebookListResult,
    NotebookPremiumFeaturesRecord,
    NotebookRecord,
    SourceGetInput,
    SourceGetResult,
    SourceListInput,
    SourceListResult,
    SourceRecord,
)
from notebooklm.types import (
    ChatGoal,
    ChatResponseLength,
    DriveSourceStatus,
    SharePermission,
    SourceStatus,
    SourceType,
    UnknownTypeWarning,
)
from tests._fixtures.recording_backend import BackendInvocation, RecordingBackend


def _notebook_record() -> NotebookRecord:
    created = datetime(2025, 1, 2, 3, 4, tzinfo=UTC)
    viewed = datetime(2026, 5, 6, 7, 8, tzinfo=UTC)
    return NotebookRecord(
        id="notebook-id",
        title="Notebook title",
        created_at=created,
        sources_count=7,
        is_owner=True,
        role="editor",
        last_viewed_at=viewed,
        emoji="🧪",
        premium_features=NotebookPremiumFeaturesRecord(True, False, None),
        chat_sessions=(
            NotebookChatSessionRecord("session-one"),
            NotebookChatSessionRecord("session-two"),
        ),
        chat_settings=NotebookChatSettingsRecord("custom", "long", "Be precise"),
    )


def _source_record() -> SourceRecord:
    created = datetime(2025, 2, 3, 4, 5, tzinfo=UTC)
    revised = datetime(2025, 3, 4, 5, 6, tzinfo=UTC)
    modified = datetime(2025, 4, 5, 6, 7, tzinfo=UTC)
    return SourceRecord(
        id="source-id",
        title="Source title",
        url="https://example.com/source.pdf",
        kind="pdf",
        created_at=created,
        status="preparing",
        drive_document_id="drive-id",
        drive_status="syncing",
        download_url="https://example.com/download",
        viewer_url="https://example.com/view",
        content_mime="application/pdf",
        word_count=123,
        revision_id="revision-id",
        revision_timestamp=revised,
        last_modified_at=modified,
    )


def test_project_notebook_preserves_all_neutral_record_fields_and_invariants() -> None:
    record = _notebook_record()

    notebook = project_notebook(record)

    assert notebook.id == record.id
    assert notebook.title == record.title
    assert notebook.created_at == record.created_at
    assert notebook.sources_count == record.sources_count
    assert notebook.role is SharePermission.EDITOR
    assert not notebook.is_owner
    assert notebook.last_viewed_at == record.last_viewed_at
    assert notebook.modified_at == record.last_viewed_at
    assert notebook.emoji == record.emoji
    assert notebook.premium_features is not None
    assert notebook.premium_features.can_edit_advanced_settings is True
    assert notebook.premium_features.can_edit_guidebook_config is False
    assert notebook.premium_features.can_view_analytics is None
    assert [session.id for session in notebook.chat_sessions] == ["session-one", "session-two"]
    assert notebook.chat_settings is not None
    assert notebook.chat_settings.goal is ChatGoal.CUSTOM
    assert notebook.chat_settings.response_length is ChatResponseLength.LONGER
    assert notebook.chat_settings.custom_prompt == "Be precise"


def test_project_notebook_preserves_legacy_owner_fallback_when_role_is_unknown() -> None:
    record = NotebookRecord("notebook-id", "Notebook", is_owner=False, role="future-role")

    notebook = project_notebook(record)

    assert notebook.role is None
    assert notebook.is_owner is False


def test_project_source_preserves_every_neutral_field_and_known_semantics() -> None:
    record = _source_record()

    source = project_source(record)

    assert source.id == record.id
    assert source.title == record.title
    assert source.url == record.url
    assert source.kind is SourceType.PDF
    assert source.created_at == record.created_at
    assert source.status is SourceStatus.PREPARING
    assert source.drive_document_id == record.drive_document_id
    assert source.drive_status is DriveSourceStatus.SYNCING
    assert source.download_url == record.download_url
    assert source.viewer_url == record.viewer_url
    assert source.content_mime == record.content_mime
    assert source.word_count == record.word_count
    assert source.revision_id == record.revision_id
    assert source.revision_timestamp == record.revision_timestamp
    assert source.last_modified_at == record.last_modified_at


@pytest.mark.parametrize("opaque_kind", [938_475, "future-source-kind"])
def test_project_source_preserves_unknown_backend_kind_discriminator(
    opaque_kind: int | str,
) -> None:
    source = project_source(
        SourceRecord(
            "source-id",
            kind="unknown",
            unrecognized_kind=opaque_kind,
        )
    )

    assert source._type_code == opaque_kind
    with pytest.warns(UnknownTypeWarning, match=str(opaque_kind)):
        assert source.kind is SourceType.UNKNOWN


@pytest.mark.asyncio
async def test_read_services_invoke_typed_operations_and_preserve_backend_order() -> None:
    notebook = _notebook_record()
    source = _source_record()
    deadline = RuntimeDeadline(timeout=5.0, started_at=10.0, monotonic=lambda: 11.0)
    backend = RecordingBackend()
    backend.set_result(NOTEBOOK_LIST_DEF, NotebookListResult((notebook,)))
    backend.set_result(NOTEBOOK_GET_DEF, NotebookGetResult(notebook))
    backend.set_result(SOURCE_LIST_DEF, SourceListResult((source,)))
    backend.set_result(SOURCE_GET_DEF, SourceGetResult(source))
    notebooks = NotebookReadService(backend)
    sources = SourceReadService(backend)

    listed_notebooks = await notebooks.list(deadline=deadline)
    fetched_notebook = await notebooks.get("notebook-id", deadline=deadline)
    listed_sources = await sources.list(
        "notebook-id",
        strict=True,
        statuses=frozenset({"ready", "preparing"}),
        kinds=frozenset({"pdf"}),
        deadline=deadline,
    )
    fetched_source = await sources.get("notebook-id", "source-id", deadline=deadline)

    assert [item.id for item in listed_notebooks] == ["notebook-id"]
    assert fetched_notebook is not None and fetched_notebook.id == "notebook-id"
    assert [item.id for item in listed_sources] == ["source-id"]
    assert fetched_source is not None and fetched_source.id == "source-id"
    assert backend.invocations == [
        BackendInvocation(Operation.NOTEBOOK_LIST, NotebookListInput(), deadline),
        BackendInvocation(Operation.NOTEBOOK_GET, NotebookGetInput("notebook-id"), deadline),
        BackendInvocation(
            Operation.SOURCE_LIST,
            SourceListInput(
                "notebook-id",
                strict=True,
                statuses=frozenset({"ready", "preparing"}),
                kinds=frozenset({"pdf"}),
            ),
            deadline,
        ),
        BackendInvocation(
            Operation.SOURCE_GET,
            SourceGetInput("notebook-id", "source-id"),
            deadline,
        ),
    ]


@pytest.mark.asyncio
async def test_read_services_preserve_semantic_not_found_results() -> None:
    backend = RecordingBackend()
    backend.set_result(NOTEBOOK_GET_DEF, NotebookGetResult(None))
    backend.set_result(SOURCE_GET_DEF, SourceGetResult(None))

    assert await NotebookReadService(backend).get("missing-notebook") is None
    assert await SourceReadService(backend).get("notebook-id", "missing-source") is None
