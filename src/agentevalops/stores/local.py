"""Disk-backed TraceStore — durable across a hard process kill."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path

from agentevalops.bundles.serializers import to_jsonable
from agentevalops.core.errors import BundleError
from agentevalops.core.schemas import TraceEvent
from agentevalops.core.types import RunId

TRACES_FILENAME = "traces.jsonl"


class LocalTraceStore:
    """Streams ``TraceEvent`` objects to ``<output_dir>/traces.jsonl``.

    Unlike ``InMemoryTraceStore``, each ``append()`` flushes and ``fsync``s
    the underlying file before returning. Events written before a hard
    process kill (not just an in-process ``KeyboardInterrupt``) are durable
    on disk, so a killed run leaves a ``traces.jsonl`` a bundle reader can
    still inspect even though no manifest was ever sealed.
    """

    def __init__(self, output_dir: Path) -> None:
        self._dir = output_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = output_dir / TRACES_FILENAME
        self._events: dict[RunId, list[TraceEvent]] = {}
        self._finalized: set[RunId] = set()
        self._fh = self._path.open("a", encoding="utf-8")

    async def append(self, run_id: RunId, event: TraceEvent) -> None:
        """Append one event.  Raises ``BundleError`` if the run is finalized."""
        if run_id in self._finalized:
            raise BundleError(
                f"Trace for run '{run_id}' is already finalized."
            )
        self._events.setdefault(run_id, []).append(event)
        self._fh.write(json.dumps(to_jsonable(event), ensure_ascii=False))
        self._fh.write("\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def stream(self, run_id: RunId) -> AsyncIterator[TraceEvent]:
        """Yield all events for a run in append order.

        .. note::
            This method snapshots the in-memory event list at call time.
            Events appended *after* ``stream()`` is called will not appear
            in the returned iterator.
        """
        stored = list(self._events.get(run_id, []))

        async def _gen() -> AsyncGenerator[TraceEvent, None]:
            for event in stored:
                yield event

        return _gen()

    async def finalize(self, run_id: RunId) -> None:
        """Mark the trace closed and close the file handle.  Idempotent."""
        self._finalized.add(run_id)
        if not self._fh.closed:
            self._fh.close()

    # ------------------------------------------------------------------
    # Helpers (not part of the TraceStore protocol)
    # ------------------------------------------------------------------

    def events(self, run_id: RunId) -> list[TraceEvent]:
        """Return a snapshot of all events for a run."""
        return list(self._events.get(run_id, []))

    def is_finalized(self, run_id: RunId) -> bool:
        """Return True if the trace has been finalized."""
        return run_id in self._finalized
