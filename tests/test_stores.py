"""Tests for InMemoryTraceStore."""

from __future__ import annotations

import asyncio

import pytest

from agentevalops.core.errors import BundleError
from agentevalops.core.schemas import TraceEvent, TraceEventKind
from agentevalops.core.types import RunId
from agentevalops.stores.memory import InMemoryTraceStore


def _event(run_id: RunId, step: int, kind: TraceEventKind) -> TraceEvent:
    return TraceEvent(run_id=run_id, step_index=step, kind=kind, payload={})


def test_append_stores_event() -> None:
    store = InMemoryTraceStore()
    run_id = RunId("r1")
    asyncio.run(store.append(run_id, _event(run_id, 0, TraceEventKind.AGENT_PLAN)))
    assert len(store.events(run_id)) == 1


def test_append_multiple_events_in_order() -> None:
    store = InMemoryTraceStore()
    run_id = RunId("r1")
    asyncio.run(store.append(run_id, _event(run_id, 0, TraceEventKind.AGENT_PLAN)))
    asyncio.run(
        store.append(run_id, _event(run_id, 1, TraceEventKind.AGENT_FINAL_ANSWER))
    )
    events = store.events(run_id)
    assert len(events) == 2
    assert events[0].kind == TraceEventKind.AGENT_PLAN
    assert events[1].kind == TraceEventKind.AGENT_FINAL_ANSWER


def test_stream_yields_events() -> None:
    store = InMemoryTraceStore()
    run_id = RunId("r1")
    asyncio.run(store.append(run_id, _event(run_id, 0, TraceEventKind.AGENT_PLAN)))

    async def _collect() -> list[TraceEvent]:
        return [e async for e in store.stream(run_id)]

    events = asyncio.run(_collect())
    assert len(events) == 1
    assert events[0].kind == TraceEventKind.AGENT_PLAN


def test_stream_unknown_run_id_yields_nothing() -> None:
    store = InMemoryTraceStore()

    async def _collect() -> list[TraceEvent]:
        return [e async for e in store.stream(RunId("missing"))]

    assert asyncio.run(_collect()) == []


def test_finalize_marks_run() -> None:
    store = InMemoryTraceStore()
    run_id = RunId("r1")
    assert not store.is_finalized(run_id)
    asyncio.run(store.finalize(run_id))
    assert store.is_finalized(run_id)


def test_finalize_is_idempotent() -> None:
    store = InMemoryTraceStore()
    run_id = RunId("r1")
    asyncio.run(store.finalize(run_id))
    asyncio.run(store.finalize(run_id))  # no exception
    assert store.is_finalized(run_id)


def test_append_after_finalize_raises() -> None:
    store = InMemoryTraceStore()
    run_id = RunId("r1")
    asyncio.run(store.finalize(run_id))
    with pytest.raises(BundleError):
        asyncio.run(store.append(run_id, _event(run_id, 0, TraceEventKind.AGENT_PLAN)))


def test_events_returns_empty_for_unknown_run() -> None:
    store = InMemoryTraceStore()
    assert store.events(RunId("no-such-run")) == []
