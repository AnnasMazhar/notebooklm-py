"""Decode the shared Tailwind document body into its neutral public value graph.

The structured-document graph is ADR-0035's explicit value-type exemption: its
public constructors own UTF-16 validation, normalization, and rendering. This
codec therefore returns :class:`StructuredDocument` directly. The proven
positional grammar remains in ``_row_adapters.documents`` until the plan's
atomic row-adapter relocation; this module is the live web-codec boundary and
must not grow backend, RPC dispatch, HTTP, or feature-facade dependencies.
"""

from __future__ import annotations

from typing import Any

from ..._row_adapters.documents import build_document
from ..._types.documents import StructuredDocument


def decode_structured_document(body: list[Any]) -> StructuredDocument:
    """Decode one web ``TailwindDoc.Body`` through validating value constructors."""
    return build_document(body)


__all__ = ["decode_structured_document"]
