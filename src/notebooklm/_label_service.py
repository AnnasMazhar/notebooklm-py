"""Transport-neutral semantic service for the P6.4 label/collection slice.

Source labels and collections are one wire surface twice: a collection is a
label with a distinct type discriminator and no notebook parent, so they share
the RPC ids ``agX4Bc`` / ``I3xc3c`` / ``le8sX`` / ``GyzE7e`` verbatim.  This
module is the single semantic authority over both; :class:`LabelKind` is the
explicit discriminator that selects the operation pair, and no wire vocabulary
crosses this boundary.

The two public facades (``client.labels`` and ``client.collections``) keep their
own argument validation, exception vocabulary, and membership joins; everything
between a validated request and a neutral :class:`LabelRecord` lives here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._backend import BackendAdapter
from ._deadline import RuntimeDeadline
from ._operations import OperationDef
from ._records import (
    COLLECTION_CREATE_DEF,
    COLLECTION_DELETE_DEF,
    COLLECTION_GET_DEF,
    COLLECTION_LIST_DEF,
    COLLECTION_UPDATE_DEF,
    LABEL_CREATE_DEF,
    LABEL_DELETE_DEF,
    LABEL_GENERATE_DEF,
    LABEL_GET_DEF,
    LABEL_LIST_DEF,
    LABEL_UPDATE_DEF,
    LabelCreateInput,
    LabelDeleteInput,
    LabelGenerateInput,
    LabelGetInput,
    LabelKind,
    LabelListInput,
    LabelRecord,
    LabelUpdateInput,
)


@dataclass(frozen=True, slots=True)
class _KindBinding:
    """The operation pair one :class:`LabelKind` dispatches through."""

    list_def: OperationDef[Any, Any]
    get_def: OperationDef[Any, Any]
    create_def: OperationDef[Any, Any]
    update_def: OperationDef[Any, Any]
    delete_def: OperationDef[Any, Any]
    generate_def: OperationDef[Any, Any] | None


_KIND_BINDINGS: dict[LabelKind, _KindBinding] = {
    LabelKind.SOURCE_LABEL: _KindBinding(
        list_def=LABEL_LIST_DEF,
        get_def=LABEL_GET_DEF,
        create_def=LABEL_CREATE_DEF,
        update_def=LABEL_UPDATE_DEF,
        delete_def=LABEL_DELETE_DEF,
        generate_def=LABEL_GENERATE_DEF,
    ),
    # Collections have no auto-grouping mode: ``agX4Bc``'s scope slot is a
    # source-label concept, so the account-level dialect binds no generate.
    LabelKind.COLLECTION: _KindBinding(
        list_def=COLLECTION_LIST_DEF,
        get_def=COLLECTION_GET_DEF,
        create_def=COLLECTION_CREATE_DEF,
        update_def=COLLECTION_UPDATE_DEF,
        delete_def=COLLECTION_DELETE_DEF,
        generate_def=None,
    ),
}


class LabelSetService:
    """Invoke the discriminated label/collection operations and return records.

    One instance is bound to one :class:`LabelKind` for its whole lifetime, so a
    facade can never accidentally address the other dialect's operations.
    """

    __slots__ = ("_backend", "_binding", "_kind")

    def __init__(self, backend: BackendAdapter, kind: LabelKind) -> None:
        self._backend = backend
        self._kind = kind
        self._binding = _KIND_BINDINGS[kind]

    @property
    def kind(self) -> LabelKind:
        """The discriminator every request from this service carries."""
        return self._kind

    async def list(
        self,
        notebook_id: str | None = None,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> tuple[LabelRecord, ...]:
        """List the whole group set in backend order."""
        result = await self._backend.invoke(
            self._binding.list_def,
            LabelListInput(self._kind, notebook_id),
            deadline=deadline,
        )
        return tuple(result.labels)

    async def get(
        self,
        label_id: str,
        notebook_id: str | None = None,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> LabelRecord | None:
        """Select one group by exact id; ``None`` is the not-found state."""
        result = await self._backend.invoke(
            self._binding.get_def,
            LabelGetInput(self._kind, label_id, notebook_id),
            deadline=deadline,
        )
        return result.label

    async def generate(
        self,
        notebook_id: str,
        *,
        replace_existing: bool = False,
        deadline: RuntimeDeadline | None = None,
    ) -> tuple[LabelRecord, ...]:
        """Auto-group sources into topic labels and return the full post-op set."""
        definition = self._binding.generate_def
        if definition is None:  # pragma: no cover - collections bind no generate
            raise ValueError(f"{self._kind.value} sets have no generation mode")
        result = await self._backend.invoke(
            definition,
            LabelGenerateInput(notebook_id, replace_existing=replace_existing),
            deadline=deadline,
        )
        return tuple(result.labels)

    async def create(
        self,
        name: str,
        notebook_id: str | None = None,
        *,
        emoji: str = "",
        deadline: RuntimeDeadline | None = None,
    ) -> LabelRecord:
        """Create one empty named group, reconciled by exact id-diff.

        Names may collide, so the backend attributes the new group by the id
        that was absent from its pre-create snapshot and raises rather than
        guessing when zero or several ids are new.
        """
        result = await self._backend.invoke(
            self._binding.create_def,
            LabelCreateInput(self._kind, name, notebook_id, emoji),
            deadline=deadline,
        )
        return result.label

    async def update(
        self,
        label_id: str,
        notebook_id: str | None = None,
        *,
        name: str | None = None,
        emoji: str | None = None,
        add_member_ids: tuple[str, ...] = (),
        remove_member_ids: tuple[str, ...] = (),
        return_object: bool = True,
        deadline: RuntimeDeadline | None = None,
    ) -> LabelRecord | None:
        """Apply one field or membership mutation to an existing group.

        The not-found contract holds in both ``return_object`` modes; the record
        is returned only when the caller asked for it.
        """
        result = await self._backend.invoke(
            self._binding.update_def,
            LabelUpdateInput(
                self._kind,
                label_id,
                notebook_id,
                name=name,
                emoji=emoji,
                add_member_ids=add_member_ids,
                remove_member_ids=remove_member_ids,
                return_object=return_object,
            ),
            deadline=deadline,
        )
        return result.label

    async def delete(
        self,
        label_ids: tuple[str, ...],
        notebook_id: str | None = None,
        *,
        deadline: RuntimeDeadline | None = None,
    ) -> None:
        """Delete groups in one batch; an absent id is an idempotent no-op."""
        await self._backend.invoke(
            self._binding.delete_def,
            LabelDeleteInput(self._kind, label_ids, notebook_id),
            deadline=deadline,
        )


def require_member_ids(
    member_ids: list[str],
    method_name: str,
    noun: str,
) -> tuple[str, ...]:
    """Reject an empty membership request and dedupe, order-preserving.

    Both dialects issue one wire call per member id, so duplicates would be
    redundant round-trips (and an append-twice on the wire). Shared by both
    facades because the choreography, not the noun, is the contract.
    """
    if not member_ids:
        raise ValueError(f"{method_name} requires at least one {noun} id")
    return tuple(dict.fromkeys(member_ids))


__all__ = ["LabelSetService", "require_member_ids"]
