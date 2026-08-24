"""Stable ``batchexecute`` notebook RPC request payload builders.

Kept in a sibling module (rather than inline in ``_notebooks.py``) so the
notebook RPC façade stays under the ADR-0008 module-size budget; mirrors the
``_settings`` / ``_source.upload_payloads`` split.
"""

from __future__ import annotations

from typing import Any

from ._source.upload_payloads import build_template_block


def build_create_notebook_params(title: str) -> list[Any]:
    """Return the canonical CREATE_NOTEBOOK RPC payload.

    The trailing :func:`build_template_block` replaced the old flat ``[2], [1]``
    tail that migrated backends now reject with ``status=3`` (#1546).
    """
    return [title, None, None, build_template_block()]


def build_get_notebook_params(notebook_id: str) -> list[Any]:
    """Return the canonical GET_NOTEBOOK (``rLM1Ne``) RPC payload.

    The Gemini-3.5 rollout migrated the read path's trailing template block from
    the flat ``[2]`` to the same nested :func:`build_template_block` wrapper the
    write path adopted in #1548 (issue #1549). Live-verified forward-compatible:
    the nested shape returns a byte-identical decoded notebook (notebook id /
    title and every ``SourceRow``) as the flat ``[2]`` on an un-migrated account,
    so it is safe across cohorts. The trailing ``None, 0`` is unchanged — only
    the template block at position 2 is migrated (the narrow scope #1549 tracks).
    """
    return [notebook_id, None, build_template_block(), None, 0]


def build_update_notebook_params(
    notebook_id: str,
    *,
    title: str | None = None,
    emoji: str | None = None,
) -> list[Any]:
    """Return the ``MutateProject`` change-property payload.

    ``ProjectMutation.changeProperty`` is the fourth mutation variant. Inside
    that block, tag 2 is the title and tag 3 is the emoji, so the positional
    JSON is ``[None, title, emoji]``. The trailing emoji slot is omitted when
    it is not being changed, preserving the long-standing title-only request
    shape (and its recorded-cassette contract). ``None`` leaves a property
    unchanged; callers validate that at least one value is supplied.
    """
    change_property = [None, title]
    if emoji is not None:
        change_property.append(emoji)
    return [notebook_id, [[None, None, None, change_property]]]
