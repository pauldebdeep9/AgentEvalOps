"""Integration tests for toy benchmark scenarios.

These tests drive the full local pipeline for each scenario using code only
(no YAML config files).  They complement test_example_configs.py which tests
the same scenarios via config files.
"""

from __future__ import annotations

import asyncio

from agentevalops.agents.mock_runner import MockAgentRunner
from agentevalops.benchmarks.toy.adapter import ToyBenchmarkAdapter
from agentevalops.core.schemas import (
    AgentInput,
    AgentOutput,
    PolicySpec,
    ResourceLimits,
    RunConfig,
    TaskSpec,
    TerminationReason,
    TraceEvent,
    TraceEventKind,
    Verdict,
)
from agentevalops.core.types import BACKEND_LOCAL, AgentId, RunId, TaskId
from agentevalops.evaluators.deterministic import DeterministicEvaluator
from agentevalops.orchestration.local import LocalOrchestrator, RunSummary
from agentevalops.policy.basic_checker import BasicPolicyChecker
from agentevalops.stores.memory import InMemoryTraceStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(run_id: str = "scenario-test") -> RunConfig:
    return RunConfig(
        run_id=RunId(run_id),
        agent_id=AgentId("mock-agent-v1"),
        backend_id=BACKEND_LOCAL,
        resource_limits=ResourceLimits(),
    )


def _run_scenario(
    scenario: str,
    policy_spec: PolicySpec | None = None,
    should_fail: bool = False,
    cost_per_task_usd: float = 0.0,
) -> RunSummary:
    config = _make_config(run_id=f"test-{scenario}")
    store = InMemoryTraceStore()
    orch = LocalOrchestrator(
        run_config=config,
        benchmark=ToyBenchmarkAdapter(scenario=scenario),
        runner=MockAgentRunner(
            should_fail=should_fail,
            cost_per_task_usd=cost_per_task_usd,
        ),
        trace_store=store,
        evaluator=DeterministicEvaluator(),
        policy_checker=BasicPolicyChecker(),
        policy_spec=policy_spec,
    )
    return asyncio.run(orch.run())


# ---------------------------------------------------------------------------
# Smoke scenario
# ---------------------------------------------------------------------------


def test_smoke_all_tasks_pass_evaluation() -> None:
    summary = _run_scenario("smoke")
    assert summary.passed_tasks == 2
    assert summary.failed_tasks == 0


def test_smoke_total_tasks() -> None:
    summary = _run_scenario("smoke")
    assert summary.total_tasks == 2


def test_smoke_pass_rate_is_one() -> None:
    summary = _run_scenario("smoke")
    assert summary.score_result is not None
    assert summary.score_result.pass_rate == 1.0


def test_smoke_trace_event_count() -> None:
    """3 events per task (plan + final_answer + terminal) × 2 tasks = 6."""
    summary = _run_scenario("smoke")
    assert summary.trace_event_count == 6


def test_smoke_total_tokens() -> None:
    """10 tokens per task × 2 tasks = 20."""
    summary = _run_scenario("smoke")
    assert summary.total_tokens == 20


def test_smoke_policy_passes_default() -> None:
    summary = _run_scenario("smoke")
    assert summary.policy_verdict is not None
    assert summary.policy_verdict.verdict == Verdict.PASS


# ---------------------------------------------------------------------------
# Failure scenario — evaluation fails, agent completes normally
# ---------------------------------------------------------------------------


def test_failure_all_tasks_fail_evaluation() -> None:
    summary = _run_scenario("failure")
    assert summary.passed_tasks == 0
    assert summary.failed_tasks == 2


def test_failure_total_tasks() -> None:
    summary = _run_scenario("failure")
    assert summary.total_tasks == 2


def test_failure_pass_rate_is_zero() -> None:
    summary = _run_scenario("failure")
    assert summary.score_result is not None
    assert summary.score_result.pass_rate == 0.0


def test_failure_agent_completes_successfully() -> None:
    """Agent returns answers without crashing; evaluations reject them."""
    summary = _run_scenario("failure")
    for result in summary.task_results:
        # Agent ran fine
        assert result.output.success is True
        # Evaluation rejected the answer
        assert result.evaluation.passed is False


def test_failure_policy_passes_zero_cost() -> None:
    """No cost → policy with a $1.00 limit still passes."""
    policy = PolicySpec(policy_id="p", max_cost_usd=1.0)
    summary = _run_scenario("failure", policy_spec=policy)
    assert summary.policy_verdict is not None
    assert summary.policy_verdict.verdict == Verdict.PASS


def test_failure_trace_event_count() -> None:
    """Happy-mode runner: 3 events per task × 2 tasks = 6."""
    summary = _run_scenario("failure")
    assert summary.trace_event_count == 6


# ---------------------------------------------------------------------------
# Policy-violation scenario — tasks pass, policy fails due to cost
# ---------------------------------------------------------------------------


def test_policy_violation_tasks_pass_evaluation() -> None:
    policy = PolicySpec(policy_id="p", max_cost_usd=0.25)
    summary = _run_scenario(
        "policy_violation",
        policy_spec=policy,
        cost_per_task_usd=0.5,
    )
    assert summary.passed_tasks == 2


def test_policy_violation_policy_fails_cost() -> None:
    policy = PolicySpec(policy_id="p", max_cost_usd=0.25)
    summary = _run_scenario(
        "policy_violation",
        policy_spec=policy,
        cost_per_task_usd=0.5,
    )
    assert summary.policy_verdict is not None
    assert summary.policy_verdict.verdict == Verdict.FAIL


def test_policy_violation_notes_mention_cost() -> None:
    policy = PolicySpec(policy_id="p", max_cost_usd=0.25)
    summary = _run_scenario(
        "policy_violation",
        policy_spec=policy,
        cost_per_task_usd=0.5,
    )
    assert summary.policy_verdict is not None
    assert "cost" in summary.policy_verdict.notes.lower()


# ---------------------------------------------------------------------------
# Trace-limit scenario — tasks pass, policy fails due to trace size
# ---------------------------------------------------------------------------


def test_trace_limit_tasks_pass_evaluation() -> None:
    policy = PolicySpec(policy_id="p", max_trace_events=5)
    summary = _run_scenario("trace_limit", policy_spec=policy)
    assert summary.passed_tasks == 2


def test_trace_limit_policy_fails() -> None:
    policy = PolicySpec(policy_id="p", max_trace_events=5)
    summary = _run_scenario("trace_limit", policy_spec=policy)
    assert summary.policy_verdict is not None
    assert summary.policy_verdict.verdict == Verdict.FAIL


def test_trace_limit_actual_event_count() -> None:
    """6 events > 5 max → fails."""
    policy = PolicySpec(policy_id="p", max_trace_events=5)
    summary = _run_scenario("trace_limit", policy_spec=policy)
    assert summary.trace_event_count == 6


# ---------------------------------------------------------------------------
# Mixed scenario — 2 pass, 1 fail
# ---------------------------------------------------------------------------


def test_mixed_two_tasks_pass() -> None:
    summary = _run_scenario("mixed")
    assert summary.passed_tasks == 2


def test_mixed_one_task_fails() -> None:
    summary = _run_scenario("mixed")
    assert summary.failed_tasks == 1


def test_mixed_total_tasks() -> None:
    summary = _run_scenario("mixed")
    assert summary.total_tasks == 3


def test_mixed_pass_rate() -> None:
    summary = _run_scenario("mixed")
    assert summary.score_result is not None
    assert abs(summary.score_result.pass_rate - 2 / 3) < 1e-9


def test_mixed_policy_passes_with_no_cost() -> None:
    policy = PolicySpec(policy_id="p", max_cost_usd=1.0)
    summary = _run_scenario("mixed", policy_spec=policy)
    assert summary.policy_verdict is not None
    assert summary.policy_verdict.verdict == Verdict.PASS


def test_mixed_trace_event_count() -> None:
    """3 events per task × 3 tasks = 9."""
    summary = _run_scenario("mixed")
    assert summary.trace_event_count == 9


# ---------------------------------------------------------------------------
# DeterministicEvaluator richer checks via scenario tasks
# ---------------------------------------------------------------------------


def test_exact_match_passes_for_smoke_task() -> None:
    """Smoke tasks use match_mode=exact; MockAgentRunner returns exact answer."""
    summary = _run_scenario("smoke")
    for result in summary.task_results:
        assert result.evaluation.passed is True
        assert "exact_match" in result.evaluation.notes


def test_exact_mismatch_fails_for_failure_task() -> None:
    """Failure tasks have mock_answer ≠ expected_output; evaluator rejects."""
    summary = _run_scenario("failure")
    fail_results = [
        r for r in summary.task_results if not r.evaluation.passed
    ]
    assert len(fail_results) > 0
    # At least one should mention mismatch or missing substring
    notes_combined = " ".join(r.evaluation.notes for r in fail_results)
    assert "mismatch" in notes_combined or "missing" in notes_combined


def test_substring_match_passes_for_mixed_substr_task() -> None:
    """Mixed scenario includes a substring-match task that should pass."""
    from agentevalops.core.types import TaskId

    summary = _run_scenario("mixed")
    substr_result = next(
        (r for r in summary.task_results if r.task_id == TaskId("toy-substr-001")),
        None,
    )
    assert substr_result is not None
    assert substr_result.evaluation.passed is True


# ---------------------------------------------------------------------------
# MockAgentRunner with emit_fake_tool_call
# ---------------------------------------------------------------------------


def test_mock_runner_emits_tool_call_when_requested() -> None:
    """emit_fake_tool_call in task metadata adds an AGENT_TOOL_CALL event."""
    task = TaskSpec(
        task_id=TaskId("tool-task"),
        benchmark_id="toy",
        description="Use the search tool.",
        metadata={
            "emit_fake_tool_call": True,
            "fake_tool_name": "search",
            "expected_output": "done",
        },
    )
    runner = MockAgentRunner()
    agent_input = AgentInput(task=task, context={"run_id": "r1"})
    events = _collect(runner, agent_input)
    kinds = [e.kind for e in events]
    assert TraceEventKind.AGENT_TOOL_CALL in kinds


def test_mock_runner_tool_call_payload_has_tool_name() -> None:
    task = TaskSpec(
        task_id=TaskId("tool-task"),
        benchmark_id="toy",
        description="Use the fetch tool.",
        metadata={
            "emit_fake_tool_call": True,
            "fake_tool_name": "fetch",
            "expected_output": "done",
        },
    )
    runner = MockAgentRunner()
    agent_input = AgentInput(task=task, context={"run_id": "r1"})
    events = _collect(runner, agent_input)
    tool_events = [
        e for e in events if e.kind == TraceEventKind.AGENT_TOOL_CALL
    ]
    assert len(tool_events) == 1
    assert tool_events[0].payload["tool_name"] == "fetch"


def test_mock_runner_tool_call_adds_fourth_event() -> None:
    task = TaskSpec(
        task_id=TaskId("tool-task"),
        benchmark_id="toy",
        description="Use a tool.",
        metadata={"emit_fake_tool_call": True, "expected_output": "ok"},
    )
    runner = MockAgentRunner()
    agent_input = AgentInput(task=task, context={"run_id": "r1"})
    events = _collect(runner, agent_input)
    assert len(events) == 4


async def _collect_async(
    runner: MockAgentRunner,
    agent_input: AgentInput,
) -> list[object]:
    it = await runner.run(agent_input, ResourceLimits())
    return [e async for e in it]


def _collect(runner: MockAgentRunner, agent_input: AgentInput) -> list[object]:
    return asyncio.run(_collect_async(runner, agent_input))


# ---------------------------------------------------------------------------
# DeterministicEvaluator required_trace_kinds
# ---------------------------------------------------------------------------


def test_required_trace_kind_passes_when_present() -> None:
    task = TaskSpec(
        task_id=TaskId("tk"),
        benchmark_id="toy",
        description="needs final answer",
        metadata={
            "required_trace_kinds": [TraceEventKind.AGENT_FINAL_ANSWER.value]
        },
    )
    output = AgentOutput(
        success=True,
        termination_reason=TerminationReason.COMPLETED,
        final_answer="ok",
    )
    trace = [
        TraceEvent(
            run_id=RunId("r"),
            step_index=0,
            kind=TraceEventKind.AGENT_FINAL_ANSWER,
            payload={},
        )
    ]
    ev = DeterministicEvaluator()
    result = asyncio.run(ev.evaluate(task, output, trace))
    assert result.passed is True


def test_required_trace_kind_fails_when_absent() -> None:
    task = TaskSpec(
        task_id=TaskId("tk"),
        benchmark_id="toy",
        description="needs final answer",
        metadata={
            "required_trace_kinds": [TraceEventKind.AGENT_FINAL_ANSWER.value]
        },
    )
    output = AgentOutput(
        success=True,
        termination_reason=TerminationReason.COMPLETED,
        final_answer="ok",
    )
    # Trace has only AGENT_TERMINAL, not AGENT_FINAL_ANSWER
    trace = [
        TraceEvent(
            run_id=RunId("r"),
            step_index=0,
            kind=TraceEventKind.AGENT_TERMINAL,
            payload={},
        )
    ]
    ev = DeterministicEvaluator()
    result = asyncio.run(ev.evaluate(task, output, trace))
    assert result.passed is False
    assert "missing" in result.notes


# ---------------------------------------------------------------------------
# DeterministicEvaluator required_tool_names
# ---------------------------------------------------------------------------


def test_required_tool_name_passes_when_present() -> None:
    task = TaskSpec(
        task_id=TaskId("tt"),
        benchmark_id="toy",
        description="needs search tool",
        metadata={"required_tool_names": ["search"]},
    )
    output = AgentOutput(
        success=True,
        termination_reason=TerminationReason.COMPLETED,
        final_answer="ok",
    )
    trace = [
        TraceEvent(
            run_id=RunId("r"),
            step_index=0,
            kind=TraceEventKind.AGENT_TOOL_CALL,
            payload={"tool_name": "search", "args": {}},
        )
    ]
    ev = DeterministicEvaluator()
    result = asyncio.run(ev.evaluate(task, output, trace))
    assert result.passed is True


def test_required_tool_name_fails_when_absent() -> None:
    task = TaskSpec(
        task_id=TaskId("tt"),
        benchmark_id="toy",
        description="needs search tool",
        metadata={"required_tool_names": ["search"]},
    )
    output = AgentOutput(
        success=True,
        termination_reason=TerminationReason.COMPLETED,
        final_answer="ok",
    )
    # No AGENT_TOOL_CALL events
    ev = DeterministicEvaluator()
    result = asyncio.run(ev.evaluate(task, output, []))
    assert result.passed is False
    assert "missing" in result.notes
