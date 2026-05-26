"""Tests that all schema objects can be constructed and enum values are stable.

Enum value stability matters because values are written to result bundles
on disk.  A rename is a breaking change and requires a schema_version bump.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agentevalops.core.schemas import (
    AgentInput,
    AgentOutput,
    EvaluationResult,
    PolicySpec,
    PolicyVerdict,
    ResourceLimits,
    ResultBundleMetadata,
    RunConfig,
    ScoreResult,
    TaskSpec,
    TerminationReason,
    TraceEvent,
    TraceEventKind,
    Verdict,
)
from agentevalops.core.types import (
    BACKEND_AWS,
    BACKEND_LOCAL,
    AgentId,
    RunId,
    TaskId,
)

# ---------------------------------------------------------------------------
# TraceEventKind
# ---------------------------------------------------------------------------


class TestTraceEventKind:
    def test_nine_members(self) -> None:
        assert len(TraceEventKind) == 9

    def test_values_are_stable(self) -> None:
        assert TraceEventKind.AGENT_PLAN.value == "agent.plan"
        assert TraceEventKind.AGENT_TOOL_CALL.value == "agent.tool_call"
        assert TraceEventKind.AGENT_TOOL_RESULT.value == "agent.tool_result"
        assert TraceEventKind.AGENT_OBSERVATION.value == "agent.observation"
        assert TraceEventKind.AGENT_FINAL_ANSWER.value == "agent.final_answer"
        assert TraceEventKind.AGENT_TERMINAL.value == "agent.terminal"
        assert TraceEventKind.EVALUATOR_SCORE.value == "evaluator.score"
        assert TraceEventKind.POLICY_VERDICT.value == "policy.verdict"
        assert TraceEventKind.COST_TICK.value == "cost.tick"

    def test_is_str_subclass(self) -> None:
        assert isinstance(TraceEventKind.AGENT_PLAN, str)
        assert TraceEventKind.AGENT_PLAN == "agent.plan"


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


class TestVerdict:
    def test_three_members(self) -> None:
        assert len(Verdict) == 3

    def test_values_are_stable(self) -> None:
        assert Verdict.PASS.value == "pass"
        assert Verdict.FAIL.value == "fail"
        assert Verdict.WARN.value == "warn"

    def test_is_str_subclass(self) -> None:
        assert isinstance(Verdict.PASS, str)


# ---------------------------------------------------------------------------
# TerminationReason
# ---------------------------------------------------------------------------


class TestTerminationReason:
    def test_six_members(self) -> None:
        assert len(TerminationReason) == 6

    def test_values_are_stable(self) -> None:
        assert TerminationReason.COMPLETED.value == "completed"
        assert TerminationReason.LIMIT_TOKENS.value == "limit_tokens"
        assert TerminationReason.LIMIT_TIME.value == "limit_time"
        assert TerminationReason.LIMIT_COST.value == "limit_cost"
        assert TerminationReason.INFRA_FAILURE.value == "infra_failure"
        assert TerminationReason.AGENT_ERROR.value == "agent_error"


# ---------------------------------------------------------------------------
# ResourceLimits
# ---------------------------------------------------------------------------


class TestResourceLimits:
    def test_defaults(self) -> None:
        rl = ResourceLimits()
        assert rl.max_tokens == 100_000
        assert rl.max_wall_seconds == 3_600.0
        assert rl.max_cost_usd == 10.0

    def test_custom_values(self) -> None:
        rl = ResourceLimits(max_tokens=500, max_wall_seconds=60.0, max_cost_usd=1.0)
        assert rl.max_tokens == 500
        assert rl.max_wall_seconds == 60.0

    def test_is_frozen(self) -> None:
        rl = ResourceLimits()
        with pytest.raises(Exception):
            rl.max_tokens = 999  # type: ignore[misc]


# ---------------------------------------------------------------------------
# RunConfig
# ---------------------------------------------------------------------------


class TestRunConfig:
    def test_minimal_construction(self) -> None:
        rc = RunConfig(
            run_id=RunId("run-1"),
            agent_id=AgentId("mock-agent"),
            backend_id=BACKEND_LOCAL,
        )
        assert rc.run_id == "run-1"
        assert rc.max_concurrent_tasks == 1
        assert isinstance(rc.resource_limits, ResourceLimits)

    def test_custom_limits(self) -> None:
        limits = ResourceLimits(max_cost_usd=1.0)
        rc = RunConfig(
            run_id=RunId("run-2"),
            agent_id=AgentId("agent-x"),
            backend_id=BACKEND_AWS,
            resource_limits=limits,
        )
        assert rc.resource_limits.max_cost_usd == 1.0


# ---------------------------------------------------------------------------
# TaskSpec
# ---------------------------------------------------------------------------


class TestTaskSpec:
    def test_minimal_construction(self) -> None:
        task = TaskSpec(
            task_id=TaskId("t-1"), benchmark_id="toy", description="Fix bug"
        )
        assert task.task_id == "t-1"
        assert task.metadata == {}

    def test_with_metadata(self) -> None:
        task = TaskSpec(
            task_id=TaskId("t-2"),
            benchmark_id="toy",
            description="Write test",
            metadata={"difficulty": "easy", "repo": "cpython"},
        )
        assert task.metadata["difficulty"] == "easy"


# ---------------------------------------------------------------------------
# AgentInput
# ---------------------------------------------------------------------------


class TestAgentInput:
    def test_construction(self) -> None:
        task = TaskSpec(TaskId("t-1"), "toy", "desc")
        ai = AgentInput(task=task)
        assert ai.task is task
        assert ai.context == {}

    def test_with_context(self) -> None:
        task = TaskSpec(TaskId("t-1"), "toy", "desc")
        ai = AgentInput(task=task, context={"env_var": "value"})
        assert ai.context["env_var"] == "value"


# ---------------------------------------------------------------------------
# AgentOutput
# ---------------------------------------------------------------------------


class TestAgentOutput:
    def test_success(self) -> None:
        out = AgentOutput(
            success=True,
            termination_reason=TerminationReason.COMPLETED,
            final_answer="42",
        )
        assert out.success is True
        assert out.error is None
        assert out.total_cost_usd == 0.0

    def test_agent_error(self) -> None:
        out = AgentOutput(
            success=False,
            termination_reason=TerminationReason.AGENT_ERROR,
            error="IndexError on line 12",
        )
        assert out.success is False
        assert out.error == "IndexError on line 12"

    def test_limit_exhausted(self) -> None:
        out = AgentOutput(
            success=False,
            termination_reason=TerminationReason.LIMIT_COST,
            total_cost_usd=10.01,
        )
        assert out.termination_reason == TerminationReason.LIMIT_COST


# ---------------------------------------------------------------------------
# TraceEvent
# ---------------------------------------------------------------------------


class TestTraceEvent:
    def test_minimal_construction(self) -> None:
        event = TraceEvent(
            run_id=RunId("run-1"),
            step_index=0,
            kind=TraceEventKind.AGENT_PLAN,
        )
        assert event.step_index == 0
        assert event.payload == {}
        assert event.cost_delta_usd == 0.0
        assert event.tokens_delta == 0

    def test_timestamp_defaults_to_utc(self) -> None:
        event = TraceEvent(RunId("r"), 0, TraceEventKind.AGENT_PLAN)
        assert event.timestamp.tzinfo is not None

    def test_explicit_payload(self) -> None:
        event = TraceEvent(
            run_id=RunId("run-1"),
            step_index=1,
            kind=TraceEventKind.AGENT_TOOL_CALL,
            payload={"tool": "read_file", "args": {"path": "/tmp/x"}},
            cost_delta_usd=0.001,
            tokens_delta=50,
        )
        assert event.payload["tool"] == "read_file"
        assert event.cost_delta_usd == pytest.approx(0.001)


# ---------------------------------------------------------------------------
# EvaluationResult
# ---------------------------------------------------------------------------


class TestEvaluationResult:
    def test_construction(self) -> None:
        er = EvaluationResult(
            evaluator_id="pytest-runner",
            evaluator_kind="deterministic",
            score=1.0,
            passed=True,
        )
        assert er.score == 1.0
        assert er.citations == []
        assert er.confidence == 1.0

    def test_failure_result(self) -> None:
        er = EvaluationResult(
            evaluator_id="pytest-runner",
            evaluator_kind="deterministic",
            score=0.0,
            passed=False,
            citations=[3, 7],
            notes="test_add_numbers failed",
        )
        assert er.passed is False
        assert er.citations == [3, 7]


# ---------------------------------------------------------------------------
# ScoreResult
# ---------------------------------------------------------------------------


class TestScoreResult:
    def test_empty_defaults(self) -> None:
        sr = ScoreResult(scorer_id="pass-at-1", run_id=RunId("run-1"))
        assert sr.aggregate_score == 0.0
        assert sr.baseline_delta is None
        assert sr.task_scores == []

    def test_with_scores(self) -> None:
        scores = [
            EvaluationResult("e", "deterministic", 1.0, True),
            EvaluationResult("e", "deterministic", 0.0, False),
        ]
        sr = ScoreResult(scorer_id="pass-at-1", run_id=RunId("r"), task_scores=scores)
        assert len(sr.task_scores) == 2


# ---------------------------------------------------------------------------
# PolicySpec
# ---------------------------------------------------------------------------


class TestPolicySpec:
    def test_defaults(self) -> None:
        ps = PolicySpec(policy_id="default")
        assert ps.max_cost_usd is None
        assert ps.allowed_tool_ids == ()
        assert ps.deny_tool_ids == ()

    def test_with_allowlist(self) -> None:
        ps = PolicySpec(
            policy_id="strict",
            max_cost_usd=5.0,
            allowed_tool_ids=("read_file", "run_tests"),
        )
        assert "read_file" in ps.allowed_tool_ids
        assert ps.max_cost_usd == 5.0

    def test_is_frozen(self) -> None:
        ps = PolicySpec("p")
        with pytest.raises(Exception):
            ps.policy_id = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# PolicyVerdict
# ---------------------------------------------------------------------------


class TestPolicyVerdict:
    def test_pass(self) -> None:
        pv = PolicyVerdict(
            checker_id="cost-checker",
            policy_id="default",
            verdict=Verdict.PASS,
        )
        assert pv.verdict == Verdict.PASS
        assert pv.citations == []
        assert pv.notes == ""

    def test_fail_with_citations(self) -> None:
        pv = PolicyVerdict(
            checker_id="tool-checker",
            policy_id="strict",
            verdict=Verdict.FAIL,
            citations=[4, 9],
            notes="Denied tool 'bash' was called at steps 4 and 9.",
        )
        assert pv.verdict == Verdict.FAIL
        assert 4 in pv.citations


# ---------------------------------------------------------------------------
# ResultBundleMetadata
# ---------------------------------------------------------------------------


class TestResultBundleMetadata:
    def test_construction(self) -> None:
        now = datetime.now(tz=timezone.utc)
        meta = ResultBundleMetadata(
            schema_version="1.0",
            run_id=RunId("run-1"),
            created_at=now,
            platform_version="0.1.0.dev0",
            backend_id=BACKEND_LOCAL,
        )
        assert meta.sealed is False
        assert meta.task_count == 0
        assert meta.schema_version == "1.0"

    def test_sealed_flag(self) -> None:
        now = datetime.now(tz=timezone.utc)
        meta = ResultBundleMetadata(
            schema_version="1.0",
            run_id=RunId("run-2"),
            created_at=now,
            platform_version="0.1.0.dev0",
            backend_id=BACKEND_AWS,
            task_count=5,
            sealed=True,
        )
        assert meta.sealed is True
        assert meta.task_count == 5


# ---------------------------------------------------------------------------
# BackendId constants
# ---------------------------------------------------------------------------


class TestBackendIdConstants:
    def test_local_value(self) -> None:
        assert BACKEND_LOCAL == "local"

    def test_aws_value(self) -> None:
        assert BACKEND_AWS == "aws"

    def test_are_str_instances(self) -> None:
        assert isinstance(BACKEND_LOCAL, str)
        assert isinstance(BACKEND_AWS, str)
