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
from agentevalops.core.perturbation import derive_seed
from agentevalops.core.protocols import (
    AgentRunner,
    BenchmarkAdapter,
    Evaluator,
    KnownGoodSource,
    Perturbing,
    PolicyChecker,
    ToolEnvironment,
    TraceStore,
)
from agentevalops.core.schemas import (
    BUDGET_STOP,
    AgentInput,
    EndStateRecord,
    EnvHandle,
    NotEvaluatedCause,
    PolicySpec,
    PreflightResult,
    RunConfig,
    RunLimits,
    RunSummary,
    TaskRunResult,
    TaskSpec,
    ToolCallRecord,
    TraceEvent,
)
from agentevalops.core.types import RunId
from agentevalops.orchestration.verdicts import (
    close_summary,
    judge,
    new_summary,
    run_policy_spec,
    scored,
    stopped,
    tally,
    task_runs,
    terminal_of,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _budget_detail(limits: RunLimits) -> str:
    """Why a task run did not start: the run's budget, and its caps."""
    return f"run budget reached before this task run started (cap {limits.label()})"


def _cause_of(exc: Exception) -> NotEvaluatedCause:
    return "configuration" if isinstance(exc, ConfigurationError) else "infrastructure"


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
    return stopped(
        task, detail, cause or _cause_of(exc), events, evaluators, repetition=repetition
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
        self._policy_spec = run_policy_spec(policy_spec)
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
        tasks = list(self._benchmark.list_tasks())
        self._order = {str(t.task_id): i for i, t in enumerate(tasks)}
        # docs/adr/0006 D18: the orchestrator owns repetitions; task_id
        # stays the scenario's id and each run carries its repetition.
        runs = task_runs(tasks, self._config.repetitions)
        summary = new_summary(self._config.run_id, runs)

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
        # R35: the cost and tokens the started task runs have reported.
        spent_usd, spent_tokens = 0.0, 0

        async def _guarded(
            task: TaskSpec, repetition: int
        ) -> tuple[TaskRunResult, list[TraceEvent]]:
            nonlocal spent_usd, spent_tokens
            if str(task.task_id) in defective:
                result = _not_evaluated_result(
                    task,
                    InfrastructureError(defective[str(task.task_id)]),
                    [],
                    self._evaluators,
                    repetition=repetition,
                    stage="preflight",
                    cause="environment_defect",
                )
                return await self._scored(result, [])
            async with semaphore:
                # R35: a task run starts only while the run's budget lasts;
                # those already running finish, and spend is summed as each
                # lands.
                limits = self._config.run_limits
                if limits is not None and limits.reached(spent_usd, spent_tokens):
                    return await self._scored(
                        stopped(
                            task,
                            _budget_detail(limits),
                            BUDGET_STOP,
                            [],
                            self._evaluators,
                            repetition=repetition,
                        ),
                        [],
                    )
                result, events = await self._run_task(task, repetition)
                spent_usd += result.output.total_cost_usd
                spent_tokens += result.output.total_tokens
                return await self._scored(result, events)

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
            tally(summary, result)
            all_events.extend(events)

        await self._trace_store.finalize(self._config.run_id)

        # --- post-run aggregation -----------------------------------------
        # Three-way partition (docs/adr/0002, Axis 3): passed + failed +
        # not_evaluated == total. failed_tasks now means a genuine,
        # evaluated merit-based failure -- it no longer silently absorbs
        # tasks that were never evaluated at all (not_evaluated_tasks does).
        # ROADMAP R27 (docs/adr/0006 D18, D20): pass^k per scenario over its
        # repetitions, and per category for the tasks that carry one.
        await close_summary(
            summary,
            tasks,
            all_events,
            self._policy_checker,
            self._policy_spec,
            self.tool_call_records,
            self.end_states,
            self._config.run_limits,
        )

        return summary

    async def _scored(
        self, result: TaskRunResult, events: list[TraceEvent]
    ) -> tuple[TaskRunResult, list[TraceEvent]]:
        """Append one ``evaluator.score`` event per evaluation result to the
        task run's stream, in ``evaluations`` order, and count them in its
        ``event_count`` (docs/adr/0007 D3, format 0.6). Every result counts:
        the real ones, the synthesised ``configuration`` ones and the
        not-evaluated ones, so the trace says what ``evaluations.json``
        says. ``step_index`` continues the task's own stream."""
        run_id: RunId = self._config.run_id
        for event in scored(result, run_id, events):
            await self._trace_store.append(run_id, event)
            events.append(event)
        return result, events

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
            # docs/adr/0006 D6: the agent may see a varied task text under a
            # seed derived from the run's seed, the task and the repetition;
            # the evaluators judge against the task as declared.
            seen = task
            seed = derive_seed(self._config.seed, str(task.task_id), repetition)
            if isinstance(self._benchmark, Perturbing):
                seen = self._benchmark.perturb(task, seed)
            # The runner is told its repetition and seed (R27): a runner that
            # randomises can be reproducible by construction, like the
            # environment (D6), without any new protocol.
            agent_input = AgentInput(
                task=seen,
                context={"run_id": run_id, "repetition": repetition, "seed": seed},
                environment=handle.tools,
            )
            limits = self._config.resource_limits

            try:
                trace_iter = await self._runner.run(agent_input, limits)
                async for event in trace_iter:
                    # docs/adr/0006 D17: the orchestrator, not the runner,
                    # says which task run an event belongs to.
                    event.task_id = task.task_id
                    event.repetition = repetition
                    await self._trace_store.append(run_id, event)
                    events.append(event)
            except (InfrastructureError, ConfigurationError) as exc:
                if terminal_of(events) is None:
                    await self._harvest(task, repetition, environment)
                    return (
                        _not_evaluated_result(
                            task, exc, events, self._evaluators, repetition=repetition
                        ),
                        events,
                    )
                # The agent finished: its agent.terminal event is in the
                # trace, so the run is finished and judged like any other
                # (docs/adr/0008, the stopped-run rule: a stopped run has no
                # terminal event). A late error from the runner's stream is
                # logged and changes no result.
                logger.warning(
                    "task run %s/%s: the runner raised after its agent.terminal "
                    "event; judged as finished: %s",
                    task.task_id,
                    repetition,
                    exc,
                )

            # docs/adr/0006 D7: the end state is taken after the agent stops
            # and before any evaluator runs or the environment is torn down.
            end_state, records = await self._harvest(task, repetition, environment)
            # Scoring is routed through the Evaluator (DeterministicEvaluator
            # by default), not BenchmarkAdapter.grade().  grade() is the
            # benchmark's own official grading and is reserved for future
            # use when an upstream benchmark's deterministic test suite
            # needs to run post-hoc.
            result = await judge(
                task, events, records, (end_state,), repetition, self._evaluators
            )
            return result, events
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
            agent_input = AgentInput(
                task=task,
                context={"run_id": self._config.run_id},
                environment=environment,
            )
            stream = await runner.run(agent_input, self._config.resource_limits)
            async for event in stream:
                event.task_id = task.task_id
                events.append(event)
            state = EndStateRecord(task.task_id, 0, dict(await environment.snapshot()))
            judged = await judge(
                task, events, environment.records(), (state,), 0, self._evaluators
            )
            outcome = judged.evaluation
            if outcome.evaluated and outcome.passed:
                return PreflightResult(task.task_id, True), False
            if not outcome.evaluated and outcome.not_evaluated_cause == "configuration":
                return (
                    PreflightResult(
                        task.task_id, False, f"known-good trajectory {outcome.notes}"
                    ),
                    False,
                )
            return (
                PreflightResult(
                    task.task_id,
                    False,
                    f"known-good trajectory did not pass: {outcome.notes}",
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
