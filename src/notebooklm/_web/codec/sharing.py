"""Web sharing codecs returning transport-neutral records."""

from __future__ import annotations

import logging
import reprlib
from typing import Any

from ..._records import (
    ShareAccessLevel,
    SharedUserRecord,
    SharePermissionLevel,
    ShareStatusRecord,
    ShareViewScope,
    SharingUserGrant,
)
from ...rpc import RPCMethod, safe_index
from ...rpc.types import ShareAccess, SharePermission, ShareViewLevel

logger = logging.getLogger("notebooklm._types.sharing")

_METHOD_ID = RPCMethod.GET_SHARE_STATUS.value
_PERMISSIONS = {
    SharePermission.OWNER.value: SharePermissionLevel.OWNER,
    SharePermission.EDITOR.value: SharePermissionLevel.EDITOR,
    SharePermission.VIEWER.value: SharePermissionLevel.VIEWER,
    SharePermission._REMOVE.value: SharePermissionLevel.REMOVE,
}
_PERMISSION_CODES = {
    SharePermissionLevel.OWNER: SharePermission.OWNER,
    SharePermissionLevel.EDITOR: SharePermission.EDITOR,
    SharePermissionLevel.VIEWER: SharePermission.VIEWER,
    SharePermissionLevel.REMOVE: SharePermission._REMOVE,
}
_VIEW_LEVEL_CODES = {
    ShareViewScope.FULL_NOTEBOOK: ShareViewLevel.FULL_NOTEBOOK,
    ShareViewScope.CHAT_ONLY: ShareViewLevel.CHAT_ONLY,
}
_warned_malformed_share_slots: set[tuple[str, str]] = set()
_MAX_DRIFT_REPR_LEN = 120


def _scalar_at(data: list[Any], position: int) -> Any:
    if len(data) <= position:
        return None
    return safe_index(
        data,
        position,
        method_id=_METHOD_ID,
        source="ShareStatus.from_api_response",
    )


def _warn_if_malformed(value: Any, field_name: str, expected: str) -> None:
    if value is None:
        return
    key = (field_name, type(value).__name__)
    if key in _warned_malformed_share_slots:
        return
    _warned_malformed_share_slots.add(key)
    logger.warning(
        "GET_SHARE_STATUS %s slot malformed — reporting 'no claim' (expected %s, got %s: %s)",
        field_name,
        expected,
        type(value).__name__,
        repr(value)[:_MAX_DRIFT_REPR_LEN],
    )


def decode_shared_user(data: list[Any]) -> SharedUserRecord:
    """Decode one collaborator entry."""

    email = ""
    if data:
        raw_email = safe_index(
            data,
            0,
            method_id=_METHOD_ID,
            source="SharedUser.from_api_response",
        )
        if isinstance(raw_email, str):
            email = raw_email
        elif raw_email is not None:
            logger.warning(
                "Share user email slot malformed — fabricating empty email "
                "(expected str at entry[0], got %s; entry=%s)",
                type(raw_email).__name__,
                reprlib.repr(data),
            )
    permission_value = (
        safe_index(
            data,
            1,
            method_id=_METHOD_ID,
            source="SharedUser.from_api_response",
        )
        if len(data) > 1
        else 3
    )
    permission = _PERMISSIONS.get(permission_value, SharePermissionLevel.VIEWER)
    info = (
        safe_index(
            data,
            3,
            method_id=_METHOD_ID,
            source="SharedUser.from_api_response",
        )
        if len(data) > 3
        else None
    )
    display_name = None
    avatar_url = None
    if isinstance(info, list):
        display_name = (
            safe_index(info, 0, method_id=_METHOD_ID, source="SharedUser.from_api_response")
            if info
            else None
        )
        avatar_url = (
            safe_index(info, 1, method_id=_METHOD_ID, source="SharedUser.from_api_response")
            if len(info) > 1
            else None
        )
    return SharedUserRecord(
        email=email,
        permission=permission,
        display_name=display_name,
        avatar_url=avatar_url,
    )


def build_get_share_status_params(notebook_id: str) -> list[Any]:
    """Build the ``GET_SHARE_STATUS`` request payload."""

    return [notebook_id, [2]]


def build_share_visibility_params(notebook_id: str, public: bool) -> list[Any]:
    """Build the ``SHARE_NOTEBOOK`` envelope that sets link visibility."""

    access = ShareAccess.ANYONE_WITH_LINK if public else ShareAccess.RESTRICTED
    return [
        [[notebook_id, None, [access.value], [access.value, ""]]],
        1,
        None,
        [2],
    ]


def build_share_view_level_params(notebook_id: str, view_level: ShareViewScope) -> list[Any]:
    """Build the ``MutateProject`` payload that sets the viewer scope."""

    code = _VIEW_LEVEL_CODES[view_level]
    return [
        notebook_id,
        [[None, None, None, None, None, None, None, None, [[code.value]]]],
    ]


def build_share_grants_params(
    notebook_id: str,
    grants: tuple[SharingUserGrant, ...],
    *,
    notify: bool,
    welcome_message: str,
) -> list[Any]:
    """Build one individual-user grant/removal envelope."""

    removal = any(grant.permission is SharePermissionLevel.REMOVE for grant in grants)
    message_block: list[Any] = (
        [0, ""] if removal else [0 if welcome_message else 1, welcome_message]
    )
    return [
        [
            [
                notebook_id,
                [
                    [grant.email, None, _PERMISSION_CODES[grant.permission].value]
                    for grant in grants
                ],
                None,
                message_block,
            ]
        ],
        1 if notify else 0,
        None,
        [2],
    ]


def decode_share_status(
    data: list[Any],
    notebook_id: str,
    *,
    view_level: ShareViewScope | None = None,
) -> ShareStatusRecord:
    """Decode ``GET_SHARE_STATUS`` without constructing exported values."""

    entries = (
        safe_index(
            data,
            0,
            method_id=_METHOD_ID,
            source="ShareStatus.from_api_response",
        )
        if data
        else None
    )
    users = (
        tuple(decode_shared_user(entry) for entry in entries if isinstance(entry, list))
        if isinstance(entries, list)
        else ()
    )
    public_slot = (
        safe_index(
            data,
            1,
            method_id=_METHOD_ID,
            source="ShareStatus.from_api_response",
        )
        if len(data) > 1
        else None
    )
    public_block = public_slot if isinstance(public_slot, list) else None
    is_public = (
        bool(
            safe_index(
                public_block,
                0,
                method_id=_METHOD_ID,
                source="ShareStatus.from_api_response",
            )
        )
        if public_block
        else False
    )

    limit_slot = _scalar_at(data, 2)
    limit = limit_slot if isinstance(limit_slot, int) and not isinstance(limit_slot, bool) else None
    if limit is None:
        _warn_if_malformed(limit_slot, "maxIndividualsShareLimit", "int")
    allowed_slot = _scalar_at(data, 3)
    allowed = allowed_slot if isinstance(allowed_slot, bool) else None
    if allowed is None:
        _warn_if_malformed(allowed_slot, "isPublicSharingAllowed", "bool")
    return ShareStatusRecord(
        notebook_id=notebook_id,
        is_public=is_public,
        access=(
            ShareAccessLevel.ANYONE_WITH_LINK
            if is_public
            else ShareAccessLevel.RESTRICTED
        ),
        view_level=view_level or ShareViewScope.FULL_NOTEBOOK,
        shared_users=users,
        max_individuals_share_limit=limit,
        is_public_sharing_allowed=allowed,
    )


__all__ = [
    "build_get_share_status_params",
    "build_share_grants_params",
    "build_share_view_level_params",
    "build_share_visibility_params",
    "decode_share_status",
    "decode_shared_user",
]
