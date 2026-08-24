"""Web response codecs that terminate transport grammar at neutral values."""

from .artifacts import decode_artifact, decode_mind_map_artifact, decode_report_suggestion
from .collections import decode_collection
from .documents import decode_structured_document
from .labels import decode_label
from .notebooks import decode_notebook, decode_notebook_description
from .notes import decode_created_note, decode_note, decode_notes
from .sharing import decode_share_status, decode_shared_user
from .sources import decode_source, decode_source_row

__all__ = [
    "decode_artifact",
    "decode_collection",
    "decode_label",
    "decode_mind_map_artifact",
    "decode_notebook",
    "decode_notebook_description",
    "decode_note",
    "decode_notes",
    "decode_created_note",
    "decode_report_suggestion",
    "decode_share_status",
    "decode_shared_user",
    "decode_source",
    "decode_source_row",
    "decode_structured_document",
]
