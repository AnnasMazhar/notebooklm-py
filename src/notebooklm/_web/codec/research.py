"""Encode and decode the web research (``DiscoverSources``) wire grammar.

The whole research feature is Google's "DiscoverSources" pipeline:
``fast`` -> ``DiscoverSourcesManifold``, ``deep`` -> ``DiscoverSourcesAsync``,
``POLL_RESEARCH`` -> ``ListDiscoverSourcesJob``, ``IMPORT_RESEARCH`` ->
``FinishDiscoverSourcesRun``. "Research" is this client's label for it.

This module owns every positional array the research operations send and the
projection of each response onto neutral records. It selects no RPC method and
performs no dispatch -- the backend binds those -- so the grammar terminates
here rather than in a feature facade.

Like the notebook/source bindings, the poll decoder still runs the proven
``_research_task_parser`` and then flattens its public models into records. That
keeps one definition of the positional grammar during the migration; the
Removal note on ``_web/backend.py`` covers replacing it with a direct
wire-to-record descent.
"""

from __future__ import annotations

from typing import Any, cast

from ..._records import (
    ResearchImportedSourceRecord,
    ResearchImportEntry,
    ResearchImportEntryKind,
    ResearchMode,
    ResearchSearchSource,
    ResearchStartResult,
    ResearchTaskRecord,
)
from ..._research_neutral import (
    RESEARCH_SOURCE_TYPE_DRIVE,
    RESEARCH_SOURCE_TYPE_WEB,
    decode_research_task_records,
)
from ..._row_adapters.research import ImportedSourceRow, ResearchStartRow, unwrap_import_rows
from ...exceptions import DecodingError

_SEARCH_SOURCE_CODES = {
    # Same constants the read side decodes ``task_info[1][1]`` with, so the
    # round trip has one definition of the tag rather than two (#1964).
    ResearchSearchSource.WEB: RESEARCH_SOURCE_TYPE_WEB,
    ResearchSearchSource.DRIVE: RESEARCH_SOURCE_TYPE_DRIVE,
}


def encode_research_start_params(
    notebook_id: str,
    query: str,
    search_source: ResearchSearchSource,
    mode: ResearchMode,
) -> list[Any]:
    """Build the kickoff params for one fast or deep research run."""
    source_type = _SEARCH_SOURCE_CODES[search_source]
    if mode is ResearchMode.FAST:
        return [[query, source_type], None, 1, notebook_id]
    return [None, [1], [query, source_type], 5, notebook_id]


def encode_research_poll_params(notebook_id: str) -> list[Any]:
    """Build the params listing every discovery job in one notebook."""
    return [None, None, notebook_id]


def encode_research_cancel_params(run_id: str) -> list[Any]:
    """Build the cancel params for one poll-level run id.

    Field 3 carries the run id; the optional field-1 client context is omitted
    to match :func:`encode_research_poll_params`.
    """
    return [None, None, run_id]


def build_report_import_entry(title: str, markdown: str) -> list[Any]:
    """Build the special deep-research report entry used by IMPORT_RESEARCH."""
    return [None, [title, markdown], None, 3, None, None, None, None, None, None, 3]


def build_web_import_entry(url: str, title: str) -> list[Any]:
    """Build a standard web-source import entry used by IMPORT_RESEARCH."""
    return [None, None, [url, title], None, None, None, None, None, None, None, 2]


def encode_research_import_params(
    notebook_id: str,
    task_id: str,
    entries: tuple[ResearchImportEntry, ...],
) -> list[Any]:
    """Build IMPORT_RESEARCH params for one already-ordered import batch."""
    source_array = [
        build_report_import_entry(entry.title, entry.report_markdown)
        if entry.kind is ResearchImportEntryKind.REPORT
        else build_web_import_entry(entry.url, entry.title)
        for entry in entries
    ]
    return [None, [1], task_id, notebook_id, source_array]


def decode_research_start(result: Any, *, method_id: str) -> ResearchStartResult:
    """Decode a kickoff response into the identifiers the run volunteered.

    v0.8.0 (#1342): a "couldn't-start" payload -- an empty / non-list result or
    a falsey ``task_id`` -- raises :class:`DecodingError` rather than reporting
    a task that was never created.
    """
    if not result or not isinstance(result, list) or len(result) == 0:
        raise DecodingError(
            "research.start returned an empty / non-list payload", method_id=method_id
        )
    start_row = ResearchStartRow(result)
    task_id = start_row.task_id_raw
    if not task_id:
        raise DecodingError(f"research.start returned no task id: {result!r}", method_id=method_id)
    return ResearchStartResult(task_id=task_id, report_id=start_row.report_id)


def decode_research_tasks(result: Any) -> tuple[ResearchTaskRecord, ...]:
    """Decode a POLL_RESEARCH payload into neutral task records, in wire order."""
    return decode_research_task_records(result)


def decode_imported_sources(result: Any) -> tuple[ResearchImportedSourceRecord, ...]:
    """Decode the acknowledged rows of an IMPORT_RESEARCH response.

    The response is documented as incomplete: it may acknowledge fewer sources
    than were committed, and a row may legitimately omit its id envelope. Rows
    that are malformed or carry no id are skipped, never raised.
    """
    imported: list[ResearchImportedSourceRecord] = []
    for row_data in unwrap_import_rows(result):
        row = ImportedSourceRow(row_data)
        if not row.is_well_formed:
            continue
        source_id = row.source_id
        if not source_id:
            continue
        # Both slots are replayed exactly as the backend volunteered them. The
        # loosely-typed wire row can carry a non-string title, and coercing it
        # here would change the entry callers already receive; the casts record
        # that compatibility impedance rather than hiding it.
        imported.append(
            ResearchImportedSourceRecord(
                id=cast(str, source_id),
                title=cast(str, row.title_slot),
            )
        )
    return tuple(imported)


__all__ = [
    "build_report_import_entry",
    "build_web_import_entry",
    "decode_imported_sources",
    "decode_research_start",
    "decode_research_tasks",
    "encode_research_cancel_params",
    "encode_research_import_params",
    "encode_research_poll_params",
    "encode_research_start_params",
]
