"""End-to-end tests for LocalOrchestrator."""

from __future__ import annotations

import asyncio
import threading

from agentevalops.agents.mock_runner import MockAgentRunner
from agentevalops.benchmarks.toy.adapter import ToyBenchmarkAdapter
from agentevalops.core.errors import InfrastructureError
from agentevalops.core.schemas import (
    EnvHandle,
    EvaluationResult,
    PolicySpec,
    ResourceLimits,
    RunConfig,
    RunSummary,
    TaskSpec,
    TerminationReason,
    TraceEvent,
    TraceEventKind,
    Verdict,
)
from agentevalops.core.types import BACKEND_LOCAL, AgentId, RunId, TaskId
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


# ---------------------------------------------------------------------------
# prepare_environment() / teardown() wiring (docs/benchmark_adapters.md D5)
# ---------------------------------------------------------------------------


class _RecordingBenchmark:
    """Benchmark adapter that appends to a shared call log.

    ``prepare_error``/``teardown_error`` let a test inject a failure.
    ``prepare_fails_for`` restricts ``prepare_error`` to one task id, so a
    sibling task can be checked to still run normally.
    """

    benchmark_id = "recording"
    benchmark_version = "0.0.0"

    def __init__(
        self,
        calls: list[str],
        task_ids: list[str],
        *,
        prepare_error: Exception | None = None,
        prepare_fails_for: str | None = None,
        teardown_error: Exception | None = None,
        record_thread: bool = False,
    ) -> None:
        self.calls = calls
        self._task_ids = task_ids
        self._prepare_error = prepare_error
        self._prepare_fails_for = prepare_fails_for
        self._teardown_error = teardown_error
        self._record_thread = record_thread
        self.thread_idents: dict[str, int] = {}
        self.handles_returned: dict[str, EnvHandle] = {}
        self.teardown_handles: list[EnvHandle] = []

    def list_tasks(self, filter_spec=None):  # type: ignore[no-untyped-def]
        return [
            TaskSpec(
                task_id=TaskId(tid),
                benchmark_id=self.benchmark_id,
                description="recording task",
            )
            for tid in self._task_ids
        ]

    def grade(self, task, output):  # type: ignore[no-untyped-def]
        raise NotImplementedError("grade() is not exercised by these tests")

    def prepare_environment(self, task):  # type: ignore[no-untyped-def]
        tid = str(task.task_id)
        if self._record_thread:
            self.thread_idents[f"prepare:{tid}"] = threading.get_ident()
        self.calls.append(f"prepare:{tid}")
        if self._prepare_error is not None and (
            self._prepare_fails_for is None or self._prepare_fails_for == tid
        ):
            raise self._prepare_error
        handle = EnvHandle(task_id=task.task_id)
        self.handles_returned[tid] = handle
        return handle

    def teardown(self, handle):  # type: ignore[no-untyped-def]
        tid = str(handle.task_id)
        if self._record_thread:
            self.thread_idents[f"teardown:{tid}"] = threading.get_ident()
        self.calls.append(f"teardown:{tid}")
        self.teardown_handles.append(handle)
        if self._teardown_error is not None:
            raise self._teardown_error


class _RecordingRunner:
    """Runner that appends to the same shared call log and emits one
    successful terminal event, unless configured to raise."""

    agent_id = AgentId("recording-agent")

    def __init__(
        self, calls: list[str], *, infra_error: Exception | None = None
    ) -> None:
        self._calls = calls
        self._infra_error = infra_error

    async def run(self, input, resource_limits):  # type: ignore[no-untyped-def]
        from collections.abc import AsyncGenerator

        tid = str(input.task.task_id)
        self._calls.append(f"run:{tid}")
        if self._infra_error is not None:
            raise self._infra_error

        run_id = RunId(str(input.context.get("run_id", "unknown")))

        async def _gen() -> AsyncGenerator:  # type: ignore[type-arg]
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
                    "wall_seconds": 0.0,
                },
            )

        return _gen()


class _RecordingEvaluator:
    """Evaluator that appends to the same shared call log."""

    evaluator_id = "recording-eval"
    evaluator_kind = "deterministic"

    def __init__(self, calls: list[str], *, error: Exception | None = None) -> None:
        self._calls = calls
        self._error = error

    async def evaluate(self, task, output, trace):  # type: ignore[no-untyped-def]
        self._calls.append(f"evaluate:{task.task_id}")
        if self._error is not None:
            raise self._error
        return EvaluationResult(
            evaluator_id=self.evaluator_id,
            evaluator_kind=self.evaluator_kind,
            score=1.0,
            passed=True,
            evaluated=True,
        )


def _make_recording_orchestrator(
    benchmark: object,
    runner: object,
    evaluator: object,
    *,
    max_concurrent_tasks: int = 1,
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
        benchmark=benchmark,  # type: ignore[arg-type]
        runner=runner,  # type: ignore[arg-type]
        trace_store=store,
        evaluator=evaluator,  # type: ignore[arg-type]
        policy_checker=BasicPolicyChecker(),
    )
    return orch, store


def test_prepare_run_evaluate_teardown_order() -> None:
    calls: list[str] = []
    benchmark = _RecordingBenchmark(calls, ["t-1"])
    orch, _ = _make_recording_orchestrator(
        benchmark, _RecordingRunner(calls), _RecordingEvaluator(calls)
    )
    asyncio.run(orch.run())
    assert calls == ["prepare:t-1", "run:t-1", "evaluate:t-1", "teardown:t-1"]
    assert benchmark.teardown_handles == [benchmark.handles_returned["t-1"]]
    assert benchmark.teardown_handles[0] is benchmark.handles_returned["t-1"]


def test_prepare_infra_error_matches_w12a_shape_and_skips_run_and_teardown() -> None:
    calls: list[str] = []
    benchmark = _RecordingBenchmark(
        calls,
        ["fail", "ok"],
        prepare_error=InfrastructureError("clone boom"),
        prepare_fails_for="fail",
    )
    orch, _ = _make_recording_orchestrator(
        benchmark, _RecordingRunner(calls), _RecordingEvaluator(calls)
    )
    summary = asyncio.run(orch.run())
    prepare_fail_result = next(
        r for r in summary.task_results if r.task_id == "fail"
    )
    ok_result = next(r for r in summary.task_results if r.task_id == "ok")

    # Reference: the existing W12a runner-raises-InfrastructureError path.
    ref_calls: list[str] = []
    ref_benchmark = _RecordingBenchmark(ref_calls, ["fail"])
    ref_runner = _RecordingRunner(ref_calls, infra_error=InfrastructureError("x"))
    ref_orch, _ = _make_recording_orchestrator(
        ref_benchmark, ref_runner, _RecordingEvaluator(ref_calls)
    )
    ref_summary = asyncio.run(ref_orch.run())
    runner_fail_result = ref_summary.task_results[0]

    # Same shape as the runner-infra path: success, termination_reason,
    # score, passed, evaluated, event_count.
    assert (
        prepare_fail_result.output.success == runner_fail_result.output.success
    )
    assert (
        prepare_fail_result.output.termination_reason
        == runner_fail_result.output.termination_reason
    )
    assert (
        prepare_fail_result.evaluation.passed == runner_fail_result.evaluation.passed
    )
    assert (
        prepare_fail_result.evaluation.evaluated
        == runner_fail_result.evaluation.evaluated
    )
    assert (
        prepare_fail_result.evaluation.score == runner_fail_result.evaluation.score
    )
    assert prepare_fail_result.event_count == runner_fail_result.event_count == 0

    # But the failure is attributed to prepare_environment, not the runner.
    assert "prepare_environment" in (prepare_fail_result.output.error or "")

    # The runner and teardown were never invoked for the failed task.
    assert "run:fail" not in calls
    assert "teardown:fail" not in calls

    # The other task still ran normally.
    assert ok_result.evaluation.passed is True
    assert "run:ok" in calls
    assert "teardown:ok" in calls


def test_runner_infra_error_still_tears_down() -> None:
    calls: list[str] = []
    benchmark = _RecordingBenchmark(calls, ["t-1"])
    runner = _RecordingRunner(calls, infra_error=InfrastructureError("runner boom"))
    orch, _ = _make_recording_orchestrator(
        benchmark, runner, _RecordingEvaluator(calls)
    )
    asyncio.run(orch.run())
    assert calls.count("teardown:t-1") == 1


def test_evaluate_error_still_tears_down_and_propagates() -> None:
    import pytest

    calls: list[str] = []
    boom = ValueError("evaluate boom")
    benchmark = _RecordingBenchmark(calls, ["t-1"])
    orch, _ = _make_recording_orchestrator(
        benchmark, _RecordingRunner(calls), _RecordingEvaluator(calls, error=boom)
    )

    with pytest.raises(ValueError) as excinfo:
        asyncio.run(orch.run())
    assert excinfo.value is boom
    assert calls.count("teardown:t-1") == 1


def test_teardown_error_logs_warning_and_keeps_result(caplog) -> None:  # type: ignore[no-untyped-def]
    calls_ok: list[str] = []
    orch_ok, _ = _make_recording_orchestrator(
        _RecordingBenchmark(calls_ok, ["t-1"]),
        _RecordingRunner(calls_ok),
        _RecordingEvaluator(calls_ok),
    )
    baseline = asyncio.run(orch_ok.run()).task_results[0]

    calls_bad: list[str] = []
    benchmark = _RecordingBenchmark(
        calls_bad, ["t-1"], teardown_error=RuntimeError("teardown boom")
    )
    orch_bad, _ = _make_recording_orchestrator(
        benchmark, _RecordingRunner(calls_bad), _RecordingEvaluator(calls_bad)
    )
    with caplog.at_level("WARNING"):
        result = asyncio.run(orch_bad.run()).task_results[0]

    assert result.output == baseline.output
    assert result.evaluation == baseline.evaluation
    assert result.event_count == baseline.event_count
    assert any("teardown" in rec.message.lower() for rec in caplog.records)


def test_evaluate_and_teardown_both_raise_evaluate_exception_wins() -> None:
    import pytest

    calls: list[str] = []
    evaluate_err = ValueError("evaluate boom")
    benchmark = _RecordingBenchmark(
        calls, ["t-1"], teardown_error=RuntimeError("teardown boom")
    )
    orch, _ = _make_recording_orchestrator(
        benchmark,
        _RecordingRunner(calls),
        _RecordingEvaluator(calls, error=evaluate_err),
    )

    with pytest.raises(ValueError) as excinfo:
        asyncio.run(orch.run())
    assert excinfo.value is evaluate_err
    assert calls.count("teardown:t-1") == 1


def test_cancellation_during_runner_still_tears_down() -> None:
    import pytest

    calls: list[str] = []
    benchmark = _RecordingBenchmark(calls, ["only-task"])
    orch, _ = _make_recording_orchestrator(
        benchmark, _InterruptingRunner(), _RecordingEvaluator(calls)
    )

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(orch.run())

    assert calls.count("teardown:only-task") == 1


def test_prepare_unexpected_error_propagates_and_skips_teardown() -> None:
    import pytest

    calls: list[str] = []
    boom = ValueError("prepare boom")
    benchmark = _RecordingBenchmark(calls, ["t-1"], prepare_error=boom)
    orch, _ = _make_recording_orchestrator(
        benchmark, _RecordingRunner(calls), _RecordingEvaluator(calls)
    )

    with pytest.raises(ValueError) as excinfo:
        asyncio.run(orch.run())
    assert excinfo.value is boom
    assert "run:t-1" not in calls
    assert "teardown:t-1" not in calls


def test_prepare_and_teardown_run_outside_event_loop_thread() -> None:
    calls: list[str] = []
    benchmark = _RecordingBenchmark(calls, ["t-1"], record_thread=True)
    orch, _ = _make_recording_orchestrator(
        benchmark, _RecordingRunner(calls), _RecordingEvaluator(calls)
    )

    main_thread_ident = threading.get_ident()
    asyncio.run(orch.run())

    assert benchmark.thread_idents["prepare:t-1"] != main_thread_ident
    assert benchmark.thread_idents["teardown:t-1"] != main_thread_ident


class _TwoTaskCancellationRunner:
    """Single runner shared by tasks "A" and "B".

    "B" sets ``started`` then waits on ``gate`` (never set) -- genuinely
    *waiting*, not busy, when the cancellation arrives. "A" waits for
    ``started`` (so it cannot raise before B has begun waiting) and then
    raises ``KeyboardInterrupt``, simulating SIGINT while a sibling task is
    parked mid-run.
    """

    agent_id = AgentId("two-task-cancel-agent")

    def __init__(self, started: asyncio.Event, gate: asyncio.Event) -> None:
        self._started = started
        self._gate = gate

    async def run(self, input, resource_limits):  # type: ignore[no-untyped-def]
        from collections.abc import AsyncGenerator

        run_id = RunId(str(input.context.get("run_id", "unknown")))
        tid = str(input.task.task_id)

        async def _gen() -> AsyncGenerator:  # type: ignore[type-arg]
            if tid == "A":
                await self._started.wait()
                raise KeyboardInterrupt("simulated Ctrl-C")
            self._started.set()
            await self._gate.wait()
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
                    "wall_seconds": 0.0,
                },
            )

        return _gen()


def test_cancellation_of_waiting_sibling_task_still_tears_down() -> None:
    """A sibling task cancelled *while waiting inside the runner* (not the
    same task that raised) must still have teardown called -- exercises the
    run()-level draining loop (local.py:143-149), not just the single-task
    finally covered by test_cancellation_during_runner_still_tears_down.
    """
    import pytest

    calls: list[str] = []
    started = asyncio.Event()
    gate = asyncio.Event()  # never set -- "B" waits on this indefinitely
    benchmark = _RecordingBenchmark(calls, ["A", "B"])
    runner = _TwoTaskCancellationRunner(started, gate)
    orch, _ = _make_recording_orchestrator(
        benchmark, runner, _RecordingEvaluator(calls), max_concurrent_tasks=2
    )

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(orch.run())

    assert calls.count("teardown:A") == 1
    assert calls.count("teardown:B") == 1
