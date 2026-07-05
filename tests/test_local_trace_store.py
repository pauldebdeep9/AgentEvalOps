"""Tests for LocalTraceStore (disk-backed, durable TraceStore).

Mirrors tests/test_stores.py (InMemoryTraceStore) for protocol parity, plus
tests specific to the durability guarantee: events survive on disk even if
the process is killed before ``finalize()`` is ever called.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agentevalops.core.errors import BundleError
from agentevalops.core.schemas import TraceEvent, TraceEventKind
from agentevalops.core.types import RunId
from agentevalops.stores.local import TRACES_FILENAME, LocalTraceStore


def _event(run_id: RunId, step: int, kind: TraceEventKind) -> TraceEvent:
    return TraceEvent(run_id=run_id, step_index=step, kind=kind, payload={})


def test_append_stores_event(tmp_path: Path) -> None:
    store = LocalTraceStore(tmp_path)
    run_id = RunId("r1")
    asyncio.run(store.append(run_id, _event(run_id, 0, TraceEventKind.AGENT_PLAN)))
    assert len(store.events(run_id)) == 1


def test_append_multiple_events_in_order(tmp_path: Path) -> None:
    store = LocalTraceStore(tmp_path)
    run_id = RunId("r1")
    asyncio.run(store.append(run_id, _event(run_id, 0, TraceEventKind.AGENT_PLAN)))
    asyncio.run(
        store.append(run_id, _event(run_id, 1, TraceEventKind.AGENT_FINAL_ANSWER))
    )
    events = store.events(run_id)
    assert len(events) == 2
    assert events[0].kind == TraceEventKind.AGENT_PLAN
    assert events[1].kind == TraceEventKind.AGENT_FINAL_ANSWER


def test_stream_yields_events(tmp_path: Path) -> None:
    store = LocalTraceStore(tmp_path)
    run_id = RunId("r1")
    asyncio.run(store.append(run_id, _event(run_id, 0, TraceEventKind.AGENT_PLAN)))

    async def _collect() -> list[TraceEvent]:
        return [e async for e in store.stream(run_id)]

    events = asyncio.run(_collect())
    assert len(events) == 1
    assert events[0].kind == TraceEventKind.AGENT_PLAN


def test_stream_unknown_run_id_yields_nothing(tmp_path: Path) -> None:
    store = LocalTraceStore(tmp_path)

    async def _collect() -> list[TraceEvent]:
        return [e async for e in store.stream(RunId("missing"))]

    assert asyncio.run(_collect()) == []


def test_finalize_marks_run(tmp_path: Path) -> None:
    store = LocalTraceStore(tmp_path)
    run_id = RunId("r1")
    assert not store.is_finalized(run_id)
    asyncio.run(store.finalize(run_id))
    assert store.is_finalized(run_id)


def test_finalize_is_idempotent(tmp_path: Path) -> None:
    store = LocalTraceStore(tmp_path)
    run_id = RunId("r1")
    asyncio.run(store.finalize(run_id))
    asyncio.run(store.finalize(run_id))  # no exception
    assert store.is_finalized(run_id)


def test_append_after_finalize_raises(tmp_path: Path) -> None:
    store = LocalTraceStore(tmp_path)
    run_id = RunId("r1")
    asyncio.run(store.finalize(run_id))
    with pytest.raises(BundleError):
        asyncio.run(store.append(run_id, _event(run_id, 0, TraceEventKind.AGENT_PLAN)))


def test_events_returns_empty_for_unknown_run(tmp_path: Path) -> None:
    store = LocalTraceStore(tmp_path)
    assert store.events(RunId("no-such-run")) == []


# ---------------------------------------------------------------------------
# Durability: events are on disk immediately, without calling finalize()
# ---------------------------------------------------------------------------


def test_append_is_durable_without_finalize(tmp_path: Path) -> None:
    """Simulates a hard kill: never call finalize(), read the raw file back."""
    store = LocalTraceStore(tmp_path)
    run_id = RunId("r1")
    asyncio.run(store.append(run_id, _event(run_id, 0, TraceEventKind.AGENT_PLAN)))
    asyncio.run(
        store.append(run_id, _event(run_id, 1, TraceEventKind.AGENT_FINAL_ANSWER))
    )
    # No finalize() call and no clean shutdown -- read the file directly, as
    # a recovery tool would after a kill -9.
    lines = (tmp_path / TRACES_FILENAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["kind"] == TraceEventKind.AGENT_PLAN.value
    assert json.loads(lines[1])["kind"] == TraceEventKind.AGENT_FINAL_ANSWER.value


def test_constructor_creates_output_dir(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "bundle-dir"
    LocalTraceStore(target)
    assert target.is_dir()


def test_finalize_closes_file_handle(tmp_path: Path) -> None:
    store = LocalTraceStore(tmp_path)
    run_id = RunId("r1")
    asyncio.run(store.finalize(run_id))
    assert store._fh.closed
