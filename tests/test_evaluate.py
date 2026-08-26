"""Tests for minieval.evaluate.

All traces and outputs are built by hand, never produced by MockRunner —
per CLAUDE.md's constructed-inputs rule, running a runner here would couple
these tests to the runner's behaviour, so a runner bug could surface as an
evaluator failure instead of where it actually lives.
"""

from __future__ import annotations

from typing import Any

import pytest

from minieval.errors import ConfigurationError
from minieval.evaluate import DeterministicEvaluator
from minieval.schemas import (
    TOOL_NAME_PAYLOAD_KEY,
    AgentOutput,
    RunId,
    TaskId,
    TaskSpec,
    TerminationReason,
    TraceEvent,
    TraceEventKind,
)

RUN = RunId("eval-test")
TASK_ID = TaskId("T01")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _task(criteria: dict[str, Any]) -> TaskSpec:
    return TaskSpec(task_id=TASK_ID, description="test task", criteria=criteria)


def _output(
    *,
    termination_reason: TerminationReason = TerminationReason.COMPLETED,
    final_answer: str | None,
) -> AgentOutput:
    return AgentOutput(
        success=True, termination_reason=termination_reason, final_answer=final_answer
    )


def _event(
    step_index: int, kind: TraceEventKind, payload: dict[str, Any] | None = None
) -> TraceEvent:
    return TraceEvent(
        run_id=RUN,
        task_id=TASK_ID,
        attempt=0,
        step_index=step_index,
        kind=kind,
        payload=payload or {},
    )


def _tool_call(step_index: int, tool_name: str) -> TraceEvent:
    return _event(
        step_index, TraceEventKind.AGENT_TOOL_CALL, {TOOL_NAME_PAYLOAD_KEY: tool_name}
    )


def _final_answer(step_index: int, answer: str) -> TraceEvent:
    return _event(
        step_index, TraceEventKind.AGENT_FINAL_ANSWER, {"final_answer": answer}
    )


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------


def test_completion_passes_when_terminated_by_completed() -> None:
    task = _task({"expected_substring": "42"})
    output = _output(termination_reason=TerminationReason.COMPLETED, final_answer="42")
    trace = [_final_answer(0, "42")]

    result = DeterministicEvaluator().evaluate(task, output, trace)

    assert result.passed is True


@pytest.mark.parametrize(
    "reason",
    [
        TerminationReason.AGENT_ERROR,
        TerminationReason.LIMIT_STEPS,
        TerminationReason.LIMIT_COST,
    ],
)
def test_completion_fails_and_names_termination_reason(
    reason: TerminationReason,
) -> None:
    task = _task({"expected_substring": "42"})
    output = _output(termination_reason=reason, final_answer="42")
    trace = [_final_answer(0, "42")]

    result = DeterministicEvaluator().evaluate(task, output, trace)

    assert result.passed is False
    assert result.notes == f"did not complete: termination_reason={reason.value}"


# ---------------------------------------------------------------------------
# expected_substring
# ---------------------------------------------------------------------------


def test_expected_substring_present_passes_and_cites_final_answer() -> None:
    task = _task({"expected_substring": "Acme Supplies"})
    answer = "PO 4501 is on file for vendor Acme Supplies."
    output = _output(final_answer=answer)
    trace = [_final_answer(0, answer)]

    result = DeterministicEvaluator().evaluate(task, output, trace)

    assert result.passed is True
    assert result.citations == [0]


def test_expected_substring_absent_fails_and_names_expected_and_actual() -> None:
    task = _task({"expected_substring": "Acme Supplies"})
    answer = "PO 4501 is on file for vendor Widget Co."
    output = _output(final_answer=answer)
    trace = [_final_answer(0, answer)]

    result = DeterministicEvaluator().evaluate(task, output, trace)

    assert result.passed is False
    assert "Acme Supplies" in result.notes
    assert "Widget Co." in result.notes


def test_expected_substring_is_case_sensitive() -> None:
    task = _task({"expected_substring": "Net 30"})
    output = _output(final_answer="the term is net 30 days")
    trace = [_final_answer(0, "the term is net 30 days")]

    result = DeterministicEvaluator().evaluate(task, output, trace)

    assert result.passed is False


# ---------------------------------------------------------------------------
# required_tool_names
# ---------------------------------------------------------------------------


def test_required_tool_names_all_present_passes() -> None:
    task = _task({"required_tool_names": ["lookup_po", "lookup_vendor"]})
    output = _output(final_answer="done")
    trace = [
        _tool_call(0, "lookup_po"),
        _tool_call(1, "lookup_vendor"),
        _final_answer(2, "done"),
    ]

    result = DeterministicEvaluator().evaluate(task, output, trace)

    assert result.passed is True


def test_required_tool_names_missing_one_fails_and_names_missing_and_called() -> None:
    task = _task({"required_tool_names": ["lookup_po", "lookup_vendor"]})
    output = _output(final_answer="done")
    trace = [_tool_call(0, "lookup_po"), _final_answer(1, "done")]

    result = DeterministicEvaluator().evaluate(task, output, trace)

    assert result.passed is False
    assert "lookup_vendor" in result.notes
    assert "lookup_po" in result.notes


def test_required_tool_names_extra_calls_still_pass() -> None:
    task = _task({"required_tool_names": ["lookup_po"]})
    output = _output(final_answer="done")
    trace = [
        _tool_call(0, "lookup_po"),
        _tool_call(1, "some_other_tool"),
        _final_answer(2, "done"),
    ]

    result = DeterministicEvaluator().evaluate(task, output, trace)

    assert result.passed is True


def test_required_tool_names_duplicate_calls_still_pass() -> None:
    task = _task({"required_tool_names": ["lookup_po"]})
    output = _output(final_answer="done")
    trace = [
        _tool_call(0, "lookup_po"),
        _tool_call(1, "lookup_po"),
        _final_answer(2, "done"),
    ]

    result = DeterministicEvaluator().evaluate(task, output, trace)

    assert result.passed is True


def test_required_tool_names_call_order_does_not_matter() -> None:
    task = _task({"required_tool_names": ["lookup_po", "lookup_vendor"]})
    output = _output(final_answer="done")
    trace = [
        _tool_call(0, "lookup_vendor"),
        _tool_call(1, "lookup_po"),
        _final_answer(2, "done"),
    ]

    result = DeterministicEvaluator().evaluate(task, output, trace)

    assert result.passed is True


def test_required_tool_names_reads_trace_not_declared_mock_tools() -> None:
    """The check must judge what happened, not what the runner was told to
    do. mock_tools records intent; only the trace is evidence. A check that
    trusted mock_tools would pass any task whose config merely looks right
    -- the failure T05 exists to catch -- and would go silently blind
    against W10's real runner, which has no mock_ keys at all."""
    output = _output(final_answer="done")

    declared_but_not_called = _task(
        {"required_tool_names": ["lookup_po"], "mock_tools": ["lookup_po"]}
    )
    trace_without_call = [_final_answer(0, "done")]
    result = DeterministicEvaluator().evaluate(
        declared_but_not_called, output, trace_without_call
    )
    assert result.passed is False

    called_but_not_declared = _task({"required_tool_names": ["lookup_po"]})
    trace_with_call = [_tool_call(0, "lookup_po"), _final_answer(1, "done")]
    result = DeterministicEvaluator().evaluate(
        called_but_not_declared, output, trace_with_call
    )
    assert result.passed is True


# ---------------------------------------------------------------------------
# required_trace_kinds
# ---------------------------------------------------------------------------


def test_required_trace_kinds_all_present_passes() -> None:
    task = _task(
        {
            "required_trace_kinds": [
                TraceEventKind.AGENT_TOOL_CALL,
                TraceEventKind.AGENT_FINAL_ANSWER,
            ]
        }
    )
    output = _output(final_answer="done")
    trace = [_tool_call(0, "lookup_po"), _final_answer(1, "done")]

    result = DeterministicEvaluator().evaluate(task, output, trace)

    assert result.passed is True


def test_required_trace_kinds_missing_one_fails_and_names_missing_kind() -> None:
    task = _task(
        {
            "required_trace_kinds": [
                TraceEventKind.AGENT_TOOL_CALL,
                TraceEventKind.AGENT_PLAN,
            ]
        }
    )
    output = _output(final_answer="done")
    trace = [_tool_call(0, "lookup_po"), _final_answer(1, "done")]

    result = DeterministicEvaluator().evaluate(task, output, trace)

    assert result.passed is False
    assert TraceEventKind.AGENT_PLAN.value in result.notes


# ---------------------------------------------------------------------------
# Skip versus pass, and AND-combination
# ---------------------------------------------------------------------------


def test_absent_required_tool_names_does_not_fail_expected_substring_task() -> None:
    """A criterion absent from a task is skipped, not run against a default.
    If the dispatch loop ever ran required_tool_names against an empty
    required list instead of skipping it, this would still happen to pass
    -- see test_required_tool_names_reads_trace_not_declared_mock_tools's
    sibling assertions for the case that actually distinguishes the two."""
    task = _task({"expected_substring": "42"})
    output = _output(final_answer="42")
    trace = [_final_answer(0, "42")]

    result = DeterministicEvaluator().evaluate(task, output, trace)

    assert result.passed is True


def test_one_satisfied_one_violated_criterion_fails() -> None:
    task = _task({"expected_substring": "42", "required_tool_names": ["lookup_po"]})
    output = _output(final_answer="42")
    trace = [_final_answer(0, "42")]

    result = DeterministicEvaluator().evaluate(task, output, trace)

    assert result.passed is False


def test_three_of_four_checks_passing_still_fails() -> None:
    task = _task(
        {
            "expected_substring": "42",
            "required_tool_names": ["lookup_po"],
            "required_trace_kinds": [TraceEventKind.AGENT_PLAN],
        }
    )
    output = _output(termination_reason=TerminationReason.COMPLETED, final_answer="42")
    trace = [_tool_call(0, "lookup_po"), _final_answer(1, "42")]

    result = DeterministicEvaluator().evaluate(task, output, trace)

    assert result.passed is False


# ---------------------------------------------------------------------------
# Citations
# ---------------------------------------------------------------------------


def test_failing_required_tool_names_still_cites_matching_calls() -> None:
    task = _task({"required_tool_names": ["lookup_po", "lookup_vendor"]})
    output = _output(final_answer="done")
    trace = [_tool_call(0, "lookup_po"), _final_answer(1, "done")]

    result = DeterministicEvaluator().evaluate(task, output, trace)

    assert result.passed is False
    assert result.citations == [0]


# ---------------------------------------------------------------------------
# ConfigurationError
# ---------------------------------------------------------------------------


def test_no_evaluator_side_criteria_raises_configuration_error() -> None:
    task = _task({"mock_answer": "42"})
    output = _output(final_answer="42")
    trace = [_final_answer(0, "42")]

    with pytest.raises(ConfigurationError):
        DeterministicEvaluator().evaluate(task, output, trace)


def test_unrecognised_required_trace_kind_raises_configuration_error() -> None:
    task = _task({"required_trace_kinds": ["not.a.real.kind"]})
    output = _output(final_answer="42")
    trace = [_final_answer(0, "42")]

    with pytest.raises(ConfigurationError):
        DeterministicEvaluator().evaluate(task, output, trace)


# ---------------------------------------------------------------------------
# evaluated
# ---------------------------------------------------------------------------


def test_evaluated_is_true_on_pass() -> None:
    task = _task({"expected_substring": "42"})
    output = _output(final_answer="42")
    trace = [_final_answer(0, "42")]

    result = DeterministicEvaluator().evaluate(task, output, trace)

    assert result.passed is True
    assert result.evaluated is True


def test_evaluated_is_true_on_fail() -> None:
    task = _task({"expected_substring": "42"})
    output = _output(final_answer="not the answer")
    trace = [_final_answer(0, "not the answer")]

    result = DeterministicEvaluator().evaluate(task, output, trace)

    assert result.passed is False
    assert result.evaluated is True
