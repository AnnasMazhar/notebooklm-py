#!/usr/bin/env python3
"""Derive and audit the P0 semantic operation catalog.

The reviewed data in this module is intentionally small and semantic: owner,
call policy, route context, composite behavior, disposition, and mappings from
the current public/native surfaces.  Volatile facts are derived from their
existing authorities instead of copied here:

* :class:`notebooklm.rpc.RPCMethod` supplies active RPC ids;
* ``IDEMPOTENCY_REGISTRY`` supplies native variants and retry policy;
* ``GOLDEN_COVERAGE`` / ``GOLDEN_EXEMPT`` supply cassette evidence;
* AST walks supply current RPC references and ``_app`` facade callers; and
* the annotated ``NotebookLMClient`` namespaces supply public methods.

``build_operation_catalog`` is the import seam for the ADR-0022 baseline
registry.  The command exits non-zero only for an incomplete/stale catalog;
known, explicitly reviewed divergences remain visible but do not make an
independently green P0 impossible.
"""

from __future__ import annotations

import argparse
import ast
import inspect
import json
import sys
import typing
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from notebooklm._idempotency import IDEMPOTENCY_REGISTRY
from notebooklm._operations import CallPolicy, Operation
from notebooklm.client import NotebookLMClient
from notebooklm.rpc import RPCMethod

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "notebooklm"
APP_ROOT = SRC_ROOT / "_app"
GOLDEN_PATH = REPO_ROOT / "tests" / "_guardrails" / "test_golden_decode_coverage.py"
RPC_EXECUTOR_PATH = SRC_ROOT / "_rpc_executor.py"
RPC_REGISTRY_EVIDENCE_PATH = REPO_ROOT / "scripts" / "operation_catalog_rpc_registry.json"

SCHEMA_VERSION = 1

_OMISSION_DISPOSITIONS: Mapping[str, str] = {
    "current": "unsupported_current_product_surface",
    "enterprise": "excluded_enterprise_surface",
    "other": "excluded_non_consumer_or_unclassified_service",
}

NativeKey = tuple[RPCMethod, str | None]


class Disposition(str, Enum):
    """Reviewed current disposition for a semantic operation."""

    SEMANTIC = "semantic"
    COMPOSITE = "composite"
    LEGACY_PRIVATE = "legacy_private"


@dataclass(frozen=True, slots=True)
class OperationSpec:
    """The hand-authored portion of one operation catalog row."""

    operation: Operation
    policy: CallPolicy
    owner: str
    route_context: str
    composite_behavior: str
    public_methods: tuple[str, ...] = ()
    native_bindings: tuple[NativeKey, ...] = ()
    web_paths: tuple[str, ...] = ()
    disposition: Disposition = Disposition.SEMANTIC
    app_authorities: tuple[str, ...] = ()
    known_divergence: str | None = None
    recency_effect: str = "none"


def _b(method: RPCMethod, variant: str | None = None) -> NativeKey:
    return method, variant


def _p(namespace: str, *methods: str) -> tuple[str, ...]:
    return tuple(f"{namespace}.{method}" for method in methods)


_CREATE_ARTIFACT = (_b(RPCMethod.CREATE_ARTIFACT),)

# This table is the catalog's reviewed source.  Do not add copied RPC ids,
# idempotency values, golden pointers, or source locations here; those belong to
# the derivation below.  A spec may share a native binding with another spec
# where one web RPC is genuinely polymorphic (labels/collections are the main
# example).
OPERATION_SPECS: tuple[OperationSpec, ...] = (
    OperationSpec(
        Operation.NOTEBOOK_LIST,
        CallPolicy.READ,
        "NotebookService",
        "account",
        "Reads the recency-ordered notebook collection without touching recency.",
        _p("notebooks", "list"),
        (_b(RPCMethod.LIST_NOTEBOOKS),),
    ),
    OperationSpec(
        Operation.NOTEBOOK_GET,
        CallPolicy.MUTATION,
        "NotebookService",
        "notebook",
        "GET_NOTEBOOK reads a notebook and writes lastViewedTime as a user-visible side effect.",
        _p("notebooks", "get", "get_or_none", "get_raw", "get_source_ids"),
        (_b(RPCMethod.GET_NOTEBOOK),),
        recency_effect="exactly one GET_NOTEBOOK per direct call",
    ),
    OperationSpec(
        Operation.NOTEBOOK_CREATE,
        CallPolicy.MUTATION,
        "NotebookService",
        "account",
        "Takes an unconditional LIST_NOTEBOOKS baseline, creates once, probes on ambiguity, and "
        "may re-read once to backfill null timestamps in adapter workflows.",
        _p("notebooks", "create"),
        (_b(RPCMethod.CREATE_NOTEBOOK),),
        recency_effect="zero or one GET_NOTEBOOK timestamp backfill after create",
    ),
    OperationSpec(
        Operation.NOTEBOOK_UPDATE,
        CallPolicy.MUTATION,
        "NotebookService",
        "notebook",
        "Title and emoji updates share MutateProject; the facade re-reads when returning a model.",
        _p("notebooks", "update", "rename", "set_emoji"),
        (_b(RPCMethod.RENAME_NOTEBOOK),),
        recency_effect="one GET_NOTEBOOK after mutation when returning the updated notebook",
    ),
    OperationSpec(
        Operation.NOTEBOOK_DELETE,
        CallPolicy.MUTATION,
        "NotebookService",
        "notebook",
        "Deletes exactly one notebook although the native method is plural.",
        _p("notebooks", "delete"),
        (_b(RPCMethod.DELETE_NOTEBOOK),),
    ),
    OperationSpec(
        Operation.NOTEBOOK_REMOVE_RECENT,
        CallPolicy.MUTATION,
        "NotebookService",
        "notebook",
        "Removes the notebook from the account's Recent list.",
        _p("notebooks", "remove_from_recent"),
        (_b(RPCMethod.REMOVE_RECENTLY_VIEWED),),
    ),
    OperationSpec(
        Operation.NOTEBOOK_SUMMARIZE,
        CallPolicy.STATEFUL_START,
        "NotebookService",
        "notebook",
        "Generates the notebook guide and projects its summary field.",
        _p("notebooks", "get_summary"),
        (_b(RPCMethod.SUMMARIZE),),
    ),
    OperationSpec(
        Operation.NOTEBOOK_DESCRIBE,
        CallPolicy.STATEFUL_START,
        "NotebookService",
        "notebook",
        "Uses the same guide generation response and projects description/topics.",
        _p("notebooks", "get_description"),
        (_b(RPCMethod.SUMMARIZE),),
    ),
    OperationSpec(
        Operation.NOTEBOOK_METADATA,
        CallPolicy.READ,
        "NotebookService",
        "notebook",
        "Combines notebook and source views concurrently through public facades.",
        _p("notebooks", "get_metadata"),
        disposition=Disposition.COMPOSITE,
        recency_effect="exactly two GET_NOTEBOOK calls (notebook.get plus sources.list)",
    ),
    OperationSpec(
        Operation.NOTEBOOK_SUGGEST_PROMPTS,
        CallPolicy.STATEFUL_START,
        "NotebookService",
        "notebook",
        "Resolves default sources through GET_NOTEBOOK, then generates prompt suggestions.",
        _p("notebooks", "suggest_prompts"),
        (_b(RPCMethod.SUGGEST_PROMPTS),),
        recency_effect="one GET_NOTEBOOK only when source_ids is omitted",
    ),
    OperationSpec(
        Operation.SOURCE_LIST,
        CallPolicy.MUTATION,
        "SourceService",
        "notebook",
        "Projects source rows embedded in GET_NOTEBOOK; strict/filter behavior stays in facade.",
        _p("sources", "list"),
        (_b(RPCMethod.GET_NOTEBOOK),),
        recency_effect="exactly one GET_NOTEBOOK per list call",
    ),
    OperationSpec(
        Operation.SOURCE_GET,
        CallPolicy.MUTATION,
        "SourceService",
        "notebook+source",
        "Selects an exact source from the notebook source snapshot.",
        _p("sources", "get", "get_or_none"),
        (_b(RPCMethod.GET_NOTEBOOK),),
        recency_effect="exactly one GET_NOTEBOOK per get/get_or_none call",
    ),
    OperationSpec(
        Operation.SOURCE_ADD_URL,
        CallPolicy.MUTATION,
        "SourceService",
        "notebook",
        "Takes an unconditional source-id baseline, routes YouTube URLs to their wire shape, "
        "uses exact new-row reconciliation, and applies an optional title afterward.",
        _p("sources", "add_url"),
        (_b(RPCMethod.ADD_SOURCE), _b(RPCMethod.ADD_SOURCE, "url")),
        recency_effect="one unconditional pre-create GET_NOTEBOOK; probes add further reads",
    ),
    OperationSpec(
        Operation.SOURCE_ADD_TEXT,
        CallPolicy.MUTATION,
        "SourceService",
        "notebook",
        "Creates text without a safe probe key; idempotent=True is rejected up front.",
        _p("sources", "add_text"),
        (_b(RPCMethod.ADD_SOURCE, "text"),),
        recency_effect="none; text creation intentionally has no probe baseline",
    ),
    OperationSpec(
        Operation.SOURCE_ADD_DRIVE,
        CallPolicy.MUTATION,
        "SourceService",
        "notebook",
        "Takes an unconditional source-id baseline and reconciles by new exact Drive document id.",
        _p("sources", "add_drive", "add_drive_file"),
        (_b(RPCMethod.ADD_SOURCE, "drive"),),
        recency_effect="one unconditional pre-create GET_NOTEBOOK; probes add further reads",
    ),
    OperationSpec(
        Operation.SOURCE_ADD_FILE,
        CallPolicy.MUTATION,
        "SourceService",
        "notebook+upload-session",
        "Stages bytes at the upload endpoint, registers the staged file, reconciles ambiguity, "
        "and may rename the resulting source.",
        _p("sources", "add_file"),
        (_b(RPCMethod.ADD_SOURCE_FILE), _b(RPCMethod.UPDATE_SOURCE)),
        ("UPLOAD_URL",),
        recency_effect="one unconditional pre-create GET_NOTEBOOK; probes add further reads",
    ),
    OperationSpec(
        Operation.SOURCE_DELETE,
        CallPolicy.MUTATION,
        "SourceService",
        "notebook+source",
        "Deletes one source through a batch-capable native method.",
        _p("sources", "delete"),
        (_b(RPCMethod.DELETE_SOURCE),),
    ),
    OperationSpec(
        Operation.SOURCE_UPDATE,
        CallPolicy.MUTATION,
        "SourceService",
        "notebook+source",
        "Updates the source title and optionally re-reads the source.",
        _p("sources", "rename"),
        (_b(RPCMethod.UPDATE_SOURCE),),
    ),
    OperationSpec(
        Operation.SOURCE_REFRESH,
        CallPolicy.MUTATION,
        "SourceService",
        "notebook+source",
        "Requests refresh; native policy currently accepts at-least-once replay.",
        _p("sources", "refresh"),
        (_b(RPCMethod.REFRESH_SOURCE),),
        known_divergence="Semantic mutation is backed by AT_LEAST_ONCE_ACCEPTED native retry; "
        "P4 records parity but must not change behavior.",
    ),
    OperationSpec(
        Operation.SOURCE_CHECK_FRESHNESS,
        CallPolicy.READ,
        "SourceService",
        "notebook+source",
        "Decodes both web-source empty and Drive-source freshness response shapes.",
        _p("sources", "check_freshness"),
        (_b(RPCMethod.CHECK_SOURCE_FRESHNESS),),
    ),
    OperationSpec(
        Operation.SOURCE_GET_GUIDE,
        CallPolicy.STATEFUL_START,
        "SourceService",
        "notebook+source",
        "Generates and decodes a source guide.",
        _p("sources", "get_guide"),
        (_b(RPCMethod.GET_SOURCE_GUIDE),),
    ),
    OperationSpec(
        Operation.SOURCE_GET_FULLTEXT,
        CallPolicy.READ,
        "SourceService",
        "notebook+source",
        "Loads source content and projects text or markdown.",
        _p("sources", "get_fulltext"),
        (_b(RPCMethod.GET_SOURCE),),
    ),
    OperationSpec(
        Operation.SOURCE_WAIT,
        CallPolicy.READ,
        "SourceService",
        "notebook+source-set",
        "Polls the notebook source snapshot; a multi-wait performs one snapshot per poll tick.",
        _p(
            "sources",
            "wait_until_ready",
            "wait_all_until_ready",
            "wait_until_registered",
            "wait_for_sources",
        ),
        disposition=Disposition.COMPOSITE,
        app_authorities=("_app/source_wait.py",),
        known_divergence="_app/source_wait.py maps outcomes while SourcesAPI owns polling. P4.2 "
        "passes one caller budget through the public facade and leaves one polling authority.",
        recency_effect="one GET_NOTEBOOK per poll tick (shared across multi-wait inputs)",
    ),
    OperationSpec(
        Operation.ARTIFACT_LIST,
        CallPolicy.READ,
        "StudioCatalog",
        "notebook",
        "Lists heterogeneous Studio artifacts and preserves partial mind-map availability rules.",
        _p(
            "artifacts",
            "list",
            "list_audio",
            "list_video",
            "list_reports",
            "list_quizzes",
            "list_flashcards",
            "list_infographics",
            "list_slide_decks",
            "list_data_tables",
        ),
        (_b(RPCMethod.LIST_ARTIFACTS),),
    ),
    OperationSpec(
        Operation.ARTIFACT_GET,
        CallPolicy.READ,
        "StudioCatalog",
        "notebook+artifact",
        "Selects one artifact or its prompt from the heterogeneous catalog.",
        _p("artifacts", "get", "get_or_none", "get_prompt", "poll_status"),
        (_b(RPCMethod.LIST_ARTIFACTS),),
    ),
    OperationSpec(
        Operation.ARTIFACT_GENERATE_AUDIO,
        CallPolicy.STATEFUL_START,
        "AudioFamilyService",
        "notebook+source-set",
        "Creates audio with format/length/instruction variants.",
        _p("artifacts", "generate_audio"),
        _CREATE_ARTIFACT,
        recency_effect="one GET_NOTEBOOK when source_ids is omitted",
    ),
    OperationSpec(
        Operation.ARTIFACT_GENERATE_VIDEO,
        CallPolicy.STATEFUL_START,
        "VideoFamilyService",
        "notebook+source-set",
        "Creates standard or cinematic video while preserving their option shapes.",
        _p("artifacts", "generate_video", "generate_cinematic_video"),
        _CREATE_ARTIFACT,
        recency_effect="one GET_NOTEBOOK when source_ids is omitted",
    ),
    OperationSpec(
        Operation.ARTIFACT_GENERATE_REPORT,
        CallPolicy.STATEFUL_START,
        "ReportFamilyService",
        "notebook+source-set",
        "Creates report or study-guide variants.",
        _p("artifacts", "generate_report", "generate_study_guide"),
        _CREATE_ARTIFACT,
        recency_effect="one GET_NOTEBOOK when source_ids is omitted",
    ),
    OperationSpec(
        Operation.ARTIFACT_GENERATE_QUIZ,
        CallPolicy.STATEFUL_START,
        "QuizFamilyService",
        "notebook+source-set",
        "Creates quiz variant 2.",
        _p("artifacts", "generate_quiz"),
        _CREATE_ARTIFACT,
        recency_effect="one GET_NOTEBOOK when source_ids is omitted",
    ),
    OperationSpec(
        Operation.ARTIFACT_GENERATE_FLASHCARDS,
        CallPolicy.STATEFUL_START,
        "QuizFamilyService",
        "notebook+source-set",
        "Creates flashcard variant 1.",
        _p("artifacts", "generate_flashcards"),
        _CREATE_ARTIFACT,
        recency_effect="one GET_NOTEBOOK when source_ids is omitted",
    ),
    OperationSpec(
        Operation.ARTIFACT_GENERATE_INFOGRAPHIC,
        CallPolicy.STATEFUL_START,
        "InfographicFamilyService",
        "notebook+source-set",
        "Creates an infographic with orientation/detail/style variants.",
        _p("artifacts", "generate_infographic"),
        _CREATE_ARTIFACT,
        recency_effect="one GET_NOTEBOOK when source_ids is omitted",
    ),
    OperationSpec(
        Operation.ARTIFACT_GENERATE_SLIDE_DECK,
        CallPolicy.STATEFUL_START,
        "SlideDeckFamilyService",
        "notebook+source-set",
        "Creates a slide deck with format and length variants.",
        _p("artifacts", "generate_slide_deck"),
        _CREATE_ARTIFACT,
        recency_effect="one GET_NOTEBOOK when source_ids is omitted",
    ),
    OperationSpec(
        Operation.ARTIFACT_GENERATE_DATA_TABLE,
        CallPolicy.STATEFUL_START,
        "DataTableFamilyService",
        "notebook+source-set",
        "Creates a data-table artifact.",
        _p("artifacts", "generate_data_table"),
        _CREATE_ARTIFACT,
        recency_effect="one GET_NOTEBOOK when source_ids is omitted",
    ),
    OperationSpec(
        Operation.ARTIFACT_GENERATE_MIND_MAP,
        CallPolicy.STATEFUL_START,
        "MindMapFamilyService",
        "notebook+source-set",
        "Creates an interactive Studio mind map (artifact type 4, variant 4).",
        _p("artifacts", "generate_mind_map"),
        _CREATE_ARTIFACT,
        recency_effect="one GET_NOTEBOOK when source_ids is omitted",
    ),
    OperationSpec(
        Operation.ARTIFACT_REVISE_SLIDE,
        CallPolicy.MUTATION,
        "SlideDeckFamilyService",
        "notebook+artifact",
        "Derives one revised slide from an existing deck.",
        _p("artifacts", "revise_slide"),
        (_b(RPCMethod.REVISE_SLIDE),),
    ),
    OperationSpec(
        Operation.ARTIFACT_RETRY,
        CallPolicy.STATEFUL_START,
        "StudioCatalog",
        "notebook+artifact",
        "Retries a failed artifact in place.",
        _p("artifacts", "retry_failed"),
        (_b(RPCMethod.RETRY_ARTIFACT),),
        app_authorities=("_app/generate_retry.py",),
        known_divergence="_app/generate_retry.py owns a user-level retry loop in addition to the "
        "facade retry call. P5 collapses backend workflow authority without changing CLI policy.",
    ),
    OperationSpec(
        Operation.ARTIFACT_DELETE,
        CallPolicy.MUTATION,
        "StudioCatalog",
        "notebook+artifact",
        "Deletes one artifact.",
        _p("artifacts", "delete"),
        (_b(RPCMethod.DELETE_ARTIFACT),),
    ),
    OperationSpec(
        Operation.ARTIFACT_RENAME,
        CallPolicy.MUTATION,
        "StudioCatalog",
        "notebook+artifact",
        "Updates title and optionally re-lists to return the artifact.",
        _p("artifacts", "rename"),
        (_b(RPCMethod.RENAME_ARTIFACT),),
    ),
    OperationSpec(
        Operation.ARTIFACT_EXPORT,
        CallPolicy.MUTATION,
        "StudioCatalog",
        "notebook+artifact",
        "Exports report/data-table representations to the supported Drive destination.",
        _p("artifacts", "export", "export_report", "export_data_table"),
        (_b(RPCMethod.EXPORT_ARTIFACT),),
    ),
    OperationSpec(
        Operation.ARTIFACT_DOWNLOAD,
        CallPolicy.READ,
        "StudioCatalog",
        "notebook+artifact",
        "Selects a representation, obtains its URL/content, and writes the requested format.",
        _p(
            "artifacts",
            "download_audio",
            "download_video",
            "download_infographic",
            "download_slide_deck",
            "download_report",
            "download_mind_map",
            "download_data_table",
            "download_quiz",
            "download_flashcards",
        ),
        (_b(RPCMethod.LIST_ARTIFACTS), _b(RPCMethod.GET_INTERACTIVE_HTML)),
        ("artifact content/download URL",),
        Disposition.COMPOSITE,
        ("_app/download.py",),
        "_app/download.py owns selection/conflict/filesystem choreography while the facade owns "
        "network reads. P4.2 passes one outer budget; P5 keeps one backend execution path.",
    ),
    OperationSpec(
        Operation.ARTIFACT_WAIT,
        CallPolicy.READ,
        "StudioCatalog",
        "notebook+artifact",
        "Polls the artifact catalog through the shared-leader polling registry.",
        _p("artifacts", "wait_for_completion"),
        (_b(RPCMethod.LIST_ARTIFACTS),),
        disposition=Disposition.COMPOSITE,
        app_authorities=("_app/generate_retry.py",),
        known_divergence="ArtifactsAPI.wait_for_completion and _app/generate_retry.py both own "
        "wait/retry timing. P4.2 passes one budget; shared-leader followers remain the explicit "
        "deadline exception.",
    ),
    OperationSpec(
        Operation.ARTIFACT_SUGGEST_REPORTS,
        CallPolicy.STATEFUL_START,
        "ReportFamilyService",
        "notebook",
        "Generates report-format suggestions.",
        _p("artifacts", "suggest_reports"),
        (_b(RPCMethod.GET_SUGGESTED_REPORTS),),
    ),
    OperationSpec(
        Operation.CHAT_ASK,
        CallPolicy.STATEFUL_START,
        "ChatService",
        "notebook+conversation+source-set",
        "Two-phase all-or-nothing operation: streamed query first, then conversation-id RPC. "
        "Citation anchors index answer_document.text, raw_response stays truncated, the byte cap "
        "fires pre-decode, the loop guard precedes the lock, and missing conversation id logs the "
        "full answer before ChatError. Cancellation between phases may leave an undiscoverable turn.",
        _p("chat", "ask"),
        (_b(RPCMethod.GET_LAST_CONVERSATION_ID), _b(RPCMethod.GET_NOTEBOOK)),
        ("QUERY_URL",),
        recency_effect="one GET_NOTEBOOK on every ask where source_ids is omitted",
    ),
    OperationSpec(
        Operation.CHAT_GET_CONVERSATION,
        CallPolicy.READ,
        "ChatService",
        "notebook",
        "Gets the most recent server conversation id.",
        _p("chat", "get_conversation_id"),
        (_b(RPCMethod.GET_LAST_CONVERSATION_ID),),
    ),
    OperationSpec(
        Operation.CHAT_GET_HISTORY,
        CallPolicy.READ,
        "ChatService",
        "notebook+conversation",
        "Loads turns and exposes raw or question/answer history projections.",
        _p("chat", "get_conversation_turns", "get_history"),
        (_b(RPCMethod.GET_CONVERSATION_TURNS), _b(RPCMethod.GET_LAST_CONVERSATION_ID)),
    ),
    OperationSpec(
        Operation.CHAT_DELETE_HISTORY,
        CallPolicy.MUTATION,
        "ChatService",
        "notebook+conversation",
        "Deletes the conversation turns.",
        _p("chat", "delete_conversation"),
        (_b(RPCMethod.DELETE_CONVERSATION),),
    ),
    OperationSpec(
        Operation.CHAT_CONFIGURE,
        CallPolicy.MUTATION,
        "ChatService",
        "notebook",
        "Reads or mutates chat settings embedded in the notebook payload.",
        _p("chat", "configure", "set_mode", "get_settings"),
        (_b(RPCMethod.RENAME_NOTEBOOK), _b(RPCMethod.GET_NOTEBOOK)),
        recency_effect="get_settings performs exactly one GET_NOTEBOOK",
    ),
    OperationSpec(
        Operation.CHAT_SAVE_NOTE,
        CallPolicy.MUTATION,
        "ChatService",
        "notebook+conversation-turn",
        "Saves an answer through the seven-element saved_from_chat note variant.",
        _p("chat", "save_answer_as_note"),
        (_b(RPCMethod.CREATE_NOTE, "saved_from_chat"),),
    ),
    OperationSpec(
        Operation.NOTE_LIST,
        CallPolicy.READ,
        "NoteService",
        "notebook",
        "Lists note rows while excluding note-backed mind maps.",
        _p("notes", "list"),
        (_b(RPCMethod.GET_NOTES_AND_MIND_MAPS),),
    ),
    OperationSpec(
        Operation.NOTE_GET,
        CallPolicy.READ,
        "NoteService",
        "notebook+note",
        "Selects an exact note from the mixed note/mind-map response.",
        _p("notes", "get", "get_or_none"),
        (_b(RPCMethod.GET_NOTES_AND_MIND_MAPS),),
    ),
    OperationSpec(
        Operation.NOTE_CREATE,
        CallPolicy.MUTATION,
        "NoteService",
        "notebook",
        "Creates a plain five-element note row without blind retry.",
        _p("notes", "create"),
        (_b(RPCMethod.CREATE_NOTE), _b(RPCMethod.CREATE_NOTE, "plain")),
    ),
    OperationSpec(
        Operation.NOTE_UPDATE,
        CallPolicy.MUTATION,
        "NoteService",
        "notebook+note",
        "Updates note title/content and discards the native echo.",
        _p("notes", "update"),
        (_b(RPCMethod.UPDATE_NOTE),),
    ),
    OperationSpec(
        Operation.NOTE_DELETE,
        CallPolicy.MUTATION,
        "NoteService",
        "notebook+note",
        "Deletes one note through the batch-capable native method.",
        _p("notes", "delete"),
        (_b(RPCMethod.DELETE_NOTE),),
    ),
    OperationSpec(
        Operation.MIND_MAP_LIST,
        CallPolicy.READ,
        "MindMapService",
        "notebook",
        "Combines note-backed JSON mind maps and interactive Studio mind maps.",
        _p("mind_maps", "list", "list_note_backed") + _p("notes", "list_mind_maps"),
        (_b(RPCMethod.GET_NOTES_AND_MIND_MAPS), _b(RPCMethod.LIST_ARTIFACTS)),
        disposition=Disposition.COMPOSITE,
    ),
    OperationSpec(
        Operation.MIND_MAP_GET,
        CallPolicy.READ,
        "MindMapService",
        "notebook+mind-map",
        "Auto-detects note-backed versus Studio representation and optionally loads a tree.",
        _p("mind_maps", "get", "get_or_none", "get_tree"),
        (
            _b(RPCMethod.GET_NOTES_AND_MIND_MAPS),
            _b(RPCMethod.LIST_ARTIFACTS),
            _b(RPCMethod.GET_INTERACTIVE_HTML),
        ),
        disposition=Disposition.COMPOSITE,
    ),
    OperationSpec(
        Operation.MIND_MAP_GENERATE_NOTE,
        CallPolicy.STATEFUL_START,
        "MindMapService",
        "notebook+source-set",
        "Generates JSON, then persists it as a plain note and returns a MindMap.",
        (_p("mind_maps", "generate")),
        (_b(RPCMethod.GENERATE_MIND_MAP), _b(RPCMethod.CREATE_NOTE, "plain")),
        disposition=Disposition.COMPOSITE,
        recency_effect="one GET_NOTEBOOK when source_ids is omitted",
    ),
    OperationSpec(
        Operation.MIND_MAP_GENERATE_INTERACTIVE,
        CallPolicy.STATEFUL_START,
        "MindMapFamilyService",
        "notebook+source-set",
        "Creates and optionally waits for an interactive Studio mind map.",
        _p("mind_maps", "generate"),
        _CREATE_ARTIFACT,
        disposition=Disposition.COMPOSITE,
        recency_effect="one GET_NOTEBOOK when source_ids is omitted",
    ),
    OperationSpec(
        Operation.MIND_MAP_UPDATE,
        CallPolicy.MUTATION,
        "MindMapService",
        "notebook+mind-map",
        "Auto-detects representation and routes title update to note or artifact mutation.",
        _p("mind_maps", "rename"),
        (_b(RPCMethod.UPDATE_NOTE), _b(RPCMethod.RENAME_ARTIFACT)),
        disposition=Disposition.COMPOSITE,
    ),
    OperationSpec(
        Operation.MIND_MAP_DELETE,
        CallPolicy.MUTATION,
        "MindMapService",
        "notebook+mind-map",
        "Auto-detects representation and routes delete to note or artifact mutation.",
        _p("mind_maps", "delete") + _p("notes", "delete_mind_map"),
        (_b(RPCMethod.DELETE_NOTE), _b(RPCMethod.DELETE_ARTIFACT)),
        disposition=Disposition.COMPOSITE,
    ),
    OperationSpec(
        Operation.RESEARCH_START,
        CallPolicy.STATEFUL_START,
        "ResearchService",
        "notebook",
        "Selects fast or deep discovery; neither start shape has a safe client token.",
        _p("research", "start"),
        (_b(RPCMethod.START_FAST_RESEARCH), _b(RPCMethod.START_DEEP_RESEARCH)),
    ),
    OperationSpec(
        Operation.RESEARCH_POLL,
        CallPolicy.READ,
        "ResearchService",
        "notebook+research-task",
        "Lists discovery jobs and resolves task aliases.",
        _p("research", "poll"),
        (_b(RPCMethod.POLL_RESEARCH),),
    ),
    OperationSpec(
        Operation.RESEARCH_WAIT,
        CallPolicy.READ,
        "ResearchService",
        "notebook+research-task",
        "Polls one research task under a bounded total timeout.",
        _p("research", "wait_for_completion"),
        (_b(RPCMethod.POLL_RESEARCH),),
        disposition=Disposition.COMPOSITE,
    ),
    OperationSpec(
        Operation.RESEARCH_CANCEL,
        CallPolicy.MUTATION,
        "ResearchService",
        "notebook+research-run",
        "Sets a research run to its terminal cancelled state.",
        _p("research", "cancel"),
        (_b(RPCMethod.CANCEL_RESEARCH),),
    ),
    OperationSpec(
        Operation.RESEARCH_IMPORT,
        CallPolicy.MUTATION,
        "ResearchService",
        "notebook+research-task",
        "Imports selected result rows without a safe dedupe token.",
        _p("research", "import_sources"),
        (_b(RPCMethod.IMPORT_RESEARCH),),
    ),
    OperationSpec(
        Operation.RESEARCH_IMPORT_VERIFY,
        CallPolicy.MUTATION,
        "ResearchService",
        "notebook+research-task",
        "Imports then verifies source appearance within one max_elapsed budget.",
        _p("research", "import_sources_with_verification"),
        (_b(RPCMethod.IMPORT_RESEARCH), _b(RPCMethod.GET_NOTEBOOK)),
        disposition=Disposition.COMPOSITE,
    ),
    OperationSpec(
        Operation.LABEL_LIST,
        CallPolicy.READ,
        "LabelService",
        "notebook",
        "Lists type-discriminated source labels.",
        _p("labels", "list"),
        (_b(RPCMethod.LIST_LABELS),),
    ),
    OperationSpec(
        Operation.LABEL_GET,
        CallPolicy.READ,
        "LabelService",
        "notebook+label",
        "Selects one label from the list response.",
        _p("labels", "get", "get_or_none"),
        (_b(RPCMethod.LIST_LABELS),),
    ),
    OperationSpec(
        Operation.LABEL_SOURCES,
        CallPolicy.READ,
        "LabelService",
        "notebook+label",
        "Resolves the label membership ids against the notebook source snapshot.",
        _p("labels", "sources"),
        (_b(RPCMethod.LIST_LABELS), _b(RPCMethod.GET_NOTEBOOK)),
        disposition=Disposition.COMPOSITE,
    ),
    OperationSpec(
        Operation.LABEL_GENERATE,
        CallPolicy.STATEFUL_START,
        "LabelService",
        "notebook",
        "Runs the auto-group mode of CreateLabel.",
        _p("labels", "generate"),
        (_b(RPCMethod.CREATE_LABEL),),
    ),
    OperationSpec(
        Operation.LABEL_CREATE,
        CallPolicy.MUTATION,
        "LabelService",
        "notebook",
        "Runs the manual-create mode of CreateLabel.",
        _p("labels", "create"),
        (_b(RPCMethod.CREATE_LABEL),),
    ),
    OperationSpec(
        Operation.LABEL_UPDATE,
        CallPolicy.MUTATION,
        "LabelService",
        "notebook+label",
        "Updates fields or source membership through field-mask variants.",
        _p("labels", "update", "rename", "set_emoji", "add_sources", "remove_sources"),
        (
            _b(RPCMethod.UPDATE_LABEL),
            _b(RPCMethod.UPDATE_LABEL, "add_sources"),
            _b(RPCMethod.UPDATE_LABEL, "remove_sources"),
        ),
    ),
    OperationSpec(
        Operation.LABEL_DELETE,
        CallPolicy.MUTATION,
        "LabelService",
        "notebook+label-set",
        "Deletes one or more labels.",
        _p("labels", "delete"),
        (_b(RPCMethod.DELETE_LABEL),),
    ),
    OperationSpec(
        Operation.COLLECTION_LIST,
        CallPolicy.READ,
        "CollectionService",
        "account",
        "Lists type-3 account labels as collections.",
        _p("collections", "list"),
        (_b(RPCMethod.LIST_LABELS),),
    ),
    OperationSpec(
        Operation.COLLECTION_GET,
        CallPolicy.READ,
        "CollectionService",
        "collection",
        "Selects one type-3 account label.",
        _p("collections", "get", "get_or_none"),
        (_b(RPCMethod.LIST_LABELS),),
    ),
    OperationSpec(
        Operation.COLLECTION_NOTEBOOKS,
        CallPolicy.READ,
        "CollectionService",
        "collection",
        "Resolves collection membership ids against notebook listing.",
        _p("collections", "notebooks"),
        (_b(RPCMethod.LIST_LABELS), _b(RPCMethod.LIST_NOTEBOOKS)),
        disposition=Disposition.COMPOSITE,
    ),
    OperationSpec(
        Operation.COLLECTION_CREATE,
        CallPolicy.MUTATION,
        "CollectionService",
        "account",
        "Creates a type-3 label with a null notebook parent.",
        _p("collections", "create"),
        (_b(RPCMethod.CREATE_LABEL),),
    ),
    OperationSpec(
        Operation.COLLECTION_UPDATE,
        CallPolicy.MUTATION,
        "CollectionService",
        "collection",
        "Renames or changes notebook membership through shared label variants.",
        _p("collections", "rename", "add_notebooks", "remove_notebooks"),
        (
            _b(RPCMethod.UPDATE_LABEL),
            _b(RPCMethod.UPDATE_LABEL, "add_notebooks"),
            _b(RPCMethod.UPDATE_LABEL, "remove_notebooks"),
        ),
    ),
    OperationSpec(
        Operation.COLLECTION_DELETE,
        CallPolicy.MUTATION,
        "CollectionService",
        "collection-set",
        "Deletes one or more type-3 labels.",
        _p("collections", "delete"),
        (_b(RPCMethod.DELETE_LABEL),),
    ),
    OperationSpec(
        Operation.SHARING_GET,
        CallPolicy.READ,
        "SharingService",
        "notebook",
        "Reads visibility and individual-user grants.",
        _p("sharing", "get_status"),
        (_b(RPCMethod.GET_SHARE_STATUS),),
    ),
    OperationSpec(
        Operation.SHARING_SET_PUBLIC,
        CallPolicy.MUTATION,
        "SharingService",
        "notebook",
        "Sets notebook visibility, then re-reads status.",
        _p("sharing", "set_public"),
        (_b(RPCMethod.SHARE_NOTEBOOK), _b(RPCMethod.GET_SHARE_STATUS)),
    ),
    OperationSpec(
        Operation.SHARING_SET_VIEW_LEVEL,
        CallPolicy.MUTATION,
        "SharingService",
        "notebook",
        "Uses MutateProject's share-access shape, then re-reads status.",
        _p("sharing", "set_view_level"),
        (_b(RPCMethod.RENAME_NOTEBOOK), _b(RPCMethod.GET_SHARE_STATUS)),
    ),
    OperationSpec(
        Operation.SHARING_UPDATE_USERS,
        CallPolicy.MUTATION,
        "SharingService",
        "notebook+user-grants",
        "Adds, replaces, updates, or removes individual grants and re-reads status.",
        _p("sharing", "add_user", "set_users", "update_user", "remove_user"),
        (_b(RPCMethod.SHARE_NOTEBOOK), _b(RPCMethod.GET_SHARE_STATUS)),
    ),
    OperationSpec(
        Operation.LEGACY_SHARE_ARTIFACT,
        CallPolicy.MUTATION,
        "LegacyShareManager",
        "notebook+artifact?",
        "Private legacy share-link mutator retained for compatibility internals.",
        native_bindings=(_b(RPCMethod.SHARE_ARTIFACT),),
        disposition=Disposition.LEGACY_PRIVATE,
    ),
    OperationSpec(
        Operation.SETTINGS_GET,
        CallPolicy.READ,
        "SettingsService",
        "account",
        "Gets the account settings row.",
        _p("settings", "get_user_settings", "get_output_language"),
        (_b(RPCMethod.GET_USER_SETTINGS),),
    ),
    OperationSpec(
        Operation.SETTINGS_SET_LANGUAGE,
        CallPolicy.MUTATION,
        "SettingsService",
        "account",
        "Sets output language and returns the server projection.",
        _p("settings", "set_output_language"),
        (_b(RPCMethod.SET_USER_SETTINGS),),
    ),
    OperationSpec(
        Operation.SETTINGS_GET_LIMITS,
        CallPolicy.READ,
        "SettingsService",
        "account",
        "Projects account quota and rollout limits from the settings row.",
        _p("settings", "get_account_limits"),
        (_b(RPCMethod.GET_USER_SETTINGS),),
    ),
)

DIVERGENCE_KINDS: Mapping[Operation, str] = {
    Operation.ARTIFACT_DOWNLOAD: "authority",
    Operation.ARTIFACT_RETRY: "authority",
    Operation.ARTIFACT_WAIT: "authority",
    Operation.SOURCE_REFRESH: "policy",
    Operation.SOURCE_WAIT: "authority",
}


# Public methods with no backend operation.  This is a reviewed disposition,
# not an ignore list: stale entries and newly discovered methods fail audit.
LOCAL_PUBLIC_METHODS: Mapping[str, str] = {
    "chat.cache_size": "local conversation-cache inspection",
    "chat.clear_cache": "local conversation-cache mutation",
    "chat.get_cached_turns": "local conversation-cache read",
    "chat.reset_after_open": "client lifecycle helper; resets loop-bound local state",
    "chat.set_bound_loop": "client lifecycle helper; binds loop-affine local state",
    "notebooks.get_share_url": "pure URL composition; performs no share mutation",
    "research.extract_report_urls": "pure report-markdown URL extraction helper",
    "research.select_cited_sources": "pure cited-source selection helper",
}


# The greenfield v0 omissions called out by the plan.  Pinning these names to
# catalog operations prevents a narrowed semantic inventory from silently
# deleting current behavior merely because it was absent from the greenfield.
GREENFIELD_OMISSION_COVERAGE: Mapping[str, tuple[Operation, ...]] = {
    "source listing": (Operation.SOURCE_LIST,),
    "settings and account limits": (Operation.SETTINGS_GET, Operation.SETTINGS_GET_LIMITS),
    "individual sharing": (Operation.SHARING_UPDATE_USERS,),
    "prompt suggestions": (Operation.NOTEBOOK_SUGGEST_PROMPTS,),
    "report suggestions": (Operation.ARTIFACT_SUGGEST_REPORTS,),
    "generic artifact actions": (
        Operation.ARTIFACT_GET,
        Operation.ARTIFACT_DELETE,
        Operation.ARTIFACT_RENAME,
    ),
    "artifact retry": (Operation.ARTIFACT_RETRY,),
    "mind maps": (Operation.MIND_MAP_LIST, Operation.MIND_MAP_GENERATE_NOTE),
    "data tables": (Operation.ARTIFACT_GENERATE_DATA_TABLE,),
    "exports and download formats": (Operation.ARTIFACT_EXPORT, Operation.ARTIFACT_DOWNLOAD),
}


APP_ORCHESTRATOR_DISPOSITIONS: Mapping[str, str] = {
    "_app/generate_retry.py": (
        "Keep adapter-neutral command composition; P4.2 passes one outer budget through the "
        "public facade and P5 removes duplicate backend retry/wait authority."
    ),
    "_app/source_wait.py": (
        "Keep typed outcome mapping; P4.2 makes the semantic SourceService the sole polling "
        "authority and passes the caller budget through SourcesAPI."
    ),
    "_app/download.py": (
        "Keep selection/conflict/filesystem choreography; P4.2 passes the outer budget and P5 "
        "makes family services the sole network execution authority."
    ),
    "_app/pagination.py": (
        "Keep the pure bounded slice as local-only application behavior; move the web fact that "
        "batchexecute does not paginate into web binding evidence when domains migrate."
    ),
}


def _qualname(stack: Sequence[str]) -> str:
    return ".".join(stack) if stack else "<module>"


def _attribute_parts(node: ast.AST) -> tuple[str, ...]:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return tuple(reversed(parts))


class _ReferenceCollector(ast.NodeVisitor):
    """Collect RPCMethod references and public facade calls with owners."""

    def __init__(self, relative_path: str, namespace_names: set[str]) -> None:
        self.relative_path = relative_path
        self.namespace_names = namespace_names
        self.stack: list[str] = []
        self.bindings: list[dict[str, set[str]]] = []
        self.literal_bindings: list[dict[str, str]] = []
        self.rpc_references: list[tuple[str, str]] = []
        self.rpc_calls: list[tuple[str, str | None, str]] = []
        self.public_calls: list[tuple[str, str]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.stack.append(node.name)
        self.bindings.append(defaultdict(set))
        literals: dict[str, str] = {}
        positional = [*node.args.posonlyargs, *node.args.args]
        if node.args.defaults:
            for arg, default in zip(
                positional[-len(node.args.defaults) :], node.args.defaults, strict=True
            ):
                if (value := _literal_string(default)) is not None:
                    literals[arg.arg] = value
        for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True):
            if default is not None and (value := _literal_string(default)) is not None:
                literals[arg.arg] = value
        self.literal_bindings.append(literals)
        self.generic_visit(node)
        self.literal_bindings.pop()
        self.bindings.pop()
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Attribute(self, node: ast.Attribute) -> None:
        parts = _attribute_parts(node)
        if len(parts) >= 2 and parts[-2] == "RPCMethod" and parts[-1] in RPCMethod.__members__:
            self.rpc_references.append((parts[-1], _qualname(self.stack)))
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if self.bindings:
            methods = {
                parts[-1]
                for item in ast.walk(node.value)
                if isinstance(item, ast.Attribute)
                and len(parts := _attribute_parts(item)) >= 2
                and parts[-2] == "RPCMethod"
                and parts[-1] in RPCMethod.__members__
            }
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.bindings[-1][target.id].update(methods)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if self.bindings and isinstance(node.target, ast.Name) and node.value is not None:
            methods = {
                parts[-1]
                for item in ast.walk(node.value)
                if isinstance(item, ast.Attribute)
                and len(parts := _attribute_parts(item)) >= 2
                and parts[-2] == "RPCMethod"
                and parts[-1] in RPCMethod.__members__
            }
            self.bindings[-1][node.target.id].update(methods)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        parts = _attribute_parts(node.func)
        if parts and parts[-1] in {"rpc_call", "_rpc_call"} and node.args:
            method_names = {
                item_parts[-1]
                for item in ast.walk(node.args[0])
                if isinstance(item, ast.Attribute)
                and len(item_parts := _attribute_parts(item)) >= 2
                and item_parts[-2] == "RPCMethod"
                and item_parts[-1] in RPCMethod.__members__
            }
            if isinstance(node.args[0], ast.Name):
                for scope in reversed(self.bindings):
                    if node.args[0].id in scope:
                        method_names.update(scope[node.args[0].id])
                        break
            variant: str | None = None
            for keyword in node.keywords:
                if keyword.arg == "operation_variant":
                    variant = _literal_string(keyword.value)
                    if variant is None and isinstance(keyword.value, ast.Name):
                        for scope in reversed(self.literal_bindings):
                            if keyword.value.id in scope:
                                variant = scope[keyword.value.id]
                                break
            for method_name in method_names:
                self.rpc_calls.append((method_name, variant, _qualname(self.stack)))
        for index, part in enumerate(parts[:-1]):
            if part in self.namespace_names:
                self.public_calls.append((f"{part}.{parts[index + 1]}", _qualname(self.stack)))
                break
        self.generic_visit(node)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def collect_public_namespace_methods() -> dict[str, str]:
    """Return every public callable on each annotated client namespace.

    Reading class annotations avoids constructing a client/HTTP session.  The
    MRO walk deliberately includes inherited public helpers, which is why
    ``chat.set_bound_loop`` and ``chat.reset_after_open`` cannot disappear from
    this inventory.
    """
    hints = typing.get_type_hints(NotebookLMClient)
    methods: dict[str, str] = {}
    for namespace, cls in sorted(hints.items()):
        if (
            namespace.startswith("_")
            or not inspect.isclass(cls)
            or not cls.__name__.endswith("API")
        ):
            continue
        for base in reversed(cls.__mro__):
            if base is object or not base.__module__.startswith("notebooklm"):
                continue
            for name, raw in vars(base).items():
                if name.startswith("_"):
                    continue
                target = raw.__func__ if isinstance(raw, (classmethod, staticmethod)) else raw
                if inspect.isfunction(target) or inspect.ismethoddescriptor(target):
                    methods[f"{namespace}.{name}"] = f"{base.__module__}.{base.__qualname__}"
    return dict(sorted(methods.items()))


def collect_app_callers(namespace_names: set[str] | None = None) -> dict[str, list[str]]:
    """AST-walk ``_app`` and map namespace method calls to their owners."""
    if namespace_names is None:
        namespace_names = {name.split(".", 1)[0] for name in collect_public_namespace_methods()}
    callers: dict[str, set[str]] = defaultdict(set)
    for path in sorted(APP_ROOT.glob("*.py")):
        relative = path.relative_to(SRC_ROOT).as_posix()
        collector = _ReferenceCollector(relative, namespace_names)
        collector.visit(_parse(path))
        for method, owner in collector.public_calls:
            callers[method].add(f"{relative}:{owner}")
    return {method: sorted(owners) for method, owners in sorted(callers.items())}


def collect_rpc_references() -> dict[RPCMethod, dict[str, list[str]]]:
    """AST-walk production code and classify current native references."""
    inventory: dict[RPCMethod, dict[str, set[str]]] = {
        method: defaultdict(set) for method in RPCMethod
    }
    for path in sorted(SRC_ROOT.rglob("*.py")):
        relative = path.relative_to(SRC_ROOT).as_posix()
        if relative in {"rpc/types.py", "_idempotency_policy.py"}:
            continue
        collector = _ReferenceCollector(relative, set())
        collector.visit(_parse(path))
        if relative.startswith("_row_adapters/"):
            role = "decoders"
        elif relative.startswith("_types/"):
            role = "projectors"
        elif relative.startswith("rpc/"):
            role = "protocol_support"
        else:
            role = "support_references"
        for method_name, owner in collector.rpc_references:
            inventory[RPCMethod[method_name]][role].add(f"{relative}:{owner}")
        if role == "support_references":
            for method_name, _variant, owner in collector.rpc_calls:
                inventory[RPCMethod[method_name]]["execution_authorities"].add(
                    f"{relative}:{owner}"
                )
    return {
        method: {role: sorted(sites) for role, sites in sorted(roles.items())}
        for method, roles in inventory.items()
    }


def collect_native_execution_sites() -> dict[NativeKey, list[str]]:
    """Return direct transport-reaching call sites per native method/variant."""
    sites: dict[NativeKey, set[str]] = defaultdict(set)
    for path in sorted(SRC_ROOT.rglob("*.py")):
        relative = path.relative_to(SRC_ROOT).as_posix()
        if relative.startswith(("_row_adapters/", "_types/", "rpc/")) or relative in {
            "_idempotency_policy.py",
        }:
            continue
        collector = _ReferenceCollector(relative, set())
        collector.visit(_parse(path))
        for method_name, variant, owner in collector.rpc_calls:
            sites[(RPCMethod[method_name], variant)].add(f"{relative}:{owner}")
    return {
        key: sorted(values)
        for key, values in sorted(sites.items(), key=lambda item: _native_key_text(item[0]))
    }


def _literal_string(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def collect_golden_evidence() -> tuple[dict[RPCMethod, list[list[str]]], dict[RPCMethod, str]]:
    """Read ``GOLDEN_COVERAGE``/``GOLDEN_EXEMPT`` without importing tests.

    The values are resolved from the guardrail AST so the catalog uses the
    existing authority while remaining importable outside pytest.
    """
    tree = _parse(GOLDEN_PATH)
    string_constants: dict[str, str] = {}
    tuple_constants: dict[str, tuple[str, ...]] = {}
    coverage_node: ast.Dict | None = None
    exempt_node: ast.Dict | None = None
    for statement in tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        value = statement.value
        for target in targets:
            if not isinstance(target, ast.Name) or value is None:
                continue
            text = _literal_string(value)
            if text is not None:
                string_constants[target.id] = text
            elif isinstance(value, ast.Tuple):
                items = tuple(_literal_string(item) or "" for item in value.elts)
                if all(items):
                    tuple_constants[target.id] = items
            if target.id == "GOLDEN_COVERAGE" and isinstance(value, ast.Dict):
                coverage_node = value
            if target.id == "GOLDEN_EXEMPT" and isinstance(value, ast.Dict):
                exempt_node = value

    def method_key(node: ast.AST | None) -> RPCMethod | None:
        parts = _attribute_parts(node) if node is not None else ()
        return RPCMethod[parts[-1]] if len(parts) >= 2 and parts[-2] == "RPCMethod" else None

    def pointer(node: ast.AST) -> list[str] | None:
        if isinstance(node, ast.Name):
            values = tuple_constants.get(node.id)
            return list(values) if values else None
        if not isinstance(node, ast.Tuple) or len(node.elts) != 2:
            return None
        values: list[str] = []
        for item in node.elts:
            if isinstance(item, ast.Name):
                value = string_constants.get(item.id)
            else:
                value = _literal_string(item)
            if value is None:
                return None
            values.append(value)
        return values

    coverage: dict[RPCMethod, list[list[str]]] = {}
    if coverage_node is not None:
        for key_node, value_node in zip(coverage_node.keys, coverage_node.values, strict=True):
            method = method_key(key_node)
            if method is None or not isinstance(value_node, (ast.Tuple, ast.List)):
                continue
            pointers = [resolved for item in value_node.elts if (resolved := pointer(item))]
            coverage[method] = pointers

    exemptions: dict[RPCMethod, str] = {}
    if exempt_node is not None:
        for key_node, value_node in zip(exempt_node.keys, exempt_node.values, strict=True):
            method = method_key(key_node)
            if method is None:
                continue
            if isinstance(value_node, ast.Name):
                reason = string_constants.get(value_node.id)
            else:
                reason = _literal_string(value_node)
            if reason is not None:
                exemptions[method] = reason
    return coverage, exemptions


def _native_key_text(binding: NativeKey) -> str:
    method, variant = binding
    return f"{method.name}:{variant if variant is not None else '<default>'}"


def _override_honored() -> tuple[bool, str]:
    """Prove the current centralized executor resolves runtime RPC overrides."""
    tree = _parse(RPC_EXECUTOR_PATH)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _attribute_parts(node.func)[-1:] == ("resolve_rpc_id",)
    ]
    return len(calls) == 1, "_rpc_executor.py:RpcExecutor._execute_once"


def _normalize_registry_omissions(
    snapshot: Mapping[str, Any] | None,
) -> list[dict[str, str]]:
    """Project ``capture_rpc_registry.py --json`` unmapped rows deterministically."""
    if snapshot is None:
        return []
    raw = snapshot.get("unmapped", {})
    if not isinstance(raw, Mapping):
        raise ValueError("rpc registry snapshot 'unmapped' must be an object")
    rows: list[dict[str, str]] = []
    for rpc_id, value in sorted(raw.items()):
        if not isinstance(rpc_id, str) or not isinstance(value, Mapping):
            raise ValueError("rpc registry snapshot contains a malformed unmapped row")
        method = value.get("method")
        family = value.get("family")
        if not isinstance(method, str) or not isinstance(family, str):
            raise ValueError("rpc registry unmapped rows require string method/family")
        disposition = _OMISSION_DISPOSITIONS.get(family)
        if disposition is None:
            raise ValueError(f"rpc registry unmapped row has unknown family {family!r}")
        rows.append(
            {
                "rpc_id": rpc_id,
                "method": method,
                "family": family,
                "disposition": disposition,
            }
        )
    return rows


def load_rpc_registry_evidence(path: Path = RPC_REGISTRY_EVIDENCE_PATH) -> Mapping[str, Any]:
    """Load the committed scrubbed output that makes baseline derivation offline.

    Refresh it from a live authenticated capture with the ``source_command``
    recorded in the file, retaining only the non-secret fields in the evidence
    schema.  The ADR-0022 derive never performs network or credential I/O.
    """
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("committed RPC registry evidence must be a JSON object")
    return value


def audit_rpc_registry_evidence(snapshot: Mapping[str, Any]) -> list[str]:
    """Validate the committed capture is non-vacuous, internally consistent evidence."""
    errors: list[str] = []
    if snapshot.get("schema_version") != 1:
        errors.append("RPC registry evidence schema_version must be 1")
    counts = snapshot.get("counts")
    if not isinstance(counts, Mapping):
        return [*errors, "RPC registry evidence counts must be an object"]
    for key in ("confirmed", "absent", "present_unparsed", "unmapped"):
        rows = snapshot.get(key)
        if not isinstance(rows, Mapping):
            errors.append(f"RPC registry evidence {key!r} must be an object")
            continue
        if counts.get(key) != len(rows):
            errors.append(
                f"RPC registry evidence count mismatch for {key}: "
                f"counts={counts.get(key)!r}, rows={len(rows)}"
            )
    confirmed = snapshot.get("confirmed")
    if counts.get("confirmed") != len(RPCMethod):
        errors.append(
            "RPC registry evidence confirmed count must cover every current RPCMethod "
            f"({counts.get('confirmed')!r} != {len(RPCMethod)})"
        )
    if isinstance(confirmed, Mapping):
        expected_names = {method.value: method.name for method in RPCMethod}
        captured_names = {
            rpc_id: row.get("name")
            for rpc_id, row in confirmed.items()
            if isinstance(rpc_id, str) and isinstance(row, Mapping)
        }
        if captured_names != expected_names:
            errors.append("RPC registry evidence confirmed rows do not match current RPCMethod ids")
    if snapshot.get("absent"):
        errors.append("RPC registry evidence reports active RPC ids absent from the live bundle")
    if snapshot.get("present_unparsed"):
        errors.append("RPC registry evidence reports active RPC ids present but unparsed")
    try:
        omissions = _normalize_registry_omissions(snapshot)
    except ValueError as exc:
        errors.append(str(exc))
    else:
        if not omissions:
            errors.append("RPC registry omissions evidence must not be empty/vacuous")
        if not any(row["family"] == "current" for row in omissions):
            errors.append("RPC registry evidence must inventory current-family product omissions")
    return errors


def audit_live_registry_against_evidence(snapshot: Mapping[str, Any]) -> list[str]:
    """Compare a fresh ``capture_rpc_registry --json`` result to committed evidence."""
    errors: list[str] = []
    if snapshot.get("absent"):
        errors.append("fresh RPC registry capture reports active ids absent from the live bundle")
    if snapshot.get("present_unparsed"):
        errors.append("fresh RPC registry capture reports active ids present but unparsed")
    try:
        fresh = _normalize_registry_omissions(snapshot)
        committed = _normalize_registry_omissions(load_rpc_registry_evidence())
    except ValueError as exc:
        return [*errors, str(exc)]
    if fresh != committed:
        fresh_ids = {row["rpc_id"] for row in fresh}
        committed_ids = {row["rpc_id"] for row in committed}
        errors.append(
            "fresh live RPC omissions differ from committed evidence; refresh "
            "scripts/operation_catalog_rpc_registry.json "
            f"(added={sorted(fresh_ids - committed_ids)}, "
            f"removed={sorted(committed_ids - fresh_ids)})"
        )
    return errors


def audit_operation_catalog() -> list[str]:
    """Return catalog completeness/staleness errors; an empty list is green."""
    errors: list[str] = []
    specs_by_operation: dict[Operation, OperationSpec] = {}
    for spec in OPERATION_SPECS:
        if spec.operation in specs_by_operation:
            errors.append(f"duplicate operation spec: {spec.operation.value}")
        specs_by_operation[spec.operation] = spec

    described_divergences = {
        spec.operation for spec in OPERATION_SPECS if spec.known_divergence is not None
    }
    if described_divergences != set(DIVERGENCE_KINDS):
        errors.append(
            "known divergence descriptions/kinds disagree: "
            f"described={sorted(item.value for item in described_divergences)}, "
            f"kinds={sorted(item.value for item in DIVERGENCE_KINDS)}"
        )

    missing_operations = sorted(
        operation.value for operation in set(Operation) - specs_by_operation.keys()
    )
    stale_operations = sorted(
        operation.value for operation in specs_by_operation.keys() - set(Operation)
    )
    if missing_operations:
        errors.append(f"Operation members missing specs: {missing_operations}")
    if stale_operations:
        errors.append(f"specs for unknown Operation members: {stale_operations}")

    native_rows = {
        (method, variant) for method, variant, _entry in IDEMPOTENCY_REGISTRY.iter_entries()
    }
    mapped_native: set[NativeKey] = set()
    for spec in OPERATION_SPECS:
        mapped_native.update(spec.native_bindings)
    missing_native = sorted(_native_key_text(row) for row in native_rows - mapped_native)
    stale_native = sorted(_native_key_text(row) for row in mapped_native - native_rows)
    if missing_native:
        errors.append(f"active RPC method/variant rows without disposition: {missing_native}")
    if stale_native:
        errors.append(f"catalog bindings absent from idempotency registry: {stale_native}")

    discovered_public = set(collect_public_namespace_methods())
    semantic_public: set[str] = set()
    for spec in OPERATION_SPECS:
        semantic_public.update(spec.public_methods)
    mapped_public = set(LOCAL_PUBLIC_METHODS) | semantic_public
    missing_public = sorted(discovered_public - mapped_public)
    stale_public = sorted(mapped_public - discovered_public)
    conflicting_public = sorted(set(LOCAL_PUBLIC_METHODS) & semantic_public)
    if missing_public:
        errors.append(f"public namespace methods without disposition: {missing_public}")
    if stale_public:
        errors.append(f"catalog public mappings no longer exist: {stale_public}")
    if conflicting_public:
        errors.append(
            f"public methods have both semantic and local dispositions: {conflicting_public}"
        )

    app_callers = collect_app_callers({method.split(".", 1)[0] for method in discovered_public})
    unknown_app_calls = sorted(set(app_callers) - discovered_public)
    if unknown_app_calls:
        errors.append(
            f"_app calls namespace methods absent from public inventory: {unknown_app_calls}"
        )

    for method, variant, entry in IDEMPOTENCY_REGISTRY.iter_entries():
        if entry.policy.value == "unclassified":
            errors.append(
                f"native idempotency row remains unclassified: "
                f"{_native_key_text((method, variant))}"
            )

    override_honored, _evidence = _override_honored()
    if not override_honored:
        errors.append("RpcExecutor must call resolve_rpc_id exactly once per binding path")

    for feature, operations in GREENFIELD_OMISSION_COVERAGE.items():
        absent = [
            operation.value for operation in operations if operation not in specs_by_operation
        ]
        if absent:
            errors.append(f"greenfield omission {feature!r} lacks catalog operations: {absent}")
    errors.extend(audit_rpc_registry_evidence(load_rpc_registry_evidence()))
    return errors


def build_operation_catalog(
    rpc_registry_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the deterministic ADR-0022 operation-catalog projection."""
    errors = audit_operation_catalog()
    if errors:
        raise ValueError("operation catalog is incomplete:\n- " + "\n- ".join(errors))
    if rpc_registry_snapshot is None:
        rpc_registry_snapshot = load_rpc_registry_evidence()

    public_origins = collect_public_namespace_methods()
    app_callers = collect_app_callers({name.split(".", 1)[0] for name in public_origins})
    rpc_references = collect_rpc_references()
    golden_coverage, golden_exempt = collect_golden_evidence()
    override_honored, override_evidence = _override_honored()
    native_execution_sites = collect_native_execution_sites()

    specs_by_native: dict[NativeKey, list[OperationSpec]] = defaultdict(list)
    specs_by_public: dict[str, list[OperationSpec]] = defaultdict(list)
    for spec in OPERATION_SPECS:
        for binding in spec.native_bindings:
            specs_by_native[binding].append(spec)
        for method in spec.public_methods:
            specs_by_public[method].append(spec)

    operation_rows: list[dict[str, Any]] = []
    for spec in sorted(OPERATION_SPECS, key=lambda item: item.operation.value):
        native_methods = {method for method, _variant in spec.native_bindings}
        authorities = {
            site
            for binding in spec.native_bindings
            for site in native_execution_sites.get(binding, [])
        }
        authorities.update(spec.app_authorities)
        decoders = {
            site
            for method in native_methods
            for role in ("decoders", "projectors")
            for site in rpc_references[method].get(role, [])
        }
        callers = {
            caller
            for public_method in spec.public_methods
            for caller in app_callers.get(public_method, [])
        }
        operation_rows.append(
            {
                "key": spec.operation.value,
                "owner": spec.owner,
                "policy": spec.policy.value,
                "route_context": spec.route_context,
                "disposition": spec.disposition.value,
                "public_methods": sorted(spec.public_methods),
                "app_callers": sorted(callers),
                "native_bindings": sorted(
                    _native_key_text(binding) for binding in spec.native_bindings
                ),
                "web_paths": sorted(spec.web_paths),
                "response_decoders_projectors": sorted(decoders),
                "execution_authorities": sorted(authorities),
                "composite_behavior": spec.composite_behavior,
                "known_divergence": spec.known_divergence,
                "known_divergence_kind": DIVERGENCE_KINDS.get(spec.operation),
                "recency_effect": spec.recency_effect,
            }
        )

    native_rows: list[dict[str, Any]] = []
    entries = sorted(
        IDEMPOTENCY_REGISTRY.iter_entries(),
        key=lambda row: (row[0].name, row[1] is not None, row[1] or ""),
    )
    for method, variant, entry in entries:
        specs = specs_by_native[(method, variant)]
        if method in golden_coverage:
            evidence_disposition = "golden_covered"
            evidence: Any = golden_coverage[method]
        elif method in golden_exempt:
            evidence_disposition = "golden_exempt"
            evidence = golden_exempt[method]
        else:
            evidence_disposition = "not_recorded"
            evidence = []
        native_rows.append(
            {
                "key": _native_key_text((method, variant)),
                "rpc_method": method.name,
                "rpc_id": method.value,
                "variant": variant,
                "idempotency_policy": entry.policy.value,
                "idempotency_notes": entry.notes,
                "semantic_operations": sorted(spec.operation.value for spec in specs),
                "owners": sorted({spec.owner for spec in specs}),
                "route_contexts": sorted({spec.route_context for spec in specs}),
                "execution_authorities": native_execution_sites.get((method, variant), []),
                "response_decoders": rpc_references[method].get("decoders", []),
                "response_projectors": rpc_references[method].get("projectors", []),
                "golden_disposition": evidence_disposition,
                "golden_evidence": evidence,
                "override_honored": override_honored,
                "override_evidence": override_evidence,
            }
        )

    public_rows: dict[str, dict[str, Any]] = {}
    for method, origin in public_origins.items():
        specs = specs_by_public.get(method, [])
        if specs:
            public_rows[method] = {
                "disposition": "semantic",
                "operations": sorted(spec.operation.value for spec in specs),
                "declared_by": origin,
                "app_callers": app_callers.get(method, []),
            }
        else:
            public_rows[method] = {
                "disposition": "local_only",
                "operations": [],
                "reason": LOCAL_PUBLIC_METHODS[method],
                "declared_by": origin,
                "app_callers": app_callers.get(method, []),
            }

    divergences = [
        {
            "operation": spec.operation.value,
            "disposition": "known_divergence",
            "kind": DIVERGENCE_KINDS[spec.operation],
            "detail": spec.known_divergence,
        }
        for spec in sorted(OPERATION_SPECS, key=lambda item: item.operation.value)
        if spec.known_divergence is not None
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "operations": operation_rows,
        "native_bindings": native_rows,
        "public_methods": public_rows,
        "app_callers": app_callers,
        "app_orchestrator_dispositions": dict(sorted(APP_ORCHESTRATOR_DISPOSITIONS.items())),
        "greenfield_omission_coverage": {
            feature: sorted(operation.value for operation in operations)
            for feature, operations in sorted(GREENFIELD_OMISSION_COVERAGE.items())
        },
        "known_divergences": divergences,
        "product_omissions": {
            "source": "scripts/operation_catalog_rpc_registry.json",
            "refresh_command": "uv run python scripts/capture_rpc_registry.py --json",
            "freshness_check": (
                "uv run python scripts/audit_operation_catalog.py "
                "--rpc-registry-json /tmp/rpc-registry.json"
            ),
            "captured_on": rpc_registry_snapshot.get("captured_on"),
            "capture_counts": rpc_registry_snapshot.get("counts", {}),
            "unmapped_live_rpcs": _normalize_registry_omissions(rpc_registry_snapshot),
            "excluded_families": {
                "enterprise": "NotebookLM Enterprise/Agentspace, not consumer-callable",
                "other": "unclassified live service family; investigate before adding",
            },
        },
        "raw_rpc_escape_hatch": {
            "member": "NotebookLMClient.rpc_call",
            "disposition": "explicitly excluded legacy web-only escape hatch",
        },
    }


def _load_snapshot(path: Path | None) -> Mapping[str, Any] | None:
    if path is None:
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("rpc registry snapshot must be a JSON object")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rpc-registry-json",
        type=Path,
        help="optional output from capture_rpc_registry.py --json for the omissions projection",
    )
    parser.add_argument("--json", action="store_true", help="print the derived catalog as JSON")
    args = parser.parse_args(argv)

    snapshot = _load_snapshot(args.rpc_registry_json)
    errors = audit_operation_catalog()
    if snapshot is not None:
        errors.extend(audit_live_registry_against_evidence(snapshot))
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(build_operation_catalog(snapshot), indent=2))
    else:
        catalog = build_operation_catalog(snapshot)
        print(
            "operation catalog: "
            f"{len(catalog['operations'])} semantic operations, "
            f"{len(catalog['native_bindings'])} native rows, "
            f"{len(catalog['public_methods'])} public namespace methods, "
            f"{len(catalog['known_divergences'])} known divergences"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
