"""Minimal evidence inputs for tests that call ``BundleWriter.write()``
directly (docs/adr/0007 decision 7, as amended 2026-11-02).

The writer requires its evidence argument: a default would quietly mark
every control "not in pack". Tests that are not about coverage pass these
minimal inputs instead -- the shipped catalogue, and a descriptor for each
of the summary's task ids with no control ids. Which tasks were selected
is the plan's to say: with no plan the writer registers the undeclared
plan, and with a declared one its selection.
"""

from __future__ import annotations

from agentevalops.catalogue import load_catalogue
from agentevalops.core.schemas import RunSummary, TraceEvent, TraceEventKind
from agentevalops.plans import EvidenceInputs

CATALOGUE = load_catalogue()


def minimal_evidence(summary: RunSummary | None = None) -> EvidenceInputs:
    """The shipped catalogue and a bare descriptor per task the summary
    ran; none for a summary with no tasks."""
    task_ids = (
        dict.fromkeys(str(r.task_id) for r in summary.task_results)
        if summary is not None
        else {}
    )
    return EvidenceInputs(
        catalogue=CATALOGUE,
        scenarios={
            task_id: {
                "category": None,
                "outcome_group": None,
                "variant": {},
                "control_ids": [],
            }
            for task_id in task_ids
        },
    )


def with_score_events(
    summary: RunSummary, events: list[TraceEvent]
) -> list[TraceEvent]:
    """A hand-built trace as the orchestrator would leave it from format 0.6
    (docs/adr/0007 D3): one ``evaluator.score`` event per evaluation result
    of each task run, after that run's own events, counted in its
    ``event_count`` and in the summary's ``trace_event_count``."""
    out = list(events)
    for result in summary.task_results:
        evaluations = result.evaluations or [result.evaluation]
        own = [
            e.step_index
            for e in out
            if e.task_id in (None, result.task_id) and e.repetition == result.repetition
        ]
        step = max(own, default=-1) + 1
        for offset, evaluation in enumerate(evaluations):
            out.append(
                TraceEvent(
                    run_id=summary.run_id,
                    step_index=step + offset,
                    kind=TraceEventKind.EVALUATOR_SCORE,
                    payload={
                        "evaluator_id": evaluation.evaluator_id,
                        "evaluator_kind": evaluation.evaluator_kind,
                        "applicable": evaluation.applicable,
                        "evaluated": evaluation.evaluated,
                        "passed": evaluation.passed,
                        "score": evaluation.score,
                        "not_evaluated_cause": evaluation.not_evaluated_cause,
                        "refs": list(evaluation.refs),
                    },
                    task_id=result.task_id,
                    repetition=result.repetition,
                )
            )
        result.event_count += len(evaluations)
    summary.trace_event_count = len(out)
    return out


def runner_fields(
    runner_id: str | None = "mock", mock_trajectory: str | None = None
) -> dict[str, str | None]:
    """The writer's two required runner fields (R28 follow-up, 2026-11-02),
    for a test that is not about them: a hand-built bundle stands in for
    a mock run that played no script."""
    return {"runner_id": runner_id, "mock_trajectory": mock_trajectory}
