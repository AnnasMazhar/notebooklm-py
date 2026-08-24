"""Web sharing codecs returning transport-neutral records."""

from __future__ import annotations

import logging
import reprlib
from typing import Any

from ..._records import SharedUserRecord, ShareStatusRecord
from ...rpc import RPCMethod, safe_index

logger = logging.getLogger("notebooklm._types.sharing")

_METHOD_ID = RPCMethod.GET_SHARE_STATUS.value
_PERMISSIONS = {1: "owner", 2: "editor", 3: "viewer"}
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
    permission = _PERMISSIONS.get(permission_value, "viewer")
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


def decode_share_status(data: list[Any], notebook_id: str) -> ShareStatusRecord:
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
        shared_users=users,
        max_individuals_share_limit=limit,
        is_public_sharing_allowed=allowed,
    )


__all__ = ["decode_share_status", "decode_shared_user"]
