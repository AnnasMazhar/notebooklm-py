"""Focused tests for the transport-neutral URL-source mutation service."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from notebooklm._deadline import RuntimeDeadline
from notebooklm._mutation_services import SourceUrlMutationService
from notebooklm._operations import Operation
from notebooklm._records import (
    SOURCE_ADD_URL_DEF,
    SourceAddCommitState,
    SourceAddTitleState,
    SourceAddUrlInput,
    SourceAddUrlReceipt,
    SourceAddUrlResult,
    SourceRecord,
)
from tests._fixtures.recording_backend import BackendInvocation, RecordingBackend


@pytest.mark.asyncio
async def test_url_service_invokes_one_typed_variant_and_forwards_deadline_unchanged() -> None:
    result = SourceAddUrlResult(
        source=SourceRecord("source-id", "Requested", url="https://youtu.be/video-id"),
        receipt=SourceAddUrlReceipt(
            commit_state=SourceAddCommitState.RECONCILED,
            title_state=SourceAddTitleState.RENAMED,
        ),
    )
    backend = RecordingBackend()
    backend.set_result(SOURCE_ADD_URL_DEF, result)
    deadline = RuntimeDeadline(timeout=30.0, started_at=5.0, monotonic=lambda: 6.0)

    received = await SourceUrlMutationService(backend).add_url(
        "notebook-id",
        "https://youtu.be/video-id",
        wait=True,
        wait_timeout=17.0,
        requested_title="Requested",
        deadline=deadline,
    )

    assert received is result
    assert backend.invocations == [
        BackendInvocation(
            Operation.SOURCE_ADD_URL,
            SourceAddUrlInput(
                notebook_id="notebook-id",
                url="https://youtu.be/video-id",
                wait=True,
                wait_timeout=17.0,
                requested_title="Requested",
            ),
            deadline,
        )
    ]


def test_url_service_has_no_wire_or_public_model_dependencies() -> None:
    path = Path(__file__).resolve().parents[2] / "src" / "notebooklm" / "_mutation_services.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imported.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}

    assert not any(
        dependency in module
        for module in imported
        for dependency in ("rpc", "httpx", "_web", "_sources", "types")
    )
    assert {"RPCMethod", "RpcCaller", "Source"}.isdisjoint(names)
