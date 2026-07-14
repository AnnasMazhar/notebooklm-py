"""Unit tests for the Phase 1 ``await_upload`` core helper (``_await_upload``).

The helper is transport-agnostic: it takes a resolved :class:`FileTransferConfig`
(real signer + in-process completion map), a token-or-URL, and returns a small
status dict. Testing it directly avoids standing up the whole HTTP MCP server
(which needs a public base URL to wire ``file_transfer`` at all).
"""

from __future__ import annotations

import asyncio
import time

import pytest

pytest.importorskip("fastmcp")

from notebooklm.mcp._filelink import FileLinkSigner, FileTransferConfig  # noqa: E402
from notebooklm.mcp.tools._fileupload import _await_upload, _extract_ul_token  # noqa: E402


def _cfg() -> FileTransferConfig:
    return FileTransferConfig(signer=FileLinkSigner(key=b"k" * 32), base_url="https://h.example")


def _mint(cfg: FileTransferConfig) -> tuple[str, str]:
    """Return ``(url, jti)`` for a fresh upload link."""
    url = cfg.upload_url({"nb": "nb1"})
    token = url.rsplit("/", 1)[1]
    jti = cfg.signer.verify(token, op="ul")["jti"]
    return url, jti


def test_extract_token_from_url_or_bare() -> None:
    assert _extract_ul_token("abc.def") == "abc.def"
    assert _extract_ul_token("https://h.example/files/ul/abc.def") == "abc.def"
    assert _extract_ul_token("https://h.example/files/ul/abc.def?filename=x#frag") == "abc.def"
    assert _extract_ul_token("  https://h.example/files/ul/abc.def/  ") == "abc.def"


async def test_received_when_result_already_recorded() -> None:
    cfg = _cfg()
    url, jti = _mint(cfg)
    cfg.jti_store.commit(jti, int(time.time()) + 60, result={"source_id": "s-1", "name": "r.pdf"})
    # accepts the full URL...
    out = await _await_upload(cfg, url, timeout_s=0, poll_interval_s=0)
    assert out["status"] == "received"
    assert out["source_id"] == "s-1"
    assert out["file"] == {"source_id": "s-1", "name": "r.pdf"}
    # ...and the bare token
    out2 = await _await_upload(cfg, jti and url.rsplit("/", 1)[1], timeout_s=0, poll_interval_s=0)
    assert out2["status"] == "received"


async def test_pending_when_not_yet_uploaded() -> None:
    cfg = _cfg()
    url, _ = _mint(cfg)
    out = await _await_upload(cfg, url, timeout_s=0, poll_interval_s=0)
    assert out["status"] == "pending"
    assert "re-invoke" in out["hint"]


async def test_expired_or_invalid_token() -> None:
    cfg = _cfg()
    out = await _await_upload(cfg, "not-a-real.token", timeout_s=0, poll_interval_s=0)
    assert out["status"] == "expired_or_invalid"
    assert "source_add" in out["hint"]


async def test_poll_picks_up_a_later_same_process_commit() -> None:
    # The whole point of the in-process map: a commit that lands WHILE await_upload is
    # polling is seen on the next tick (no DB, same event loop).
    cfg = _cfg()
    url, jti = _mint(cfg)

    async def _upload_lands() -> None:
        await asyncio.sleep(0.02)
        cfg.jti_store.commit(jti, int(time.time()) + 60, result={"source_id": "s-late"})

    lander = asyncio.create_task(_upload_lands())
    out = await _await_upload(cfg, url, timeout_s=2.0, poll_interval_s=0.01)
    await lander
    assert out["status"] == "received"
    assert out["source_id"] == "s-late"
