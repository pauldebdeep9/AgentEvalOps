"""pass^k over repeated task runs (docs/adr/0006 D18, D20; ROADMAP R27;
testing-framework 3.6.1).

The orchestrator runs each task k times (D18). This module groups the
task runs by task id: a scenario *passes* only if every one of its k
repetitions passed; it is *not evaluated* if any repetition was not
evaluated (not evaluated is not failed, D20), and then carries a cause --
the first repetition's, or ``"mixed"`` when the repetitions' causes
differ -- so "not evaluated" never appears without one; otherwise it
*fails*. Per category, the pass^k rate is passing scenarios over
scenarios. Raw counts and k here; each rate's 95% interval is
``scorers/bootstrap.py``'s, which ``aggregate()`` adds (ROADMAP R36,
reinstating what the W25 cut dropped).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence

from agentevalops.core.schemas import CategoryPassK, PassKResult, TaskRunResult

__all__ = ["NOT_EVALUATED_MIXED", "pass_k_by_category", "pass_k_results"]

#: The cause a scenario carries when its not-evaluated repetitions give
#: different causes.
NOT_EVALUATED_MIXED: str = "mixed"

#: The cause recorded for a not-evaluated run that names none. The
#: orchestrator always names one (docs/adr/0006 D16); this keeps the
#: promise that a cause is never absent even for a hand-built result.
_UNKNOWN_CAUSE: str = "unknown"


def pass_k_results(task_results: Sequence[TaskRunResult]) -> list[PassKResult]:
    """One ``PassKResult`` per task id, in first-seen order, from the
    task runs' derived verdicts."""
    groups: dict[str, list[TaskRunResult]] = {}
    for result in task_results:
        groups.setdefault(str(result.task_id), []).append(result)
    out: list[PassKResult] = []
    for task_id, runs in groups.items():
        ordered = sorted(runs, key=lambda r: r.repetition)
        passed = sum(
            1 for r in ordered if r.evaluation.evaluated and r.evaluation.passed
        )
        not_evaluated = [r for r in ordered if not r.evaluation.evaluated]
        failed = len(ordered) - passed - len(not_evaluated)
        cause: str | None = None
        verdict: bool | None
        if not_evaluated:
            causes = [
                r.evaluation.not_evaluated_cause or _UNKNOWN_CAUSE
                for r in not_evaluated
            ]
            cause = (
                causes[0]
                if all(c == causes[0] for c in causes)
                else NOT_EVALUATED_MIXED
            )
            verdict = None
        else:
            verdict = passed == len(ordered)
        out.append(
            PassKResult(
                task_id=task_id,
                k=len(ordered),
                passed=passed,
                failed=failed,
                not_evaluated=len(not_evaluated),
                pass_k=verdict,
                not_evaluated_cause=cause,
            )
        )
    return out


def pass_k_by_category(
    results: Sequence[PassKResult], categories: Mapping[str, str]
) -> list[CategoryPassK]:
    """The roll-up per category, in first-seen order, for the results whose
    task id *categories* maps; a task without a category is left out."""
    groups: dict[str, list[PassKResult]] = {}
    for result in results:
        category = categories.get(result.task_id)
        if category is None:
            continue
        groups.setdefault(category, []).append(result)
    out: list[CategoryPassK] = []
    for category, members in groups.items():
        passed = sum(1 for r in members if r.pass_k is True)
        failed = sum(1 for r in members if r.pass_k is False)
        not_evaluated = [r for r in members if r.pass_k is None]
        causes = Counter(r.not_evaluated_cause or _UNKNOWN_CAUSE for r in not_evaluated)
        out.append(
            CategoryPassK(
                category=category,
                k=max(r.k for r in members),
                scenarios=len(members),
                passed=passed,
                failed=failed,
                not_evaluated=len(not_evaluated),
                pass_k_rate=passed / len(members),
                not_evaluated_causes=dict(causes),
            )
        )
    return out
