"""Tests for minieval.schemas.

These test the two things schemas can get wrong on their own: JSON
round-tripping, and the derived aggregates.  Everything else here is
dataclass plumbing that the type checker already covers.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from minieval.schemas import (
    AgentOutput,
    Attempt,
    EvaluationResult,
    PolicySpec,
    PolicyVerdict,
    ResourceLimits,
    RunConfig,
    RunId,
    RunSummary,
    Stability,
    TaskId,
    TaskResult,
    TaskSpec,
    TerminationReason,
    TraceEvent,
    TraceEventKind,
    Verdict,
)

RUN = RunId("test-run")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _task(task_id: str = "T01") -> TaskSpec:
    return TaskSpec(task_id=TaskId(task_id), description="a task")


def _attempt(
    index: int,
    *,
    passed: bool,
    evaluated: bool = True,
    cost: float = 0.01,
) -> Attempt:
    return Attempt(
        index=index,
        output=AgentOutput(
            success=passed,
            termination_reason=(
                TerminationReason.COMPLETED
                if evaluated
                else TerminationReason.INFRA_FAILURE
            ),
            cost_usd=cost,
        ),
        evaluation=EvaluationResult(
            evaluator_id="deterministic-v1",
            passed=passed,
            evaluated=evaluated,
        ),
        policy=PolicyVerdict(policy_id="default", verdict=Verdict.PASS),
    )


# ---------------------------------------------------------------------------
# TraceEvent serialisation
# ---------------------------------------------------------------------------


def test_trace_event_json_round_trip_preserves_every_field() -> None:
    original = TraceEvent(
        run_id=RUN,
        task_id=TaskId("T01"),
        attempt=2,
        step_index=3,
        kind=TraceEventKind.AGENT_TOOL_CALL,
        payload={"tool_name": "lookup_po", "args": {"po": "4501"}},
        timestamp=datetime(2026, 8, 26, 9, 30, tzinfo=UTC),
        cost_usd=0.0004,
    )

    restored = TraceEvent.from_json(original.to_json())

    assert restored == original
    assert restored.kind is TraceEventKind.AGENT_TOOL_CALL
    assert restored.timestamp.tzinfo is not None


def test_trace_event_from_json_rejects_unknown_kind() -> None:
    line = (
        TraceEvent(
            run_id=RUN,
            task_id=TaskId("T01"),
            attempt=0,
            step_index=0,
            kind=TraceEventKind.AGENT_PLAN,
        )
        .to_json()
        .replace("agent.plan", "agent.telepathy")
    )

    with pytest.raises(ValueError):
        TraceEvent.from_json(line)


# ---------------------------------------------------------------------------
# TaskResult aggregates
# ---------------------------------------------------------------------------


def test_k_of_n_counts_passing_attempts() -> None:
    result = TaskResult(
        task=_task(),
        attempts=[
            _attempt(0, passed=True),
            _attempt(1, passed=False),
            _attempt(2, passed=True),
        ],
    )
    assert (result.k, result.n) == (2, 3)


@pytest.mark.parametrize(
    ("outcomes", "expected"),
    [
        ([True, True, True], Stability.STABLE_PASS),
        ([False, False, False], Stability.STABLE_FAIL),
        ([True, False, True], Stability.FLAKY),
    ],
)
def test_stability_classifies_repeat_behaviour(
    outcomes: list[bool], expected: Stability
) -> None:
    result = TaskResult(
        task=_task(),
        attempts=[_attempt(i, passed=p) for i, p in enumerate(outcomes)],
    )
    assert result.stability is expected


def test_task_never_evaluated_is_not_reported_as_a_failure() -> None:
    result = TaskResult(
        task=_task(),
        attempts=[_attempt(i, passed=False, evaluated=False) for i in range(3)],
    )
    assert result.stability is Stability.NOT_EVALUATED
    assert result.evaluated == 0
    assert result.k == 0  # zero passes, but NOT classified as STABLE_FAIL


def test_stability_ignores_unevaluated_attempts_when_classifying() -> None:
    """Two judged passes plus one never-judged attempt is a stable pass.

    The unevaluated attempt is absent evidence, not contrary evidence.
    """
    result = TaskResult(
        task=_task(),
        attempts=[
            _attempt(0, passed=True),
            _attempt(1, passed=False, evaluated=False),
            _attempt(2, passed=True),
        ],
    )
    assert result.stability is Stability.STABLE_PASS
    assert (result.k, result.evaluated, result.n) == (2, 2, 3)


# ---------------------------------------------------------------------------
# RunSummary partition
# ---------------------------------------------------------------------------


def _summary() -> RunSummary:
    config = RunConfig(
        run_id=RUN,
        repeats=3,
        limits=ResourceLimits(max_steps=4, max_cost_usd=0.5),
        policy=PolicySpec(deny_tool_names=("post_invoice",)),
    )
    return RunSummary(
        run_id=RUN,
        config=config,
        task_results=[
            TaskResult(
                task=_task("T01"),
                attempts=[_attempt(i, passed=True) for i in range(3)],
            ),
            TaskResult(
                task=_task("T02"),
                attempts=[
                    _attempt(0, passed=True),
                    _attempt(1, passed=False),
                    _attempt(2, passed=False),
                ],
            ),
            TaskResult(
                task=_task("T03"),
                attempts=[_attempt(i, passed=False, evaluated=False) for i in range(3)],
            ),
        ],
    )


def test_attempt_partition_is_exhaustive() -> None:
    s = _summary()
    assert s.total_attempts == 9
    assert (s.passed_attempts, s.failed_attempts, s.not_evaluated_attempts) == (
        4,
        2,
        3,
    )
    assert (
        s.passed_attempts + s.failed_attempts + s.not_evaluated_attempts
        == s.total_attempts
    )


def test_failed_attempts_excludes_infrastructure_failures() -> None:
    """The whole point of the three-way partition.

    T03 failed three times, but never on merit.  Counting it as failure
    would report a 4/9 pass rate against 6 real judgements.
    """
    s = _summary()
    assert s.failed_attempts == 2
    assert s.tasks_by_stability(Stability.NOT_EVALUATED) == [TaskId("T03")]


def test_tasks_by_stability_returns_names_not_counts() -> None:
    s = _summary()
    assert s.tasks_by_stability(Stability.STABLE_PASS) == [TaskId("T01")]
    assert s.tasks_by_stability(Stability.FLAKY) == [TaskId("T02")]
    assert s.tasks_by_stability(Stability.STABLE_FAIL) == []


def test_cost_aggregates_across_tasks_and_attempts() -> None:
    s = _summary()
    assert s.total_cost_usd == pytest.approx(0.09)
