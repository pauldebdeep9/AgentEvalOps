"""LocalOrchestrator — synchronous task-loop wiring up the WBS 1 contracts."""

from __future__ import annotations

from dataclasses import dataclass, field

from agentevalops.core.protocols import (
    AgentRunner,
    BenchmarkAdapter,
    Evaluator,
    PolicyChecker,
    TraceStore,
)
from agentevalops.core.schemas import (
    AgentInput,
    AgentOutput,
    EvaluationResult,
    PolicySpec,
    PolicyVerdict,
    RunConfig,
    ScoreResult,
    TaskSpec,
    TerminationReason,
    TraceEvent,
    TraceEventKind,
)
from agentevalops.core.types import RunId, TaskId
from agentevalops.scorers.simple import SimpleScorer

# ---------------------------------------------------------------------------
# Result types (WBS 2-local — not part of the core contracts)
# ---------------------------------------------------------------------------


@dataclass
class TaskRunResult:
    """Outcome of a single task within a run."""

    task_id: TaskId
    output: AgentOutput
    evaluation: EvaluationResult
    event_count: int


@dataclass
class RunSummary:
    """Aggregated result of a complete LocalOrchestrator run."""

    run_id: RunId
    task_results: list[TaskRunResult] = field(default_factory=list)
    policy_verdict: PolicyVerdict | None = None
    score_result: ScoreResult | None = None
    total_tasks: int = 0
    passed_tasks: int = 0
    failed_tasks: int = 0
    trace_event_count: int = 0
    total_cost_usd: float = 0.0
    total_tokens: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _combined_output(summary: RunSummary) -> AgentOutput:
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


def _output_from_terminal(terminal: TraceEvent | None) -> AgentOutput:
    """Reconstruct ``AgentOutput`` from the payload of an AGENT_TERMINAL event.

    If no terminal event was emitted, returns a synthetic error output.
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


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class LocalOrchestrator:
    """Wires together the WBS 1 protocols for a local evaluation run.

    Not implemented:
    - Result bundle writing (WBS 3)
    - Trace replay
    - Cloud / AWS backends
    - LLM judge, SWE-bench, FastAPI dashboard
    """

    def __init__(
        self,
        run_config: RunConfig,
        benchmark: BenchmarkAdapter,
        runner: AgentRunner,
        trace_store: TraceStore,
        evaluator: Evaluator,
        policy_checker: PolicyChecker,
        policy_spec: PolicySpec | None = None,
    ) -> None:
        self._config = run_config
        self._benchmark = benchmark
        self._runner = runner
        self._trace_store = trace_store
        self._evaluator = evaluator
        self._policy_checker = policy_checker
        self._policy_spec = policy_spec or PolicySpec(policy_id="default")

    async def run(self) -> RunSummary:
        """Execute all benchmark tasks sequentially and return a summary."""
        summary = RunSummary(run_id=self._config.run_id)
        tasks = list(self._benchmark.list_tasks())
        summary.total_tasks = len(tasks)

        all_events: list[TraceEvent] = []

        for task in tasks:
            result, events = await self._run_task(task)
            summary.task_results.append(result)
            if result.evaluation.passed:
                summary.passed_tasks += 1
            summary.total_cost_usd += result.output.total_cost_usd
            summary.total_tokens += result.output.total_tokens
            all_events.extend(events)

        await self._trace_store.finalize(self._config.run_id)

        # --- post-run aggregation -----------------------------------------
        summary.failed_tasks = summary.total_tasks - summary.passed_tasks
        summary.trace_event_count = len(all_events)

        all_evals = [r.evaluation for r in summary.task_results]
        summary.score_result = SimpleScorer().score(
            self._config.run_id, all_evals
        )

        combined = _combined_output(summary)
        summary.policy_verdict = await self._policy_checker.check(
            self._policy_spec,
            combined,
            all_events,
        )

        return summary

    async def _run_task(
        self, task: TaskSpec
    ) -> tuple[TaskRunResult, list[TraceEvent]]:
        """Run a single task end-to-end and return its result + events."""
        run_id: RunId = self._config.run_id
        agent_input = AgentInput(
            task=task,
            context={"run_id": run_id},
        )
        limits = self._config.resource_limits

        trace_iter = await self._runner.run(agent_input, limits)
        events: list[TraceEvent] = []
        terminal: TraceEvent | None = None

        async for event in trace_iter:
            await self._trace_store.append(run_id, event)
            events.append(event)
            if event.kind == TraceEventKind.AGENT_TERMINAL:
                terminal = event

        output = _output_from_terminal(terminal)
        evaluation = await self._evaluator.evaluate(task, output, events)

        return (
            TaskRunResult(
                task_id=task.task_id,
                output=output,
                evaluation=evaluation,
                event_count=len(events),
            ),
            events,
        )
