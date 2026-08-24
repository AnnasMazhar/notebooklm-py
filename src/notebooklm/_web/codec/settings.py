"""Web codecs for account settings and advertised limits."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..._records import (
    AccountLimitsRecord,
    SettingsGetLimitsResult,
    SettingsGetResult,
    SettingsSetLanguageResult,
    UserSettingsRecord,
)
from ...rpc import RPCMethod, safe_index

_ACCOUNT_LIMITS_PATH = (0, 1)
_NOTEBOOK_LIMIT_INDEX = 1
_SOURCE_LIMIT_INDEX = 2
_TIER_INDEX = 4
_GET_SETTINGS_PREFIX = (0, 2)
_GET_SETTINGS_TAIL = (4, 0)
_SET_LANGUAGE_PREFIX = (2,)
_SET_LANGUAGE_TAIL = (4, 0)


def encode_get_user_settings() -> list[Any]:
    """Build a fresh account-routed ``GetOrCreateAccount`` payload."""
    return [
        None,
        [1, None, None, None, None, None, None, None, None, None, [1]],
    ]


def encode_set_output_language(language: str) -> list[Any]:
    """Build the exact ``MutateAccount`` language payload."""
    return [[[None, [[None, None, None, None, [language]]]]]]


def _extract_language(
    data: Any,
    required_prefix: Sequence[int],
    optional_tail: Sequence[int],
    *,
    method_id: str,
    source: str,
) -> object | None:
    """Preserve mandatory-envelope drift and optional-language tolerance."""
    block = safe_index(data, *required_prefix, method_id=method_id, source=source)
    result: Any = block
    for index in optional_tail:
        if not isinstance(result, list) or not 0 <= index < len(result):
            return None
        result = result[index]
    # The legacy projection returned unknown truthy leaves verbatim despite its
    # ``str | None`` annotation. Keep that tolerant runtime behavior.
    return result or None


def _nested_list(data: Any, path: Sequence[int]) -> list[Any] | None:
    result = data
    try:
        for index in path:
            if not isinstance(result, list):
                return None
            result = result[index]
    except IndexError:
        return None
    return result if isinstance(result, list) else None


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) and value > 0 else None


def decode_account_limits(data: Any) -> AccountLimitsRecord:
    """Decode limits tolerantly while preserving the untouched limit block."""
    limits = _nested_list(data, _ACCOUNT_LIMITS_PATH)
    if limits is None:
        return AccountLimitsRecord()
    return AccountLimitsRecord(
        notebook_limit=(
            _positive_int(limits[_NOTEBOOK_LIMIT_INDEX])
            if len(limits) > _NOTEBOOK_LIMIT_INDEX
            else None
        ),
        source_limit=(
            _positive_int(limits[_SOURCE_LIMIT_INDEX])
            if len(limits) > _SOURCE_LIMIT_INDEX
            else None
        ),
        raw_limits=tuple(limits),
        tier=(_positive_int(limits[_TIER_INDEX]) if len(limits) > _TIER_INDEX else None),
    )


def decode_get_user_settings(data: Any) -> SettingsGetResult:
    """Decode both public settings projections from one response."""
    return SettingsGetResult(
        UserSettingsRecord(
            limits=decode_account_limits(data),
            output_language=_extract_language(
                data,
                _GET_SETTINGS_PREFIX,
                _GET_SETTINGS_TAIL,
                method_id=RPCMethod.GET_USER_SETTINGS.value,
                source="_settings._extract_output_language",
            ),
        )
    )


def decode_get_account_limits(data: Any) -> SettingsGetLimitsResult:
    """Decode only tolerant account limits, never the strict language prefix."""
    return SettingsGetLimitsResult(decode_account_limits(data))


def decode_set_output_language(data: Any) -> SettingsSetLanguageResult:
    """Decode the optional language slot from a mutation response."""
    return SettingsSetLanguageResult(
        _extract_language(
            data,
            _SET_LANGUAGE_PREFIX,
            _SET_LANGUAGE_TAIL,
            method_id=RPCMethod.SET_USER_SETTINGS.value,
            source="_settings.set_output_language",
        )
    )


__all__ = [
    "decode_account_limits",
    "decode_get_account_limits",
    "decode_get_user_settings",
    "decode_set_output_language",
    "encode_get_user_settings",
    "encode_set_output_language",
]
