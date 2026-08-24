"""Compatibility exports for retired generic artifact download plumbing.

Representation selection now belongs to :mod:`notebooklm._studio.representations`
and byte retrieval to :mod:`notebooklm._studio.downloads`. Security-sensitive
helper aliases stay importable; none dispatches an RPC.
"""

from __future__ import annotations

from typing import Any

from .._auth.cookies import load_httpx_cookies
from .._studio.downloads import (
    _DOWNLOAD_WRITER_QUEUE_SIZE,
    DownloadResult,
    StudioDownloadClient,
    _await_writer_exit,
    _download_display_host,
    _is_trusted_download_host,
    _make_download_client,
)


def _load_httpx_cookies(storage_path: Any) -> Any:
    """Load download cookies through the historical patchable seam."""

    return load_httpx_cookies(path=storage_path)


__all__ = [
    "DownloadResult",
    "StudioDownloadClient",
    "_DOWNLOAD_WRITER_QUEUE_SIZE",
    "_await_writer_exit",
    "_download_display_host",
    "_is_trusted_download_host",
    "_load_httpx_cookies",
    "_make_download_client",
]
