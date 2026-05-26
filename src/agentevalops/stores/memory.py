"""In-memory TraceStore for local development and testing."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator

from agentevalops.core.errors import BundleError
from agentevalops.core.schemas import TraceEvent
from agentevalops.core.types import RunId


class InMemoryTraceStore:
    """Stores ``TraceEvent`` objects in a plain dict — no file I/O.

    Suitable for local development, tests, and the toy benchmark flow.
    Events are lost when the process exits.
    """

    def __init__(self) -> None:
        self._events: dict[RunId, list[TraceEvent]] = {}
        self._finalized: set[RunId] = set()

    async def append(self, run_id: RunId, event: TraceEvent) -> None:
        """Append one event.  Raises ``BundleError`` if the run is finalized."""
        if run_id in self._finalized:
            raise BundleError(
                f"Trace for run '{run_id}' is already finalized."
            )
        if run_id not in self._events:
            self._events[run_id] = []
        self._events[run_id].append(event)

    def stream(self, run_id: RunId) -> AsyncIterator[TraceEvent]:
        """Yield all events for a run in append order."""
        stored = list(self._events.get(run_id, []))

        async def _gen() -> AsyncGenerator[TraceEvent, None]:
            for event in stored:
                yield event

        return _gen()

    async def finalize(self, run_id: RunId) -> None:
        """Mark the trace closed.  Idempotent."""
        self._finalized.add(run_id)

    # ------------------------------------------------------------------
    # Helpers (not part of the TraceStore protocol)
    # ------------------------------------------------------------------

    def events(self, run_id: RunId) -> list[TraceEvent]:
        """Return a snapshot of all events for a run."""
        return list(self._events.get(run_id, []))

    def is_finalized(self, run_id: RunId) -> bool:
        """Return True if the trace has been finalized."""
        return run_id in self._finalized
