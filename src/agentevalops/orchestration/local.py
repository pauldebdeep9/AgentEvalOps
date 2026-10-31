"""LocalOrchestrator — async task-loop wiring up the WBS 1 contracts."""

from __future__ import annotations

import asyncio
import contextvars
import dataclasses
import functools
import logging
from collections.abc import Sequence
from typing import Any

from agentevalops.core.errors import ConfigurationError, InfrastructureError
from agentevalops.core.protocols import (
    AgentRunner,
    BenchmarkAdapter,
    Evaluator,
    KnownGoodSource,
    PolicyChecker,
    Scorer,
    ToolEnvironment,
    TraceStore,
)
from agentevalops.core.schemas import (
    AgentInput,
    AgentOutput,
    EndStateRecord,
    EnvHandle,
    EvaluationResult,
    EvaluatorContext,
    NotEvaluatedCause,
    PolicySpec,
    PreflightResult,
    RunConfig,
    RunSummary,
    TaskRunResult,
    TaskSpec,
    TerminationReason,
    ToolCallRecord,
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


def _cause_of(exc: Exception) -> NotEvaluatedCause:
    return "configuration" if isinstance(exc, ConfigurationError) else "infrastructure"


def _applicable(
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


def _not_evaluated(
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


def _verdict(results: Sequence[EvaluationResult]) -> EvaluationResult:
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
        return _not_evaluated(
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


def _not_evaluated_result(
    task: TaskSpec,
    exc: Exception,
    events: list[TraceEvent],
    evaluators: Sequence[Evaluator],
    *,
    repetition: int = 0,
    stage: str | None = None,
    cause: NotEvaluatedCause | None = None,
) -> TaskRunResult:
    """Build the shared not-evaluated result for an infra/config failure.

    Used by the W12a runner-failure catch, the ``prepare_environment``
    guard (docs/benchmark_adapters.md D5) and the missing-environment guard
    (docs/adr/0006 D1). Every evaluator that would have applied records the
    task run as not evaluated, with the cause (D16). ``stage``, when given,
    is folded into the error/notes text so the paths stay distinguishable.
    """
    detail = f"{stage}: {exc}" if stage else str(exc)
    cause = cause or _cause_of(exc)
    output = AgentOutput(
        success=False,
        termination_reason=TerminationReason.INFRA_FAILURE,
        error=detail,
    )
    chosen, missing = _applicable(task, evaluators)
    evaluations = [
        _not_evaluated(e.evaluator_id, e.evaluator_kind, cause, detail)
        for e in chosen
    ] + [_not_evaluated(kind, kind, cause, detail) for kind in missing]
    return TaskRunResult(
        task_id=task.task_id,
        output=output,
        evaluation=_verdict(evaluations),
        event_count=len(events),
        evaluations=evaluations,
        repetition=repetition,
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
        evaluator: Evaluator | Sequence[Evaluator],
        policy_checker: PolicyChecker,
        policy_spec: PolicySpec | None = None,
    ) -> None:
        self._config = run_config
        self._benchmark = benchmark
        self._runner = runner
        self._trace_store = trace_store
        #: docs/adr/0006 D13: every evaluator the run configures.
        self._evaluators: list[Evaluator] = (
            list(evaluator) if isinstance(evaluator, Sequence) else [evaluator]
        )
        self._policy_checker = policy_checker
        self._policy_spec = policy_spec or PolicySpec(policy_id="default")
        # Harness data (docs/adr/0006 D4, D7, D19), kept as each task run
        # finishes so an interrupted run still writes what it observed.
        self._records: list[ToolCallRecord] = []
        self._end_states: list[EndStateRecord] = []
        self._order: dict[str, int] = {}

    def _ordered(self, key: tuple[str, int]) -> tuple[int, int]:
        return (self._order.get(key[0], len(self._order)), key[1])

    @property
    def tool_call_records(self) -> list[ToolCallRecord]:
        """Every harness call record so far, by task order, repetition and
        call index -- what ``tool_calls.jsonl`` holds."""
        return sorted(
            self._records,
            key=lambda r: (*self._ordered((str(r.task_id), r.repetition)), r.index),
        )

    @property
    def end_states(self) -> list[EndStateRecord]:
        """Each finished task run's end state -- what ``end_states.jsonl``
        holds."""
        return sorted(
            self._end_states,
            key=lambda s: self._ordered((str(s.task_id), s.repetition)),
        )

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
        self._order = {str(t.task_id): i for i, t in enumerate(tasks)}
        # docs/adr/0006 D18: the orchestrator owns repetitions; task_id
        # stays the scenario's id and each run carries its repetition.
        runs = [
            (task, repetition)
            for task in tasks
            for repetition in range(max(1, self._config.repetitions))
        ]
        summary.total_tasks = len(runs)

        # docs/adr/0006 D16: prove each scenario solvable before the agent
        # runs it. A scenario whose known-good script fails is defective:
        # every run of it is not evaluated, and the agent is never called.
        defective: dict[str, str] = {}
        if isinstance(self._benchmark, KnownGoodSource):
            for task in tasks:
                script = self._benchmark.known_good_script(task)
                if script is None:
                    continue
                check, defect = await self._preflight(task, script)
                summary.preflight.append(check)
                if defect:
                    defective[str(task.task_id)] = check.cause or "failed"

        limit = max(1, self._config.max_concurrent_tasks)
        semaphore = asyncio.Semaphore(limit)

        async def _guarded(
            task: TaskSpec, repetition: int
        ) -> tuple[TaskRunResult, list[TraceEvent]]:
            if str(task.task_id) in defective:
                return (
                    _not_evaluated_result(
                        task,
                        InfrastructureError(defective[str(task.task_id)]),
                        [],
                        self._evaluators,
                        repetition=repetition,
                        stage="preflight",
                        cause="environment_defect",
                    ),
                    [],
                )
            async with semaphore:
                return await self._run_task(task, repetition)

        all_events: list[TraceEvent] = []

        futures = [asyncio.ensure_future(_guarded(t, r)) for t, r in runs]
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
                cause = result.evaluation.not_evaluated_cause or "unknown"
                summary.not_evaluated_by_cause[cause] = (
                    summary.not_evaluated_by_cause.get(cause, 0) + 1
                )
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

        scorer: Scorer = SimpleScorer(self._config.run_id)
        summary.score_result = scorer.summarize(
            [r.evaluation for r in summary.task_results]
        )

        combined = _combined_output(summary)
        summary.policy_verdict = await self._policy_checker.check(
            self._policy_spec,
            combined,
            all_events,
            EvaluatorContext(
                records=tuple(self.tool_call_records),
                end_states=tuple(self.end_states),
            ),
        )

        return summary

    async def _run_task(
        self, task: TaskSpec, repetition: int = 0
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
        propagating out of ``evaluate()`` or a cancellation, including a
        repeated one (see ``_teardown``). A ``teardown`` error is only
        logged -- a cleanup failure must never replace or hide the task's
        real outcome.
        """
        run_id: RunId = self._config.run_id
        events: list[TraceEvent] = []

        try:
            handle = await self._prepare(task)
        except (InfrastructureError, ConfigurationError) as exc:
            return (
                _not_evaluated_result(
                    task,
                    exc,
                    events,
                    self._evaluators,
                    repetition=repetition,
                    stage="prepare_environment",
                ),
                events,
            )

        try:
            if handle.tools is None:
                # docs/adr/0006 D1: a benchmark that hands back no tool
                # environment is a wiring bug. Recorded as not evaluated --
                # never left to look like an agent failure, and never
                # silently papered over by a runner's own default.
                return (
                    _not_evaluated_result(
                        task,
                        ConfigurationError(
                            f"benchmark '{task.benchmark_id}' returned no tool "
                            "environment for this task (docs/adr/0006 D1)"
                        ),
                        events,
                        self._evaluators,
                        repetition=repetition,
                        stage="prepare_environment",
                    ),
                    events,
                )
            environment = handle.tools
            agent_input = AgentInput(
                task=task,
                context={"run_id": run_id},
                environment=handle.tools,
            )
            limits = self._config.resource_limits

            try:
                trace_iter = await self._runner.run(agent_input, limits)
                terminal: TraceEvent | None = None
                async for event in trace_iter:
                    # docs/adr/0006 D17: the orchestrator, not the runner,
                    # says which task run an event belongs to.
                    event.task_id = task.task_id
                    event.repetition = repetition
                    await self._trace_store.append(run_id, event)
                    events.append(event)
                    if event.kind == TraceEventKind.AGENT_TERMINAL:
                        terminal = event
            except (InfrastructureError, ConfigurationError) as exc:
                await self._harvest(task, repetition, environment)
                return (
                    _not_evaluated_result(
                        task, exc, events, self._evaluators, repetition=repetition
                    ),
                    events,
                )

            # docs/adr/0006 D7: the end state is taken after the agent stops
            # and before any evaluator runs or the environment is torn down.
            end_state, records = await self._harvest(task, repetition, environment)
            output = _output_from_terminal(terminal)
            # Scoring is routed through the Evaluator (DeterministicEvaluator
            # by default), not BenchmarkAdapter.grade().  grade() is the
            # benchmark's own official grading and is reserved for future
            # use when an upstream benchmark's deterministic test suite
            # needs to run post-hoc.
            chosen, missing = _applicable(task, self._evaluators)
            context = EvaluatorContext(
                records=records,
                end_states=(end_state,),
                task_id=task.task_id,
                repetition=repetition,
                applicable_evaluators=task.applicable_evaluators,
            )
            evaluations = [
                await evaluator.evaluate(task, output, events, context)
                for evaluator in chosen
            ] + [
                _not_evaluated(
                    kind,
                    kind,
                    "configuration",
                    f"the task declares '{kind}', which this run does not configure",
                )
                for kind in missing
            ]

            return (
                TaskRunResult(
                    task_id=task.task_id,
                    output=output,
                    evaluation=_verdict(evaluations),
                    event_count=len(events),
                    evaluations=evaluations,
                    repetition=repetition,
                ),
                events,
            )
        finally:
            await self._teardown(task, handle)

    async def _preflight(
        self, task: TaskSpec, script: Sequence[dict[str, Any]]
    ) -> tuple[PreflightResult, bool]:
        """Run *task*'s known-good script with the scripted mock against a
        fresh environment, judge it with the task's evaluators, and tear the
        environment down (docs/adr/0006 D16). Nothing here reaches the run's
        trace or harness streams: the preflight checks the scenario, not the
        agent.

        Returns the result and whether the scenario is *defective* -- its
        known-good was judged and failed, or could not even be run. A
        known-good that could not be judged because the run does not
        configure an evaluator the scenario declares is not a defect: the
        result says so, and each run of the task is then reported not
        evaluated with cause ``configuration`` by the normal path (D13).
        """
        # Deferred: orchestration otherwise needs no agent implementation.
        from agentevalops.agents.mock_runner import MockAgentRunner

        try:
            handle = await self._prepare(task)
        except (InfrastructureError, ConfigurationError) as exc:
            failed = PreflightResult(task.task_id, False, f"prepare_environment: {exc}")
            return failed, True
        try:
            environment = handle.tools
            if environment is None:
                return PreflightResult(task.task_id, False, "no tool environment"), True
            try:
                runner = MockAgentRunner(scripts={str(task.task_id): script})
            except ConfigurationError as exc:
                return PreflightResult(task.task_id, False, f"bad script: {exc}"), True
            events: list[TraceEvent] = []
            terminal: TraceEvent | None = None
            agent_input = AgentInput(
                task=task,
                context={"run_id": self._config.run_id},
                environment=environment,
            )
            stream = await runner.run(agent_input, self._config.resource_limits)
            async for event in stream:
                event.task_id = task.task_id
                events.append(event)
                if event.kind == TraceEventKind.AGENT_TERMINAL:
                    terminal = event
            state = EndStateRecord(task.task_id, 0, dict(await environment.snapshot()))
            context = EvaluatorContext(
                records=tuple(environment.records()),
                end_states=(state,),
                task_id=task.task_id,
                repetition=0,
                applicable_evaluators=task.applicable_evaluators,
            )
            chosen, missing = _applicable(task, self._evaluators)
            output = _output_from_terminal(terminal)
            evaluations = [
                await e.evaluate(task, output, events, context) for e in chosen
            ] + [
                _not_evaluated(
                    kind,
                    kind,
                    "configuration",
                    f"the task declares '{kind}', which this run does not configure",
                )
                for kind in missing
            ]
            verdict = _verdict(evaluations)
            if verdict.evaluated and verdict.passed:
                return PreflightResult(task.task_id, True), False
            if not verdict.evaluated and verdict.not_evaluated_cause == "configuration":
                return (
                    PreflightResult(
                        task.task_id, False, f"known-good trajectory {verdict.notes}"
                    ),
                    False,
                )
            return (
                PreflightResult(
                    task.task_id,
                    False,
                    f"known-good trajectory did not pass: {verdict.notes}",
                ),
                True,
            )
        finally:
            await self._teardown(task, handle)

    async def _harvest(
        self, task: TaskSpec, repetition: int, environment: ToolEnvironment
    ) -> tuple[EndStateRecord, tuple[ToolCallRecord, ...]]:
        """Take the environment's end state and its call records for this
        task run, stamped with the run's task and repetition (the
        environment may not know its repetition), and keep both for the
        bundle (docs/adr/0006 D4, D7, D19)."""
        state = await environment.snapshot()
        end_state = EndStateRecord(
            task_id=task.task_id, repetition=repetition, state=dict(state)
        )
        records = tuple(
            dataclasses.replace(r, task_id=task.task_id, repetition=repetition)
            for r in environment.records()
        )
        self._end_states.append(end_state)
        self._records.extend(records)
        return end_state, records

    async def _prepare(self, task: TaskSpec) -> EnvHandle:
        """Run ``BenchmarkAdapter.prepare_environment`` off the event loop,
        and never leak what it creates (docs/adr/0006 D22).

        Not ``asyncio.to_thread``: cancelling that await abandons a prepare
        already running in its worker thread, and the handle it then returns
        reaches nobody -- the environment is never torn down. Instead the job
        is submitted once and waited on through any cancellation, as in
        ``_teardown``. If it produced a handle, that handle is torn down, and
        then the cancellation is re-raised. If it failed, there is nothing to
        tear down (D5: a failed prepare cleans up after itself).
        """
        loop = asyncio.get_running_loop()
        ctx = contextvars.copy_context()
        job = loop.run_in_executor(
            None,
            functools.partial(ctx.run, self._benchmark.prepare_environment, task),
        )
        cancelled: asyncio.CancelledError | None = None
        while not job.done():
            try:
                await asyncio.wait((job,))
            except asyncio.CancelledError as exc:
                cancelled = exc
        if cancelled is None:
            return job.result()
        if job.exception() is None:
            await self._teardown(task, job.result())
        raise cancelled

    async def _teardown(self, task: TaskSpec, handle: EnvHandle) -> None:
        """Run ``BenchmarkAdapter.teardown`` exactly once, off the event loop.

        Not ``asyncio.to_thread``: cancelling an awaited ``to_thread`` also
        cancels its executor job if no worker thread has started it yet. A
        task cancelled twice -- once by ``asyncio.run``'s shutdown and again
        by ``run()``'s drain loop -- therefore lost its teardown whenever the
        second cancel won that race (always, on CI's Linux runners; ROADMAP
        §0, 2026-10-31). Instead the job is submitted once and never
        cancelled; each cancellation is absorbed while it is still running
        and the last one is re-raised once teardown has finished, so it can
        neither be skipped nor run twice. A teardown error is logged and
        never replaces the task's real outcome.
        """
        loop = asyncio.get_running_loop()
        ctx = contextvars.copy_context()
        job = loop.run_in_executor(
            None, functools.partial(ctx.run, self._benchmark.teardown, handle)
        )
        cancelled: asyncio.CancelledError | None = None
        while not job.done():
            try:
                await asyncio.wait((job,))
            except asyncio.CancelledError as exc:
                cancelled = exc
        error = job.exception()
        if error is not None:
            logger.warning(
                "BenchmarkAdapter.teardown failed for task %s",
                task.task_id,
                exc_info=error,
            )
        if cancelled is not None:
            raise cancelled
