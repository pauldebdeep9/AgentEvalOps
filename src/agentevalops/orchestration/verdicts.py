"""How a run's evaluation results become verdicts and a summary -- one
place, for the orchestrator that runs the tasks and for ``rescore``, which
re-derives a bundle's verdicts from its records (docs/adr/0008 R1, R3).

Moved out of ``orchestration/local.py`` unchanged (R29 (b1)): which
evaluators apply to a task (docs/adr/0006 D13), the not-evaluated result,
a task run's output from its terminal event, a task run's verdict from its
evaluators' results, the run-level output the policy checker judges, and
the summary's counts, score result and pass^k (docs/adr/0002 Axis 3;
docs/adr/0006 D18, D20). No second copy of any of these rules exists.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from agentevalops.core.protocols import Evaluator, Scorer
from agentevalops.core.schemas import (
    AgentOutput,
    EvaluationResult,
    NotEvaluatedCause,
    RunSummary,
    TaskRunResult,
    TaskSpec,
    TerminationReason,
    TraceEvent,
)
from agentevalops.scorers.pass_k import pass_k_by_category, pass_k_results
from agentevalops.scorers.simple import SimpleScorer


def output_from_terminal(terminal: TraceEvent | None) -> AgentOutput:
    """Reconstruct ``AgentOutput`` from the payload of an AGENT_TERMINAL event.

    If no terminal event was emitted, returns a synthetic error output.
    The run records the result as the task run's output; validate-bundle
    checks a bundle's carried outputs against it (docs/adr/0008 R6).
    """
    if terminal is None:
        return AgentOutput(
            success=False,
            termination_reason=TerminationReason.AGENT_ERROR,
            error="Agent did not emit an AGENT_TERMINAL event.",
        )
    p = terminal.payload
    return AgentOutput(
        success=bool(p.get("success", False)),
        termination_reason=TerminationReason(
            p.get("termination_reason", TerminationReason.AGENT_ERROR.value)
        ),
        final_answer=p.get("final_answer"),
        total_cost_usd=float(p.get("total_cost_usd", 0.0)),
        total_tokens=int(p.get("total_tokens", 0)),
        wall_seconds=float(p.get("wall_seconds", 0.0)),
    )


def combined_output(summary: RunSummary) -> AgentOutput:
    """Synthesise an AgentOutput that represents the whole run.

    Used as the *output* argument to ``PolicyChecker.check`` so cost and
    success span all tasks, not just the last one.
    """
    all_success = bool(summary.task_results) and all(
        r.output.success for r in summary.task_results
    )
    return AgentOutput(
        success=all_success,
        termination_reason=TerminationReason.COMPLETED,
        total_cost_usd=summary.total_cost_usd,
        total_tokens=summary.total_tokens,
    )


def applicable(
    task: TaskSpec, evaluators: Sequence[Evaluator]
) -> tuple[list[Evaluator], list[str]]:
    """The configured evaluators that apply to *task*, and any kind the task
    declares that the run did not configure (docs/adr/0006 D13)."""
    if not task.applicable_evaluators:
        return list(evaluators), []
    declared = set(task.applicable_evaluators)
    chosen = [e for e in evaluators if e.evaluator_kind in declared]
    configured = {e.evaluator_kind for e in evaluators}
    return chosen, sorted(declared - configured)


def not_evaluated(
    evaluator_id: str, evaluator_kind: str, cause: NotEvaluatedCause, detail: str
) -> EvaluationResult:
    return EvaluationResult(
        evaluator_id=evaluator_id,
        evaluator_kind=evaluator_kind,
        score=0.0,
        passed=False,
        evaluated=False,
        not_evaluated_cause=cause,
        notes=f"not evaluated: {cause} ({detail})",
    )


def unconfigured(kind: str) -> EvaluationResult:
    """The result for an evaluator kind a task declares that the run does
    not configure (docs/adr/0006 D13)."""
    return not_evaluated(
        kind,
        kind,
        "configuration",
        f"the task declares '{kind}', which this run does not configure",
    )


def verdict(results: Sequence[EvaluationResult]) -> EvaluationResult:
    """A task run's verdict from its evaluators' results (docs/adr/0006 D13).

    Results an evaluator marked not applicable are not counted. If any
    counted evaluator ran and failed, the task failed -- a real failure is
    never downgraded. Otherwise, if any could not evaluate, the task is not
    evaluated. Otherwise it passed. With exactly one counted result, that
    result is the verdict.
    """
    counted = [r for r in results if r.applicable]
    if len(counted) == 1:
        return counted[0]
    if not counted:
        return not_evaluated(
            "task-verdict", "verdict", "configuration", "no evaluator applied"
        )
    failed = [r for r in counted if r.evaluated and not r.passed]
    unevaluated = [r for r in counted if not r.evaluated]
    notes = "; ".join(
        f"{r.evaluator_kind}: "
        + ("not evaluated" if not r.evaluated else "passed" if r.passed else "failed")
        for r in counted
    )
    refs = [ref for r in counted for ref in r.refs]
    if failed:
        return EvaluationResult(
            "task-verdict", "verdict", 0.0, False, notes=notes, refs=refs
        )
    if unevaluated:
        return EvaluationResult(
            "task-verdict",
            "verdict",
            0.0,
            False,
            evaluated=False,
            not_evaluated_cause=unevaluated[0].not_evaluated_cause,
            notes=notes,
            refs=refs,
        )
    return EvaluationResult(
        "task-verdict", "verdict", 1.0, True, notes=notes, refs=refs
    )


def tally(summary: RunSummary, result: TaskRunResult) -> None:
    """Add one task run to *summary*: its result, the passed and
    not-evaluated counts (by cause), its cost and tokens."""
    summary.task_results.append(result)
    if not result.evaluation.evaluated:
        summary.not_evaluated_tasks += 1
        cause = result.evaluation.not_evaluated_cause or "unknown"
        summary.not_evaluated_by_cause[cause] = (
            summary.not_evaluated_by_cause.get(cause, 0) + 1
        )
    elif result.evaluation.passed:
        summary.passed_tasks += 1
    summary.total_cost_usd += result.output.total_cost_usd
    summary.total_tokens += result.output.total_tokens


def aggregate(
    summary: RunSummary,
    categories: Mapping[str, str],
    trace_event_count: int,
) -> None:
    """The post-run aggregation over a summary whose task runs are all
    tallied: the failed count (docs/adr/0002, Axis 3: passed + failed +
    not_evaluated == total), the trace length, the score result, and pass^k
    per scenario and per category (*categories* maps task id to category;
    docs/adr/0006 D18, D20)."""
    summary.failed_tasks = (
        summary.total_tasks - summary.passed_tasks - summary.not_evaluated_tasks
    )
    summary.trace_event_count = trace_event_count
    scorer: Scorer = SimpleScorer(summary.run_id)
    summary.score_result = scorer.summarize(
        [r.evaluation for r in summary.task_results]
    )
    summary.pass_k = pass_k_results(summary.task_results)
    summary.pass_k_by_category = pass_k_by_category(summary.pass_k, dict(categories))


def categories_of(tasks: Sequence[TaskSpec]) -> dict[str, str]:
    """Task id to category, for the tasks whose metadata names one."""
    return {
        str(t.task_id): str(t.metadata["category"])
        for t in tasks
        if isinstance(t.metadata.get("category"), str) and t.metadata["category"]
    }
