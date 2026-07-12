"""Transport-neutral ``source wait`` business logic.

This is the Click-free core behind ``source wait`` (imported directly by the
``cli/source_cmd.py`` / ``cli/_source_render.py`` command layer): it owns the
source-readiness polling loop and the translation of the three
``SourceWaitError`` subclasses into a discriminated :class:`SourceWaitOutcome`.
Every transport adapter (the Click CLI today, the FastMCP server / future HTTP
later) drives this core and renders the typed outcome into its own envelope
vocabulary + exit-code policy.

The long-running wait is wrapped in a caller-supplied ``wait_context`` async
context manager so the adapter can render its own progress surface (the CLI
passes a Rich elapsed-time spinner); the neutral default is a no-op. The
caller is responsible for resolving ``plan.source_id`` to a full UUID BEFORE
calling this executor, so the adapter's progress message and JSON envelope
carry the resolved id consistently.

Typed-outcome contract (the exit policy is owned by the adapter):

* :class:`SourceWaitReady`           — source reached READY before timeout (CLI exits 0).
* :class:`SourceWaitNotFound`        — :class:`SourceNotFoundError` (CLI exits 1).
* :class:`SourceWaitProcessingError` — :class:`SourceProcessingError` (CLI exits 1).
* :class:`SourceWaitTimeout`         — :class:`SourceTimeoutError` (CLI exits 2).

This module is transport-neutral — no ``click`` / ``rich`` / ``cli`` /
``fastmcp`` imports (enforced by ``tests/_guardrails/test_app_boundary.py``).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..types import (
    Source,
    SourceNotFoundError,
    SourceProcessingError,
    SourceTimeoutError,
)

if TYPE_CHECKING:
    from ..client import NotebookLMClient

#: Upper bound on a single ``source_wait`` timeout (seconds) — bounds how long one
#: request can hold a worker, and turns a ``timeout=inf`` into a clean rejection.
MAX_WAIT_TIMEOUT = 3600.0

#: Max source ids one ``source_wait`` may target — blocks pathological fan-out while
#: preserving normal all-source waits (notebooks are source-limited).
MAX_WAIT_SOURCE_IDS = 100

#: Max simultaneous per-source pollers one multi-source wait spawns.
MAX_WAIT_CONCURRENT_SOURCES = 8


@dataclass(frozen=True)
class SourceWaitPlan:
    """Prepared inputs for ``execute_source_wait``."""

    notebook_id: str
    source_id: str
    timeout: float
    interval: float


@dataclass(frozen=True)
class SourceWaitReady:
    """Source reached READY before timeout. Caller exits 0."""

    source: Source


@dataclass(frozen=True)
class SourceWaitNotFound:
    """``client.sources.wait_until_ready`` raised :class:`SourceNotFoundError`."""

    error: SourceNotFoundError


@dataclass(frozen=True)
class SourceWaitProcessingError:
    """``client.sources.wait_until_ready`` raised :class:`SourceProcessingError`."""

    error: SourceProcessingError


@dataclass(frozen=True)
class SourceWaitTimeout:
    """``client.sources.wait_until_ready`` raised :class:`SourceTimeoutError`."""

    error: SourceTimeoutError


SourceWaitOutcome = (
    SourceWaitReady | SourceWaitNotFound | SourceWaitProcessingError | SourceWaitTimeout
)


async def execute_source_wait(
    client: NotebookLMClient,
    plan: SourceWaitPlan,
    *,
    wait_context: Callable[[], AbstractAsyncContextManager[None]] | None = None,
) -> SourceWaitOutcome:
    """Run the ``source wait`` workflow and return a typed outcome.

    The caller is responsible for resolving ``plan.source_id`` to a full
    UUID BEFORE calling this executor (so the spinner message and the
    caller's JSON envelope carry the resolved id consistently).

    Presentation and exit-code policy live in the caller — this executor
    only owns the polling loop and exception-to-outcome mapping. The
    optional ``wait_context`` lets the adapter wrap the wait in its own
    progress surface; the neutral default is a no-op context.
    """
    try:
        context = wait_context or contextlib.nullcontext
        async with context():
            source = await client.sources.wait_until_ready(
                plan.notebook_id,
                plan.source_id,
                timeout=plan.timeout,
                initial_interval=plan.interval,
            )
    except SourceNotFoundError as exc:
        return SourceWaitNotFound(error=exc)
    except SourceProcessingError as exc:
        return SourceWaitProcessingError(error=exc)
    except SourceTimeoutError as exc:
        return SourceWaitTimeout(error=exc)
    return SourceWaitReady(source=source)


async def wait_all_sources(
    client: NotebookLMClient,
    notebook_id: str,
    source_ids: list[str],
    *,
    timeout: float,
    interval: float,
    max_concurrent: int = MAX_WAIT_CONCURRENT_SOURCES,
) -> list[SourceWaitOutcome]:
    """Wait for many sources with at most ``max_concurrent`` in-flight pollers.

    One typed outcome per source, in input order. Each per-source wait runs through
    :func:`execute_source_wait` (which maps the three handled ``SourceWait*``
    failures to a typed outcome instead of raising), so a slow/failed source never
    discards its siblings' progress. An UNEXPECTED escape (auth/transport
    ``RPCError``, a bug) cancels + drains the still-running sibling pollers before
    re-raising — the adapter's classify-once handler then maps it — rather than
    leaking coroutines. This is the single implementation both the REST route and
    the MCP tool call (previously duplicated; the MCP copy was unbounded).
    """
    if not source_ids:
        return []

    outcomes: list[SourceWaitOutcome | None] = [None] * len(source_ids)
    source_iter = iter(enumerate(source_ids))

    async def _worker() -> None:
        for index, sid in source_iter:
            outcomes[index] = await execute_source_wait(
                client,
                SourceWaitPlan(
                    notebook_id=notebook_id,
                    source_id=sid,
                    timeout=timeout,
                    interval=interval,
                ),
            )

    tasks = [asyncio.create_task(_worker()) for _ in range(min(len(source_ids), max_concurrent))]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

    ready_outcomes: list[SourceWaitOutcome] = []
    for outcome in outcomes:
        if outcome is None:
            raise AssertionError("source wait worker exited without producing an outcome")
        ready_outcomes.append(outcome)
    return ready_outcomes


__all__ = [
    "MAX_WAIT_CONCURRENT_SOURCES",
    "MAX_WAIT_SOURCE_IDS",
    "MAX_WAIT_TIMEOUT",
    "SourceWaitNotFound",
    "SourceWaitOutcome",
    "SourceWaitPlan",
    "SourceWaitProcessingError",
    "SourceWaitReady",
    "SourceWaitTimeout",
    "execute_source_wait",
    "wait_all_sources",
]
