"""Retired generic artifact generation authority.

Generation is owned by the typed services in :mod:`notebooklm._studio`.  This
module remains importable for one release so downstream private imports fail at
the removed class name instead of accidentally dispatching a second RPC path.
"""

__all__: list[str] = []
