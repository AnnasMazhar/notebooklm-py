"""Web workflow bindings for settings, limits, and suggestion compatibility."""

from __future__ import annotations

from .._deadline import RuntimeDeadline
from .._notebook_payloads import build_get_notebook_params
from .._operations import Operation
from .._records import (
    ArtifactSuggestReportsInput,
    ArtifactSuggestReportsResult,
    NotebookSuggestPromptsInput,
    NotebookSuggestPromptsResult,
    SettingsGetInput,
    SettingsGetLimitsInput,
    SettingsGetLimitsResult,
    SettingsGetResult,
    SettingsSetLanguageInput,
    SettingsSetLanguageResult,
)
from ..rpc import RPCMethod
from .codec import settings as settings_codec
from .codec import suggestions as suggestions_codec
from .research import ResearchWebHandlers


class SettingsSuggestionWebHandlers(ResearchWebHandlers):
    """Reusable account and suggestion handlers mixed into the web backend."""

    async def _notebook_suggest_prompts(
        self,
        value: NotebookSuggestPromptsInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> NotebookSuggestPromptsResult:
        source_ids = value.source_ids
        if source_ids is None:
            notebook = await self._rpc_call(
                RPCMethod.GET_NOTEBOOK,
                build_get_notebook_params(value.notebook_id),
                operation=Operation.NOTEBOOK_SUGGEST_PROMPTS,
                deadline=deadline,
                source_path=f"/notebook/{value.notebook_id}",
            )
            source_ids = suggestions_codec.decode_prompt_source_ids(
                notebook,
                notebook_id=value.notebook_id,
            )
        result = await self._rpc_call(
            RPCMethod.SUGGEST_PROMPTS,
            suggestions_codec.encode_prompt_suggestions(
                value.notebook_id,
                source_ids,
                mode=value.mode,
                query=value.query,
            ),
            operation=Operation.NOTEBOOK_SUGGEST_PROMPTS,
            deadline=deadline,
            source_path=f"/notebook/{value.notebook_id}",
            allow_null=True,
        )
        return suggestions_codec.decode_prompt_suggestions(result)

    async def _artifact_suggest_reports(
        self,
        value: ArtifactSuggestReportsInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> ArtifactSuggestReportsResult:
        result = await self._rpc_call(
            RPCMethod.GET_SUGGESTED_REPORTS,
            suggestions_codec.encode_report_suggestions(value.notebook_id),
            operation=Operation.ARTIFACT_SUGGEST_REPORTS,
            deadline=deadline,
            source_path=f"/notebook/{value.notebook_id}",
            allow_null=True,
        )
        return suggestions_codec.decode_report_suggestions(result)

    async def _settings_get(
        self,
        value: SettingsGetInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> SettingsGetResult:
        del value
        result = await self._rpc_call(
            RPCMethod.GET_USER_SETTINGS,
            settings_codec.encode_get_user_settings(),
            operation=Operation.SETTINGS_GET,
            deadline=deadline,
            source_path="/",
        )
        return settings_codec.decode_get_user_settings(result)

    async def _settings_get_limits(
        self,
        value: SettingsGetLimitsInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> SettingsGetLimitsResult:
        del value
        result = await self._rpc_call(
            RPCMethod.GET_USER_SETTINGS,
            settings_codec.encode_get_user_settings(),
            operation=Operation.SETTINGS_GET_LIMITS,
            deadline=deadline,
            source_path="/",
        )
        return settings_codec.decode_get_account_limits(result)

    async def _settings_set_language(
        self,
        value: SettingsSetLanguageInput,
        *,
        deadline: RuntimeDeadline | None,
    ) -> SettingsSetLanguageResult:
        result = await self._rpc_call(
            RPCMethod.SET_USER_SETTINGS,
            settings_codec.encode_set_output_language(value.language),
            operation=Operation.SETTINGS_SET_LANGUAGE,
            deadline=deadline,
            source_path="/",
        )
        return settings_codec.decode_set_output_language(result)


__all__ = ["SettingsSuggestionWebHandlers"]
