"""Web response codecs that terminate transport grammar at neutral values."""

from .documents import decode_structured_document
from .notes import decode_created_note, decode_note, decode_notes

__all__ = ["decode_created_note", "decode_note", "decode_notes", "decode_structured_document"]
