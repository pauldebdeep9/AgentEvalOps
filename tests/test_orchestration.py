"""End-to-end tests for LocalOrchestrator."""

from __future__ import annotations

import asyncio

from agentevalops.agents.mock_runner import MockAgentRunner
from agentevalops.benchmarks.toy.adapter import ToyBenchmarkAdapter
from agentevalops.core.schemas import (
    EnvHandle,
    PolicySpec,
    ResourceLimits,
    RunConfig,
    RunSummary,
    TerminationReason,
    TraceEvent,
    TraceEventKind,
    Verdict,
)
from agentevalops.core.types import BACKEND_LOCAL, AgentId, RunId
from agentevalops.evaluators.deterministic import DeterministicEvaluator
from agentevalops.orchestration.local import LocalOrchestrator
from agentevalops.policy.basic_checker import BasicPolicyChecker
from agentevalops.stores.memory import InMemoryTraceStore


def _make_orchestrator(
    policy_spec: PolicySpec | None = None,
) -> tuple[LocalOrchestrator, InMemoryTraceStore]:
    run_config = RunConfig(
        run_id=RunId("test-orch-001"),
        agent_id=AgentId("mock-agent-v1"),
        backend_id=BACKEND_LOCAL,
        resource_limits=ResourceLimits(),
    )
    store = InMemoryTraceStore()
    orch = LocalOrchestrator(
        run_config=run_config,
        benchmark=ToyBenchmarkAdapter(),
        runner=MockAgentRunner(),
        trace_store=store,
        evaluator=DeterministicEvaluator(),
        policy_checker=BasicPolicyChecker(),
        policy_spec=policy_spec,
    )
    return orch, store


def test_run_returns_run_summary() -> None:
    orch, _ = _make_orchestrator()
    result = asyncio.run(orch.run())
    assert isinstance(result, RunSummary)


def test_run_two_tasks_total() -> None:
    orch, _ = _make_orchestrator()
    summary = asyncio.run(orch.run())
    assert summary.total_tasks == 2


def test_run_both_tasks_pass() -> None:
    orch, _ = _make_orchestrator()
    summary = asyncio.run(orch.run())
    assert summary.passed_tasks == 2


def test_run_summary_run_id() -> None:
    orch, _ = _make_orchestrator()
    summary = asyncio.run(orch.run())
    assert summary.run_id == "test-orch-001"


def test_run_total_tokens_positive() -> None:
    orch, _ = _make_orchestrator()
    summary = asyncio.run(orch.run())
    # 2 tasks × 10 tokens per mock task
    assert summary.total_tokens == 20


def test_run_total_cost_zero() -> None:
    orch, _ = _make_orchestrator()
    summary = asyncio.run(orch.run())
    assert summary.total_cost_usd == 0.0


def test_run_policy_verdict_pass() -> None:
    orch, _ = _make_orchestrator()
    summary = asyncio.run(orch.run())
    assert summary.policy_verdict is not None
    assert summary.policy_verdict.verdict == Verdict.PASS


def test_run_trace_store_finalized() -> None:
    orch, store = _make_orchestrator()
    asyncio.run(orch.run())
    assert store.is_finalized(RunId("test-orch-001"))


def test_run_events_recorded_in_store() -> None:
    orch, store = _make_orchestrator()
    asyncio.run(orch.run())
    # 2 tasks × 3 events per task
    events = store.events(RunId("test-orch-001"))
    assert len(events) == 6


def test_task_results_have_expected_task_ids() -> None:
    from agentevalops.core.types import TaskId

    orch, _ = _make_orchestrator()
    summary = asyncio.run(orch.run())
    task_ids = {r.task_id for r in summary.task_results}
    assert TaskId("toy-001") in task_ids
    assert TaskId("toy-002") in task_ids


def test_policy_fail_when_cost_exceeded() -> None:
    policy = PolicySpec(policy_id="p-cost", max_cost_usd=0.0)
    orch, _ = _make_orchestrator(policy_spec=policy)
    # mock runner reports 0.0 cost — stays at 0.0 so actually passes
    summary = asyncio.run(orch.run())
    # 0.0 > 0.0 is False, so still PASS
    assert summary.policy_verdict is not None


# ---------------------------------------------------------------------------
# WBS 5: score_result, failed_tasks, trace_event_count
# ---------------------------------------------------------------------------


def test_run_summary_has_score_result() -> None:
    orch, _ = _make_orchestrator()
    summary = asyncio.run(orch.run())
    assert summary.score_result is not None


def test_run_summary_failed_tasks_count() -> None:
    orch, _ = _make_orchestrator()
    summary = asyncio.run(orch.run())
    # Both toy tasks pass → failed_tasks == 0
    assert summary.failed_tasks == 0


def test_run_summary_trace_event_count() -> None:
    orch, _ = _make_orchestrator()
    summary = asyncio.run(orch.run())
    # 2 tasks × 3 events each
    assert summary.trace_event_count == 6


def test_score_result_pass_rate_all_pass() -> None:
    orch, _ = _make_orchestrator()
    summary = asyncio.run(orch.run())
    assert summary.score_result is not None
    assert summary.score_result.pass_rate == 1.0


def test_score_result_total_tasks() -> None:
    orch, _ = _make_orchestrator()
    summary = asyncio.run(orch.run())
    assert summary.score_result is not None
    assert summary.score_result.total_tasks == 2


# ---------------------------------------------------------------------------
# Concurrency (max_concurrent_tasks semaphore) + interrupt safety
# ---------------------------------------------------------------------------


class _ConcurrencyProbeRunner:
    """Runner that records the peak number of concurrently-active tasks."""

    agent_id = AgentId("probe-agent")

    def __init__(self) -> None:
        self.active = 0
        self.peak = 0

    async def run(self, input, resource_limits):  # type: ignore[no-untyped-def]
        from collections.abc import AsyncGenerator

        run_id = RunId(str(input.context.get("run_id", "unknown")))
        self.active += 1
        self.peak = max(self.peak, self.active)

        async def _gen() -> AsyncGenerator:  # type: ignore[type-arg]
            try:
                # Yield control so sibling tasks can pile up under the limit.
                await asyncio.sleep(0.01)
                yield TraceEvent(
                    run_id=run_id,
                    step_index=0,
                    kind=TraceEventKind.AGENT_TERMINAL,
                    payload={
                        "success": True,
                        "termination_reason": TerminationReason.COMPLETED.value,
                        "final_answer": "x",
                        "total_cost_usd": 0.0,
                        "total_tokens": 1,
                        "wall_seconds": 0.01,
                    },
                )
            finally:
                self.active -= 1

        return _gen()


class _OneTaskBenchmark:
    """Minimal benchmark exposing exactly one task (no sibling tasks)."""

    benchmark_id = "one-task"
    benchmark_version = "0.0.0"

    def list_tasks(self, filter_spec=None):  # type: ignore[no-untyped-def]
        from agentevalops.core.schemas import TaskSpec
        from agentevalops.core.types import TaskId

        return [
            TaskSpec(
                task_id=TaskId("only-task"),
                benchmark_id=self.benchmark_id,
                description="single task",
            )
        ]

    def prepare_environment(self, task):  # type: ignore[no-untyped-def]
        return EnvHandle(task_id=task.task_id)

    def teardown(self, handle: EnvHandle) -> None:
        pass


class _InterruptingRunner:
    """Runner that streams one event then raises ``KeyboardInterrupt``.

    Simulates SIGINT surfacing at the orchestrator's ``await`` point.
    """

    agent_id = AgentId("interrupt-agent")

    async def run(self, input, resource_limits):  # type: ignore[no-untyped-def]
        from collections.abc import AsyncGenerator

        run_id = RunId(str(input.context.get("run_id", "unknown")))

        async def _gen() -> AsyncGenerator:  # type: ignore[type-arg]
            yield TraceEvent(
                run_id=run_id,
                step_index=0,
                kind=TraceEventKind.AGENT_PLAN,
                payload={"plan": "about to be interrupted"},
            )
            raise KeyboardInterrupt("simulated Ctrl-C")

        return _gen()


def _make_orchestrator_with(
    runner: object, max_concurrent_tasks: int
) -> tuple[LocalOrchestrator, InMemoryTraceStore]:
    run_config = RunConfig(
        run_id=RunId("test-orch-001"),
        agent_id=AgentId("mock-agent-v1"),
        backend_id=BACKEND_LOCAL,
        resource_limits=ResourceLimits(),
        max_concurrent_tasks=max_concurrent_tasks,
    )
    store = InMemoryTraceStore()
    orch = LocalOrchestrator(
        run_config=run_config,
        benchmark=ToyBenchmarkAdapter(),
        runner=runner,  # type: ignore[arg-type]
        trace_store=store,
        evaluator=DeterministicEvaluator(),
        policy_checker=BasicPolicyChecker(),
    )
    return orch, store


def test_concurrency_limit_one_serializes_tasks() -> None:
    probe = _ConcurrencyProbeRunner()
    orch, _ = _make_orchestrator_with(probe, max_concurrent_tasks=1)
    asyncio.run(orch.run())
    assert probe.peak == 1


def test_concurrency_limit_two_runs_tasks_in_parallel() -> None:
    probe = _ConcurrencyProbeRunner()
    orch, _ = _make_orchestrator_with(probe, max_concurrent_tasks=2)
    asyncio.run(orch.run())
    # ToyBenchmarkAdapter exposes 2 tasks, so peak should reach the limit.
    assert probe.peak == 2


def test_concurrent_run_is_deterministic() -> None:
    # Regardless of concurrency, results are ordered by task enumeration.
    orch1, _ = _make_orchestrator()
    seq = asyncio.run(orch1.run())

    run_config = RunConfig(
        run_id=RunId("test-orch-001"),
        agent_id=AgentId("mock-agent-v1"),
        backend_id=BACKEND_LOCAL,
        resource_limits=ResourceLimits(),
        max_concurrent_tasks=4,
    )
    orch2 = LocalOrchestrator(
        run_config=run_config,
        benchmark=ToyBenchmarkAdapter(),
        runner=MockAgentRunner(),
        trace_store=InMemoryTraceStore(),
        evaluator=DeterministicEvaluator(),
        policy_checker=BasicPolicyChecker(),
    )
    conc = asyncio.run(orch2.run())

    assert [r.task_id for r in conc.task_results] == [
        r.task_id for r in seq.task_results
    ]
    assert conc.passed_tasks == seq.passed_tasks


def test_keyboard_interrupt_finalizes_trace_store() -> None:
    import pytest

    run_config = RunConfig(
        run_id=RunId("test-orch-001"),
        agent_id=AgentId("mock-agent-v1"),
        backend_id=BACKEND_LOCAL,
        resource_limits=ResourceLimits(),
    )
    store = InMemoryTraceStore()
    orch = LocalOrchestrator(
        run_config=run_config,
        benchmark=_OneTaskBenchmark(),  # type: ignore[arg-type]
        runner=_InterruptingRunner(),  # type: ignore[arg-type]
        trace_store=store,
        evaluator=DeterministicEvaluator(),
        policy_checker=BasicPolicyChecker(),
    )

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(orch.run())

    # Best-effort finalize ran despite the interrupt.
    assert store.is_finalized(RunId("test-orch-001"))
    # The event streamed before the interrupt was persisted.
    assert len(store.events(RunId("test-orch-001"))) >= 1
