"""Shared transport labels for source status/type selectors."""

from __future__ import annotations

from typing import Literal

SourceStatusLabel = Literal[
    "unknown",
    "unspecified",
    "processing",
    "ready",
    "error",
    "pending_deletion",
    "preparing",
]
SourceTypeLabel = Literal[
    "google_docs",
    "google_slides",
    "google_spreadsheet",
    "pdf",
    "pasted_text",
    "web_page",
    "google_drive_audio",
    "google_drive_video",
    "youtube",
    "markdown",
    "docx",
    "powerpoint",
    "csv",
    "epub",
    "image",
    "media",
    "unknown",
]

__all__ = ["SourceStatusLabel", "SourceTypeLabel"]
