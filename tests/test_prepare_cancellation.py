"""A task cancelled while ``prepare_environment`` runs: the prepare is waited
out and whatever it created is torn down (docs/adr/0006 D22; R23 slice 7).

Deterministic, like 9cc2b00's teardown test: the prepare signals that it has
started and then blocks until the test releases it, so the cancellation
always arrives while the prepare job is running in its worker thread.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from agentevalops.agents.mock_runner import MockAgentRunner
from agentevalops.core.errors import InfrastructureError
from agentevalops.core.schemas import (
    EnvHandle,
    ResourceLimits,
    RunConfig,
    RunSummary,
    TaskSpec,
)
from agentevalops.core.types import BACKEND_LOCAL, AgentId, RunId, TaskId
from agentevalops.environments import toy_environment
from agentevalops.evaluators.deterministic import DeterministicEvaluator
from agentevalops.orchestration.local import LocalOrchestrator
from agentevalops.policy.basic_checker import BasicPolicyChecker
from agentevalops.stores.memory import InMemoryTraceStore


class _SlowPrepareBenchmark:
    benchmark_id = "slow"
    benchmark_version = "0"

    def __init__(self, fail: bool = False) -> None:
        self.release = threading.Event()
        self.started: asyncio.Event | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.prepared: list[EnvHandle] = []
        self.torn_down: list[EnvHandle] = []
        self._fail = fail

    def list_tasks(self, filter_spec=None):  # type: ignore[no-untyped-def]
        return [TaskSpec(task_id=TaskId("t-1"), benchmark_id="slow", description="d")]

    def grade(self, task, output):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def prepare_environment(self, task):  # type: ignore[no-untyped-def]
        assert self.loop is not None and self.started is not None
        self.loop.call_soon_threadsafe(self.started.set)
        assert self.release.wait(timeout=5), "test never released the prepare"
        if self._fail:
            raise InfrastructureError("clone failed")
        handle = EnvHandle(task_id=task.task_id, tools=toy_environment(task.task_id))
        self.prepared.append(handle)
        return handle

    def teardown(self, handle):  # type: ignore[no-untyped-def]
        self.torn_down.append(handle)


def _cancel_during_prepare(
    benchmark: _SlowPrepareBenchmark,
) -> asyncio.Task[RunSummary]:
    async def scenario() -> asyncio.Task[RunSummary]:
        benchmark.loop = asyncio.get_running_loop()
        benchmark.started = asyncio.Event()
        orch = LocalOrchestrator(
            run_config=RunConfig(
                run_id=RunId("prep"),
                agent_id=AgentId("mock"),
                backend_id=BACKEND_LOCAL,
                resource_limits=ResourceLimits(),
            ),
            benchmark=benchmark,  # type: ignore[arg-type]
            runner=MockAgentRunner(),
            trace_store=InMemoryTraceStore(),
            evaluator=DeterministicEvaluator(),
            policy_checker=BasicPolicyChecker(),
        )
        run = asyncio.ensure_future(orch.run())
        await asyncio.wait_for(benchmark.started.wait(), timeout=5)
        run.cancel()  # arrives while prepare runs in its worker thread
        benchmark.release.set()
        await asyncio.wait({run}, timeout=10)
        return run

    return asyncio.run(scenario())


def test_cancelled_prepare_is_waited_out_and_torn_down() -> None:
    """Fail-first: before the fix the cancelled await dropped the handle the
    still-running prepare returned, and teardown never ran."""
    benchmark = _SlowPrepareBenchmark()
    run = _cancel_during_prepare(benchmark)
    assert run.cancelled()
    assert len(benchmark.prepared) == 1
    assert benchmark.torn_down == benchmark.prepared  # exactly once, that handle


def test_cancelled_prepare_that_fails_has_nothing_to_tear_down() -> None:
    benchmark = _SlowPrepareBenchmark(fail=True)
    run = _cancel_during_prepare(benchmark)
    assert run.cancelled()
    assert benchmark.prepared == [] and benchmark.torn_down == []


def test_prepare_infra_error_without_cancellation_is_still_not_evaluated() -> None:
    benchmark = _SlowPrepareBenchmark(fail=True)

    async def scenario() -> RunSummary:
        benchmark.loop = asyncio.get_running_loop()
        benchmark.started = asyncio.Event()
        benchmark.release.set()
        orch = LocalOrchestrator(
            run_config=RunConfig(
                run_id=RunId("prep"), agent_id=AgentId("mock"), backend_id=BACKEND_LOCAL
            ),
            benchmark=benchmark,  # type: ignore[arg-type]
            runner=MockAgentRunner(),
            trace_store=InMemoryTraceStore(),
            evaluator=DeterministicEvaluator(),
            policy_checker=BasicPolicyChecker(),
        )
        return await orch.run()

    summary = asyncio.run(scenario())
    assert summary.not_evaluated_tasks == 1
    assert benchmark.torn_down == []


@pytest.mark.parametrize("attempt", range(5))
def test_cancelled_prepare_teardown_is_deterministic(attempt: int) -> None:
    benchmark = _SlowPrepareBenchmark()
    _cancel_during_prepare(benchmark)
    assert benchmark.torn_down == benchmark.prepared != []
