"""JSON-envelope compatibility contract baseline tests."""

from __future__ import annotations

import dataclasses
import enum
import importlib
from pathlib import Path

import pytest
import scripts.audit_public_api_compat as public_audit

import notebooklm
from tests._baselines.compatibility_contracts import (
    _evidence_ast_fingerprint,
    _secret_serialization_violations,
    _validate_no_secret_channel_models,
    derive_json_envelope_contract,
)
from tests._baselines.json_envelope_contracts import _normalize_conditional_key_groups
from tests._baselines.json_envelope_specs import projection_spec_ids


def _exported_models() -> dict[str, type]:
    package_dir = Path(notebooklm.__file__).resolve().parent
    modules = {public_audit.PUBLIC_PACKAGE}
    for path in package_dir.glob("*.py"):
        if path.stem.startswith("_") or path.stem in public_audit.EXCLUDED_TOP_LEVEL_MODULES:
            continue
        modules.add(f"{public_audit.PUBLIC_PACKAGE}.{path.stem}")
    modules.update(
        f"{public_audit.PUBLIC_PACKAGE}.{name}"
        for name in public_audit.EXTRA_PUBLIC_PACKAGES
        if (package_dir / name / "__init__.py").is_file()
    )

    models: dict[str, type] = {}
    for module_name in sorted(modules):
        module = importlib.import_module(module_name)
        for name in getattr(module, "__all__", ()):
            value = getattr(module, name)
            if isinstance(value, type) and (
                dataclasses.is_dataclass(value) or issubclass(value, enum.Enum)
            ):
                models[f"{value.__module__}.{value.__qualname__}"] = value
    return models


def test_json_envelope_preserves_original_exact_contract_assertions() -> None:
    contract = derive_json_envelope_contract()
    expected = {
        name: cls
        for name, cls in _exported_models().items()
        if dataclasses.is_dataclass(cls) and name != "notebooklm.auth.AuthTokens"
    }
    inventory = contract["exported_dataclass_key_inventory"]

    assert set(inventory) == set(expected)
    assert "notebooklm.auth.AuthTokens" not in inventory
    assert "notebooklm.artifacts.RateLimitRetryEvent" in inventory
    for name, cls in expected.items():
        field_names = [field.name for field in dataclasses.fields(cls)]
        assert inventory[name]["dataclass_fields"] == field_names
        assert inventory[name]["to_jsonable_keys"] == field_names

    assert set(contract["channels"]) == {"cli --json", "mcp tool result", "rest response"}
    for reachable in contract["channels"].values():
        assert reachable
        assert set(reachable) <= set(inventory)
        assert "notebooklm.auth.AuthTokens" not in reachable
        assert "notebooklm.types.AskResult" in reachable
        assert "notebooklm.types.ChatReference" in reachable
        for row in reachable.values():
            assert row["projections"]
            assert all(
                projection["keys"] or projection.get("optional_keys")
                for projection in row["projections"]
            )
            assert all(projection["evidence"] for projection in row["projections"])

    cli = contract["channels"]["cli --json"]
    ask_keys = cli["notebooklm.types.AskResult"]["projections"][0]["keys"]
    assert "raw_response" not in ask_keys
    assert "answer_document" not in ask_keys
    cited = cli["notebooklm.types.CitedSourceSelection"]["projections"]
    assert [projection["keys"] for projection in cited[:2]] == [
        ["cited_only", "cited_sources_selected", "cited_only_fallback"],
        ["cited_only", "cited_only_fallback"],
    ]
    notebook_projections = cli["notebooklm.types.Notebook"]["projections"]
    create = next(
        projection
        for projection in notebook_projections
        if projection["mode"] == "manual-create-projection"
    )
    assert create["keys"] == ["notebook"]
    assert create["nested_keys"]["notebook"] == [
        "id",
        "title",
        "role",
        "created_at",
        "last_viewed_at",
        "modified_at",
    ]
    assert cli["notebooklm.types.NotebookMetadata"]["projections"][0]["keys"] == [
        "id",
        "title",
        "created_at",
        "last_viewed_at",
        "modified_at",
        "is_owner",
        "role",
        "sources",
    ]
    assert cli["notebooklm.types.SourceSummary"]["projections"][0]["keys"] == [
        "type",
        "title",
        "url",
    ]
    assert [
        projection["keys"]
        for projection in cli["notebooklm.types.NotebookDescription"]["projections"]
    ] == [
        ["notebook_id", "summary"],
        ["notebook_id", "summary", "suggested_topics"],
    ]
    assert cli["notebooklm.types.SuggestedTopic"]["projections"][0]["keys"] == ["question"]
    assert "notebooklm.types.ResearchTask" in cli
    assert "notebooklm.types.ResearchSource" in cli
    assert cli["notebooklm.types.SourceGuide"]["projections"][0]["keys"] == [
        "source_id",
        "summary",
        "keywords",
    ]
    cli_generation = {
        projection["mode"]: projection
        for projection in cli["notebooklm.types.GenerationStatus"]["projections"]
    }
    assert cli_generation["manual-poll-projection"]["keys"] == [
        "task_id",
        "status",
        "url",
        "error",
        "error_code",
        "metadata",
    ]
    assert cli_generation["manual-wait-projection"]["keys"] == [
        "artifact_id",
        "status",
        "url",
        "error",
    ]
    assert cli_generation["manual-retry-kickoff-projection"]["keys"] == [
        "task_id",
        "status",
        "url",
        "error",
        "error_code",
    ]
    assert cli_generation["manual-generation-completed-projection"]["keys"] == [
        "task_id",
        "status",
        "url",
    ]
    assert cli_generation["manual-generation-pending-projection"]["keys"] == [
        "task_id",
        "status",
    ]
    assert cli_generation["manual-generation-failure-envelope"]["keys"] == [
        "error",
        "code",
        "message",
    ]
    assert cli_generation["nested-timeout-transition-projection"]["keys"] == [
        "task_id",
        "status",
        "url",
        "error",
        "error_code",
        "metadata",
    ]

    mcp = contract["channels"]["mcp tool result"]
    mcp_metadata = mcp["notebooklm.types.NotebookMetadata"]["projections"][0]
    assert mcp_metadata["keys"] == ["notebook_id", "description", "metadata"]
    assert mcp_metadata["nested_keys"]["metadata"] == ["notebook", "sources"]
    assert mcp_metadata["nested_keys"]["metadata.sources"] == ["kind", "title", "url"]
    mcp_source_summary = mcp["notebooklm.types.SourceSummary"]["projections"][0]
    assert mcp_source_summary["mode"] == "nested-dataclass"
    assert mcp_source_summary["keys"] == ["kind", "title", "url"]
    assert mcp_source_summary["evidence"] == [
        "nested-via:notebooklm.types.NotebookMetadata.sources"
    ]
    assert all(
        projection["mode"] != "dataclass-full"
        for projection in mcp["notebooklm.types.Source"]["projections"]
    )
    mcp_description = mcp["notebooklm.types.NotebookDescription"]["projections"][0]
    assert mcp_description["keys"] == ["notebook_id", "description"]
    assert mcp_description["nested_keys"]["description"] == ["summary", "suggested_topics"]
    assert mcp["notebooklm.types.SuggestedTopic"]["projections"][0]["keys"] == [
        "question",
        "prompt",
    ]
    assert mcp["notebooklm.types.ResearchStart"]["projections"][0]["keys"] == [
        "notebook_id",
        "query",
        "mode",
        "poll_task_id",
    ]
    assert mcp["notebooklm.types.ResearchTask"]["projections"][0]["mode"] == (
        "manual-status-projection"
    )
    mcp_research_source = mcp["notebooklm.types.ResearchSource"]["projections"][0]
    assert mcp_research_source["mode"] == "nested-public-dict-report-omitted"
    assert mcp_research_source["keys"] == ["url", "title", "result_type"]
    assert mcp_research_source["optional_keys"] == [
        "research_task_id",
        "source_ordinal",
        "hint",
    ]
    assert "notebooklm.types.SourceFulltext" in mcp
    assert "notebooklm.types.SourceGuide" in mcp
    mcp_source = {
        projection["mode"]: projection
        for projection in mcp["notebooklm.types.Source"]["projections"]
    }
    assert mcp_source["manual-compact-projection"]["keys"] == [
        "id",
        "title",
        "kind",
        "status_label",
        "drive_status_label",
        "created_at",
    ]
    account_success_keys = [
        "email",
        "authuser",
        "available",
        "notebook_limit",
        "source_limit",
        "tier",
        "output_language",
        "output_language_is_default",
    ]
    mcp_settings = mcp["notebooklm.types.UserSettings"]["projections"][0]
    mcp_limits = mcp["notebooklm.types.AccountLimits"]["projections"][0]
    assert mcp_settings["mode"] == "transitive-server-info-account-success-wrapper"
    assert mcp_settings["keys"] == ["server", "version", "auth"]
    assert mcp_settings["optional_keys"] == ["account"]
    assert mcp_settings["nested_keys"]["account"] == account_success_keys
    assert mcp_settings["model_contribution_keys"] == [
        "notebook_limit",
        "source_limit",
        "tier",
        "output_language",
        "output_language_is_default",
    ]
    assert mcp_limits["model_contribution_keys"] == [
        "notebook_limit",
        "source_limit",
        "tier",
    ]
    mcp_generation = {
        projection["mode"]: projection
        for projection in mcp["notebooklm.types.GenerationStatus"]["projections"]
    }
    assert mcp_generation["app-status-view-projection"]["keys"] == [
        "notebook_id",
        "task_id",
        "status",
        "url",
        "error",
        "error_code",
        "metadata",
        "is_complete",
        "media_ready",
    ]
    assert mcp_generation["manual-retry-projection"]["keys"] == [
        "notebook_id",
        "artifact_id",
        "task_id",
        "status",
    ]
    assert mcp_generation["manual-generate-projection"]["keys"] == [
        "notebook_id",
        "kind",
        "task_id",
        "status",
        "url",
        "error",
    ]

    rest = contract["channels"]["rest response"]
    assert rest["notebooklm.types.ResearchStart"]["projections"][0]["keys"] == [
        "task_id",
        "report_id",
        "notebook_id",
        "query",
        "mode",
        "poll_id",
    ]
    assert rest["notebooklm.types.ResearchTask"]["projections"][0]["mode"] == (
        "manual-status-projection"
    )
    rest_research_source = rest["notebooklm.types.ResearchSource"]["projections"][0]
    assert rest_research_source["mode"] == "nested-public-dict-projection"
    assert rest_research_source["keys"] == ["url", "title", "result_type"]
    assert rest_research_source["optional_keys"] == [
        "research_task_id",
        "report_markdown",
        "source_ordinal",
        "hint",
    ]
    assert rest_research_source["evidence"] == [
        "notebooklm/_app/research.py:src.to_public_dict()",
        "notebooklm/server/routes/research.py:to_jsonable(result.sources)",
    ]
    assert "notebooklm.types.SourceFulltext" in rest
    assert "notebooklm.types.SourceGuide" in rest
    assert "notebooklm.types.UserSettings" in rest
    assert "notebooklm.types.AccountLimits" in rest
    rest_settings = rest["notebooklm.types.UserSettings"]["projections"][0]
    rest_limits = rest["notebooklm.types.AccountLimits"]["projections"][0]
    assert rest_settings["keys"] == ["server", "version", "auth"]
    assert rest_settings["nested_keys"]["account"] == account_success_keys
    assert rest_settings["model_contribution_keys"][-2:] == [
        "output_language",
        "output_language_is_default",
    ]
    assert rest_limits["model_contribution_keys"] == [
        "notebook_limit",
        "source_limit",
        "tier",
    ]
    rest_generation = {
        projection["mode"]: projection
        for projection in rest["notebooklm.types.GenerationStatus"]["projections"]
    }
    assert rest_generation["app-status-view-projection"]["keys"] == [
        "notebook_id",
        "task_id",
        "status",
        "url",
        "error",
        "error_code",
        "metadata",
        "is_complete",
        "media_ready",
    ]
    assert rest_generation["manual-retry-projection"]["keys"] == [
        "notebook_id",
        "artifact_id",
        "task_id",
        "status",
    ]
    assert rest_generation["manual-generate-projection"]["keys"] == [
        "notebook_id",
        "kind",
        "task_id",
        "status",
        "url",
        "error",
    ]
    assert "notebooklm.types.NotebookMetadata" not in rest
    assert "notebooklm.types.SourceSummary" not in rest
    assert (
        "notebooklm.types.CitedSourceSelection"
        in contract["supplemental_import_references"]["cli --json"]
    )
    for channel in ("cli --json", "mcp tool result"):
        for model in ("notebooklm.types.MindMap", "notebooklm.types.MindMapResult"):
            projections = contract["channels"][channel][model]["projections"]
            assert all(projection["mode"] != "dataclass-full" for projection in projections)
            assert all("final" in projection["mode"] for projection in projections)
    assert (
        contract["secret_bearing_exclusions"]["notebooklm.auth.AuthTokens"]["adapter_reachable"]
        is False
    )


def test_json_envelope_covers_exported_models_and_exact_adapter_variants() -> None:
    contract = derive_json_envelope_contract()
    expected = {
        name: cls
        for name, cls in _exported_models().items()
        if dataclasses.is_dataclass(cls) and name != "notebooklm.auth.AuthTokens"
    }
    inventory = contract["exported_dataclass_key_inventory"]
    assert set(inventory) == set(expected)
    assert "notebooklm.artifacts.RateLimitRetryEvent" in inventory
    for name, cls in expected.items():
        fields = [field.name for field in dataclasses.fields(cls)]
        assert inventory[name]["dataclass_fields"] == fields
        assert inventory[name]["to_jsonable_keys"] == fields

    projection_ids: list[str] = []
    for channel in contract["channels"].values():
        assert "notebooklm.auth.AuthTokens" not in channel
        assert {"notebooklm.types.AskResult", "notebooklm.types.ChatReference"} <= set(channel)
        for row in channel.values():
            for projection in row["projections"]:
                assert projection["id"]
                assert projection["keys"] or projection.get("optional_keys")
                assert projection["evidence"]
                projection_ids.append(projection["id"])
                if not projection["evidence"][0].startswith("nested-via:"):
                    assert projection["evidence_shape_fingerprints"]
                    assert projection["shape_derivation"]
    assert len(projection_ids) == len(set(projection_ids))
    declared_ids = projection_spec_ids()
    assert set(projection_ids) >= {
        projection_id for channel_ids in declared_ids.values() for projection_id in channel_ids
    }

    cli = contract["channels"]["cli --json"]
    cli_ask = cli["notebooklm.types.AskResult"]["projections"][0]
    assert cli_ask["optional_keys"] == ["note", "note_save_error"]
    assert cli_ask["nested_keys"]["note"] == ["id", "title"]
    assert {"raw_response", "answer_document"}.isdisjoint(cli_ask["keys"])
    for model, list_key in (
        ("notebooklm.types.Notebook", "notebooks"),
        ("notebooklm.types.Source", "sources"),
        ("notebooklm.types.Artifact", "artifacts"),
        ("notebooklm.types.Collection", "collections"),
    ):
        projection = next(
            row for row in cli[model]["projections"] if row["mode"] == "manual-list-final-wrapper"
        )
        assert projection["nested_keys"][list_key][0] == "index"
    cli_source = {row["mode"]: row for row in cli["notebooklm.types.Source"]["projections"]}
    assert cli_source["transitive-add-final-wrapper"]["keys"] == ["source"]
    assert cli_source["transitive-add-drive-final-wrapper"]["nested_keys"]["source"][-2:] == [
        "drive_file_id",
        "mime_type",
    ]
    assert "transitive-refresh-projection" not in cli_source
    clean = cli_source["transitive-clean-success-final-wrapper"]
    assert clean["keys"] == [
        "action",
        "notebook_id",
        "status",
        "candidates",
        "deleted_count",
        "failure_count",
    ]
    assert clean["optional_keys"] == ["candidate_count", "failures"]
    assert clean["nested_keys"]["failures"] == ["id", "error"]
    clean_error = cli_source["transitive-clean-confirm-required-error-wrapper"]
    assert clean_error["keys"] == [
        "error",
        "code",
        "message",
        "action",
        "notebook_id",
        "candidate_count",
        "candidates",
    ]
    assert clean_error["nested_keys"]["candidates"] == ["id", "title", "status", "reason"]
    assert {
        "transitive-research-import-new-source-projection",
        "transitive-research-import-existing-source-projection",
    } <= set(cli_source)
    processing_error = cli_source["transitive-wait-processing-error-final-wrapper"]
    assert processing_error["keys"] == ["source_id", "status", "status_code", "error"]
    assert processing_error["model_contribution_keys"] == ["status"]
    timeout_error = cli_source["transitive-wait-timeout-final-wrapper"]
    assert timeout_error["keys"] == [
        "source_id",
        "status",
        "last_status_code",
        "timeout_seconds",
        "error",
    ]
    assert timeout_error["model_contribution_keys"] == ["status"]
    resolver_error = cli_source["transitive-delete-resolver-error-text-wrapper"]
    assert resolver_error["keys"] == ["error", "code", "message"]
    assert resolver_error["model_contribution_keys"] == ["id", "title"]
    confirm_by_id = cli_source["transitive-delete-confirm-by-id-error-wrapper"]
    assert confirm_by_id["keys"] == [
        "error",
        "code",
        "message",
        "action",
        "source_id",
        "notebook_id",
    ]
    assert confirm_by_id["conditional_key_groups"] == [
        {
            "condition": "partial-id expansion differs from the requested source id",
            "keys": ["status_message"],
        }
    ]
    confirm_by_title = cli_source["transitive-delete-confirm-by-title-error-wrapper"]
    assert confirm_by_title["keys"][-3:] == ["source_id", "title", "notebook_id"]
    direct_import = cli_source["transitive-research-import-direct-final-wrapper"]
    assert direct_import["nested_keys"] == {
        "imported_sources": ["id", "title"],
        "already_present_sources": ["id", "title", "url"],
    }
    assert {
        "transitive-research-wait-imported-sources-final-wrapper",
        "transitive-source-add-research-imported-sources-final-wrapper",
    } <= set(cli_source)
    cli_label = {row["mode"]: row for row in cli["notebooklm.types.Label"]["projections"]}
    assert cli_label["manual-list-final-wrapper"]["keys"] == ["labels", "count"]
    assert cli_label["manual-list-final-wrapper"]["nested_keys"]["labels[].sources"] == [
        "id",
        "title",
    ]
    assert cli_label["resolver-ambiguous-id-error-wrapper"]["nested_keys"]["candidates"] == [
        "id",
        "emoji",
        "source_count",
    ]
    assert cli_label["resolver-near-miss-error-wrapper"]["nested_keys"]["candidates"] == [
        "id",
        "title",
    ]
    cli_collection = {row["mode"]: row for row in cli["notebooklm.types.Collection"]["projections"]}
    assert cli_collection["resolver-ambiguous-name-error-wrapper"]["nested_keys"]["candidates"] == [
        "id",
        "emoji",
        "notebook_count",
    ]
    assert cli_collection["resolver-near-miss-error-wrapper"]["model_contribution_keys"] == [
        "id",
        "name",
    ]
    cli_note = {row["mode"]: row for row in cli["notebooklm.types.Note"]["projections"]}
    assert cli_note["manual-list-final-wrapper"]["keys"] == [
        "notebook_id",
        "notes",
        "count",
    ]
    assert cli_note["transitive-history-save-note-final-wrapper"]["nested_keys"]["note"] == [
        "id",
        "title",
    ]
    cli_research = {row["mode"]: row for row in cli["notebooklm.types.ResearchTask"]["projections"]}
    assert cli_research["manual-status-projection"]["nested_keys"]["tasks"] == [
        "task_id",
        "status",
        "query",
        "sources",
        "summary",
        "report",
    ]
    assert cli_research["manual-status-projection"]["nested_keys"]["tasks[].sources"] == [
        "url",
        "title",
        "result_type",
    ]
    assert cli_research["transitive-wait-completed-final-projection"]["nested_keys"] == {
        "imported_sources": ["id", "title"]
    }
    no_research = cli_research["transitive-wait-no-research-branch-contribution"]
    assert no_research["keys"] == ["status", "error"]
    assert no_research["model_contribution_keys"] == ["status"]
    assert "timeout branch has no ResearchTask" in no_research["contribution_semantics"]
    assert "transitive-source-add-research-completed-final-projection" in cli_research
    cli_artifact_modes = {row["mode"] for row in cli["notebooklm.types.Artifact"]["projections"]}
    assert {
        "transitive-download-no-artifacts-final-wrapper",
        "transitive-download-error-final-wrapper",
        "transitive-download-all-dry-run-final-wrapper",
        "transitive-download-all-executed-final-wrapper",
        "transitive-download-single-dry-run-final-wrapper",
        "transitive-download-single-downloaded-final-wrapper",
    } <= cli_artifact_modes
    cli_mind_map_modes = {row["mode"] for row in cli["notebooklm.types.MindMap"]["projections"]}
    assert "transitive-artifact-delete-final-carveout" in cli_mind_map_modes
    cli_mind_map = {row["mode"]: row for row in cli["notebooklm.types.MindMap"]["projections"]}
    assert cli_mind_map["transitive-artifact-delete-final-carveout"]["conditional_key_groups"] == [
        {"condition": "note-backed mind map", "keys": ["kind", "note"]}
    ]
    cli_notebook = {row["mode"]: row for row in cli["notebooklm.types.Notebook"]["projections"]}
    assert cli_notebook["artifact-list-title-contribution"]["keys"] == ["title"]
    assert cli_notebook["source-list-title-contribution"]["keys"] == ["title"]

    mcp = contract["channels"]["mcp tool result"]
    assert "notebooklm.types.CitedSourceSelection" in mcp
    mcp_research_sources = {
        row["mode"]: row for row in mcp["notebooklm.types.ResearchSource"]["projections"]
    }
    assert {
        "nested-public-dict-report-omitted",
        "nested-public-dict-report-included-truncated",
        "transitive-import-source-count-contribution",
    } <= set(mcp_research_sources)
    assert (
        "report_markdown"
        not in mcp_research_sources["nested-public-dict-report-omitted"]["optional_keys"]
    )
    mcp_research_tasks = {
        row["mode"]: row for row in mcp["notebooklm.types.ResearchTask"]["projections"]
    }
    assert mcp_research_tasks["transitive-cancel-terminal-final-wrapper"]["keys"] == [
        "status",
        "notebook_id",
        "poll_task_id",
        "run_id",
        "cancel_requested",
    ]
    assert mcp_research_tasks["transitive-cancel-nonterminal-final-wrapper"]["keys"][-1] == (
        "run_status_before"
    )
    assert mcp_research_tasks["transitive-import-final-wrapper"]["model_contribution_keys"] == [
        "sources"
    ]
    count_contribution = mcp_research_sources["transitive-import-source-count-contribution"]
    assert count_contribution["keys"] == ["sources_found"]
    assert "no source field envelope" in count_contribution["contribution_semantics"]
    mcp_ask = {row["mode"]: row for row in mcp["notebooklm.types.AskResult"]["projections"]}
    lite = mcp_ask["app-view:mcp-final-lite-references"]
    assert lite["nested_keys"]["references"] == []
    assert lite["nested_optional_keys"]["references"] == [
        "source_id",
        "citation_number",
        "cited_text",
    ]
    lite_reference = next(
        row
        for row in mcp["notebooklm.types.ChatReference"]["projections"]
        if row["mode"] == "nested-lite-projection"
    )
    assert lite_reference["keys"] == []
    assert lite_reference["optional_keys"] == ["source_id", "citation_number", "cited_text"]
    assert not any(
        row["id"].startswith("mcp.AskResult.app-view-mcp-final-lite-references.nested-references")
        for row in mcp["notebooklm.types.ChatReference"]["projections"]
    )
    full_reference_keys = [
        "source_id",
        "citation_number",
        "cited_text",
        "start_char",
        "end_char",
        "chunk_id",
        "passage_id",
        "answer_start_char",
        "answer_end_char",
        "score",
        "fragment_start_char",
        "fragment_end_char",
        "answer_anchor_start",
        "answer_anchor_end",
    ]
    for channel_name, ask_mode in (
        ("cli --json", "app-view-cli-final-with-note-outcome"),
        ("mcp tool result", "app-view-mcp-final-full-references"),
        ("rest response", "app-view-ask-result-view"),
    ):
        channel_rows = contract["channels"][channel_name]
        prefix = f"{channel_name.split()[0]}.AskResult.{ask_mode}.nested-"
        nested_reference = next(
            row
            for row in channel_rows["notebooklm.types.ChatReference"]["projections"]
            if row["id"] == f"{prefix}references-ChatReference"
        )
        assert nested_reference["keys"] == full_reference_keys
        nested_turn_key = next(
            row
            for row in channel_rows["notebooklm.types.ConversationTurnKey"]["projections"]
            if row["id"] == f"{prefix}turn_key-ConversationTurnKey"
        )
        assert nested_turn_key["keys"] == ["session_id", "turn_id", "turn_code"]
        nested_next_step = next(
            row
            for row in channel_rows["notebooklm.types.NextStepSuggestion"]["projections"]
            if row["id"] == f"{prefix}next_steps-NextStepSuggestion"
        )
        assert nested_next_step["keys"] == ["question", "type_code"]
    mcp_source = {row["mode"]: row for row in mcp["notebooklm.types.Source"]["projections"]}
    assert mcp_source["nested-dataclass-source-rename-result"]["nested_keys"]["source"]
    assert mcp_source["app-view:source-add-drive-final-wrapper"]["keys"] == [
        "source",
        "file_id",
        "mime_type",
        "notebook_id",
        "status",
    ]
    compact_list = mcp_source["manual-compact-list-final-wrapper"]
    assert compact_list["keys"] == ["notebook_id", "sources", "total", "offset", "has_more"]
    assert compact_list["nested_keys"]["sources"] == [
        "id",
        "title",
        "kind",
        "status_label",
        "drive_status_label",
        "created_at",
    ]
    assert mcp_source["app-view:source-read-full-final-wrapper"]["keys"] == [
        "notebook_id",
        "source_id",
        "source",
        "content",
        "char_count",
        "truncated",
        "output_format",
    ]
    assert mcp_source["app-view:source-add-drive-file-final-wrapper"]["keys"] == [
        "source",
        "document_id",
        "notebook_id",
        "status",
    ]
    assert "optional_keys" not in mcp_source["app-view:source-add-drive-file-final-wrapper"]
    assert "transitive-batch-added-item-final-wrapper" in mcp_source
    batch_error = mcp_source["transitive-batch-error-item-final-wrapper"]
    assert batch_error["nested_keys"]["results[].error"] == [
        "code",
        "message",
        "retriable",
    ]
    assert batch_error["nested_optional_keys"]["results[].error"] == [
        "unconfirmed",
        "candidates",
        "hint",
    ]
    wait = mcp_source["app-view:source-wait-final-wrapper"]
    for bucket in ("timed_out", "failed", "not_found"):
        assert wait["nested_keys"][bucket] == ["source_id", "error"]
    assert wait["nested_optional_keys"]["ready"] == ["warning"]
    assert {
        "transitive-research-import-new-source-projection",
        "transitive-research-import-existing-source-projection",
    } <= set(mcp_source)
    remote_upload = mcp_source["transitive-remote-upload-await-final-wrapper"]
    assert remote_upload["keys"] == ["status", "source_id", "file"]
    assert remote_upload["nested_keys"]["file"] == [
        "source_id",
        "name",
        "size",
        "mime",
        "sha256",
    ]
    assert mcp_source["transitive-remote-upload-http-final-wrapper"]["keys"] == [
        "status",
        "source_id",
    ]
    mcp_note = {row["mode"]: row for row in mcp["notebooklm.types.Note"]["projections"]}
    assert "created_at" not in mcp_note["manual-studio-summary-projection"]["keys"]
    assert "transitive-studio-delete-confirmation-wrapper" in mcp_note
    for model in ("notebooklm.types.Note", "notebooklm.types.Artifact"):
        studio_rows = {row["mode"]: row for row in mcp[model]["projections"]}
        assert studio_rows["manual-studio-full-list-final-wrapper"]["nested_union_keys"] == {
            "items": {
                "note": ["id", "title", "type", "content"],
                "artifact": ["id", "title", "type", "status_label", "url"],
            }
        }
        summary_union = studio_rows["manual-studio-summary-list-final-wrapper"][
            "nested_union_keys"
        ]["items"]
        assert summary_union["note"] == [
            "id",
            "title",
            "type",
            "content_preview",
            "char_count",
        ]
        assert summary_union["artifact"][-2:] == ["created_at", "generation_prompt"]
        by_item = studio_rows["manual-studio-by-item-final-wrapper"]["nested_union_keys"]["items"]
        assert by_item["note"][-1] == "content"
        assert by_item["artifact"][-1] == "generation_prompt"
    mcp_artifact_modes = {row["mode"] for row in mcp["notebooklm.types.Artifact"]["projections"]}
    assert {
        "transitive-mcp-stdio-download-selected-wrapper",
        "transitive-mcp-stdio-download-error-wrapper",
        "transitive-mcp-stdio-download-dry-all-wrapper",
        "transitive-mcp-stdio-download-executed-wrapper",
        "transitive-mcp-remote-download-broker-wrapper",
    } <= mcp_artifact_modes
    broker = next(
        row
        for row in mcp["notebooklm.types.Artifact"]["projections"]
        if row["mode"] == "transitive-mcp-remote-download-broker-wrapper"
    )
    assert broker["optional_keys"] == ["artifact_id"]
    assert broker["conditional_key_groups"] == [
        {
            "condition": "inline textual artifact content",
            "keys": ["content", "char_count", "truncated"],
        }
    ]
    mcp_notebook = {row["mode"]: row for row in mcp["notebooklm.types.Notebook"]["projections"]}
    delete_preview = mcp_notebook["always-listed-delete-confirmation-title-wrapper"]
    assert delete_preview["nested_keys"]["preview"] == ["action", "notebook_id", "title"]
    assert delete_preview["model_contribution_keys"] == ["title"]
    suggested_topic = mcp["notebooklm.types.SuggestedTopic"]["projections"]
    assert suggested_topic == [
        {
            "id": (
                "mcp.NotebookDescription.nested-notebook-describe-final."
                "nested-suggested_topics-SuggestedTopic"
            ),
            "mode": "nested-dataclass",
            "keys": ["question", "prompt"],
            "evidence": ["nested-via:notebooklm.types.NotebookDescription.suggested_topics"],
        }
    ]
    source_summary = mcp["notebooklm.types.SourceSummary"]["projections"]
    assert source_summary == [
        {
            "id": (
                "mcp.NotebookMetadata.transitive-notebook-describe-final-with-metadata."
                "nested-sources-SourceSummary"
            ),
            "mode": "nested-dataclass",
            "keys": ["kind", "title", "url"],
            "evidence": ["nested-via:notebooklm.types.NotebookMetadata.sources"],
        }
    ]
    prompt_rows = {
        row["mode"]: row for row in mcp["notebooklm.types.PromptSuggestion"]["projections"]
    }
    suggestion_only = prompt_rows["manual-chat-suggestion-only-final-wrapper"]
    assert suggestion_only["keys"] == ["notebook_id", "suggested_prompts"]
    assert suggestion_only["optional_keys"] == ["history", "conversation_id", "source_ids"]
    share_rows = {row["mode"]: row for row in mcp["notebooklm.types.ShareStatus"]["projections"]}
    share_preview = share_rows["conditional-public-widening-confirmation-wrapper"]
    assert share_preview["model_contribution_keys"] == ["is_public"]
    assert share_preview["projection_condition"] == "current ShareStatus.is_public is false"
    for model in (
        "notebooklm.types.Notebook",
        "notebooklm.types.Source",
        "notebooklm.types.Note",
        "notebooklm.types.Artifact",
    ):
        error_row = next(
            row
            for row in mcp[model]["projections"]
            if row["mode"] == "conditional-resolver-error-text-contribution"
        )
        assert error_row["keys"] == ["message"]
        assert error_row["optional_keys"] == ["hint"]
        assert error_row["model_contribution_keys"] == ["id", "title"]
    label_error = next(
        row
        for row in mcp["notebooklm.types.Label"]["projections"]
        if row["mode"] == "always-listed-resolver-error-text-contribution"
    )
    assert label_error["model_contribution_keys"] == ["id", "name"]

    expected_resolver_ids = {
        "cli": {
            "cli.Notebook.conditional-noncanonical-resolver-id-contribution",
            "cli.Notebook.always-listed-collection-membership-resolver-id-contribution",
            "cli.Source.conditional-noncanonical-resolver-id-contribution",
            "cli.Artifact.conditional-noncanonical-resolver-id-contribution",
            "cli.Artifact.always-listed-download-resolver-id-contribution",
            "cli.Note.conditional-noncanonical-resolver-id-contribution",
            "cli.Collection.always-listed-resolver-id-contribution",
            "cli.Label.always-listed-resolver-id-contribution",
        },
        "mcp": {
            "mcp.Notebook.conditional-noncanonical-resolver-id-contribution",
            "mcp.Source.conditional-noncanonical-resolver-id-contribution",
            "mcp.Note.conditional-noncanonical-resolver-id-contribution",
            "mcp.Artifact.conditional-noncanonical-resolver-id-contribution",
            "mcp.Note.always-listed-studio-item-resolver-id-contribution",
            "mcp.Artifact.always-listed-studio-item-resolver-id-contribution",
            "mcp.Artifact.always-listed-studio-download-resolver-id-contribution",
        },
    }
    assert expected_resolver_ids["cli"] <= set(declared_ids["cli --json"])
    assert expected_resolver_ids["mcp"] <= set(declared_ids["mcp tool result"])
    for expected_ids in expected_resolver_ids.values():
        for projection_id in expected_ids:
            projection = next(
                item
                for channel in contract["channels"].values()
                for model in channel.values()
                for item in model["projections"]
                if item["id"] == projection_id
            )
            assert projection["keys"] == ["id"]

    rest = contract["channels"]["rest response"]
    assert "notebooklm.types.NotebookMetadata" not in rest
    for model, collection_key in (
        ("notebooklm.types.Notebook", "notebooks"),
        ("notebooklm.types.Source", "sources"),
        ("notebooklm.types.Artifact", "artifacts"),
        ("notebooklm.types.Note", "notes"),
    ):
        paged = next(
            row for row in rest[model]["projections"] if "list-paged-final-wrapper" in row["mode"]
        )
        assert collection_key in paged["keys"]
        assert paged["nested_keys"]["meta"] == ["total", "has_more", "limit", "offset"]
    rest_generation = {
        row["mode"]: row for row in rest["notebooklm.types.GenerationStatus"]["projections"]
    }
    for mode in ("http-failed-409-error-envelope", "http-removed-410-error-envelope"):
        assert rest_generation[mode]["nested_keys"]["error"] == ["category", "message"]
    rest_source = {row["mode"]: row for row in rest["notebooklm.types.Source"]["projections"]}
    rest_batch_error = rest_source["transitive-batch-error-item-final-wrapper"]
    assert rest_batch_error["nested_keys"]["results[].error"] == [
        "category",
        "message",
        "retriable",
    ]
    rest_wait = rest_source["app-view:source-wait-final-wrapper"]
    for bucket in ("timed_out", "failed", "not_found"):
        assert rest_wait["nested_keys"][bucket] == ["source_id", "error"]
    assert "nested_optional_keys" not in rest_wait
    rest_research_task = {
        row["mode"]: row for row in rest["notebooklm.types.ResearchTask"]["projections"]
    }
    assert rest_research_task["transitive-import-final-wrapper"]["keys"] == [
        "status",
        "notebook_id",
        "run_id",
        "imported",
        "sources_found",
    ]
    for model in ("notebooklm.types.MindMap", "notebooklm.types.MindMapResult"):
        mind_map = rest[model]["projections"][0]
        assert mind_map["keys"] == ["notebook_id", "kind", "mind_map"]
        assert mind_map["nested_keys"]["mind_map"]
    rest_mind_map_modes = {row["mode"] for row in rest["notebooklm.types.MindMap"]["projections"]}
    assert "transitive-artifact-rename-final-wrapper" in rest_mind_map_modes

    assert (
        contract["secret_bearing_exclusions"]["notebooklm.auth.AuthTokens"]["adapter_reachable"]
        is False
    )


def test_json_envelope_rejects_secret_bearing_channel_reachability() -> None:
    with pytest.raises(ValueError, match="require a redacted adapter projection"):
        _validate_no_secret_channel_models(
            {"cli --json": {"notebooklm.types.Notebook", "notebooklm.auth.AuthTokens"}}
        )

    source = """
from dataclasses import asdict
from notebooklm.auth import AuthTokens

def leak(client, explicit: AuthTokens):
    copied = client.auth
    to_jsonable(copied)
    asdict(explicit)
    to_jsonable({"credentials": [client.auth]})
"""
    assert _secret_serialization_violations(source, filename="mutation.py") == [
        "mutation.py:7",
        "mutation.py:8",
        "mutation.py:9",
    ]


def test_json_envelope_evidence_fingerprint_detects_adapter_shape_mutation() -> None:
    original = """
def sink(result):
    payload = {"id": result.id, "title": result.title}
    return payload
"""
    mutated = original.replace('"title": result.title', '"name": result.title')

    assert _evidence_ast_fingerprint(original, "payload =") != _evidence_ast_fingerprint(
        mutated, "payload ="
    )


def test_json_envelope_conditional_key_groups_preserve_cooccurrence_mutations() -> None:
    before = _normalize_conditional_key_groups(
        ({"condition": "inline", "keys": ("content", "char_count", "truncated")},)
    )
    after = _normalize_conditional_key_groups(
        ({"condition": "inline", "keys": ("content", "char_count")},)
    )

    assert before != after
    with pytest.raises(ValueError, match="duplicate conditional key group"):
        _normalize_conditional_key_groups(
            (
                {"condition": "inline", "keys": ("content",)},
                {"condition": "inline", "keys": ("char_count",)},
            )
        )
