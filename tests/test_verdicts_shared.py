"""The per-run code the orchestrator and rescore share
(orchestration/verdicts.py; R29 (b2)): which terminal event is a run's, the
score events stamped with a given time, and a stopped run's result.
Everything else here is proved by the run itself: the suite and a toy, a
seed and a known-bad run written again, identical to before the move."""

from __future__ import annotations

from datetime import datetime, timezone

from agentevalops.core.schemas import (
    EvaluationResult,
    TaskSpec,
    TerminationReason,
    TraceEvent,
    TraceEventKind,
)
from agentevalops.core.types import RunId, TaskId
from agentevalops.evaluators.deterministic import DeterministicEvaluator
from agentevalops.orchestration.verdicts import (
    is_carried,
    score_events,
    scored,
    stopped,
    terminal_of,
    verdict,
)

RUN = RunId("r")


def _event(step: int, kind: TraceEventKind, **payload: object) -> TraceEvent:
    return TraceEvent(run_id=RUN, step_index=step, kind=kind, payload=dict(payload))


def test_a_runs_terminal_event_is_its_last() -> None:
    first = _event(1, TraceEventKind.AGENT_TERMINAL, success=False)
    last = _event(3, TraceEventKind.AGENT_TERMINAL, success=True)
    events = [
        _event(0, TraceEventKind.AGENT_PLAN),
        first,
        _event(2, TraceEventKind.AGENT_PLAN),
        last,
    ]
    assert terminal_of(events) is last
    assert terminal_of([_event(0, TraceEventKind.AGENT_PLAN)]) is None


def test_score_events_take_a_given_time_and_count_with_the_run() -> None:
    task = TaskSpec(task_id=TaskId("t"), benchmark_id="b", description="d")
    result = stopped(
        task,
        "prepare_environment: gone",
        "infrastructure",
        [],
        [DeterministicEvaluator()],
    )
    when = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    events = [_event(0, TraceEventKind.AGENT_PLAN)]
    new = score_events(result, RUN, events, when)
    assert [e.timestamp for e in new] == [when] and new[0].step_index == 1
    assert scored(result, RUN, events, when)[0].timestamp == when
    assert result.event_count == 2


def test_a_stopped_runs_result_is_carried_and_says_why() -> None:
    task = TaskSpec(task_id=TaskId("t"), benchmark_id="b", description="d")
    result = stopped(
        task,
        "runner: boom",
        "configuration",
        [],
        [DeterministicEvaluator()],
        repetition=2,
    )
    assert result.output.termination_reason is TerminationReason.INFRA_FAILURE
    assert is_carried(result.output.termination_reason) and is_carried("infra_failure")
    assert result.output.error == "runner: boom" and result.repetition == 2
    (evaluation,) = result.evaluations
    assert isinstance(evaluation, EvaluationResult)
    assert (evaluation.evaluator_id, evaluation.evaluated) == (
        "deterministic-v1",
        False,
    )
    assert evaluation.not_evaluated_cause == "configuration"
    assert result.evaluation == verdict(result.evaluations)
