"""How a run's evaluation results become verdicts and a summary -- one
place, for the orchestrator that runs the tasks and for ``rescore``, which
re-derives a bundle's verdicts from its records (docs/adr/0008 R1, R3).

Moved out of ``orchestration/local.py`` unchanged (R29 (b1)): which
evaluators apply to a task (docs/adr/0006 D13), the not-evaluated result,
a task run's output from its terminal event, a task run's verdict from its
evaluators' results, the run-level output the policy checker judges, and
the summary's counts, score result and pass^k (docs/adr/0002 Axis 3;
docs/adr/0006 D18, D20). Moved too (R29 (b2)): the order of a run's task
runs, which terminal event is a run's, how one finished task run is judged
from its records and how a stopped one's result is made, its
``evaluator.score`` events and event count, the summary's closing steps
and the run-level policy check, and which task runs are carried rather
than evaluated. No second copy of any of these rules exists.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from agentevalops.core.protocols import Evaluator, PolicyChecker, Scorer
from agentevalops.core.schemas import (
    AgentOutput,
    EndStateRecord,
    EvaluationResult,
    EvaluatorContext,
    NotEvaluatedCause,
    PolicySpec,
    PolicyVerdict,
    RunSummary,
    TaskRunResult,
    TaskSpec,
    TerminationReason,
    ToolCallRecord,
    TraceEvent,
    TraceEventKind,
)
from agentevalops.core.types import RunId
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


#: The output of a task run the orchestrator stopped before any evaluator
#: could judge it -- its infrastructure or configuration failed, or its
#: scenario failed the preflight -- and that nothing else produces. Its
#: recorded result is carried, never recomputed (docs/adr/0008 R6).
CARRIED = TerminationReason.INFRA_FAILURE


def is_carried(termination_reason: object) -> bool:
    """Whether a task run whose output ended with *termination_reason* (the
    enum or its recorded string) is carried rather than evaluated."""
    return termination_reason in (CARRIED, CARRIED.value)


def task_runs(
    tasks: Sequence[TaskSpec], repetitions: int
) -> list[tuple[TaskSpec, int]]:
    """Every task run, task by task and then by repetition: the order of a
    run's results whatever its concurrency (docs/adr/0006 D18; the
    orchestrator owns repetitions, and task_id stays the scenario's id)."""
    return [(task, rep) for task in tasks for rep in range(max(1, repetitions))]


def new_summary(run_id: RunId, runs: Sequence[tuple[TaskSpec, int]]) -> RunSummary:
    """A run's summary before any result: its id and how many task runs it
    holds."""
    return RunSummary(run_id=run_id, total_tasks=len(runs))


async def evaluate(
    task: TaskSpec,
    output: AgentOutput,
    events: list[TraceEvent],
    context: EvaluatorContext,
    evaluators: Sequence[Evaluator],
) -> list[EvaluationResult]:
    """One task run judged: each configured evaluator that applies to
    *task*, in configuration order, then the result that says so for each
    kind the task declares and the run does not configure (docs/adr/0006
    D13). Evaluators see the task as declared and the agent's own events,
    never an ``evaluator.score`` event."""
    chosen, missing = applicable(task, evaluators)
    return [await e.evaluate(task, output, events, context) for e in chosen] + [
        unconfigured(kind) for kind in missing
    ]


def score_events(
    result: TaskRunResult,
    run_id: RunId,
    events: Sequence[TraceEvent],
    timestamp: datetime | None = None,
) -> list[TraceEvent]:
    """One ``evaluator.score`` event per evaluation result of *result*, in
    ``evaluations`` order (docs/adr/0007 D3, format 0.6). Every result
    counts: the real ones, the synthesised ``configuration`` ones and the
    not-evaluated ones, so the trace says what ``evaluations.json`` says.
    ``step_index`` continues the task run's own stream, *events*. A
    *timestamp*, when given, stamps them all instead of the clock."""
    step = max((e.step_index for e in events), default=-1) + 1
    stamp: dict[str, Any] = {} if timestamp is None else {"timestamp": timestamp}
    return [
        TraceEvent(
            run_id=run_id,
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
            **stamp,
        )
        for offset, evaluation in enumerate(result.evaluations or [result.evaluation])
    ]


async def check_policy(
    checker: PolicyChecker,
    spec: PolicySpec,
    summary: RunSummary,
    all_events: list[TraceEvent],
    records: Sequence[ToolCallRecord],
    end_states: Sequence[EndStateRecord],
) -> PolicyVerdict:
    """The run-level policy verdict: *checker* judging *spec* over the
    whole run's output, every event (score events included) and every
    harness record."""
    return await checker.check(
        spec,
        combined_output(summary),
        all_events,
        EvaluatorContext(records=tuple(records), end_states=tuple(end_states)),
    )


def run_policy_spec(spec: PolicySpec | None) -> PolicySpec:
    """The policy a run is checked against: its own, or the default policy
    when it names none."""
    return spec or PolicySpec(policy_id="default")


def terminal_of(events: Sequence[TraceEvent]) -> TraceEvent | None:
    """A task run's terminal event: the last ``agent.terminal`` its agent
    emitted, or None when it emitted none."""
    terminal = None
    for event in events:
        if event.kind == TraceEventKind.AGENT_TERMINAL:
            terminal = event
    return terminal


async def judge(
    task: TaskSpec,
    events: list[TraceEvent],
    records: Sequence[ToolCallRecord],
    end_states: Sequence[EndStateRecord],
    repetition: int,
    evaluators: Sequence[Evaluator],
) -> TaskRunResult:
    """One finished task run judged from its own records: the output its
    terminal event gives, each evaluator's result over its events, tool
    calls and end state (docs/adr/0006 D7, D13), and the verdict.
    Evaluators see the task as declared, never as the agent saw it."""
    output = output_from_terminal(terminal_of(events))
    context = EvaluatorContext(
        records=tuple(records),
        end_states=tuple(end_states),
        task_id=task.task_id,
        repetition=repetition,
        applicable_evaluators=task.applicable_evaluators,
    )
    evaluations = await evaluate(task, output, events, context, evaluators)
    return TaskRunResult(
        task_id=task.task_id,
        output=output,
        evaluation=verdict(evaluations),
        event_count=len(events),
        evaluations=evaluations,
        repetition=repetition,
    )


def stopped(
    task: TaskSpec,
    detail: str,
    cause: NotEvaluatedCause,
    events: Sequence[TraceEvent],
    evaluators: Sequence[Evaluator],
    *,
    repetition: int = 0,
) -> TaskRunResult:
    """The result of a task run the orchestrator stopped before any
    evaluator could judge it -- its infrastructure or configuration failed,
    or its scenario failed the preflight: every evaluator that would have
    applied records it not evaluated, with *cause* and *detail* (D16), and
    its output says ``infra_failure``."""
    output = AgentOutput(
        success=False,
        termination_reason=CARRIED,
        error=detail,
    )
    chosen, missing = applicable(task, evaluators)
    evaluations = [
        not_evaluated(e.evaluator_id, e.evaluator_kind, cause, detail) for e in chosen
    ] + [not_evaluated(kind, kind, cause, detail) for kind in missing]
    return TaskRunResult(
        task_id=task.task_id,
        output=output,
        evaluation=verdict(evaluations),
        event_count=len(events),
        evaluations=evaluations,
        repetition=repetition,
    )


def scored(
    result: TaskRunResult,
    run_id: RunId,
    events: Sequence[TraceEvent],
    timestamp: datetime | None = None,
) -> list[TraceEvent]:
    """*result*'s ``evaluator.score`` events, and its ``event_count``: the
    run's own *events* and these (docs/adr/0007 D3)."""
    new = score_events(result, run_id, events, timestamp)
    result.event_count = len(events) + len(new)
    return new


async def close_summary(
    summary: RunSummary,
    tasks: Sequence[TaskSpec],
    all_events: list[TraceEvent],
    checker: PolicyChecker,
    spec: PolicySpec,
    records: Sequence[ToolCallRecord],
    end_states: Sequence[EndStateRecord],
) -> None:
    """The summary's closing steps once every task run is tallied: the
    counts, score result and pass^k (docs/adr/0002 Axis 3; docs/adr/0006
    D18, D20), then the run-level policy verdict over every event."""
    aggregate(summary, categories_of(tasks), len(all_events))
    summary.policy_verdict = await check_policy(
        checker, spec, summary, all_events, records, end_states
    )
