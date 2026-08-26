"""Tests for minieval.runners.mock.

Covers protocol conformance, the happy-path event shape, the invariants
that must hold regardless of task shape, and the two design rules the mock
runner exists to model faithfully: it does not enforce ``ResourceLimits``
(W6's job) and it cannot see evaluator-side criteria (the evaluator's job).
Failure modes (``mock_fail``, ``mock_pass_on``) are tested separately.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from minieval.errors import ConfigurationError, InfrastructureError
from minieval.protocols import AgentRunner
from minieval.runners.mock import MOCK_WRONG_ANSWER, MockRunner
from minieval.schemas import (
    TOOL_NAME_PAYLOAD_KEY,
    AgentOutput,
    ResourceLimits,
    RunId,
    TaskId,
    TaskSpec,
    TerminationReason,
    TraceEvent,
    TraceEventKind,
)

RUN = RunId("mock-run")

_INVARIANT_CRITERIA: list[dict[str, Any]] = [
    {"mock_answer": "42"},
    {"mock_answer": "42", "mock_tools": ["search", "calc"]},
    {"mock_answer": "42", "mock_tools": ["search"], "mock_extra_steps": 2},
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _collect(
    runner: MockRunner, task: TaskSpec, limits: ResourceLimits
) -> list[TraceEvent]:
    return [event async for event in runner.run(task, limits)]


def _events(
    criteria: dict[str, Any], *, attempt: int = 0, limits: ResourceLimits | None = None
) -> list[TraceEvent]:
    task = TaskSpec(task_id=TaskId("T01"), description="mock task", criteria=criteria)
    runner = MockRunner(run_id=RUN, attempt=attempt)
    return asyncio.run(_collect(runner, task, limits or ResourceLimits()))


async def _collect_or_raise(
    runner: MockRunner, task: TaskSpec, limits: ResourceLimits
) -> tuple[list[TraceEvent], InfrastructureError | ConfigurationError | None]:
    events: list[TraceEvent] = []
    try:
        async for event in runner.run(task, limits):
            events.append(event)
    except (InfrastructureError, ConfigurationError) as exc:
        return events, exc
    return events, None


def _events_or_raise(
    criteria: dict[str, Any], *, attempt: int = 0, limits: ResourceLimits | None = None
) -> tuple[list[TraceEvent], InfrastructureError | ConfigurationError | None]:
    task = TaskSpec(task_id=TaskId("T01"), description="mock task", criteria=criteria)
    runner = MockRunner(run_id=RUN, attempt=attempt)
    return asyncio.run(_collect_or_raise(runner, task, limits or ResourceLimits()))


# ---------------------------------------------------------------------------
# Conformance
# ---------------------------------------------------------------------------


def test_mock_runner_satisfies_agent_runner_protocol() -> None:
    runner = MockRunner(run_id=RUN, attempt=0)
    # isinstance, never issubclass: AgentRunner declares a non-method member
    # (agent_id), and issubclass raises TypeError against a runtime_checkable
    # Protocol that has any non-method members.
    assert isinstance(runner, AgentRunner)


def test_mock_runner_sets_agent_id() -> None:
    runner = MockRunner(run_id=RUN, attempt=0)
    assert runner.agent_id == "mock"


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


def test_zero_tools_emits_only_final_answer_then_terminal() -> None:
    events = _events({"mock_answer": "42"})
    assert [e.kind for e in events] == [
        TraceEventKind.AGENT_FINAL_ANSWER,
        TraceEventKind.AGENT_TERMINAL,
    ]


def test_two_tools_emits_plan_call_result_per_tool_then_final_and_terminal() -> None:
    events = _events({"mock_answer": "42", "mock_tools": ["search", "calc"]})
    assert [e.kind for e in events] == [
        TraceEventKind.AGENT_PLAN,
        TraceEventKind.AGENT_TOOL_CALL,
        TraceEventKind.AGENT_TOOL_RESULT,
        TraceEventKind.AGENT_PLAN,
        TraceEventKind.AGENT_TOOL_CALL,
        TraceEventKind.AGENT_TOOL_RESULT,
        TraceEventKind.AGENT_FINAL_ANSWER,
        TraceEventKind.AGENT_TERMINAL,
    ]


def test_tool_names_appear_under_payload_key_in_order() -> None:
    events = _events({"mock_answer": "42", "mock_tools": ["search", "calc"]})
    call_and_result_names = [
        e.payload[TOOL_NAME_PAYLOAD_KEY]
        for e in events
        if e.kind in (TraceEventKind.AGENT_TOOL_CALL, TraceEventKind.AGENT_TOOL_RESULT)
    ]
    assert call_and_result_names == ["search", "search", "calc", "calc"]


# ---------------------------------------------------------------------------
# Invariants — hold for any task shape, asserted as rules, not examples
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("criteria", _INVARIANT_CRITERIA)
def test_step_index_is_contiguous_from_zero(criteria: dict[str, Any]) -> None:
    events = _events(criteria)
    assert [e.step_index for e in events] == list(range(len(events)))


@pytest.mark.parametrize("criteria", _INVARIANT_CRITERIA)
def test_terminal_is_last_event_and_appears_exactly_once(
    criteria: dict[str, Any],
) -> None:
    events = _events(criteria)
    terminal_positions = [
        i for i, e in enumerate(events) if e.kind is TraceEventKind.AGENT_TERMINAL
    ]
    assert terminal_positions == [len(events) - 1]


@pytest.mark.parametrize("criteria", _INVARIANT_CRITERIA)
def test_terminal_steps_equals_plan_and_final_answer_event_count(
    criteria: dict[str, Any],
) -> None:
    """The definition of a step, restated as an assertion.

    A step is one model turn, and every turn emits exactly one
    AGENT_PLAN or AGENT_FINAL_ANSWER. Asserting the identity here, rather
    than a fixed expected number per case, is what stops the two terminal
    branches (success and agent_error) from ever recomputing it differently.
    """
    events = _events(criteria)
    turn_kinds = (TraceEventKind.AGENT_PLAN, TraceEventKind.AGENT_FINAL_ANSWER)
    expected_steps = sum(1 for e in events if e.kind in turn_kinds)
    assert events[-1].payload["steps"] == expected_steps


@pytest.mark.parametrize("criteria", _INVARIANT_CRITERIA)
def test_terminal_cost_equals_sum_of_all_event_costs(
    criteria: dict[str, Any],
) -> None:
    events = _events(criteria)
    total_cost = sum(e.cost_usd for e in events)
    assert events[-1].payload["cost_usd"] == pytest.approx(total_cost)


# ---------------------------------------------------------------------------
# Reconstruction
# ---------------------------------------------------------------------------


def test_terminal_payload_reconstructs_agent_output_on_happy_path() -> None:
    events = _events({"mock_answer": "42", "mock_tools": ["search"]})
    output = AgentOutput.from_payload(events[-1].payload)
    assert output.success is True
    assert output.termination_reason is TerminationReason.COMPLETED


# ---------------------------------------------------------------------------
# Design rules
# ---------------------------------------------------------------------------


def test_runner_ignores_resource_limits() -> None:
    """Enforcement is W6's job. A runner that quietly enforced limits would
    make W6's limit tests pass for the wrong reason."""
    criteria = {"mock_answer": "42", "mock_tools": ["search", "calc"]}
    unconstrained = _events(criteria, limits=ResourceLimits())
    constrained = _events(
        criteria, limits=ResourceLimits(max_steps=1, max_cost_usd=0.0)
    )
    assert [(e.kind, e.payload) for e in constrained] == [
        (e.kind, e.payload) for e in unconstrained
    ]


def test_runner_ignores_evaluator_side_criteria() -> None:
    """The runner must never see the answer key.

    Adding expected_substring, required_tool_names, and
    required_trace_kinds must not change one byte of what gets emitted —
    that is what stops a future change from letting the runner peek at it.
    """
    base_criteria = {"mock_answer": "42", "mock_tools": ["search"]}
    polluted_criteria: dict[str, Any] = {
        **base_criteria,
        "expected_substring": "definitely not 42",
        "required_tool_names": ["some_other_tool"],
        "required_trace_kinds": [TraceEventKind.AGENT_TOOL_CALL],
    }

    plain = _events(base_criteria)
    polluted = _events(polluted_criteria)

    assert [e.kind for e in plain] == [e.kind for e in polluted]
    assert plain[-1].payload["final_answer"] == polluted[-1].payload["final_answer"]


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_infra_failure_with_tools_raises_after_at_least_one_event() -> None:
    events, exc = _events_or_raise(
        {"mock_answer": "42", "mock_tools": ["search"], "mock_fail": "infra"}
    )
    assert isinstance(exc, InfrastructureError)
    assert len(events) >= 1
    assert all(e.kind is not TraceEventKind.AGENT_TERMINAL for e in events)
    assert [e.step_index for e in events] == list(range(len(events)))


def test_infra_failure_with_zero_tools_raises_after_final_answer() -> None:
    events, exc = _events_or_raise({"mock_answer": "42", "mock_fail": "infra"})
    assert isinstance(exc, InfrastructureError)
    assert events[-1].kind is TraceEventKind.AGENT_FINAL_ANSWER
    assert all(e.kind is not TraceEventKind.AGENT_TERMINAL for e in events)
    assert [e.step_index for e in events] == list(range(len(events)))


def test_agent_error_does_not_raise() -> None:
    """The runner contract's core distinction: an agent failing at its task
    is a result (AGENT_TERMINAL, success=False), never an exception. Only
    infrastructure failure may interrupt the stream."""
    try:
        events = _events(
            {"mock_answer": "42", "mock_tools": ["search"], "mock_fail": "agent_error"}
        )
    except Exception as exc:
        pytest.fail(f"mock_fail='agent_error' must not raise, but raised {exc!r}")

    terminal = events[-1]
    assert terminal.kind is TraceEventKind.AGENT_TERMINAL
    assert terminal.payload["success"] is False
    assert terminal.payload["termination_reason"] == TerminationReason.AGENT_ERROR
    assert terminal.payload["final_answer"] is None
    assert isinstance(terminal.payload["error"], str) and terminal.payload["error"]


def test_mock_extra_steps_increases_steps_and_diverges_from_event_count() -> None:
    """Event count must grow faster than turn count — the property W6's
    max_steps enforcement depends on, since max_steps counts turns."""
    base_criteria = {"mock_answer": "42", "mock_tools": ["search"]}
    extra_criteria = {**base_criteria, "mock_extra_steps": 2}

    base_events = _events(base_criteria)
    extra_events = _events(extra_criteria)

    steps_delta = extra_events[-1].payload["steps"] - base_events[-1].payload["steps"]
    event_count_delta = len(extra_events) - len(base_events)

    assert steps_delta == 2
    assert event_count_delta > steps_delta


def test_mock_pass_on_returns_verbatim_answer_only_on_listed_attempts() -> None:
    criteria = {"mock_answer": "42", "mock_pass_on": [0, 2]}
    final_answers = {
        attempt: _events(criteria, attempt=attempt)[-1].payload["final_answer"]
        for attempt in (0, 1, 2)
    }
    assert final_answers[0] == "42"
    assert final_answers[2] == "42"
    assert final_answers[1] == MOCK_WRONG_ANSWER


def test_mock_pass_on_wrong_answer_still_reports_success() -> None:
    """The runner does not know whether its answer is correct — only that
    it finished. Judging correctness is the evaluator's job. If this test
    is ever changed to expect success=False here, judgement has quietly
    moved into the runner, and the evaluated/passed distinction this
    project is built around stops meaning anything."""
    events = _events({"mock_answer": "42", "mock_pass_on": [0]}, attempt=1)
    terminal = events[-1]
    assert terminal.payload["final_answer"] == MOCK_WRONG_ANSWER
    assert terminal.payload["success"] is True


def test_unrecognised_mock_fail_raises_configuration_error() -> None:
    """Not InfrastructureError: a typo in a config is not an infrastructure
    failure, and W6 treats the two completely differently."""
    events, exc = _events_or_raise(
        {"mock_answer": "42", "mock_fail": "not_a_real_failure_mode"}
    )
    assert isinstance(exc, ConfigurationError)
    assert events == []


def test_empty_criteria_produces_well_formed_terminal() -> None:
    events = _events({})
    terminal = events[-1]
    assert terminal.kind is TraceEventKind.AGENT_TERMINAL
    assert terminal.payload["termination_reason"] == TerminationReason.COMPLETED
    output = AgentOutput.from_payload(terminal.payload)
    assert output.success is True
