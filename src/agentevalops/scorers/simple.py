"""SimpleScorer — aggregate a list of EvaluationResults into a ScoreResult."""

from __future__ import annotations

from collections.abc import Sequence

from agentevalops.core.schemas import EvaluationResult, ScoreResult
from agentevalops.core.types import RunId


class SimpleScorer:
    """Compute aggregate metrics from a completed set of task evaluations.

    All computation is done locally in pure Python with no external I/O.
    Inputs are never mutated.

    Metrics computed:

    - ``total_tasks``         — number of evaluations supplied.
    - ``passed_tasks``        — evaluated and passed.
    - ``failed_tasks``        — evaluated and failed.
    - ``not_evaluated_tasks`` — never evaluated (docs/adr/0006 D20: the
      same three-way split as ``RunSummary``; before format 0.5 these were
      counted as failed).
    - ``pass_rate``           — ``passed_tasks / total_tasks`` (0.0 if
      empty): a not-evaluated task counts against it, never silently.
    - ``aggregate_score``     — mean of ``score`` values (0.0 if empty).

    Satisfies the ``Scorer`` protocol through :meth:`summarize`.
    """

    scorer_id: str = "simple-v1"

    def __init__(self, run_id: RunId = RunId("")) -> None:
        self._run_id = run_id

    def summarize(
        self,
        scores: Sequence[EvaluationResult],
        baseline: ScoreResult | None = None,
    ) -> ScoreResult:
        """The ``Scorer`` protocol's entry point. No baseline comparison
        yet; ``baseline`` is accepted and ignored."""
        return self.score(self._run_id, list(scores))

    def score(
        self,
        run_id: RunId,
        results: list[EvaluationResult],
    ) -> ScoreResult:
        """Return a ``ScoreResult`` for *results*.

        Handles an empty list safely (all aggregates are 0.0 / 0).
        """
        total = len(results)
        if total == 0:
            return ScoreResult(
                scorer_id=self.scorer_id,
                run_id=run_id,
                task_scores=[],
                total_tasks=0,
                passed_tasks=0,
                failed_tasks=0,
                pass_rate=0.0,
                aggregate_score=0.0,
            )

        passed = sum(1 for r in results if r.evaluated and r.passed)
        not_evaluated = sum(1 for r in results if not r.evaluated)
        failed = total - passed - not_evaluated
        pass_rate = passed / total
        aggregate_score = sum(r.score for r in results) / total

        return ScoreResult(
            scorer_id=self.scorer_id,
            run_id=run_id,
            task_scores=list(results),  # copy — do not mutate caller's list
            total_tasks=total,
            passed_tasks=passed,
            failed_tasks=failed,
            pass_rate=pass_rate,
            aggregate_score=aggregate_score,
            not_evaluated_tasks=not_evaluated,
        )
