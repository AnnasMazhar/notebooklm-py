"""Pure-value type for a NotebookLM collection (account-level notebook group).

Re-exported from ``notebooklm.types``. A ``Collection`` groups whole notebooks
(account-level, playlist-style) — the wire tuple is ``[name, member_notebook_ids,
collection_id, emoji]``, shaped like a source ``Label`` (``[name, sources, id,
emoji]``) only while **empty**. Once populated the member slot carries *bare*
notebook-id strings (``["nb_id", ...]``), not the label's wrapped singletons
(``[["src_id"], ...]``) — confirmed live on PR #2009 — so it decodes through
:class:`~notebooklm._row_adapters.labels.CollectionRow` rather than
:class:`~notebooklm._row_adapters.labels.LabelRow`, whose strict decoder rejects
the bare form.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Collection:
    """A NotebookLM collection (a named, emoji-labeled group of notebooks).

    Account-level (unlike a notebook-scoped :class:`~notebooklm.types.Label`):
    it carries no notebook parent. Membership is many-to-many — a notebook may
    belong to multiple collections, and a collection owns a list of notebook IDs
    (the notebook carries no back-reference). Nesting and sharing are not
    supported by the service.
    """

    id: str
    name: str
    emoji: str | None = None
    # Member notebook UUIDs. Empty for a freshly-created (still empty) collection.
    notebook_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_api_response(cls, data: list[Any], *, method_id: str | None = None) -> Collection:
        """Parse one collection 4-tuple ``[name, notebook_ids, collection_id, emoji]``.

        Strict per ADR-0019/0011 — the row adapter's ``safe_index`` descent
        raises on drift, and any type drift (non-string name/id, a member that
        isn't a string, a non-string emoji) raises too; there is no
        degrade-to-sentinel path.
        """
        from .._row_adapters.labels import CollectionRow

        row = CollectionRow.from_collection_tuple(data, method_id=method_id)
        return cls(
            id=row.id,
            name=row.name,
            emoji=row.emoji or None,
            notebook_ids=list(row.notebook_ids),
        )
