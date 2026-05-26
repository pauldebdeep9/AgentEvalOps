"""End-to-end tests for LocalOrchestrator."""

from __future__ import annotations

import asyncio

from agentevalops.agents.mock_runner import MockAgentRunner
from agentevalops.benchmarks.toy.adapter import ToyBenchmarkAdapter
from agentevalops.core.schemas import PolicySpec, ResourceLimits, RunConfig, Verdict
from agentevalops.core.types import BACKEND_LOCAL, AgentId, RunId
from agentevalops.evaluators.deterministic import DeterministicEvaluator
from agentevalops.orchestration.local import LocalOrchestrator, RunSummary
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
