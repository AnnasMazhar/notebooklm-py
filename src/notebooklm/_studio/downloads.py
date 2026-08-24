"""Trusted remote byte retrieval for Studio representations.

The client owns filesystem staging and transport selection. Both httpx and the
optional curl_cffi path reuse the existing trusted-host predicate and validate
every redirect hop before bytes are written.
"""

from __future__ import annotations

import asyncio
import logging
import os
import queue
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .._artifact._download_client import (
    _download_display_host,
    _is_trusted_download_host,
    _make_download_client,
)
from .._artifact._redirect_guard import redirect_revalidation_hooks
from .._curl_cffi_transport import resolve_transport_factory
from ..exceptions import ArtifactDownloadError

logger = logging.getLogger(__name__)

_DOWNLOAD_WRITER_QUEUE_SIZE = 8


async def _await_writer_exit(
    writer_thread: threading.Thread,
    *,
    re_raise_cancel: bool = False,
) -> None:
    """Shield a writer join so cleanup never races an open temporary file."""

    join_task = asyncio.ensure_future(asyncio.to_thread(writer_thread.join))
    cancelled_error: asyncio.CancelledError | None = None
    while not join_task.done():
        try:
            await asyncio.shield(join_task)
        except asyncio.CancelledError as exc:
            cancelled_error = exc
    if cancelled_error is not None and re_raise_cancel:
        raise cancelled_error


@dataclass
class DownloadResult:
    """Outcome of a multi-URL retrieval without hiding partial failure."""

    succeeded: list[str] = field(default_factory=list)
    failed: list[tuple[str, Exception]] = field(default_factory=list)

    @property
    def all_succeeded(self) -> bool:
        return not self.failed

    @property
    def partial(self) -> bool:
        return bool(self.succeeded) and bool(self.failed)


def _reject_html_download(response: httpx.Response) -> None:
    if "text/html" in response.headers.get("content-type", ""):
        raise ArtifactDownloadError(
            "media",
            details="Download failed: received HTML instead of media file. "
            "Authentication may have expired. Run 'notebooklm login'.",
        )


def _reject_empty_download(total_bytes: int) -> None:
    if total_bytes == 0:
        raise ArtifactDownloadError(
            "media",
            details="Download produced 0 bytes -- the remote file may be missing or empty",
        )


class StudioDownloadClient:
    """Representation-agnostic, allowlisted byte retrieval client."""

    __slots__ = ("_cookie_loader", "_storage_path")

    def __init__(
        self,
        *,
        storage_path: Path | None,
        cookie_loader: Callable[[Any], Any],
    ) -> None:
        self._storage_path = storage_path
        self._cookie_loader = cookie_loader

    async def download_batch(self, urls_and_paths: list[tuple[str, str]]) -> DownloadResult:
        """Retrieve multiple representations and retain per-item failures."""

        result = DownloadResult()
        cookies = await asyncio.to_thread(self._cookie_loader, self._storage_path)
        client, guarded_get = _make_download_client(cookies, timeout=60.0)
        async with client:
            for url, output_path in urls_and_paths:
                display_host = ""
                parsed_path = ""
                try:
                    parsed = urlparse(url)
                    display_host = _download_display_host(parsed)
                    parsed_path = parsed.path
                    if parsed.scheme != "https":
                        raise ArtifactDownloadError(
                            "media", details=f"Download URL must use HTTPS: {url[:80]}"
                        )
                    if not _is_trusted_download_host(parsed.hostname):
                        raise ArtifactDownloadError(
                            "media", details=f"Untrusted download domain: {display_host}"
                        )
                    response = await guarded_get(url)
                    if response.status_code in (401, 403):
                        raise ArtifactDownloadError(
                            "media",
                            details=(
                                f"Authentication failed (HTTP {response.status_code}) "
                                f"on {display_host}{parsed.path}"
                            ),
                        )
                    response.raise_for_status()
                    if "text/html" in response.headers.get("content-type", ""):
                        raise ArtifactDownloadError(
                            "media", details="Received HTML instead of media file"
                        )
                    output_file = Path(output_path)
                    output_file.parent.mkdir(parents=True, exist_ok=True)
                    await asyncio.to_thread(output_file.write_bytes, response.content)
                    result.succeeded.append(output_path)
                    logger.debug(
                        "Downloaded %s%s (%d bytes)",
                        display_host,
                        parsed.path,
                        len(response.content),
                    )
                except (httpx.HTTPError, ValueError, ArtifactDownloadError) as exc:
                    reason = (
                        f"HTTP {exc.response.status_code}"
                        if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None
                        else exc.__class__.__name__
                    )
                    logger.warning(
                        "Download failed for %s%s: %s", display_host, parsed_path, reason
                    )
                    result.failed.append((url, exc))
        return result

    async def download(self, url: str, output_path: str) -> str:
        """Atomically stream one trusted representation to ``output_path``."""

        parsed = urlparse(url)
        display_host = _download_display_host(parsed)
        if parsed.scheme != "https":
            raise ArtifactDownloadError("media", details=f"Download URL must use HTTPS: {url[:80]}")
        if not _is_trusted_download_host(parsed.hostname):
            raise ArtifactDownloadError(
                "media", details=f"Untrusted download domain: {display_host}"
            )

        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path_str = tempfile.mkstemp(
            dir=output_file.parent,
            prefix=output_file.name + ".",
            suffix=".tmp",
        )
        os.close(fd)
        temp_file = Path(temp_path_str)

        try:
            cookies = await asyncio.to_thread(self._cookie_loader, self._storage_path)
            timeout = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=30.0)
            try:
                factory = resolve_transport_factory()
                if factory is not httpx.AsyncClient:
                    async with factory(
                        cookies=cookies, follow_redirects=False, timeout=timeout
                    ) as client:
                        response = await client.get_guarded(
                            url, is_trusted_host=_is_trusted_download_host
                        )
                        response.raise_for_status()
                        _reject_html_download(response)
                        _reject_empty_download(len(response.content))
                        await asyncio.to_thread(temp_file.write_bytes, response.content)
                    os.replace(temp_file, output_file)
                    logger.debug(
                        "Downloaded %s%s (%d bytes)",
                        display_host,
                        parsed.path,
                        len(response.content),
                    )
                    return output_path

                async with httpx.AsyncClient(  # noqa: SIM117
                    cookies=cookies,
                    follow_redirects=True,
                    timeout=timeout,
                    event_hooks=redirect_revalidation_hooks(_is_trusted_download_host),
                ) as client:
                    async with client.stream("GET", url) as response:
                        response.raise_for_status()
                        _reject_html_download(response)
                        total_bytes = await self._stream_response(response, temp_file)
                        _reject_empty_download(total_bytes)
                        os.replace(temp_file, output_file)
                        logger.debug(
                            "Downloaded %s%s (%d bytes)",
                            display_host,
                            parsed.path,
                            total_bytes,
                        )
                        return output_path
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (401, 403):
                    raise ArtifactDownloadError(
                        "media",
                        details=(
                            f"Authentication required for {display_host}{parsed.path}"
                            " -- try `notebooklm login`"
                        ),
                        cause=exc,
                        status_code=exc.response.status_code,
                    ) from exc
                raise ArtifactDownloadError(
                    "media",
                    details=f"HTTP error downloading {display_host}{parsed.path}",
                    cause=exc,
                    status_code=exc.response.status_code,
                ) from exc
            except httpx.RequestError as exc:
                raise ArtifactDownloadError(
                    "media",
                    details=f"Network error downloading {display_host}{parsed.path}",
                    cause=exc,
                ) from exc
        except BaseException:
            temp_file.unlink(missing_ok=True)
            raise

    @staticmethod
    async def _stream_response(response: httpx.Response, temp_file: Path) -> int:
        """Drain one response through a bounded single-writer queue."""

        chunk_q: queue.Queue[bytes | None] = queue.Queue(maxsize=_DOWNLOAD_WRITER_QUEUE_SIZE)
        writer_failed = threading.Event()
        writer_error: list[BaseException] = []

        def _writer_loop() -> None:
            try:
                with open(temp_file, "wb") as fh:
                    while True:
                        item = chunk_q.get()
                        if item is None:
                            return
                        fh.write(item)
            except BaseException as exc:
                writer_error.append(exc)
                writer_failed.set()
            finally:
                while True:
                    try:
                        chunk_q.get_nowait()
                    except queue.Empty:
                        break

        writer_thread = threading.Thread(
            target=_writer_loop,
            name=f"artifact-dl-writer-{temp_file.name}",
            daemon=True,
        )
        writer_thread.start()
        total_bytes = 0
        try:
            async for chunk in response.aiter_bytes(chunk_size=65536):
                if writer_failed.is_set():
                    break
                try:
                    chunk_q.put_nowait(chunk)
                except queue.Full:
                    await asyncio.to_thread(chunk_q.put, chunk)
                total_bytes += len(chunk)
            if not writer_failed.is_set():
                try:
                    chunk_q.put_nowait(None)
                except queue.Full:
                    await asyncio.to_thread(chunk_q.put, None)
            await _await_writer_exit(writer_thread, re_raise_cancel=True)
            if writer_error:
                raise next(iter(writer_error))
        except BaseException:
            while True:
                try:
                    chunk_q.put_nowait(None)
                    break
                except queue.Full:
                    pass
                try:
                    chunk_q.get_nowait()
                except queue.Empty:
                    pass
            await _await_writer_exit(writer_thread)
            raise
        return total_bytes


__all__ = ["DownloadResult", "StudioDownloadClient"]
