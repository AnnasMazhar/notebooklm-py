"""Contract tests for the first live P3 web document-codec slice."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from notebooklm._source.content import SourceContentRenderer
from notebooklm._types.documents import BlockKind, StructuredDocument
from notebooklm._web.codec.documents import decode_structured_document
from tests._fixtures.source_content import CodecSourceContentService

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CODEC_PATH = PROJECT_ROOT / "src" / "notebooklm" / "_web" / "codec" / "documents.py"


def test_codec_constructs_the_exempt_utf16_value_graph() -> None:
    # Body.content -> one StructuralElement -> Paragraph -> one TextRun.
    text_run = ["🔬A"]
    paragraph_element = [0, 3, text_run]
    paragraph = [[paragraph_element]]
    body = [[[0, 3, paragraph]]]

    document = decode_structured_document(body)

    assert type(document) is StructuredDocument
    assert document.text == "🔬A"
    assert document.extent == 3
    assert document.slice(0, 2) == "🔬"
    assert document.blocks[0].kind is BlockKind.PARAGRAPH


@pytest.mark.asyncio
async def test_get_source_fulltext_delegates_document_construction_to_web_codec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content_blocks = [["legacy text"]]
    response = [["source-id", "Title", []], None, None, [content_blocks]]
    sentinel = StructuredDocument()
    seen: list[list[Any]] = []

    def decode(body: list[Any]) -> StructuredDocument:
        seen.append(body)
        return sentinel

    monkeypatch.setattr("notebooklm._web.codec.sources.decode_structured_document", decode)

    fulltext = await SourceContentRenderer(CodecSourceContentService(response)).get_fulltext(
        "notebook-id", "source-id"
    )

    assert seen == [content_blocks]
    assert fulltext.document is sentinel
    assert fulltext.content == "legacy text"


def test_document_codec_has_no_backend_rpc_http_or_feature_dependencies() -> None:
    tree = ast.parse(CODEC_PATH.read_text(encoding="utf-8"), filename=str(CODEC_PATH))
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_modules.add(node.module or "")

    forbidden = {"_backend", "rpc", "httpx", "client", "_source", "cli", "mcp", "server"}
    assert not {
        module
        for module in imported_modules
        if any(part in forbidden for part in module.split("."))
    }
