"""Exact transport authorities, discriminators, and recency contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from notebooklm._operations import Operation
from notebooklm.rpc import RPCMethod

if __package__:
    from ._operation_catalog_specs import OPERATION_SPECS, NativeKey, _b, _p
else:  # pragma: no cover - direct script execution
    from _operation_catalog_specs import OPERATION_SPECS, NativeKey, _b, _p


@dataclass(frozen=True, slots=True)
class AuthorityRule:
    """One exact current execution authority for a semantic operation."""

    site: str
    discriminator: str


def _rules(*rows: tuple[str, str]) -> tuple[AuthorityRule, ...]:
    return tuple(AuthorityRule(*row) for row in rows)


@dataclass(frozen=True, slots=True)
class AppAuthorityRule:
    """One application/public-helper execution authority."""

    site: str
    binding: str
    discriminator: str


@dataclass(frozen=True, slots=True)
class AppAuthoritySourceContract:
    """Fail-closed source evidence for a delegated application authority."""

    required_calls: tuple[tuple[str, ...], ...]
    internal_caller: str
    caller_target: tuple[str, ...]
    public_export: str


# A shared RPC method is not enough to identify an operation.  These rules
# allocate its direct transport callsites using the public intent/payload
# discriminator that makes the binding semantic.  Unshared bindings are
# derived directly; every shared operation/binding must appear here.
SHARED_RPC_AUTHORITY_RULES: dict[tuple[Operation, NativeKey], tuple[AuthorityRule, ...]] = {
    (Operation.NOTEBOOK_LIST, _b(RPCMethod.LIST_NOTEBOOKS)): _rules(
        ("_web/backend.py:WebRpcBackend._notebook_list", "public=notebooks.list")
    ),
    (Operation.NOTEBOOK_CREATE, _b(RPCMethod.LIST_NOTEBOOKS)): _rules(
        (
            "_web/backend.py:WebRpcBackend._notebook_list",
            "pre-create baseline/probe or quota verification",
        )
    ),
    (Operation.COLLECTION_NOTEBOOKS, _b(RPCMethod.LIST_NOTEBOOKS)): _rules(
        ("_web/backend.py:WebRpcBackend._notebook_list", "collection membership expansion")
    ),
    (Operation.NOTEBOOK_GET, _b(RPCMethod.GET_NOTEBOOK)): _rules(
        ("_web/backend.py:WebRpcBackend._notebook_get", "typed notebook lookup"),
        ("_notebooks.py:NotebooksAPI.get_raw", "raw/source-id notebook lookup"),
    ),
    (Operation.NOTEBOOK_CREATE, _b(RPCMethod.GET_NOTEBOOK)): _rules(
        ("_web/backend.py:WebRpcBackend._notebook_get", "null-timestamp backfill only")
    ),
    (Operation.NOTEBOOK_UPDATE, _b(RPCMethod.GET_NOTEBOOK)): _rules(
        ("_web/backend.py:WebRpcBackend._notebook_update", "unconditional post-mutation read")
    ),
    (Operation.NOTEBOOK_METADATA, _b(RPCMethod.GET_NOTEBOOK)): _rules(
        ("_web/backend.py:WebRpcBackend._notebook_get", "metadata notebook branch"),
        ("_source/listing.py:SourceLister.list", "metadata source branch"),
    ),
    (Operation.NOTEBOOK_SUGGEST_PROMPTS, _b(RPCMethod.GET_NOTEBOOK)): _rules(
        ("_notebooks.py:NotebooksAPI.get_raw", "source_ids is None")
    ),
    (Operation.SOURCE_LIST, _b(RPCMethod.GET_NOTEBOOK)): _rules(
        ("_web/backend.py:WebRpcBackend._source_list", "public=sources.list")
    ),
    (Operation.SOURCE_GET, _b(RPCMethod.GET_NOTEBOOK)): _rules(
        ("_web/backend.py:WebRpcBackend._source_get", "select exact source id")
    ),
    (Operation.SOURCE_ADD_URL, _b(RPCMethod.GET_NOTEBOOK)): _rules(
        ("_source/listing.py:SourceLister.list", "unconditional baseline plus ambiguity probes")
    ),
    (Operation.SOURCE_ADD_DRIVE, _b(RPCMethod.GET_NOTEBOOK)): _rules(
        ("_source/listing.py:SourceLister.list", "unconditional baseline plus ambiguity probes")
    ),
    (Operation.SOURCE_ADD_FILE, _b(RPCMethod.GET_NOTEBOOK)): _rules(
        ("_source/listing.py:SourceLister.list", "unconditional baseline plus registration probes")
    ),
    (Operation.SOURCE_WAIT, _b(RPCMethod.GET_NOTEBOOK)): _rules(
        ("_source/listing.py:SourceLister.list", "one shared source snapshot per poll tick")
    ),
    (Operation.CHAT_ASK, _b(RPCMethod.GET_NOTEBOOK)): _rules(
        ("_notebooks.py:NotebooksAPI.get_raw", "source_ids is None")
    ),
    (Operation.CHAT_CONFIGURE, _b(RPCMethod.GET_NOTEBOOK)): _rules(
        ("_chat/api.py:ChatAPI.get_settings", "public=chat.get_settings only")
    ),
    (Operation.RESEARCH_IMPORT_VERIFY, _b(RPCMethod.GET_NOTEBOOK)): _rules(
        ("_source/listing.py:SourceLister.list", "pre-import baseline and verification probes")
    ),
    (Operation.LABEL_SOURCES, _b(RPCMethod.GET_NOTEBOOK)): _rules(
        ("_source/listing.py:SourceLister.list", "resolve label source ids")
    ),
}

_GENERATION_OPERATIONS = {
    Operation.ARTIFACT_GENERATE_AUDIO: "artifact_type=audio",
    Operation.ARTIFACT_GENERATE_VIDEO: "artifact_type=video|cinematic-video",
    Operation.ARTIFACT_GENERATE_REPORT: "artifact_type=report|study-guide",
    Operation.ARTIFACT_GENERATE_QUIZ: "artifact_type=quiz",
    Operation.ARTIFACT_GENERATE_FLASHCARDS: "artifact_type=flashcards",
    Operation.ARTIFACT_GENERATE_INFOGRAPHIC: "artifact_type=infographic",
    Operation.ARTIFACT_GENERATE_SLIDE_DECK: "artifact_type=slide-deck",
    Operation.ARTIFACT_GENERATE_DATA_TABLE: "artifact_type=data-table",
}
for _operation, _discriminator in _GENERATION_OPERATIONS.items():
    SHARED_RPC_AUTHORITY_RULES[(_operation, _b(RPCMethod.CREATE_ARTIFACT))] = _rules(
        ("_artifact/generation.py:ArtifactGenerationService._call_generate", _discriminator)
    )
    SHARED_RPC_AUTHORITY_RULES[(_operation, _b(RPCMethod.GET_NOTEBOOK))] = _rules(
        ("_notebooks.py:NotebooksAPI.get_raw", "source_ids is None")
    )

SHARED_RPC_AUTHORITY_RULES.update(
    {
        (Operation.MIND_MAP_GENERATE_INTERACTIVE, _b(RPCMethod.CREATE_ARTIFACT)): _rules(
            ("_mind_maps_api.py:MindMapsAPI.generate", "kind=INTERACTIVE")
        ),
        (Operation.MIND_MAP_GENERATE_INTERACTIVE, _b(RPCMethod.GET_NOTEBOOK)): _rules(
            ("_notebooks.py:NotebooksAPI.get_raw", "kind=INTERACTIVE and source_ids is None")
        ),
        (Operation.ARTIFACT_GENERATE_MIND_MAP, _b(RPCMethod.GENERATE_MIND_MAP)): _rules(
            ("_artifact/generation.py:ArtifactGenerationService.generate_mind_map", "legacy facade")
        ),
        (Operation.MIND_MAP_GENERATE_NOTE, _b(RPCMethod.GENERATE_MIND_MAP)): _rules(
            (
                "_artifact/generation.py:ArtifactGenerationService.generate_mind_map",
                "kind=NOTE_BACKED",
            )
        ),
        (Operation.ARTIFACT_GENERATE_MIND_MAP, _b(RPCMethod.CREATE_NOTE, "plain")): _rules(
            ("_note_service.py:NoteService.create_note", "persist generated JSON")
        ),
        (Operation.NOTE_CREATE, _b(RPCMethod.CREATE_NOTE, "plain")): _rules(
            ("_note_service.py:NoteService.create_note", "public=notes.create")
        ),
        (Operation.MIND_MAP_GENERATE_NOTE, _b(RPCMethod.CREATE_NOTE, "plain")): _rules(
            ("_note_service.py:NoteService.create_note", "kind=NOTE_BACKED persistence")
        ),
        (Operation.ARTIFACT_GENERATE_MIND_MAP, _b(RPCMethod.GET_NOTEBOOK)): _rules(
            ("_notebooks.py:NotebooksAPI.get_raw", "source_ids is None")
        ),
        (Operation.MIND_MAP_GENERATE_NOTE, _b(RPCMethod.GET_NOTEBOOK)): _rules(
            ("_notebooks.py:NotebooksAPI.get_raw", "kind=NOTE_BACKED and source_ids is None")
        ),
    }
)


NON_RPC_AUTHORITY_RULES: Mapping[Operation, tuple[tuple[str, str, str, str], ...]] = {
    Operation.CHAT_ASK: (
        (
            "stream",
            "streamed_query",
            "_chat/transport.py:chat_aware_authed_post",
            "GenerateFreeFormStreamed POST; response bytes are incrementally buffered",
        ),
    ),
    Operation.SOURCE_ADD_FILE: (
        (
            "download",
            "drive_https_download",
            "_source/drive_import.py:DriveFetcher._request",
            "sources.add_drive_file only: cookie-authenticated Drive GET before file upload",
        ),
        (
            "upload",
            "resumable_upload",
            "_source/upload.py:SourceUploadPipeline.start_resumable_upload",
            "create resumable upload session",
        ),
        (
            "upload",
            "resumable_upload",
            "_source/upload.py:SourceUploadPipeline.upload_file_streaming._do_finalize",
            "stream bytes and finalize session",
        ),
        (
            "upload",
            "resumable_upload",
            "_source/upload.py:SourceUploadPipeline.cancel_upload_session",
            "pre-finalize cancellation cleanup",
        ),
    ),
    Operation.ARTIFACT_DOWNLOAD: (
        (
            "download",
            "artifact_https_download",
            "_artifact/downloads.py:ArtifactDownloadService.download_url",
            "HTTPS media representation; inline/locally formatted representations skip this path",
        ),
    ),
}

# Every manually allocated non-RPC authority must contain these transport calls,
# and every contract row must be allocated to exactly one semantic operation.
NON_RPC_SOURCE_CONTRACTS: Mapping[str, tuple[tuple[str, ...], ...]] = {
    "_chat/transport.py:chat_aware_authed_post": (("perform_authed_post",),),
    "_source/drive_import.py:DriveFetcher._request": (("stream",),),
    "_source/upload.py:SourceUploadPipeline.start_resumable_upload": (("post",),),
    "_source/upload.py:SourceUploadPipeline.upload_file_streaming._do_finalize": (
        ("stream_upload",),
    ),
    "_source/upload.py:SourceUploadPipeline.cancel_upload_session": (("post",),),
    "_artifact/downloads.py:ArtifactDownloadService.download_url": (
        ("get_guarded",),
        ("stream",),
    ),
}


_GENERATION_RETRY_AUTHORITY = AppAuthorityRule(
    "artifacts.py:with_rate_limit_retry",
    "public_helper",
    "exported retry helper re-invokes the internal facade generation after rate limiting; "
    "P4.2 removes the internal use while preserving the public helper",
)


APP_OPERATION_AUTHORITIES: Mapping[Operation, tuple[AppAuthorityRule, ...]] = {
    **dict.fromkeys(
        (*_GENERATION_OPERATIONS, Operation.ARTIFACT_REVISE_SLIDE),
        (_GENERATION_RETRY_AUTHORITY,),
    ),
    Operation.ARTIFACT_DOWNLOAD: (
        AppAuthorityRule(
            "_app/download.py:execute_download",
            "public_facade",
            "selection/conflict/filesystem choreography; each facade call owns its own budget",
        ),
    ),
}


# The generation retry authority lives in an exported public helper, not in the
# thin ``_app`` wrapper that calls it.  Pin both the helper's loop ingredients
# and the one production-internal call edge so moving, bypassing, or duplicating
# the loop changes the generated catalog rather than silently preserving a stale
# hand-authored site.
APP_AUTHORITY_SOURCE_CONTRACTS: Mapping[str, AppAuthoritySourceContract] = {
    _GENERATION_RETRY_AUTHORITY.site: AppAuthoritySourceContract(
        required_calls=(("generate_fn",), ("calculate_backoff_delay",), ("sleep_func",)),
        internal_caller="_app/generate_retry.py:generate_with_retry",
        caller_target=("artifact_retry", "with_rate_limit_retry"),
        public_export="with_rate_limit_retry",
    )
}


@dataclass(frozen=True, slots=True)
class RecencyRule:
    """Structured GET_NOTEBOOK side-effect contract for a public call case."""

    public_methods: tuple[str, ...]
    minimum_calls: int
    maximum_calls: int | None
    unit: str
    condition: str
    authority_sites: tuple[str, ...] = ()


_GET_TYPED = "_web/backend.py:WebRpcBackend._notebook_get"
_UPDATE_TYPED = "_web/backend.py:WebRpcBackend._notebook_update"
_GET_RAW = "_notebooks.py:NotebooksAPI.get_raw"
_GET_SOURCES = "_source/listing.py:SourceLister.list"
_GET_SOURCE_LIST = "_web/backend.py:WebRpcBackend._source_list"
_GET_SOURCE = "_web/backend.py:WebRpcBackend._source_get"


RECENCY_CONTRACTS: dict[Operation, tuple[RecencyRule, ...]] = {
    Operation.NOTEBOOK_GET: (
        RecencyRule(
            _p("notebooks", "get", "get_or_none", "get_raw", "get_source_ids"),
            1,
            1,
            "public_call",
            "always",
            (_GET_TYPED, _GET_RAW),
        ),
    ),
    Operation.NOTEBOOK_CREATE: (
        RecencyRule(
            _p("notebooks", "create"),
            0,
            1,
            "public_call",
            "only when the created row has a null timestamp requiring backfill",
            (_GET_TYPED,),
        ),
    ),
    Operation.NOTEBOOK_UPDATE: (
        RecencyRule(
            _p("notebooks", "update", "rename", "set_emoji"),
            1,
            1,
            "public_call",
            "always after a successful mutation",
            (_UPDATE_TYPED,),
        ),
    ),
    Operation.NOTEBOOK_METADATA: (
        RecencyRule(
            _p("notebooks", "get_metadata"),
            2,
            2,
            "public_call",
            "always: concurrent notebook.get plus source listing",
            (_GET_TYPED, _GET_SOURCES),
        ),
    ),
    Operation.NOTEBOOK_SUGGEST_PROMPTS: (
        RecencyRule(
            _p("notebooks", "suggest_prompts"),
            0,
            1,
            "public_call",
            "one only when source_ids is omitted",
            (_GET_RAW,),
        ),
    ),
    Operation.SOURCE_LIST: (
        RecencyRule(_p("sources", "list"), 1, 1, "public_call", "always", (_GET_SOURCE_LIST,)),
    ),
    Operation.SOURCE_GET: (
        RecencyRule(
            _p("sources", "get", "get_or_none"),
            1,
            1,
            "public_call",
            "always",
            (_GET_SOURCE,),
        ),
    ),
    Operation.SOURCE_ADD_TEXT: (
        RecencyRule(_p("sources", "add_text"), 0, 0, "public_call", "always: no probe baseline"),
    ),
    Operation.SOURCE_ADD_URL: (
        RecencyRule(
            _p("sources", "add_url"),
            1,
            None,
            "public_call",
            "one baseline plus one read for each ambiguity probe",
            (_GET_SOURCES,),
        ),
    ),
    Operation.SOURCE_ADD_DRIVE: (
        RecencyRule(
            _p("sources", "add_drive"),
            1,
            None,
            "public_call",
            "one baseline plus one read for each ambiguity probe",
            (_GET_SOURCES,),
        ),
    ),
    Operation.SOURCE_ADD_FILE: (
        RecencyRule(
            _p("sources", "add_file", "add_drive_file"),
            1,
            None,
            "public_call",
            "one baseline plus registration/reconciliation probes",
            (_GET_SOURCES,),
        ),
    ),
    Operation.SOURCE_WAIT: (
        RecencyRule(
            _p(
                "sources",
                "wait_until_ready",
                "wait_all_until_ready",
                "wait_until_registered",
                "wait_for_sources",
            ),
            1,
            1,
            "poll_tick",
            "one shared snapshot per tick regardless of source count",
            (_GET_SOURCES,),
        ),
    ),
    Operation.CHAT_ASK: (
        RecencyRule(
            _p("chat", "ask"),
            0,
            1,
            "public_call",
            "one only when source_ids is omitted",
            (_GET_RAW,),
        ),
    ),
    Operation.CHAT_CONFIGURE: (
        RecencyRule(
            _p("chat", "get_settings"),
            1,
            1,
            "public_call",
            "always for get_settings",
            ("_chat/api.py:ChatAPI.get_settings",),
        ),
        RecencyRule(
            _p("chat", "configure", "set_mode"),
            0,
            0,
            "public_call",
            "configure/set_mode mutate embedded settings without reading the notebook",
        ),
    ),
}

for _operation in (*_GENERATION_OPERATIONS, Operation.ARTIFACT_GENERATE_MIND_MAP):
    RECENCY_CONTRACTS[_operation] = (
        RecencyRule(
            next(spec.public_methods for spec in OPERATION_SPECS if spec.operation is _operation),
            0,
            1,
            "public_call",
            "one only when source_ids is omitted",
            (_GET_RAW,),
        ),
    )
for _operation, _kind in (
    (Operation.MIND_MAP_GENERATE_NOTE, "NOTE_BACKED"),
    (Operation.MIND_MAP_GENERATE_INTERACTIVE, "INTERACTIVE"),
):
    RECENCY_CONTRACTS[_operation] = (
        RecencyRule(
            _p("mind_maps", "generate"),
            0,
            1,
            "public_call",
            f"one only when kind={_kind} and source_ids is omitted",
            (_GET_RAW,),
        ),
    )

SHARED_RPC_AUTHORITY_RULES.update(
    {
        (Operation.ARTIFACT_LIST, _b(RPCMethod.LIST_ARTIFACTS)): _rules(
            ("_artifact/listing.py:ArtifactListingService.list_raw", "heterogeneous list")
        ),
        (Operation.ARTIFACT_GET, _b(RPCMethod.LIST_ARTIFACTS)): _rules(
            ("_artifact/listing.py:ArtifactListingService.list_raw", "select artifact/prompt")
        ),
        (Operation.ARTIFACT_RENAME, _b(RPCMethod.LIST_ARTIFACTS)): _rules(
            ("_artifact/listing.py:ArtifactListingService.list_raw", "return_object=True post-read")
        ),
        (Operation.ARTIFACT_DOWNLOAD, _b(RPCMethod.LIST_ARTIFACTS)): _rules(
            ("_artifact/listing.py:ArtifactListingService.list_raw", "select downloadable artifact")
        ),
        (Operation.ARTIFACT_WAIT, _b(RPCMethod.LIST_ARTIFACTS)): _rules(
            (
                "_artifact/listing.py:ArtifactListingService.list_raw",
                "one catalog read per poll tick",
            )
        ),
        (Operation.MIND_MAP_LIST, _b(RPCMethod.LIST_ARTIFACTS)): _rules(
            ("_artifact/listing.py:ArtifactListingService.list_raw", "filter interactive maps")
        ),
        (Operation.MIND_MAP_GET, _b(RPCMethod.LIST_ARTIFACTS)): _rules(
            (
                "_artifact/listing.py:ArtifactListingService.list_raw",
                "auto-detect/select interactive id",
            )
        ),
        (Operation.MIND_MAP_GENERATE_INTERACTIVE, _b(RPCMethod.LIST_ARTIFACTS)): _rules(
            ("_artifact/listing.py:ArtifactListingService.list_raw", "post-create settle/id match")
        ),
        (Operation.LABEL_LIST, _b(RPCMethod.LIST_LABELS)): _rules(
            ("_labels.py:LabelsAPI.list", "label_type=source")
        ),
        (Operation.LABEL_GET, _b(RPCMethod.LIST_LABELS)): _rules(
            ("_labels.py:LabelsAPI.list", "select source-label id")
        ),
        (Operation.LABEL_SOURCES, _b(RPCMethod.LIST_LABELS)): _rules(
            ("_labels.py:LabelsAPI.list", "resolve source-label membership")
        ),
        (Operation.COLLECTION_LIST, _b(RPCMethod.LIST_LABELS)): _rules(
            ("_collections.py:CollectionsAPI.list", "label_type=collection")
        ),
        (Operation.COLLECTION_GET, _b(RPCMethod.LIST_LABELS)): _rules(
            ("_collections.py:CollectionsAPI.list", "select collection id")
        ),
        (Operation.COLLECTION_NOTEBOOKS, _b(RPCMethod.LIST_LABELS)): _rules(
            ("_collections.py:CollectionsAPI.list", "resolve collection membership")
        ),
        (Operation.RESEARCH_POLL, _b(RPCMethod.POLL_RESEARCH)): _rules(
            ("_research.py:ResearchAPI._poll_task_models", "single public poll")
        ),
        (Operation.RESEARCH_WAIT, _b(RPCMethod.POLL_RESEARCH)): _rules(
            ("_research.py:ResearchAPI._poll_task_models", "one read per wait poll tick")
        ),
        (Operation.ARTIFACT_RENAME, _b(RPCMethod.RENAME_ARTIFACT)): _rules(
            ("_artifacts.py:ArtifactsAPI.rename", "public=artifacts.rename")
        ),
        (Operation.MIND_MAP_UPDATE, _b(RPCMethod.RENAME_ARTIFACT)): _rules(
            ("_artifacts.py:ArtifactsAPI.rename", "kind=INTERACTIVE")
        ),
        (Operation.NOTEBOOK_UPDATE, _b(RPCMethod.RENAME_NOTEBOOK)): _rules(
            ("_web/backend.py:WebRpcBackend._notebook_update", "title|emoji mutation")
        ),
        (Operation.CHAT_CONFIGURE, _b(RPCMethod.RENAME_NOTEBOOK)): _rules(
            ("_chat/api.py:ChatAPI.configure", "chat settings mutation payload")
        ),
        (Operation.SHARING_SET_VIEW_LEVEL, _b(RPCMethod.RENAME_NOTEBOOK)): _rules(
            ("_sharing.py:SharingAPI.set_view_level", "share-view-level payload")
        ),
        (Operation.SHARING_SET_PUBLIC, _b(RPCMethod.SHARE_NOTEBOOK)): _rules(
            ("_sharing.py:SharingAPI.set_public", "visibility entry")
        ),
        (Operation.SHARING_UPDATE_USERS, _b(RPCMethod.SHARE_NOTEBOOK)): _rules(
            ("_sharing.py:SharingAPI.set_users", "user grant/upsert entries"),
            ("_sharing.py:SharingAPI.remove_user", "user removal entry"),
        ),
        (Operation.NOTEBOOK_SUMMARIZE, _b(RPCMethod.SUMMARIZE)): _rules(
            ("_notebooks.py:NotebooksAPI.get_summary", "summary projection")
        ),
        (Operation.NOTEBOOK_DESCRIBE, _b(RPCMethod.SUMMARIZE)): _rules(
            ("_notebooks.py:NotebooksAPI.get_description", "description/topics projection")
        ),
        (Operation.LABEL_UPDATE, _b(RPCMethod.UPDATE_LABEL)): _rules(
            ("_labels.py:LabelsAPI.update", "label field-mask mutation")
        ),
        (Operation.COLLECTION_UPDATE, _b(RPCMethod.UPDATE_LABEL)): _rules(
            ("_collections.py:CollectionsAPI.rename", "collection name mutation")
        ),
        (Operation.NOTE_UPDATE, _b(RPCMethod.UPDATE_NOTE)): _rules(
            ("_note_service.py:NoteService.update_note", "public=notes.update")
        ),
        (Operation.MIND_MAP_UPDATE, _b(RPCMethod.UPDATE_NOTE)): _rules(
            ("_note_service.py:NoteService.update_note", "kind=NOTE_BACKED")
        ),
        (Operation.SOURCE_ADD_URL, _b(RPCMethod.UPDATE_SOURCE)): _rules(
            ("_sources.py:SourcesAPI.rename", "optional post-create title")
        ),
        (Operation.SOURCE_ADD_FILE, _b(RPCMethod.UPDATE_SOURCE)): _rules(
            ("_source/upload.py:SourceUploadPipeline.rename", "optional post-upload title")
        ),
        (Operation.SOURCE_UPDATE, _b(RPCMethod.UPDATE_SOURCE)): _rules(
            ("_sources.py:SourcesAPI.rename", "public=sources.rename")
        ),
    }
)

SHARED_RPC_AUTHORITY_RULES.update(
    {
        (Operation.LABEL_GENERATE, _b(RPCMethod.CREATE_LABEL)): _rules(
            ("_labels.py:LabelsAPI.generate", "label_mode=auto-group")
        ),
        (Operation.LABEL_CREATE, _b(RPCMethod.CREATE_LABEL)): _rules(
            ("_labels.py:LabelsAPI.create", "label_type=source")
        ),
        (Operation.COLLECTION_CREATE, _b(RPCMethod.CREATE_LABEL)): _rules(
            ("_collections.py:CollectionsAPI.create", "label_type=collection")
        ),
        (Operation.ARTIFACT_DELETE, _b(RPCMethod.DELETE_ARTIFACT)): _rules(
            ("_artifacts.py:ArtifactsAPI.delete", "public=artifacts.delete")
        ),
        (Operation.MIND_MAP_DELETE, _b(RPCMethod.DELETE_ARTIFACT)): _rules(
            ("_artifacts.py:ArtifactsAPI.delete", "kind=INTERACTIVE")
        ),
        (Operation.NOTE_DELETE, _b(RPCMethod.DELETE_NOTE)): _rules(
            ("_note_service.py:NoteService.delete_note", "public=notes.delete")
        ),
        (Operation.MIND_MAP_DELETE, _b(RPCMethod.DELETE_NOTE)): _rules(
            ("_note_service.py:NoteService.delete_note", "kind=NOTE_BACKED")
        ),
        (Operation.LABEL_DELETE, _b(RPCMethod.DELETE_LABEL)): _rules(
            ("_labels.py:LabelsAPI.delete", "label_type=source")
        ),
        (Operation.COLLECTION_DELETE, _b(RPCMethod.DELETE_LABEL)): _rules(
            ("_collections.py:CollectionsAPI.delete", "label_type=collection")
        ),
        (Operation.ARTIFACT_DOWNLOAD, _b(RPCMethod.GET_INTERACTIVE_HTML)): _rules(
            (
                "_artifact/downloads.py:ArtifactDownloadService._get_artifact_content",
                "quiz|flashcards interactive representation",
            ),
            (
                "_artifact/downloads.py:ArtifactDownloadService._get_interactive_mind_map_tree",
                "interactive mind-map representation",
            ),
        ),
        (Operation.MIND_MAP_GET, _b(RPCMethod.GET_INTERACTIVE_HTML)): _rules(
            ("_mind_maps_api.py:MindMapsAPI.get_tree", "kind=INTERACTIVE")
        ),
        (Operation.MIND_MAP_GENERATE_INTERACTIVE, _b(RPCMethod.GET_INTERACTIVE_HTML)): _rules(
            ("_mind_maps_api.py:MindMapsAPI.get_tree", "wait=True post-generation tree")
        ),
        (Operation.CHAT_ASK, _b(RPCMethod.GET_LAST_CONVERSATION_ID)): _rules(
            ("_chat/api.py:ChatAPI.get_conversation_id", "pre/post streamed query conversation id")
        ),
        (Operation.CHAT_GET_CONVERSATION, _b(RPCMethod.GET_LAST_CONVERSATION_ID)): _rules(
            ("_chat/api.py:ChatAPI.get_conversation_id", "public=chat.get_conversation_id")
        ),
        (Operation.CHAT_GET_HISTORY, _b(RPCMethod.GET_LAST_CONVERSATION_ID)): _rules(
            ("_chat/api.py:ChatAPI.get_conversation_id", "conversation_id is omitted")
        ),
        (Operation.NOTE_LIST, _b(RPCMethod.GET_NOTES_AND_MIND_MAPS)): _rules(
            ("_note_service.py:NoteService.fetch_note_rows", "filter kind=NOTE")
        ),
        (Operation.NOTE_GET, _b(RPCMethod.GET_NOTES_AND_MIND_MAPS)): _rules(
            ("_note_service.py:NoteService.fetch_note_rows", "select note id")
        ),
        (Operation.MIND_MAP_LIST, _b(RPCMethod.GET_NOTES_AND_MIND_MAPS)): _rules(
            ("_note_service.py:NoteService.fetch_note_rows", "filter kind=NOTE_BACKED")
        ),
        (Operation.MIND_MAP_GET, _b(RPCMethod.GET_NOTES_AND_MIND_MAPS)): _rules(
            ("_note_service.py:NoteService.fetch_note_rows", "auto-detect/select note-backed id")
        ),
        (Operation.SHARING_GET, _b(RPCMethod.GET_SHARE_STATUS)): _rules(
            ("_sharing.py:SharingAPI.get_status", "public=sharing.get_status")
        ),
        (Operation.SHARING_SET_PUBLIC, _b(RPCMethod.GET_SHARE_STATUS)): _rules(
            ("_sharing.py:SharingAPI.get_status", "post-public-mutation read")
        ),
        (Operation.SHARING_SET_VIEW_LEVEL, _b(RPCMethod.GET_SHARE_STATUS)): _rules(
            ("_sharing.py:SharingAPI.get_status", "post-view-level-mutation read")
        ),
        (Operation.SHARING_UPDATE_USERS, _b(RPCMethod.GET_SHARE_STATUS)): _rules(
            ("_sharing.py:SharingAPI.get_status", "post-user-grant mutation read")
        ),
        (Operation.NOTEBOOK_CREATE, _b(RPCMethod.GET_USER_SETTINGS)): _rules(
            ("_web/backend.py:WebRpcBackend._notebook_limit_error", "quota-error diagnosis only")
        ),
        (Operation.SOURCE_ADD_FILE, _b(RPCMethod.GET_USER_SETTINGS)): _rules(
            ("_sources.py:SourcesAPI._get_source_limit", "invalid-argument diagnosis only")
        ),
        (Operation.SETTINGS_GET, _b(RPCMethod.GET_USER_SETTINGS)): _rules(
            ("_settings.py:SettingsAPI._fetch_user_settings", "settings row projection")
        ),
        (Operation.SETTINGS_GET_LIMITS, _b(RPCMethod.GET_USER_SETTINGS)): _rules(
            ("_settings.py:SettingsAPI._fetch_user_settings", "account-limit projection")
        ),
        (Operation.RESEARCH_IMPORT, _b(RPCMethod.IMPORT_RESEARCH)): _rules(
            ("_research.py:ResearchAPI.import_sources", "single import attempt")
        ),
        (Operation.RESEARCH_IMPORT_VERIFY, _b(RPCMethod.IMPORT_RESEARCH)): _rules(
            ("_research.py:ResearchAPI.import_sources", "verified import attempt")
        ),
    }
)
