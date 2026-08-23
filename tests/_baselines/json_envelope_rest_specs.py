"""Reviewed REST response public-model projection declarations."""

from __future__ import annotations

REST_PROJECTION_SPECS: tuple[dict[str, object], ...] = (
    {
        "model": "notebooklm.types.AccountLimits",
        "mode": "transitive-server-info-account-success-wrapper",
        "keys": ("server", "version", "auth"),
        "optional_keys": ("account",),
        "nested_keys": {
            "auth": (
                "authenticated",
                "storage_exists",
                "json_valid",
                "cookies_present",
                "sid_cookie",
                "profile",
            ),
            "account": (
                "email",
                "authuser",
                "available",
                "notebook_limit",
                "source_limit",
                "tier",
                "output_language",
                "output_language_is_default",
            ),
        },
        "nested_optional_keys": {"auth": ("startup_error",)},
        "model_contribution_keys": ("notebook_limit", "source_limit", "tier"),
        "evidence": (
            "notebooklm/server/routes/meta.py:async def _account_block",
            "notebooklm/server/routes/meta.py:async def server_info",
        ),
    },
    {
        "model": "notebooklm.types.Artifact",
        "mode": "dataclass-full",
        "evidence": ("notebooklm/server/routes/artifacts.py:to_jsonable(artifacts)",),
        "nested_fields": "all",
    },
    {
        "model": "notebooklm.types.Artifact",
        "mode": "dataclass-list-default-final-wrapper",
        "keys": ("notebook_id", "artifacts"),
        "nested_keys": {
            "artifacts": (
                "id",
                "title",
                "kind",
                "status",
                "created_at",
                "updated_at",
                "url",
                "generation_prompt",
                "source_ids",
                "audio_url",
                "video_url",
                "slides_url",
                "report_url",
                "data_table_url",
                "infographic_url",
                "mind_map_id",
                "mind_map_kind",
                "is_note_backed_mind_map",
            )
        },
        "evidence": (
            "notebooklm/server/routes/artifacts.py:def list_artifacts",
            "notebooklm/server/_pagination.py:if limit is None",
        ),
        "derive": "runtime:list-wrapper-rest-artifact-default",
    },
    {
        "model": "notebooklm.types.Artifact",
        "mode": "dataclass-list-paged-final-wrapper",
        "keys": ("notebook_id", "artifacts", "meta"),
        "nested_keys": {
            "artifacts": (
                "id",
                "title",
                "kind",
                "status",
                "created_at",
                "updated_at",
                "url",
                "generation_prompt",
                "source_ids",
                "audio_url",
                "video_url",
                "slides_url",
                "report_url",
                "data_table_url",
                "infographic_url",
                "mind_map_id",
                "mind_map_kind",
                "is_note_backed_mind_map",
            ),
            "meta": ("total", "has_more", "limit", "offset"),
        },
        "evidence": (
            "notebooklm/server/routes/artifacts.py:def list_artifacts",
            "notebooklm/server/_pagination.py:def paginate_envelope",
        ),
        "derive": "runtime:list-wrapper-rest-artifact-paged",
    },
    {
        "model": "notebooklm.types.GenerationStatus",
        "mode": "app-status-view-projection",
        "evidence": (
            "notebooklm/_app/artifacts.py:status_view",
            "notebooklm/server/routes/artifacts.py:projected =",
        ),
        "derive": "runtime:generation-status-view",
    },
    {
        "model": "notebooklm.types.GenerationStatus",
        "mode": "manual-retry-projection",
        "keys": ("notebook_id", "artifact_id", "task_id", "status"),
        "evidence": ("notebooklm/server/routes/artifacts.py:async def retry",),
    },
    {
        "model": "notebooklm.types.GenerationStatus",
        "mode": "manual-generate-projection",
        "keys": ("notebook_id", "kind", "task_id", "status", "url", "error"),
        "evidence": (
            "notebooklm/_app/generate_retry.py:generation_outcome_from_status",
            "notebooklm/server/routes/artifacts.py:_generation_payload",
        ),
    },
    {
        "model": "notebooklm.types.GenerationStatus",
        "mode": "http-failed-409-error-envelope",
        "keys": ("error",),
        "nested_keys": {"error": ("category", "message")},
        "evidence": (
            "notebooklm/server/routes/artifacts.py:state == GenerationState.FAILED",
            "notebooklm/server/_errors.py:http_error_response",
        ),
    },
    {
        "model": "notebooklm.types.GenerationStatus",
        "mode": "http-removed-410-error-envelope",
        "keys": ("error",),
        "nested_keys": {"error": ("category", "message")},
        "evidence": (
            "notebooklm/server/routes/artifacts.py:state == GenerationState.REMOVED",
            "notebooklm/server/_errors.py:http_error_response",
        ),
    },
    {
        "model": "notebooklm.types.AskResult",
        "mode": "app-view:ask_result_view",
        "evidence": (
            "notebooklm/_app/views.py:ask_result_view",
            "notebooklm/server/routes/chat.py:ask_result_view",
        ),
        "derive": "runtime:ask-result-base",
        "nested_fields": ("references", "turn_key", "next_steps"),
    },
    {
        "model": "notebooklm.types.MindMap",
        "mode": "dataclass-full-nested-in-generation-wrapper",
        "evidence": ('notebooklm/server/routes/artifacts.py:payload["mind_map"]',),
        "derive": "runtime:mind-map-rest-final",
    },
    {
        "model": "notebooklm.types.MindMapResult",
        "mode": "dataclass-full-nested-in-generation-wrapper",
        "evidence": ('notebooklm/server/routes/artifacts.py:payload["mind_map"]',),
        "derive": "runtime:mind-map-rest-final",
    },
    {
        "model": "notebooklm.types.MindMap",
        "mode": "transitive-artifact-rename-final-wrapper",
        "keys": (
            "status",
            "notebook_id",
            "artifact_id",
            "new_title",
            "is_mind_map",
        ),
        "evidence": (
            "notebooklm/_app/artifacts.py:async def rename_artifact",
            "notebooklm/server/routes/artifacts.py:async def rename",
        ),
    },
    {
        "model": "notebooklm.types.Note",
        "mode": "dataclass-full",
        "evidence": ("notebooklm/server/routes/notes.py:to_jsonable",),
        "nested_fields": "all",
    },
    {
        "model": "notebooklm.types.Note",
        "mode": "dataclass-list-default-final-wrapper",
        "keys": ("notebook_id", "notes"),
        "nested_keys": {"notes": ("id", "notebook_id", "title", "content", "created_at")},
        "evidence": (
            "notebooklm/server/routes/notes.py:def list_notes",
            "notebooklm/server/_pagination.py:if limit is None",
        ),
        "derive": "runtime:list-wrapper-rest-note-default",
    },
    {
        "model": "notebooklm.types.Note",
        "mode": "dataclass-list-paged-final-wrapper",
        "keys": ("notebook_id", "notes", "meta"),
        "nested_keys": {
            "notes": ("id", "notebook_id", "title", "content", "created_at"),
            "meta": ("total", "has_more", "limit", "offset"),
        },
        "evidence": (
            "notebooklm/server/routes/notes.py:def list_notes",
            "notebooklm/server/_pagination.py:def paginate_envelope",
        ),
        "derive": "runtime:list-wrapper-rest-note-paged",
    },
    {
        "model": "notebooklm.types.Notebook",
        "mode": "app-view:notebook_view",
        "evidence": (
            "notebooklm/_app/views.py:notebook_view",
            "notebooklm/server/routes/notebooks.py:notebook_view",
        ),
    },
    {
        "model": "notebooklm.types.Notebook",
        "mode": "app-view:notebook-list-default-final-wrapper",
        "evidence": (
            "notebooklm/server/routes/notebooks.py:def list_notebooks",
            "notebooklm/server/_pagination.py:if limit is None",
        ),
        "derive": "runtime:list-wrapper-rest-notebook-default",
    },
    {
        "model": "notebooklm.types.Notebook",
        "mode": "app-view:notebook-list-paged-final-wrapper",
        "evidence": (
            "notebooklm/server/routes/notebooks.py:def list_notebooks",
            "notebooklm/server/_pagination.py:def paginate_envelope",
        ),
        "derive": "runtime:list-wrapper-rest-notebook-paged",
    },
    {
        "model": "notebooklm.types.PromptSuggestion",
        "mode": "manual-rest-final-wrapper",
        "evidence": ('notebooklm/server/routes/notebooks.py:"suggestions":',),
        "derive": "runtime:prompt-suggestion-rest",
    },
    {
        "model": "notebooklm.types.ResearchStart",
        "mode": "dataclass-full-with-poll-id",
        "keys": ("task_id", "report_id", "notebook_id", "query", "mode", "poll_id"),
        "evidence": ("notebooklm/server/routes/research.py:to_jsonable(result)",),
    },
    {
        "model": "notebooklm.types.ResearchTask",
        "mode": "manual-status-projection",
        "keys": (
            "notebook_id",
            "run_id",
            "task_id",
            "kind",
            "status",
            "status_code",
            "termination_reason",
            "reason_message",
            "hint",
            "discovery_mode",
            "created_at",
            "updated_at",
            "duration_seconds",
            "query",
            "sources",
            "summary",
            "report",
        ),
        "evidence": ("notebooklm/server/routes/research.py:research_status",),
    },
    {
        "model": "notebooklm.types.ResearchTask",
        "mode": "transitive-import-final-wrapper",
        "keys": ("status", "notebook_id", "run_id", "imported", "sources_found"),
        "model_contribution_keys": ("sources",),
        "evidence": (
            "notebooklm/_app/research.py:status = await client.research.poll",
            'notebooklm/server/routes/research.py:"sources_found": len(sources)',
        ),
    },
    {
        "model": "notebooklm.types.ResearchSource",
        "mode": "nested-public-dict-projection",
        "evidence": (
            "notebooklm/_app/research.py:src.to_public_dict()",
            "notebooklm/server/routes/research.py:to_jsonable(result.sources)",
        ),
        "derive": "runtime:research-source-public-dict",
    },
    {
        "model": "notebooklm.types.ResearchSource",
        "mode": "transitive-import-source-count-contribution",
        "keys": ("sources_found",),
        "contribution_semantics": (
            "ResearchSource list membership contributes only to sources_found; no source "
            "field envelope is serialized on this path"
        ),
        "evidence": (
            "notebooklm/_app/research.py:sources=[src.to_public_dict() for src in status.sources]",
            'notebooklm/server/routes/research.py:"sources_found": len(sources)',
        ),
    },
    {
        "model": "notebooklm.types.ShareStatus",
        "mode": "app-view:share_status_view",
        "evidence": (
            "notebooklm/_app/views.py:share_status_view",
            "notebooklm/server/routes/share.py:share_status_view",
        ),
    },
    {
        "model": "notebooklm.types.ShareStatus",
        "mode": "app-view:share_status_view+view_level",
        "evidence": ("notebooklm/server/routes/share.py:include_view_level=True",),
    },
    {
        "model": "notebooklm.types.SharedUser",
        "mode": "nested-manual-field-projection",
        "keys": ("email", "permission", "display_name", "avatar_url"),
        "evidence": ("notebooklm/_app/views.py:shared_users",),
    },
    {
        "model": "notebooklm.types.Source",
        "mode": "app-view:source_view",
        "evidence": (
            "notebooklm/_app/views.py:source_view",
            "notebooklm/server/routes/sources.py:source_view",
        ),
    },
    {
        "model": "notebooklm.types.Source",
        "mode": "app-view:source-list-default-final-wrapper",
        "evidence": (
            "notebooklm/server/routes/sources.py:def list_sources",
            "notebooklm/server/_pagination.py:if limit is None",
        ),
        "derive": "runtime:list-wrapper-rest-source-default",
    },
    {
        "model": "notebooklm.types.Source",
        "mode": "app-view:source-list-paged-final-wrapper",
        "evidence": (
            "notebooklm/server/routes/sources.py:def list_sources",
            "notebooklm/server/_pagination.py:def paginate_envelope",
        ),
        "derive": "runtime:list-wrapper-rest-source-paged",
    },
    {
        "model": "notebooklm.types.Source",
        "mode": "app-view:source-wait-final-wrapper",
        "evidence": (
            "notebooklm/server/routes/sources.py:def _aggregate_wait_outcomes",
            "notebooklm/_app/views.py:source_view",
        ),
        "derive": "runtime:source-view-rest-wait",
    },
    {
        "model": "notebooklm.types.Source",
        "mode": "transitive-batch-added-item-final-wrapper",
        "keys": ("status", "notebook_id", "added", "failed", "results"),
        "nested_keys": {"results": ("input", "status", "source_id", "title", "status_label")},
        "evidence": ("notebooklm/server/routes/sources.py:async def add_batch",),
    },
    {
        "model": "notebooklm.types.Source",
        "mode": "transitive-batch-error-item-final-wrapper",
        "keys": ("status", "notebook_id", "added", "failed", "results"),
        "nested_keys": {
            "results": ("input", "status", "error"),
            "results[].error": ("category", "message", "retriable"),
        },
        "nested_optional_keys": {"results[].error": ("unconfirmed", "candidates", "hint")},
        "evidence": (
            "notebooklm/server/routes/sources.py:error_item(exc)",
            "notebooklm/server/_errors.py:def error_item",
        ),
    },
    {
        "model": "notebooklm.types.SourceFulltext",
        "mode": "manual-content-projection",
        "keys": (
            "notebook_id",
            "source_id",
            "content",
            "char_count",
            "truncated",
            "output_format",
        ),
        "evidence": ("notebooklm/server/routes/sources.py:get_source_content",),
    },
    {
        "model": "notebooklm.types.SourceGuide",
        "mode": "manual-guide-projection",
        "keys": ("notebook_id", "source_id", "summary", "keywords"),
        "evidence": ('notebooklm/server/routes/sources.py:detail == "summary"',),
    },
    {
        "model": "notebooklm.types.UserSettings",
        "mode": "transitive-server-info-account-success-wrapper",
        "keys": ("server", "version", "auth"),
        "optional_keys": ("account",),
        "nested_keys": {
            "auth": (
                "authenticated",
                "storage_exists",
                "json_valid",
                "cookies_present",
                "sid_cookie",
                "profile",
            ),
            "account": (
                "email",
                "authuser",
                "available",
                "notebook_limit",
                "source_limit",
                "tier",
                "output_language",
                "output_language_is_default",
            ),
        },
        "nested_optional_keys": {"auth": ("startup_error",)},
        "model_contribution_keys": (
            "notebook_limit",
            "source_limit",
            "tier",
            "output_language",
            "output_language_is_default",
        ),
        "evidence": (
            "notebooklm/server/routes/meta.py:async def _account_block",
            "notebooklm/server/routes/meta.py:async def server_info",
        ),
    },
)

__all__ = ["REST_PROJECTION_SPECS"]
