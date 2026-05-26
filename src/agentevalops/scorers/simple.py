"""SimpleScorer — aggregate a list of EvaluationResults into a ScoreResult."""

from __future__ import annotations

from agentevalops.core.schemas import EvaluationResult, ScoreResult
from agentevalops.core.types import RunId


class SimpleScorer:
    """Compute aggregate metrics from a completed set of task evaluations.

    All computation is done locally in pure Python with no external I/O.
    Inputs are never mutated.

    Metrics computed:

    - ``total_tasks``    — number of evaluations supplied.
    - ``passed_tasks``   — count of evaluations with ``passed=True``.
    - ``failed_tasks``   — ``total_tasks - passed_tasks``.
    - ``pass_rate``      — ``passed_tasks / total_tasks`` (0.0 if empty).
    - ``aggregate_score``— mean of ``score`` values (0.0 if empty).
    """

    scorer_id: str = "simple-v1"

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

        passed = sum(1 for r in results if r.passed)
        failed = total - passed
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
        )
