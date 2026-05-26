"""Tests for BasicPolicyChecker."""

from __future__ import annotations

import asyncio

from agentevalops.core.schemas import (
    AgentOutput,
    PolicySpec,
    TerminationReason,
    TraceEvent,
    TraceEventKind,
    Verdict,
)
from agentevalops.core.types import RunId
from agentevalops.policy.basic_checker import BasicPolicyChecker


def _ok_output(cost: float = 0.0) -> AgentOutput:
    return AgentOutput(
        success=True,
        termination_reason=TerminationReason.COMPLETED,
        total_cost_usd=cost,
        total_tokens=10,
        wall_seconds=0.01,
    )


def _tool_call_event(step: int, tool: str) -> TraceEvent:
    return TraceEvent(
        run_id=RunId("r1"),
        step_index=step,
        kind=TraceEventKind.AGENT_TOOL_CALL,
        payload={"tool": tool},
    )


def test_checker_id() -> None:
    assert BasicPolicyChecker().checker_id == "basic-policy-v1"


def test_pass_no_constraints() -> None:
    checker = BasicPolicyChecker()
    policy = PolicySpec(policy_id="p1")
    result = asyncio.run(checker.check(policy, _ok_output(), []))
    assert result.verdict == Verdict.PASS


def test_pass_cost_under_ceiling() -> None:
    checker = BasicPolicyChecker()
    policy = PolicySpec(policy_id="p1", max_cost_usd=1.0)
    result = asyncio.run(checker.check(policy, _ok_output(cost=0.5), []))
    assert result.verdict == Verdict.PASS


def test_fail_cost_exceeds_ceiling() -> None:
    checker = BasicPolicyChecker()
    policy = PolicySpec(policy_id="p1", max_cost_usd=1.0)
    result = asyncio.run(checker.check(policy, _ok_output(cost=1.01), []))
    assert result.verdict == Verdict.FAIL
    assert "cost" in result.notes


def test_fail_denied_tool_used() -> None:
    checker = BasicPolicyChecker()
    policy = PolicySpec(policy_id="p1", deny_tool_ids=("bash", "curl"))
    trace = [_tool_call_event(0, "bash")]
    result = asyncio.run(checker.check(policy, _ok_output(), trace))
    assert result.verdict == Verdict.FAIL
    assert 0 in result.citations


def test_pass_allowed_tool_used() -> None:
    checker = BasicPolicyChecker()
    policy = PolicySpec(policy_id="p1", deny_tool_ids=("bash",))
    trace = [_tool_call_event(0, "python")]
    result = asyncio.run(checker.check(policy, _ok_output(), trace))
    assert result.verdict == Verdict.PASS


def test_fail_combines_cost_and_tool() -> None:
    checker = BasicPolicyChecker()
    policy = PolicySpec(
        policy_id="p1", max_cost_usd=0.5, deny_tool_ids=("bash",)
    )
    trace = [_tool_call_event(1, "bash")]
    result = asyncio.run(checker.check(policy, _ok_output(cost=1.0), trace))
    assert result.verdict == Verdict.FAIL
    assert 1 in result.citations
    assert "cost" in result.notes


def test_pass_notes_message() -> None:
    checker = BasicPolicyChecker()
    policy = PolicySpec(policy_id="p1")
    result = asyncio.run(checker.check(policy, _ok_output(), []))
    assert result.notes == "all checks passed"


# ---------------------------------------------------------------------------
# WBS 5: max_trace_events check
# ---------------------------------------------------------------------------


def _plain_events(n: int) -> list[TraceEvent]:
    return [
        TraceEvent(
            run_id=RunId("r1"),
            step_index=i,
            kind=TraceEventKind.AGENT_PLAN,
            payload={},
        )
        for i in range(n)
    ]


def test_fail_trace_events_exceeded() -> None:
    checker = BasicPolicyChecker()
    policy = PolicySpec(policy_id="p1", max_trace_events=2)
    result = asyncio.run(checker.check(policy, _ok_output(), _plain_events(5)))
    assert result.verdict == Verdict.FAIL
    assert "5" in result.notes
    assert "2" in result.notes


def test_pass_trace_events_at_limit() -> None:
    checker = BasicPolicyChecker()
    policy = PolicySpec(policy_id="p1", max_trace_events=5)
    result = asyncio.run(checker.check(policy, _ok_output(), _plain_events(5)))
    assert result.verdict == Verdict.PASS


def test_pass_no_max_trace_events() -> None:
    checker = BasicPolicyChecker()
    policy = PolicySpec(policy_id="p1")
    result = asyncio.run(checker.check(policy, _ok_output(), _plain_events(100)))
    assert result.verdict == Verdict.PASS
