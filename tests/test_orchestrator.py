"""Expectations for minieval.orchestrator.run_benchmark, written before the
implementation exists.

WBS.md marks W6 high risk for one reason: it is the only item where the
implementation and its tests can be wrong in the same direction and still
come back green, because the same process writes both. Writing these
expectations first -- against toy.py's documented Stability table rather
than against a loop's observed behaviour -- is the control for that.

This file imports minieval.orchestrator, which does not exist yet.
Collecting it is expected to fail with ImportError. That failure is the
deliverable of this prompt, not a bug to fix.

The adversarial-runner tests near the bottom encode one conformance rule:
an attempt is judged only if its trace has exactly one AGENT_TERMINAL, as
the last event. Any other shape is recorded evaluated=False, with a note
naming the specific violation, never a judgement fabricated from a trace
the harness does not understand -- and policy is withheld too
(Verdict.WARN, no citations), not graded PASS against evidence the
harness has just declared it does not understand. A limit-terminated
attempt (see the Limits section) is judged normally and is explicitly
exempt from conformance checking -- a missing terminal there is expected,
not a violation.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest

from minieval.benchmarks.toy import toy_tasks
from minieval.errors import ConfigurationError
from minieval.evaluate import DeterministicEvaluator
from minieval.orchestrator import (
    NOTE_MULTIPLE_TERMINALS_EMITTED,
    NOTE_NO_EVENTS_EMITTED,
    NOTE_NO_TERMINAL_EMITTED,
    NOTE_POLICY_NOT_ASSESSED,
    NOTE_TERMINAL_NOT_LAST,
    run_benchmark,
)
from minieval.protocols import AgentRunner
from minieval.runners.mock import MockRunner
from minieval.schemas import (
    ResourceLimits,
    RunConfig,
    RunId,
    RunSummary,
    Stability,
    TaskId,
    TaskResult,
    TaskSpec,
    TerminationReason,
    TraceEvent,
    TraceEventKind,
    Verdict,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_runner_factory(run_id: RunId, attempt: int) -> AgentRunner:
    return MockRunner(run_id=run_id, attempt=attempt)


def _task_result(summary: RunSummary, task_id: str) -> TaskResult:
    for result in summary.task_results:
        if str(result.task.task_id) == task_id:
            return result
    raise AssertionError(f"no task result for {task_id}")


@pytest.fixture(scope="module")
def toy_summary() -> RunSummary:
    config = RunConfig(run_id=RunId("w6-toy"), runner="mock", repeats=3)
    return asyncio.run(
        run_benchmark(
            toy_tasks(), config, _mock_runner_factory, DeterministicEvaluator()
        )
    )


_EXPECTED_STABILITY: dict[str, Stability] = {
    "T01": Stability.STABLE_PASS,
    "T02": Stability.STABLE_PASS,
    "T03": Stability.STABLE_PASS,
    "T04": Stability.STABLE_FAIL,
    "T05": Stability.STABLE_FAIL,
    "T06": Stability.STABLE_PASS,
    "T07": Stability.STABLE_FAIL,
    "T08": Stability.NOT_EVALUATED,
    "T09": Stability.FLAKY,
}
"""Copied literally from toy.py's module docstring table -- not re-derived
from the tasks' criteria, so this test checks the docstring's claim
instead of agreeing with the data by construction. Must be kept in sync
with toy.py by hand if that table ever changes.
"""


# ---------------------------------------------------------------------------
# The partition identity, as an identity
# ---------------------------------------------------------------------------


def test_partition_identity_holds_over_full_toy_set(toy_summary: RunSummary) -> None:
    s = toy_summary
    assert (
        s.passed_attempts + s.failed_attempts + s.not_evaluated_attempts
        == s.total_attempts
    )


def test_partition_exact_numbers_over_full_toy_set(toy_summary: RunSummary) -> None:
    """The identity above catches an implementation that is internally
    consistent and wrong. These exact numbers catch one that is neither.
    9 tasks x 3 repeats = 27. T01/T02/T03/T06 pass all 3 (12). T09 passes
    2 of 3. T04/T05/T07 fail all 3 (9), T09 fails 1 more (10 total
    failed). T08 is never evaluated (3)."""
    s = toy_summary
    assert s.total_attempts == 27
    assert s.passed_attempts == 14
    assert s.failed_attempts == 10
    assert s.not_evaluated_attempts == 3


# ---------------------------------------------------------------------------
# The Stability table, per task -- currently claims; this is what checks them
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("task_id,expected", sorted(_EXPECTED_STABILITY.items()))
def test_task_stability_matches_toy_docstring_table(
    toy_summary: RunSummary, task_id: str, expected: Stability
) -> None:
    result = _task_result(toy_summary, task_id)
    assert result.stability is expected


# ---------------------------------------------------------------------------
# Per-task k/n
# ---------------------------------------------------------------------------


def test_t01_passes_all_three(toy_summary: RunSummary) -> None:
    result = _task_result(toy_summary, "T01")
    assert (result.k, result.n) == (3, 3)


def test_t04_fails_all_three(toy_summary: RunSummary) -> None:
    result = _task_result(toy_summary, "T04")
    assert (result.k, result.n) == (0, 3)


def test_t08_zero_evaluated_of_three(toy_summary: RunSummary) -> None:
    result = _task_result(toy_summary, "T08")
    assert result.evaluated == 0
    assert result.n == 3


def test_t09_passes_two_of_three(toy_summary: RunSummary) -> None:
    """The task that would be invisible at repeats=1: mock_pass_on=[0, 2]
    only shows up as a failure under repeats > 1."""
    result = _task_result(toy_summary, "T09")
    assert (result.k, result.n) == (2, 3)


# ---------------------------------------------------------------------------
# The three-way distinction -- the whole reason the partition is three-way
# ---------------------------------------------------------------------------


def test_t08_not_evaluated_and_excluded_from_failed_attempts(
    toy_summary: RunSummary,
) -> None:
    """The natural wrong implementation folds an unevaluated attempt into
    failed_attempts. TaskResult's own failed contribution is
    evaluated - k (see RunSummary.failed_attempts); asserting that is zero
    for T08, on top of its stability, is the second half of this claim."""
    result = _task_result(toy_summary, "T08")
    assert result.stability is Stability.NOT_EVALUATED
    assert result.evaluated - result.k == 0


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------


def test_t07_terminates_with_limit_steps_under_a_low_max_steps() -> None:
    """Set explicitly rather than relying on ResourceLimits' default: T07's
    STABLE_FAIL classification in toy.py's docstring only holds below 17
    turns, and the default (12) happens to qualify -- this test does not
    depend on that coincidence."""
    tasks = [t for t in toy_tasks() if str(t.task_id) == "T07"]
    config = RunConfig(
        run_id=RunId("w6-limit-steps"),
        runner="mock",
        repeats=1,
        limits=ResourceLimits(max_steps=5, max_cost_usd=100.0),
    )

    summary = asyncio.run(
        run_benchmark(tasks, config, _mock_runner_factory, DeterministicEvaluator())
    )

    attempt = summary.task_results[0].attempts[0]
    assert attempt.output.termination_reason is TerminationReason.LIMIT_STEPS
    # A judged failure, not an unjudged one: the agent used its budget
    # without finishing, and that is real evidence, not a broken trace.
    assert attempt.evaluation.evaluated is True


def test_max_steps_counts_turns_not_events() -> None:
    """ResourceLimits.max_steps counts model turns, not events and not
    tool calls: a model batching four parallel tool calls into one turn
    spends one step, not four, so a step budget does not bound tool-call
    volume. Nothing in this suite pinned that until now. T07's own limit
    tests don't either: T07 lands STABLE_FAIL and LIMIT_STEPS under both
    turn counting and a bug that counts every event, differing only in
    where inside its 17-turn, ~50-event trace the limit actually fires --
    a difference no existing assertion looks at. An event-counting
    implementation of the mid-stream check passes all 29 other tests in
    this file.

    This test pins the exact stopping point instead of just the reason.
    With 10 tool calls (3 events per round -- AGENT_PLAN, AGENT_TOOL_CALL,
    AGENT_TOOL_RESULT -- so events outnumber turns roughly 3:1) and
    max_steps=5, turn counting and event counting both reach the ceiling,
    but at different points: turn counting stops once 5 AGENT_PLAN events
    have arrived (13 events, mid-round-5), while event counting stops
    once 5 events of any kind have arrived (2 turns, mid-round-2). The
    last two assertions are what discriminate -- under event counting,
    `steps` and the AGENT_PLAN/AGENT_FINAL_ANSWER count in the trace both
    come out at 2, not 5, and the 3:1 event-to-turn ratio collapses to
    2.5:1 within a much shorter trace. The first two assertions hold
    under both interpretations and are here as a sanity check, not the
    discriminator.
    """
    max_steps = 5
    tools = [f"tool{i}" for i in range(10)]
    task = TaskSpec(
        task_id=TaskId("MANYTOOLS"),
        description="many tool calls",
        criteria={
            "mock_answer": "done",
            "mock_tools": tools,
            "expected_substring": "done",
        },
    )
    config = RunConfig(
        run_id=RunId("w6-turns-not-events"),
        runner="mock",
        repeats=1,
        limits=ResourceLimits(max_steps=max_steps, max_cost_usd=100.0),
    )

    summary = asyncio.run(
        run_benchmark([task], config, _mock_runner_factory, DeterministicEvaluator())
    )

    attempt = summary.task_results[0].attempts[0]
    turn_kinds = (TraceEventKind.AGENT_PLAN, TraceEventKind.AGENT_FINAL_ANSWER)
    turn_events_in_trace = sum(1 for e in attempt.events if e.kind in turn_kinds)

    assert attempt.output.termination_reason is TerminationReason.LIMIT_STEPS
    assert attempt.output.steps == max_steps
    assert turn_events_in_trace == max_steps
    assert len(attempt.events) > turn_events_in_trace


def test_low_cost_ceiling_produces_limit_cost() -> None:
    tasks = [t for t in toy_tasks() if str(t.task_id) == "T02"]
    config = RunConfig(
        run_id=RunId("w6-limit-cost"),
        runner="mock",
        repeats=1,
        limits=ResourceLimits(max_steps=100, max_cost_usd=0.001),
    )

    summary = asyncio.run(
        run_benchmark(tasks, config, _mock_runner_factory, DeterministicEvaluator())
    )

    attempt = summary.task_results[0].attempts[0]
    assert attempt.output.termination_reason is TerminationReason.LIMIT_COST
    assert attempt.evaluation.evaluated is True


# ---------------------------------------------------------------------------
# Limits, exactly at the boundary -- CLAUDE.md names this case explicitly:
# "limit hit exactly rather than exceeded." A limit is a ceiling a task may
# fully use, not a trigger that fires the instant it is touched.
# ---------------------------------------------------------------------------


def test_task_needing_exactly_max_steps_completes_and_is_judged() -> None:
    """4 tool calls plus a final answer is exactly 5 turns. A task that
    needs precisely max_steps turns must complete, not be killed for
    reaching its budget -- the earlier `>=` fix stopped the moment the
    budget-th turn arrived, which silently kills every task that fits
    its budget exactly, the single most common case a limit exists to
    allow."""
    tools = [f"tool{i}" for i in range(4)]
    task = TaskSpec(
        task_id=TaskId("EXACTSTEPS"),
        description="needs exactly max_steps turns",
        criteria={
            "mock_answer": "done",
            "mock_tools": tools,
            "expected_substring": "done",
        },
    )
    config = RunConfig(
        run_id=RunId("w6-exact-steps"),
        runner="mock",
        repeats=1,
        limits=ResourceLimits(max_steps=5, max_cost_usd=100.0),
    )

    summary = asyncio.run(
        run_benchmark([task], config, _mock_runner_factory, DeterministicEvaluator())
    )

    attempt = summary.task_results[0].attempts[0]
    assert attempt.output.termination_reason is TerminationReason.COMPLETED
    assert attempt.evaluation.evaluated is True


def test_task_needing_max_steps_plus_one_terminates_limit_steps_at_budget() -> None:
    """The other half of the boundary: one turn over the budget is a real
    violation. attempt.output.steps must equal max_steps exactly -- the
    budget that was actually spent -- not max_steps + 1, which was the
    original `>` bug's artifact of detecting the crossing only after
    appending the event that caused it."""
    tools = [f"tool{i}" for i in range(5)]
    task = TaskSpec(
        task_id=TaskId("OVERSTEPS"),
        description="needs one more turn than max_steps",
        criteria={
            "mock_answer": "done",
            "mock_tools": tools,
            "expected_substring": "done",
        },
    )
    config = RunConfig(
        run_id=RunId("w6-over-steps"),
        runner="mock",
        repeats=1,
        limits=ResourceLimits(max_steps=5, max_cost_usd=100.0),
    )

    summary = asyncio.run(
        run_benchmark([task], config, _mock_runner_factory, DeterministicEvaluator())
    )

    attempt = summary.task_results[0].attempts[0]
    assert attempt.output.termination_reason is TerminationReason.LIMIT_STEPS
    assert attempt.output.steps == 5


def test_task_needing_exactly_max_cost_usd_completes_and_is_judged() -> None:
    """The same boundary, the other limit. One tool call costs exactly
    0.05 (5 events at MOCK_EVENT_COST_USD each) to complete; a task whose
    full trace costs exactly max_cost_usd must complete, not be killed
    for reaching it."""
    task = TaskSpec(
        task_id=TaskId("EXACTCOST"),
        description="needs exactly max_cost_usd",
        criteria={
            "mock_answer": "done",
            "mock_tools": ["tool0"],
            "expected_substring": "done",
        },
    )
    config = RunConfig(
        run_id=RunId("w6-exact-cost"),
        runner="mock",
        repeats=1,
        limits=ResourceLimits(max_steps=100, max_cost_usd=0.05),
    )

    summary = asyncio.run(
        run_benchmark([task], config, _mock_runner_factory, DeterministicEvaluator())
    )

    attempt = summary.task_results[0].attempts[0]
    assert attempt.output.termination_reason is TerminationReason.COMPLETED
    assert attempt.evaluation.evaluated is True


def test_task_over_max_cost_usd_terminates_limit_cost_at_budget() -> None:
    """The other half: exceeding the cost ceiling by even one event is a
    real violation, and attempt.output.cost_usd must equal max_cost_usd
    exactly -- the budget actually spent, not the budget plus the one
    event that was refused."""
    task = TaskSpec(
        task_id=TaskId("OVERCOST"),
        description="needs more than max_cost_usd",
        criteria={
            "mock_answer": "done",
            "mock_tools": ["tool0"],
            "expected_substring": "done",
        },
    )
    config = RunConfig(
        run_id=RunId("w6-over-cost"),
        runner="mock",
        repeats=1,
        limits=ResourceLimits(max_steps=100, max_cost_usd=0.04),
    )

    summary = asyncio.run(
        run_benchmark([task], config, _mock_runner_factory, DeterministicEvaluator())
    )

    attempt = summary.task_results[0].attempts[0]
    assert attempt.output.termination_reason is TerminationReason.LIMIT_COST
    assert attempt.output.cost_usd == pytest.approx(0.04)


# ---------------------------------------------------------------------------
# Config errors abort, infrastructure errors do not
# ---------------------------------------------------------------------------


def test_infrastructure_error_yields_unevaluated_attempt_and_run_continues() -> None:
    failing_task = TaskSpec(
        task_id=TaskId("FAIL"),
        description="raises infra",
        criteria={"mock_answer": "x", "mock_fail": "infra"},
    )
    ok_task = TaskSpec(
        task_id=TaskId("OK"),
        description="passes cleanly",
        criteria={"mock_answer": "42", "expected_substring": "42"},
    )
    config = RunConfig(run_id=RunId("w6-infra"), runner="mock", repeats=1)

    summary = asyncio.run(
        run_benchmark(
            [failing_task, ok_task],
            config,
            _mock_runner_factory,
            DeterministicEvaluator(),
        )
    )

    failing_result = _task_result(summary, "FAIL")
    ok_result = _task_result(summary, "OK")
    assert failing_result.n == 1
    assert failing_result.evaluated == 0
    assert ok_result.k == 1

    failing_attempt = failing_result.attempts[0]
    assert failing_attempt.policy.verdict is Verdict.WARN
    assert failing_attempt.policy.citations == []
    assert failing_attempt.policy.notes == NOTE_POLICY_NOT_ASSESSED


def test_runner_configuration_error_propagates() -> None:
    """Per W3's rule: a setup mistake aborts the whole run rather than
    producing N identical evaluated=False results. Asserted around the
    entire call -- it must not be caught anywhere inside run_benchmark."""
    bad_task = TaskSpec(
        task_id=TaskId("BAD"),
        description="unrecognised mock_fail value",
        criteria={"mock_answer": "x", "mock_fail": "not_a_real_failure_mode"},
    )
    config = RunConfig(run_id=RunId("w6-config-error"), runner="mock", repeats=1)

    with pytest.raises(ConfigurationError):
        asyncio.run(
            run_benchmark(
                [bad_task], config, _mock_runner_factory, DeterministicEvaluator()
            )
        )


def test_evaluator_configuration_error_propagates() -> None:
    """The same abort applies when the ConfigurationError comes from the
    evaluator instead of the runner. W5's vacuous-criteria guard raises it
    for a mis-specified task, so this path is reachable and must not be
    swallowed either."""
    mis_specified_task = TaskSpec(
        task_id=TaskId("MIS"),
        description="no evaluator-side criteria",
        criteria={"mock_answer": "x"},
    )
    config = RunConfig(run_id=RunId("w6-config-error-eval"), runner="mock", repeats=1)

    with pytest.raises(ConfigurationError):
        asyncio.run(
            run_benchmark(
                [mis_specified_task],
                config,
                _mock_runner_factory,
                DeterministicEvaluator(),
            )
        )


class _RaisesRuntimeErrorRunner:
    agent_id: str = "raises-runtime-error"

    async def run(
        self, task: TaskSpec, limits: ResourceLimits
    ) -> AsyncGenerator[TraceEvent, None]:
        raise RuntimeError("not a modelled failure mode")
        yield  # pragma: no cover -- unreachable; makes this an async generator


def test_unmodelled_exception_propagates() -> None:
    """A bug is not a modelled failure mode. Only InfrastructureError is
    caught per attempt; anything else, including a plain RuntimeError,
    must propagate out of run_benchmark like the ConfigurationError case
    above."""
    task = TaskSpec(task_id=TaskId("BUG"), description="triggers a bug", criteria={})
    config = RunConfig(run_id=RunId("w6-bug"), runner="mock", repeats=1)

    def make_runner(run_id: RunId, attempt: int) -> AgentRunner:
        return _RaisesRuntimeErrorRunner()

    with pytest.raises(RuntimeError):
        asyncio.run(
            run_benchmark([task], config, make_runner, DeterministicEvaluator())
        )


# ---------------------------------------------------------------------------
# Adversarial traces -- small runner doubles, never MockRunner
# ---------------------------------------------------------------------------


class _NoTerminalRunner:
    """Emits AGENT_FINAL_ANSWER but never AGENT_TERMINAL, then returns
    normally. This is T08's exact shape: with zero mock_tools, the only
    event MockRunner emits before InfrastructureError interrupts it is
    AGENT_FINAL_ANSWER -- but here nothing raises at all, so the stream
    just ends. Nothing signals failure; only its absence of a terminal
    event does.
    """

    agent_id: str = "no-terminal"

    def __init__(self, run_id: RunId, attempt: int) -> None:
        self._run_id = run_id
        self._attempt = attempt

    async def run(
        self, task: TaskSpec, limits: ResourceLimits
    ) -> AsyncGenerator[TraceEvent, None]:
        yield TraceEvent(
            run_id=self._run_id,
            task_id=task.task_id,
            attempt=self._attempt,
            step_index=0,
            kind=TraceEventKind.AGENT_FINAL_ANSWER,
            payload={"final_answer": "should not be trusted"},
        )


def test_no_terminal_event_is_not_judged() -> None:
    """Conformance rule: an attempt is judged only if its trace has exactly
    one AGENT_TERMINAL, as the last event. This trace has none, so it is
    not judged -- evaluated=False, with a note naming the violation.
    protocols.py's contract says AGENT_TERMINAL's payload is what
    reconstructs AgentOutput; no other event, including
    AGENT_FINAL_ANSWER, is a substitute. Inventing an AgentOutput from the
    last event anyway would fabricate a judgement from a trace the harness
    does not understand, in a project whose whole claim is that "never
    judged" is an honest category. The note is what lets W8's report tell
    this apart from a genuine InfrastructureError -- both land in
    NOT_EVALUATED."""
    task = TaskSpec(task_id=TaskId("NOTERM"), description="no terminal", criteria={})
    config = RunConfig(run_id=RunId("w6-no-terminal"), runner="mock", repeats=1)

    def make_runner(run_id: RunId, attempt: int) -> AgentRunner:
        return _NoTerminalRunner(run_id, attempt)

    summary = asyncio.run(
        run_benchmark([task], config, make_runner, DeterministicEvaluator())
    )

    attempt = summary.task_results[0].attempts[0]
    assert attempt.evaluation.evaluated is False
    assert attempt.evaluation.notes == NOTE_NO_TERMINAL_EMITTED
    assert summary.task_results[0].stability is Stability.NOT_EVALUATED
    # Not graded PASS against evidence known to be incomplete.
    assert attempt.policy.verdict is Verdict.WARN
    assert attempt.policy.citations == []
    assert attempt.policy.notes == NOTE_POLICY_NOT_ASSESSED


class _EmptyRunner:
    agent_id: str = "empty"

    async def run(
        self, task: TaskSpec, limits: ResourceLimits
    ) -> AsyncGenerator[TraceEvent, None]:
        return
        yield  # pragma: no cover -- unreachable; makes this an async generator


def test_runner_emitting_nothing_is_not_judged() -> None:
    """Same conformance rule as the no-terminal case above, the emptiest
    possible violation: zero AGENT_TERMINAL because zero events."""
    task = TaskSpec(task_id=TaskId("EMPTY"), description="emits nothing", criteria={})
    config = RunConfig(run_id=RunId("w6-empty"), runner="mock", repeats=1)

    def make_runner(run_id: RunId, attempt: int) -> AgentRunner:
        return _EmptyRunner()

    summary = asyncio.run(
        run_benchmark([task], config, make_runner, DeterministicEvaluator())
    )

    attempt = summary.task_results[0].attempts[0]
    assert attempt.evaluation.evaluated is False
    assert attempt.evaluation.notes == NOTE_NO_EVENTS_EMITTED
    assert summary.task_results[0].stability is Stability.NOT_EVALUATED
    assert attempt.policy.verdict is Verdict.WARN
    assert attempt.policy.citations == []
    assert attempt.policy.notes == NOTE_POLICY_NOT_ASSESSED


class _DoubleTerminalRunner:
    agent_id: str = "double-terminal"

    def __init__(self, run_id: RunId, attempt: int) -> None:
        self._run_id = run_id
        self._attempt = attempt

    async def run(
        self, task: TaskSpec, limits: ResourceLimits
    ) -> AsyncGenerator[TraceEvent, None]:
        yield TraceEvent(
            run_id=self._run_id,
            task_id=task.task_id,
            attempt=self._attempt,
            step_index=0,
            kind=TraceEventKind.AGENT_TERMINAL,
            payload={
                "success": True,
                "termination_reason": TerminationReason.COMPLETED.value,
                "final_answer": "first",
                "steps": 1,
                "cost_usd": 0.01,
            },
        )
        yield TraceEvent(
            run_id=self._run_id,
            task_id=task.task_id,
            attempt=self._attempt,
            step_index=1,
            kind=TraceEventKind.AGENT_TERMINAL,
            payload={
                "success": False,
                "termination_reason": TerminationReason.AGENT_ERROR.value,
                "final_answer": None,
                "steps": 1,
                "cost_usd": 0.01,
                "error": "second terminal, should be ignored",
            },
        )


def test_two_terminal_events_is_not_judged_and_does_not_take_the_first() -> None:
    """Conformance rule, third case: exactly one AGENT_TERMINAL is required,
    so two is a violation even though the first one alone would have been
    well-formed. Explicitly NOT first-wins: picking one of two disagreeing
    terminal events would fabricate a judgement from a trace the harness
    does not understand, the same reasoning as the missing-terminal cases
    above -- a runner bug is not a modelled failure mode, and the protocol
    says AGENT_TERMINAL "is always the last event," which a second one
    contradicts. Both events still land in the trace; only the judgement
    is withheld."""
    task = TaskSpec(
        task_id=TaskId("DBLTERM"), description="double terminal", criteria={}
    )
    config = RunConfig(run_id=RunId("w6-double-terminal"), runner="mock", repeats=1)

    def make_runner(run_id: RunId, attempt: int) -> AgentRunner:
        return _DoubleTerminalRunner(run_id, attempt)

    summary = asyncio.run(
        run_benchmark([task], config, make_runner, DeterministicEvaluator())
    )

    attempt = summary.task_results[0].attempts[0]
    assert len(attempt.events) == 2
    assert attempt.evaluation.evaluated is False
    assert attempt.evaluation.notes == NOTE_MULTIPLE_TERMINALS_EMITTED
    assert summary.task_results[0].stability is Stability.NOT_EVALUATED
    assert attempt.policy.verdict is Verdict.WARN
    assert attempt.policy.citations == []
    assert attempt.policy.notes == NOTE_POLICY_NOT_ASSESSED


class _TerminalNotLastRunner:
    """Emits a well-formed AGENT_TERMINAL, then one more event after it.
    Exactly one terminal is present -- unlike the double-terminal case
    above -- but it is not the last event, which the runner contract in
    protocols.py also requires."""

    agent_id: str = "terminal-not-last"

    def __init__(self, run_id: RunId, attempt: int) -> None:
        self._run_id = run_id
        self._attempt = attempt

    async def run(
        self, task: TaskSpec, limits: ResourceLimits
    ) -> AsyncGenerator[TraceEvent, None]:
        yield TraceEvent(
            run_id=self._run_id,
            task_id=task.task_id,
            attempt=self._attempt,
            step_index=0,
            kind=TraceEventKind.AGENT_TERMINAL,
            payload={
                "success": True,
                "termination_reason": TerminationReason.COMPLETED.value,
                "final_answer": "done",
                "steps": 1,
                "cost_usd": 0.01,
            },
        )
        yield TraceEvent(
            run_id=self._run_id,
            task_id=task.task_id,
            attempt=self._attempt,
            step_index=1,
            kind=TraceEventKind.AGENT_PLAN,
            payload={},
        )


def test_terminal_followed_by_another_event_is_not_judged() -> None:
    """Conformance rule, fourth case: exactly one AGENT_TERMINAL is
    necessary but not sufficient -- it must also be last. Added as a
    constant (NOTE_TERMINAL_NOT_LAST) in W6 part 2 for this exact shape,
    but left untested until now; untested vocabulary is the thing
    DESIGN.md §7 exists to prevent."""
    task = TaskSpec(
        task_id=TaskId("TERMNOTLAST"), description="terminal not last", criteria={}
    )
    config = RunConfig(run_id=RunId("w6-terminal-not-last"), runner="mock", repeats=1)

    def make_runner(run_id: RunId, attempt: int) -> AgentRunner:
        return _TerminalNotLastRunner(run_id, attempt)

    summary = asyncio.run(
        run_benchmark([task], config, make_runner, DeterministicEvaluator())
    )

    attempt = summary.task_results[0].attempts[0]
    assert len(attempt.events) == 2
    assert attempt.evaluation.evaluated is False
    assert attempt.evaluation.notes == NOTE_TERMINAL_NOT_LAST
    assert summary.task_results[0].stability is Stability.NOT_EVALUATED
    assert attempt.policy.verdict is Verdict.WARN
    assert attempt.policy.citations == []
    assert attempt.policy.notes == NOTE_POLICY_NOT_ASSESSED


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def test_attempts_carry_their_own_trace_and_indices_are_0_to_repeats_minus_1(
    toy_summary: RunSummary,
) -> None:
    result = _task_result(toy_summary, "T02")
    assert [a.index for a in result.attempts] == [0, 1, 2]
    for attempt in result.attempts:
        assert attempt.events
        assert all(e.attempt == attempt.index for e in attempt.events)


def test_run_summary_config_is_the_config_passed_in() -> None:
    config = RunConfig(run_id=RunId("w6-config-identity"), runner="mock", repeats=1)
    tasks = [toy_tasks()[0]]

    summary = asyncio.run(
        run_benchmark(tasks, config, _mock_runner_factory, DeterministicEvaluator())
    )

    assert summary.config is config


def test_tasks_are_attempted_in_the_order_given() -> None:
    tasks = list(reversed(toy_tasks()[:3]))
    config = RunConfig(run_id=RunId("w6-order"), runner="mock", repeats=1)

    summary = asyncio.run(
        run_benchmark(tasks, config, _mock_runner_factory, DeterministicEvaluator())
    )

    assert [str(r.task.task_id) for r in summary.task_results] == [
        str(t.task_id) for t in tasks
    ]
