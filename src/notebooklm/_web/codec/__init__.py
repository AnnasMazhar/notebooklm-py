"""Web response codecs that terminate transport grammar at neutral values."""

from .documents import decode_structured_document
from .notes import decode_created_note, decode_note, decode_notes
from .studio_documents import (
    decode_generation_status,
    encode_report_generation,
    encode_video_generation,
)

__all__ = [
    "decode_created_note",
    "decode_generation_status",
    "decode_note",
    "decode_notes",
    "decode_structured_document",
    "encode_report_generation",
    "encode_video_generation",
]
