"""StateBasedEvaluator -- the environment's end state against the scenario's
expected one (docs/adr/0006 D7, D12, D13)."""

from __future__ import annotations

from agentevalops.core.schemas import (
    AgentOutput,
    EvaluationResult,
    EvaluatorContext,
    TaskSpec,
    TraceEvent,
)
from agentevalops.evaluators.templates import mismatches


class StateBasedEvaluator:
    """Compares the task run's end state, as the harness snapshotted it,
    with ``task.metadata["expected_end_state"]``.

    The expected state is a template: every key it names must be present
    and equal, lists in full and in order, and keys it does not name are
    unconstrained -- so a scenario pins what matters (which decisions,
    notices, referrals and exports happened, and that nothing else did)
    without spelling out free text. A task without an expected end state
    is not this evaluator's to judge: the result is not applicable (D13).
    No model, no I/O; the end state is read from the ``EvaluatorContext``,
    never from the environment itself.
    """

    evaluator_id: str = "state-based-v1"
    evaluator_kind: str = "state_based"

    async def evaluate(
        self,
        task: TaskSpec,
        output: AgentOutput,
        trace: list[TraceEvent],
        context: EvaluatorContext | None = None,
    ) -> EvaluationResult:
        expected = task.metadata.get("expected_end_state")
        if not isinstance(expected, dict):
            return self._result(
                passed=False,
                applicable=False,
                notes="not applicable: the task declares no expected_end_state",
            )
        actual = context.end_state if context is not None else None
        if actual is None:
            return self._result(
                passed=False,
                notes="no end state was recorded for this task run",
            )
        found = mismatches(expected, actual)
        if found:
            return self._result(passed=False, notes="; ".join(found))
        schema = actual.get("schema", "unknown")
        return self._result(
            passed=True,
            notes=(
                f"end state matches the expected one ({len(expected)} key(s) "
                f"checked, schema {schema})"
            ),
        )

    def _result(
        self, *, passed: bool, notes: str, applicable: bool = True
    ) -> EvaluationResult:
        return EvaluationResult(
            evaluator_id=self.evaluator_id,
            evaluator_kind=self.evaluator_kind,
            score=1.0 if passed else 0.0,
            passed=passed,
            applicable=applicable,
            notes=notes,
        )
