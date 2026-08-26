"""Runs every task in a benchmark, repeated `config.repeats` times, and
grades each attempt.

The orchestrator is the only place ``EvaluationResult.evaluated`` is ever
set to ``False``. W5's evaluator is forbidden from doing so — an
evaluator's job is to grade a result it was given, and by the time it runs
one was; this module is the one party that knows whether an attempt ever
reached it. This is the other half of that rule; the first half lives in
``evaluate.py``.

Three outcomes per attempt, checked in this order:

1. **Limit-terminated.** ``ResourceLimits`` is enforced mid-stream, from
   events as they arrive — never a separately maintained counter, for the
   same reason ``mock.py``'s own step count is derived rather than
   tallied. A limit-terminated attempt IS evaluated: it has a real trace
   and a real outcome (the agent used its budget without finishing), which
   is a judged failure, not an unjudged one. Conformance checking (below)
   is skipped for it deliberately, checked first and unconditionally — a
   missing terminal event is expected when the stream was stopped early,
   not a sign of a malformed trace. Policy is graded normally: the
   evidence is real, just incomplete by design.

2. **Non-conforming.** An attempt is judged only if its trace conforms to
   the ``AgentRunner`` contract in ``protocols.py``: exactly one
   ``AGENT_TERMINAL`` event, and it is the last one. A runner that raises
   ``InfrastructureError`` counts as non-conforming too. Recorded with
   ``evaluated=False``, a note naming the specific violation, and whatever
   partial trace was captured before the failure — a partial trace is
   still evidence. The evaluator is never called on it. Fabricating an
   ``AgentOutput`` from, say, ``AGENT_FINAL_ANSWER`` when no terminal event
   exists would manufacture a judgement from a trace the harness does not
   actually understand — the same failure T08's shape in ``toy.py`` exists
   to catch. Policy is withheld too, for the same reason: grading a
   trace the harness has just declared it does not understand would
   produce a ``PASS`` meaning "no violations found in evidence known to be
   incomplete", which reads in a report as "no violations". Instead:
   ``Verdict.WARN``, no citations, a note saying why.

3. **Conforms.** ``AgentOutput.from_payload`` reconstructs the output from
   the terminal event; both the evaluator and the policy checker get it
   and the full event list.

``ConfigurationError`` and every exception other than
``InfrastructureError`` propagate out of ``run_benchmark`` uncaught: a
setup mistake aborts the whole run rather than producing N identical
unevaluated results, and a bug is not a modelled failure mode.

Sequential only: no ``asyncio.gather``, no semaphore, no concurrency of any
kind. DESIGN.md §5 cuts that deliberately.
"""

from __future__ import annotations

from collections.abc import Callable

from minieval.errors import InfrastructureError
from minieval.policy import check_policy
from minieval.protocols import AgentRunner, Evaluator
from minieval.schemas import (
    AgentOutput,
    Attempt,
    EvaluationResult,
    PolicyVerdict,
    RunConfig,
    RunId,
    RunSummary,
    TaskResult,
    TaskSpec,
    TerminationReason,
    TraceEvent,
    TraceEventKind,
    Verdict,
)

NOTE_NO_EVENTS_EMITTED: str = "no events emitted"
NOTE_NO_TERMINAL_EMITTED: str = "no AGENT_TERMINAL emitted"
NOTE_MULTIPLE_TERMINALS_EMITTED: str = "2 AGENT_TERMINAL events emitted"
NOTE_TERMINAL_NOT_LAST: str = "AGENT_TERMINAL is not the last event"
NOTE_POLICY_NOT_ASSESSED: str = (
    "policy could not be assessed because the trace was non-conforming"
)

_TURN_KINDS = (TraceEventKind.AGENT_PLAN, TraceEventKind.AGENT_FINAL_ANSWER)
"""Same definition of a step as mock.py's: membership in this set, counted
fresh from the events collected so far every time it's needed — never a
separately incremented counter that could disagree with the trace. Kept
local rather than imported from mock.py, which is a real runner's private
detail, not shared vocabulary.
"""


def _steps(events: list[TraceEvent]) -> int:
    return sum(1 for e in events if e.kind in _TURN_KINDS)


def _cost(events: list[TraceEvent]) -> float:
    return sum(e.cost_usd for e in events)


def _conformance_violation(events: list[TraceEvent]) -> str | None:
    """Name the runner-contract violation in ``events``, or return ``None``
    if it conforms: exactly one ``AGENT_TERMINAL``, as the last event."""
    if not events:
        return NOTE_NO_EVENTS_EMITTED
    terminal_count = sum(1 for e in events if e.kind is TraceEventKind.AGENT_TERMINAL)
    if terminal_count == 0:
        return NOTE_NO_TERMINAL_EMITTED
    if terminal_count > 1:
        return NOTE_MULTIPLE_TERMINALS_EMITTED
    if events[-1].kind is not TraceEventKind.AGENT_TERMINAL:
        return NOTE_TERMINAL_NOT_LAST
    return None


async def _collect(
    runner: AgentRunner, task: TaskSpec, config: RunConfig
) -> tuple[list[TraceEvent], InfrastructureError | None, TerminationReason | None]:
    """Stream one attempt, stopping early on a limit breach.

    Returns the events collected, the ``InfrastructureError`` if the
    runner raised one, and the limit reason if a limit stopped the stream
    — at most one of the latter two is ever set. The async generator is
    always closed before returning, on every exit path (limit break,
    caught exception, or an uncaught one propagating through), so a real
    runner in W10 releases its client. ``AgentRunner.run`` is typed as
    ``AsyncGenerator`` in protocols.py precisely so ``aclose()`` here is a
    checked call, not an unchecked cast standing in for a claim about
    implementations mypy cannot verify.
    """
    stream = runner.run(task, config.limits)
    events: list[TraceEvent] = []
    infra_error: InfrastructureError | None = None
    limit_reason: TerminationReason | None = None
    try:
        async for event in stream:
            # Peek before committing: a task needing exactly max_steps
            # turns (or exactly max_cost_usd of cost) must complete, not
            # terminate for reaching its budget. Checking what the count
            # WOULD become, before appending, and refusing to append the
            # event that would cross the ceiling, is what keeps
            # AgentOutput.steps/cost_usd an honest derivation of the
            # trace afterward — reported and actual never disagree,
            # because the event that would have caused a disagreement was
            # never recorded in the first place.
            prospective_steps = _steps(events) + (1 if event.kind in _TURN_KINDS else 0)
            if prospective_steps > config.limits.max_steps:
                limit_reason = TerminationReason.LIMIT_STEPS
                break
            prospective_cost = _cost(events) + event.cost_usd
            if prospective_cost > config.limits.max_cost_usd:
                limit_reason = TerminationReason.LIMIT_COST
                break
            events.append(event)
    except InfrastructureError as exc:
        infra_error = exc
    finally:
        await stream.aclose()
    return events, infra_error, limit_reason


async def _run_attempt(
    task: TaskSpec,
    config: RunConfig,
    runner: AgentRunner,
    attempt_index: int,
    evaluator: Evaluator,
) -> Attempt:
    events, infra_error, limit_reason = await _collect(runner, task, config)

    output: AgentOutput
    evaluation: EvaluationResult
    policy: PolicyVerdict

    if limit_reason is not None:
        # Checked first, unconditionally: conformance checking does not
        # apply here. A missing terminal event is expected when the
        # stream was stopped early, not a sign of a malformed trace.
        output = AgentOutput(
            success=False,
            termination_reason=limit_reason,
            final_answer=None,
            steps=_steps(events),
            cost_usd=_cost(events),
        )
        evaluation = evaluator.evaluate(task, output, events)
        policy = check_policy(config.policy, output, events)
    else:
        note: str | None
        if infra_error is not None:
            note = str(infra_error)
        else:
            note = _conformance_violation(events)

        if note is not None:
            output = AgentOutput(
                success=False,
                termination_reason=TerminationReason.INFRA_FAILURE,
                final_answer=None,
                steps=_steps(events),
                cost_usd=_cost(events),
                error=note,
            )
            evaluation = EvaluationResult(
                evaluator_id=evaluator.evaluator_id,
                passed=False,
                evaluated=False,
                citations=[],
                notes=note,
            )
            policy = PolicyVerdict(
                policy_id=config.policy.policy_id,
                verdict=Verdict.WARN,
                citations=[],
                notes=NOTE_POLICY_NOT_ASSESSED,
            )
        else:
            output = AgentOutput.from_payload(events[-1].payload)
            evaluation = evaluator.evaluate(task, output, events)
            policy = check_policy(config.policy, output, events)

    return Attempt(
        index=attempt_index,
        output=output,
        evaluation=evaluation,
        policy=policy,
        events=events,
    )


async def run_benchmark(
    tasks: list[TaskSpec],
    config: RunConfig,
    make_runner: Callable[[RunId, int], AgentRunner],
    evaluator: Evaluator,
) -> RunSummary:
    task_results: list[TaskResult] = []

    for task in tasks:
        attempts: list[Attempt] = []
        for attempt_index in range(config.repeats):
            runner = make_runner(config.run_id, attempt_index)
            attempt = await _run_attempt(task, config, runner, attempt_index, evaluator)
            attempts.append(attempt)
        task_results.append(TaskResult(task=task, attempts=attempts))

    return RunSummary(run_id=config.run_id, config=config, task_results=task_results)
