"""Transport-neutral semantic service for settings and account limits."""

from __future__ import annotations

from typing import cast

from ._backend import BackendAdapter
from ._deadline import RuntimeDeadline
from ._projectors import project_account_limits, project_user_settings
from ._records import (
    SETTINGS_GET_DEF,
    SETTINGS_GET_LIMITS_DEF,
    SETTINGS_SET_LANGUAGE_DEF,
    SettingsGetInput,
    SettingsGetLimitsInput,
    SettingsSetLanguageInput,
)
from .types import AccountLimits, UserSettings


class SettingsService:
    """Invoke typed account operations and project their public values."""

    __slots__ = ("_backend",)

    def __init__(self, backend: BackendAdapter) -> None:
        self._backend = backend

    async def get_user_settings(
        self,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> UserSettings:
        result = await self._backend.invoke(
            SETTINGS_GET_DEF,
            SettingsGetInput(),
            deadline=deadline,
        )
        return project_user_settings(result.settings)

    async def get_output_language(
        self,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> str | None:
        result = await self._backend.invoke(
            SETTINGS_GET_DEF,
            SettingsGetInput(),
            deadline=deadline,
        )
        return cast(str | None, result.settings.output_language)

    async def get_account_limits(
        self,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> AccountLimits:
        result = await self._backend.invoke(
            SETTINGS_GET_LIMITS_DEF,
            SettingsGetLimitsInput(),
            deadline=deadline,
        )
        return project_account_limits(result.limits)

    async def set_output_language(
        self,
        language: str,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> str | None:
        result = await self._backend.invoke(
            SETTINGS_SET_LANGUAGE_DEF,
            SettingsSetLanguageInput(language),
            deadline=deadline,
        )
        return cast(str | None, result.output_language)


__all__ = ["SettingsService"]
