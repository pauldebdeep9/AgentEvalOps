"""LocalOrchestrator — async task-loop wiring up the WBS 1 contracts."""

from __future__ import annotations

import asyncio
import logging

from agentevalops.core.errors import ConfigurationError, InfrastructureError
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
    RunConfig,
    RunSummary,
    TaskRunResult,
    TaskSpec,
    TerminationReason,
    TraceEvent,
    TraceEventKind,
)
from agentevalops.core.types import RunId
from agentevalops.scorers.simple import SimpleScorer

logger = logging.getLogger(__name__)

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


def _not_evaluated_result(
    task: TaskSpec,
    exc: Exception,
    events: list[TraceEvent],
    evaluator: Evaluator,
    *,
    stage: str | None = None,
) -> TaskRunResult:
    """Build the shared not-evaluated result for an infra/config failure.

    Used by both the W12a runner-failure catch and the
    ``prepare_environment`` guard (docs/benchmark_adapters.md D5) so the two
    paths share one construction site instead of two copies. ``stage``, when
    given, is folded into the error/notes text so the two paths stay
    distinguishable (e.g. "prepare_environment: <exc>") without adding any
    new schema field.
    """
    detail = f"{stage}: {exc}" if stage else str(exc)
    output = AgentOutput(
        success=False,
        termination_reason=TerminationReason.INFRA_FAILURE,
        error=detail,
    )
    evaluation = EvaluationResult(
        evaluator_id=evaluator.evaluator_id,
        evaluator_kind=evaluator.evaluator_kind,
        score=0.0,
        passed=False,
        evaluated=False,
        notes=f"not evaluated: infrastructure failure before completion ({detail})",
    )
    return TaskRunResult(
        task_id=task.task_id,
        output=output,
        evaluation=evaluation,
        event_count=len(events),
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
        """Execute all benchmark tasks and return a summary.

        Tasks run concurrently, bounded by ``run_config.max_concurrent_tasks``
        (a semaphore).  Regardless of concurrency, ``task_results`` and the
        aggregated ``all_events`` are ordered by task-enumeration order so the
        summary is deterministic.  If the run is interrupted (``KeyboardInterrupt``
        or task cancellation), the trace store is finalized on a best-effort
        basis before the exception propagates, so partially-streamed traces are
        still persisted.
        """
        summary = RunSummary(run_id=self._config.run_id)
        tasks = list(self._benchmark.list_tasks())
        summary.total_tasks = len(tasks)

        limit = max(1, self._config.max_concurrent_tasks)
        semaphore = asyncio.Semaphore(limit)

        async def _guarded(
            task: TaskSpec,
        ) -> tuple[TaskRunResult, list[TraceEvent]]:
            async with semaphore:
                return await self._run_task(task)

        all_events: list[TraceEvent] = []

        futures = [asyncio.ensure_future(_guarded(t)) for t in tasks]
        try:
            results = await asyncio.gather(*futures)
        except (KeyboardInterrupt, asyncio.CancelledError):
            # Cancel any siblings and retrieve each future's outcome so no
            # exception is left "never retrieved", then persist whatever was
            # streamed on a best-effort basis before re-raising.
            for fut in futures:
                fut.cancel()
            for fut in futures:
                try:
                    await fut
                except BaseException:  # noqa: BLE001 - draining, intentional
                    pass
            await self._trace_store.finalize(self._config.run_id)
            raise

        for result, events in results:
            summary.task_results.append(result)
            if not result.evaluation.evaluated:
                summary.not_evaluated_tasks += 1
            elif result.evaluation.passed:
                summary.passed_tasks += 1
            summary.total_cost_usd += result.output.total_cost_usd
            summary.total_tokens += result.output.total_tokens
            all_events.extend(events)

        await self._trace_store.finalize(self._config.run_id)

        # --- post-run aggregation -----------------------------------------
        # Three-way partition (docs/adr/0002, Axis 3): passed + failed +
        # not_evaluated == total. failed_tasks now means a genuine,
        # evaluated merit-based failure -- it no longer silently absorbs
        # tasks that were never evaluated at all (not_evaluated_tasks does).
        summary.failed_tasks = (
            summary.total_tasks
            - summary.passed_tasks
            - summary.not_evaluated_tasks
        )
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
        """Run a single task end-to-end and return its result + events.

        A runner is contractually allowed to raise ``InfrastructureError``/
        ``ConfigurationError`` instead of yielding a terminal event
        (``core/protocols.py``'s ``AgentRunner`` docstring: "Raise only on
        infrastructure failure"). Before docs/adr/0002 (2026-08-05) that
        raise propagated straight out of this method, through
        ``asyncio.gather()``, aborting the *entire* run -- every other
        task's results discarded, no bundle ever written (see the fixture
        at ``tests/fixtures/bundles/partial-infra-failure-2026-08-03/`` and
        this session's fault-injected reproduction). It is now caught here,
        per task: the task comes back as a well-formed, honestly-labeled
        ``not_evaluated`` result and the run continues.

        Deliberately narrow: only the two documented "legitimate runner
        failure" exception types are caught here. An exception of any other
        type is a bug, not a modeled failure mode, and is left to propagate
        out of the whole run -- ``cli.py``'s best-effort finalize path
        (docs/adr/0002, Axis 4) is what handles *that*, distinctly, as a
        ``"partial"`` bundle rather than a normal one with some
        not-evaluated tasks in it.

        ``BenchmarkAdapter.prepare_environment``/``teardown``
        (docs/benchmark_adapters.md D5) bracket the runner/evaluate work.
        ``prepare_environment`` runs first, inside the caller's concurrency
        semaphore, via ``asyncio.to_thread`` (D5: synchronous, like
        ``list_tasks``/``grade`` -- a git clone must not block the event
        loop). A failure there is guarded the same narrow way as the W12a
        runner catch, sharing ``_not_evaluated_result`` rather than copying
        it, and never calls ``teardown`` (no handle was produced). Once a
        handle exists, everything else -- the runner, the W12a catch, and
        evaluation -- runs inside a ``try/finally`` so ``teardown`` always
        runs: on the normal return, on the W12a catch, and on any exception
        propagating out of ``evaluate()`` or a cancellation. ``teardown``
        itself is guarded by a bare ``except Exception`` that only logs --
        a cleanup failure must never replace or hide the task's real
        outcome.
        """
        run_id: RunId = self._config.run_id
        events: list[TraceEvent] = []

        try:
            handle = await asyncio.to_thread(
                self._benchmark.prepare_environment, task
            )
        except (InfrastructureError, ConfigurationError) as exc:
            return (
                _not_evaluated_result(
                    task,
                    exc,
                    events,
                    self._evaluator,
                    stage="prepare_environment",
                ),
                events,
            )

        try:
            agent_input = AgentInput(
                task=task,
                context={"run_id": run_id},
            )
            limits = self._config.resource_limits

            try:
                trace_iter = await self._runner.run(agent_input, limits)
                terminal: TraceEvent | None = None
                async for event in trace_iter:
                    await self._trace_store.append(run_id, event)
                    events.append(event)
                    if event.kind == TraceEventKind.AGENT_TERMINAL:
                        terminal = event
            except (InfrastructureError, ConfigurationError) as exc:
                return (
                    _not_evaluated_result(task, exc, events, self._evaluator),
                    events,
                )

            output = _output_from_terminal(terminal)
            # Scoring is routed through the Evaluator (DeterministicEvaluator
            # by default), not BenchmarkAdapter.grade().  grade() is the
            # benchmark's own official grading and is reserved for future
            # use when an upstream benchmark's deterministic test suite
            # needs to run post-hoc.
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
        finally:
            try:
                await asyncio.to_thread(self._benchmark.teardown, handle)
            except Exception:  # noqa: BLE001 - cleanup failure must not mask the real outcome
                logger.warning(
                    "BenchmarkAdapter.teardown failed for task %s",
                    task.task_id,
                    exc_info=True,
                )
