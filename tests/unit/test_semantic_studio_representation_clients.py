"""P5.7 boundaries for Studio byte retrieval and local serialization."""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from notebooklm._artifact._download_client import (
    _is_trusted_download_host as canonical_trusted_download_host,
)
from notebooklm._artifact._download_client import (
    _make_download_client as canonical_make_download_client,
)
from notebooklm._artifact.downloads import ArtifactDownloadService
from notebooklm._studio import downloads as studio_downloads
from notebooklm._studio.downloads import DownloadResult, StudioDownloadClient
from notebooklm._studio.serialization import StudioSerializationClient


def _service() -> ArtifactDownloadService:
    return ArtifactDownloadService(
        rpc=MagicMock(),
        listing=MagicMock(),
        mind_maps=MagicMock(),
    )


def test_download_client_reuses_canonical_factory_and_host_policy() -> None:
    """P5.7 must not create a second transport or SSRF policy authority."""

    assert studio_downloads._make_download_client is canonical_make_download_client
    assert studio_downloads._is_trusted_download_host is canonical_trusted_download_host

    download_source = inspect.getsource(StudioDownloadClient.download)
    batch_source = inspect.getsource(StudioDownloadClient.download_batch)
    assert "redirect_revalidation_hooks(_is_trusted_download_host)" in download_source
    assert "get_guarded" in download_source
    assert "_make_download_client" in batch_source


def test_representation_clients_do_not_acquire_rpc_or_backend_authority() -> None:
    download_source = inspect.getsource(studio_downloads)
    serialization_source = inspect.getsource(StudioSerializationClient)

    for forbidden in ("RPCMethod", "RpcCaller", "BackendAdapter", "rpc_call"):
        assert forbidden not in download_source
        assert forbidden not in serialization_source


@pytest.mark.asyncio
async def test_curl_single_download_uses_guarded_redirect_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = MagicMock()
    response.headers = {"content-type": "application/pdf"}
    response.content = b"representation-bytes"
    response.raise_for_status = MagicMock()
    client = MagicMock()
    client.get_guarded = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    curl_factory = MagicMock(return_value=client)
    monkeypatch.setattr(studio_downloads, "resolve_transport_factory", lambda: curl_factory)

    output = tmp_path / "artifact.pdf"
    downloader = StudioDownloadClient(
        storage_path=None,
        cookie_loader=lambda _: httpx.Cookies(),
    )

    assert await downloader.download(
        "https://storage.googleapis.com/artifact.pdf",
        str(output),
    ) == str(output)
    client.get_guarded.assert_awaited_once_with(
        "https://storage.googleapis.com/artifact.pdf",
        is_trusted_host=canonical_trusted_download_host,
    )
    assert curl_factory.call_args.kwargs["follow_redirects"] is False
    assert output.read_bytes() == b"representation-bytes"


@pytest.mark.asyncio
async def test_artifact_service_delegates_all_remote_byte_retrieval() -> None:
    service = _service()
    remote = MagicMock(spec=StudioDownloadClient)
    remote.download = AsyncMock(return_value="one.bin")
    remote.download_batch = AsyncMock(return_value=DownloadResult(succeeded=["one.bin"], failed=[]))
    service._remote = remote

    assert await service.download_url("https://storage.googleapis.com/one", "one.bin") == "one.bin"
    batch = await service.download_urls_batch([("https://storage.googleapis.com/one", "one.bin")])

    remote.download.assert_awaited_once_with(
        "https://storage.googleapis.com/one",
        "one.bin",
    )
    remote.download_batch.assert_awaited_once_with(
        [("https://storage.googleapis.com/one", "one.bin")]
    )
    assert batch.all_succeeded


@pytest.mark.asyncio
async def test_serialization_client_preserves_text_json_and_csv_bytes(tmp_path: Path) -> None:
    serializer = StudioSerializationClient()
    text_path = tmp_path / "nested" / "report.md"
    json_path = tmp_path / "map.json"
    csv_path = tmp_path / "table.csv"

    assert await serializer.write_text(str(text_path), "# Café") == str(text_path)
    assert await serializer.write_json_string(
        str(json_path),
        '{"title": "Café", "children": [1]}',
    ) == str(json_path)
    assert await serializer.write_csv(
        str(csv_path),
        ["name", "count"],
        [["Café", 2]],
    ) == str(csv_path)

    assert text_path.read_bytes() == "# Café".encode()
    assert json_path.read_text(encoding="utf-8") == (
        '{\n  "title": "Café",\n  "children": [\n    1\n  ]\n}'
    )
    assert csv_path.read_bytes() == b"\xef\xbb\xbfname,count\r\nCaf\xc3\xa9,2\r\n"


def test_download_result_retains_partial_failure_contract() -> None:
    error = RuntimeError("no bytes")
    result = DownloadResult(succeeded=["ok.bin"], failed=[("bad", error)])

    assert result.succeeded == ["ok.bin"]
    assert result.failed == [("bad", error)]
    assert result.partial is True
    assert result.all_succeeded is False
