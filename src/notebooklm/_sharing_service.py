"""Transport-neutral semantic service for the P6.5 sharing slice.

Owns the sharing domain's policy — which permissions a caller may send, whether
a grant batch is coherent, and what each intent logs — and invokes only typed
operation definitions through :class:`~notebooklm._backend.BackendAdapter`. It
holds no wire vocabulary: the ``SHARE_NOTEBOOK`` / ``GET_SHARE_STATUS`` /
``MutateProject`` grammar lives in ``_web/codec/sharing.py``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

from ._backend import BackendAdapter
from ._deadline import RuntimeDeadline
from ._projectors import project_share_status
from ._records import (
    SHARING_GET_DEF,
    SHARING_SET_PUBLIC_DEF,
    SHARING_SET_VIEW_LEVEL_DEF,
    SHARING_UPDATE_USERS_DEF,
    SharePermissionLevel,
    ShareViewScope,
    SharingGetInput,
    SharingSetPublicInput,
    SharingSetViewLevelInput,
    SharingUpdateUsersInput,
    SharingUserGrant,
)

if TYPE_CHECKING:
    from .types import ShareStatus

# Explicitly the pre-migration logger name rather than ``__name__``. These
# DEBUG lines are the only record of which grantee a call actually addressed —
# a typoed address is exactly what they get read for — so the migration keeps
# them reachable under the logger operators already configure. The same
# deliberate re-homing is applied to the notebook/source loggers in
# ``_web/backend.py``.
logger = logging.getLogger("notebooklm._sharing")


class SharingService:
    """Validate sharing intents and invoke their typed backend operations."""

    __slots__ = ("_backend",)

    def __init__(self, backend: BackendAdapter) -> None:
        self._backend = backend

    async def get_status(
        self,
        notebook_id: str,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> ShareStatus:
        logger.debug("Getting share status for notebook: %s", notebook_id)
        result = await self._backend.invoke(
            SHARING_GET_DEF,
            SharingGetInput(notebook_id),
            deadline=deadline,
        )
        return project_share_status(result.status)

    async def set_public(
        self,
        notebook_id: str,
        public: bool,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> ShareStatus:
        logger.debug("Setting notebook %s public=%s", notebook_id, public)
        result = await self._backend.invoke(
            SHARING_SET_PUBLIC_DEF,
            SharingSetPublicInput(notebook_id, public),
            deadline=deadline,
        )
        return project_share_status(result.status)

    async def set_view_level(
        self,
        notebook_id: str,
        view_level: ShareViewScope,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> ShareStatus:
        logger.debug("Setting notebook %s view level to %s", notebook_id, view_level.name)
        result = await self._backend.invoke(
            SHARING_SET_VIEW_LEVEL_DEF,
            SharingSetViewLevelInput(notebook_id, view_level),
            deadline=deadline,
        )
        return project_share_status(result.status)

    async def set_users(
        self,
        notebook_id: str,
        grants: Sequence[tuple[str, SharePermissionLevel]],
        *,
        notify: bool = True,
        welcome_message: str = "",
        deadline: RuntimeDeadline | None = None,
    ) -> ShareStatus:
        """Upsert several individual-user permissions in one request.

        Rejects an empty batch, an owner assignment, a removal smuggled in as a
        grant, and a repeated grantee — the backend answers a batch containing
        the same address twice with success while silently leaving that user's
        permission unchanged, so there is no first-wins or last-wins rule to
        honour. The duplicate comparison is exact: RFC 5321 makes the local
        part case-sensitive, so folding case here would reject two addresses
        the server may well treat as distinct identities.
        """
        if not grants:
            raise ValueError("Must provide at least one user grant")
        seen: set[str] = set()
        for email, permission in grants:
            if permission is SharePermissionLevel.OWNER:
                raise ValueError("Cannot assign OWNER permission")
            if permission is SharePermissionLevel.REMOVE:
                raise ValueError("Use remove_user() instead")
            if email in seen:
                raise ValueError(
                    f"Duplicate email in grants: {email!r}. The backend silently "
                    "ignores a repeated grantee instead of applying either entry; "
                    "send one grant per user."
                )
            seen.add(email)

        logger.debug(
            "Setting %d user permission(s) on notebook %s: %s",
            len(grants),
            notebook_id,
            [(email, permission.name) for email, permission in grants],
        )
        result = await self._backend.invoke(
            SHARING_UPDATE_USERS_DEF,
            SharingUpdateUsersInput(
                notebook_id,
                tuple(SharingUserGrant(email, permission) for email, permission in grants),
                notify=notify,
                welcome_message=welcome_message,
            ),
            deadline=deadline,
        )
        return project_share_status(result.status)

    async def update_user(
        self,
        notebook_id: str,
        email: str,
        permission: SharePermissionLevel,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> ShareStatus:
        """Replace one user's permission through the shared upsert."""
        logger.debug(
            "Updating user %s permission to %s in notebook %s",
            email,
            permission.name,
            notebook_id,
        )
        return await self.set_users(
            notebook_id,
            [(email, permission)],
            notify=False,
            deadline=deadline,
        )

    async def remove_user(
        self,
        notebook_id: str,
        email: str,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> ShareStatus:
        """Revoke one user's access.

        Singular by design. A batch of removals only works when every target is
        currently shared: if any requested address is already absent the backend
        silently drops the whole request — including the users that *are*
        present — and reports no failure. A plural removal therefore needs a
        share-status preflight and post-verification rather than a wider entry
        list; see ``docs/rpc-reference.md``.
        """
        logger.debug("Removing user %s from notebook %s", email, notebook_id)
        result = await self._backend.invoke(
            SHARING_UPDATE_USERS_DEF,
            SharingUpdateUsersInput(
                notebook_id,
                (SharingUserGrant(email, SharePermissionLevel.REMOVE),),
                notify=False,
            ),
            deadline=deadline,
        )
        return project_share_status(result.status)


__all__ = ["SharingService"]
