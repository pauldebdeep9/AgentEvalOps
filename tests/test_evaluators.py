"""Tests for DeterministicEvaluator."""

from __future__ import annotations

import asyncio

from agentevalops.core.schemas import (
    AgentOutput,
    TaskSpec,
    TerminationReason,
    TraceEvent,
    TraceEventKind,
)
from agentevalops.core.types import RunId, TaskId
from agentevalops.evaluators.deterministic import DeterministicEvaluator


def _task() -> TaskSpec:
    return TaskSpec(
        task_id=TaskId("t-1"),
        benchmark_id="toy",
        description="test",
        metadata={},
    )


def _completed_output() -> AgentOutput:
    return AgentOutput(
        success=True,
        termination_reason=TerminationReason.COMPLETED,
        final_answer="ok",
        total_cost_usd=0.0,
        total_tokens=5,
        wall_seconds=0.01,
    )


def _failed_output(reason: TerminationReason) -> AgentOutput:
    return AgentOutput(
        success=False,
        termination_reason=reason,
        error="something went wrong",
    )


def _terminal_event() -> TraceEvent:
    run_id = RunId("r1")
    return TraceEvent(
        run_id=run_id,
        step_index=2,
        kind=TraceEventKind.AGENT_TERMINAL,
        payload={},
    )


def test_evaluator_id() -> None:
    ev = DeterministicEvaluator()
    assert ev.evaluator_id == "deterministic-v1"
    assert ev.evaluator_kind == "deterministic"


def test_completed_passes() -> None:
    ev = DeterministicEvaluator()
    result = asyncio.run(ev.evaluate(_task(), _completed_output(), []))
    assert result.passed is True
    assert result.score == 1.0


def test_agent_error_fails() -> None:
    ev = DeterministicEvaluator()
    result = asyncio.run(
        ev.evaluate(_task(), _failed_output(TerminationReason.AGENT_ERROR), [])
    )
    assert result.passed is False
    assert result.score == 0.0


def test_limit_tokens_fails() -> None:
    ev = DeterministicEvaluator()
    result = asyncio.run(
        ev.evaluate(_task(), _failed_output(TerminationReason.LIMIT_TOKENS), [])
    )
    assert result.passed is False


def test_citations_from_terminal_events() -> None:
    ev = DeterministicEvaluator()
    trace = [_terminal_event()]
    result = asyncio.run(ev.evaluate(_task(), _completed_output(), trace))
    assert 2 in result.citations


def test_notes_contain_termination_reason() -> None:
    ev = DeterministicEvaluator()
    result = asyncio.run(ev.evaluate(_task(), _completed_output(), []))
    assert "completed" in result.notes


# ---------------------------------------------------------------------------
# WBS 5: expected_output checks
# ---------------------------------------------------------------------------


def _task_with_expected(expected: str) -> TaskSpec:
    return TaskSpec(
        task_id=TaskId("t-exp"),
        benchmark_id="toy",
        description="test",
        metadata={"expected_output": expected},
    )


def test_evaluator_fails_when_expected_output_mismatch() -> None:
    ev = DeterministicEvaluator()
    output = AgentOutput(
        success=True,
        termination_reason=TerminationReason.COMPLETED,
        final_answer="wrong answer",
    )
    result = asyncio.run(ev.evaluate(_task_with_expected("hello"), output, []))
    assert result.passed is False
    assert result.score == 0.0


def test_evaluator_passes_when_expected_output_matches() -> None:
    ev = DeterministicEvaluator()
    output = AgentOutput(
        success=True,
        termination_reason=TerminationReason.COMPLETED,
        final_answer="hello world",
    )
    result = asyncio.run(
        ev.evaluate(_task_with_expected("hello"), output, [])
    )
    assert result.passed is True
    assert result.score == 1.0


def test_evaluator_passes_without_expected_output() -> None:
    """Tasks without expected_output skip the substring check."""
    ev = DeterministicEvaluator()
    output = AgentOutput(
        success=True,
        termination_reason=TerminationReason.COMPLETED,
        final_answer="anything",
    )
    result = asyncio.run(ev.evaluate(_task(), output, []))
    assert result.passed is True


def test_notes_mention_expected_output_mismatch() -> None:
    ev = DeterministicEvaluator()
    output = AgentOutput(
        success=True,
        termination_reason=TerminationReason.COMPLETED,
        final_answer="wrong",
    )
    result = asyncio.run(ev.evaluate(_task_with_expected("hello"), output, []))
    assert "mismatch" in result.notes


def test_notes_mention_missing_final_answer_event() -> None:
    """Non-empty trace with no AGENT_FINAL_ANSWER → notes mention absence."""
    ev = DeterministicEvaluator()
    trace = [_terminal_event()]  # only AGENT_TERMINAL, no AGENT_FINAL_ANSWER
    result = asyncio.run(ev.evaluate(_task(), _completed_output(), trace))
    assert "AGENT_FINAL_ANSWER" in result.notes


# ---------------------------------------------------------------------------
# match_mode: exact
# ---------------------------------------------------------------------------


def _task_exact(expected: str) -> TaskSpec:
    return TaskSpec(
        task_id=TaskId("t-exact"),
        benchmark_id="toy",
        description="exact match test",
        metadata={"expected_output": expected, "match_mode": "exact"},
    )


def test_exact_match_passes_when_equal() -> None:
    ev = DeterministicEvaluator()
    output = AgentOutput(
        success=True,
        termination_reason=TerminationReason.COMPLETED,
        final_answer="hello world",
    )
    result = asyncio.run(ev.evaluate(_task_exact("hello world"), output, []))
    assert result.passed is True


def test_exact_match_fails_when_not_equal() -> None:
    ev = DeterministicEvaluator()
    output = AgentOutput(
        success=True,
        termination_reason=TerminationReason.COMPLETED,
        final_answer="hello world extra",
    )
    result = asyncio.run(ev.evaluate(_task_exact("hello world"), output, []))
    assert result.passed is False


def test_exact_match_fails_when_only_substring() -> None:
    """'hello' is a substring of 'hello world' but not an exact match."""
    ev = DeterministicEvaluator()
    output = AgentOutput(
        success=True,
        termination_reason=TerminationReason.COMPLETED,
        final_answer="hello world",
    )
    result = asyncio.run(ev.evaluate(_task_exact("hello"), output, []))
    assert result.passed is False


def test_exact_match_notes_mention_mismatch() -> None:
    ev = DeterministicEvaluator()
    output = AgentOutput(
        success=True,
        termination_reason=TerminationReason.COMPLETED,
        final_answer="hello world",
    )
    result = asyncio.run(ev.evaluate(_task_exact("hello"), output, []))
    assert "mismatch" in result.notes


# ---------------------------------------------------------------------------
# expected_substring check
# ---------------------------------------------------------------------------


def _task_with_substring(substring: str) -> TaskSpec:
    return TaskSpec(
        task_id=TaskId("t-sub"),
        benchmark_id="toy",
        description="substring test",
        metadata={"expected_substring": substring},
    )


def test_expected_substring_passes_when_present() -> None:
    ev = DeterministicEvaluator()
    output = AgentOutput(
        success=True,
        termination_reason=TerminationReason.COMPLETED,
        final_answer="The magic_word is here.",
    )
    result = asyncio.run(ev.evaluate(_task_with_substring("magic_word"), output, []))
    assert result.passed is True


def test_expected_substring_fails_when_absent() -> None:
    ev = DeterministicEvaluator()
    output = AgentOutput(
        success=True,
        termination_reason=TerminationReason.COMPLETED,
        final_answer="no magic here",
    )
    result = asyncio.run(ev.evaluate(_task_with_substring("magic_word"), output, []))
    assert result.passed is False


def test_expected_substring_notes_mention_missing() -> None:
    ev = DeterministicEvaluator()
    output = AgentOutput(
        success=True,
        termination_reason=TerminationReason.COMPLETED,
        final_answer="no magic here",
    )
    result = asyncio.run(ev.evaluate(_task_with_substring("magic_word"), output, []))
    assert "missing" in result.notes


# ---------------------------------------------------------------------------
# required_trace_kinds
# ---------------------------------------------------------------------------


def _task_with_required_kinds(kinds: list[str]) -> TaskSpec:
    return TaskSpec(
        task_id=TaskId("t-kinds"),
        benchmark_id="toy",
        description="trace kinds test",
        metadata={"required_trace_kinds": kinds},
    )


def _event(kind: TraceEventKind, step: int = 0) -> TraceEvent:
    return TraceEvent(
        run_id=RunId("r1"),
        step_index=step,
        kind=kind,
        payload={},
    )


def test_required_trace_kind_present_passes() -> None:
    ev = DeterministicEvaluator()
    task = _task_with_required_kinds([TraceEventKind.AGENT_FINAL_ANSWER.value])
    trace = [_event(TraceEventKind.AGENT_FINAL_ANSWER)]
    result = asyncio.run(ev.evaluate(task, _completed_output(), trace))
    assert result.passed is True


def test_required_trace_kind_absent_fails() -> None:
    ev = DeterministicEvaluator()
    task = _task_with_required_kinds([TraceEventKind.AGENT_FINAL_ANSWER.value])
    trace = [_event(TraceEventKind.AGENT_TERMINAL)]
    result = asyncio.run(ev.evaluate(task, _completed_output(), trace))
    assert result.passed is False


def test_required_trace_kind_absent_notes_missing() -> None:
    ev = DeterministicEvaluator()
    task = _task_with_required_kinds([TraceEventKind.AGENT_FINAL_ANSWER.value])
    result = asyncio.run(ev.evaluate(task, _completed_output(), []))
    assert "missing" in result.notes


# ---------------------------------------------------------------------------
# required_tool_names
# ---------------------------------------------------------------------------


def _task_with_required_tools(names: list[str]) -> TaskSpec:
    return TaskSpec(
        task_id=TaskId("t-tools"),
        benchmark_id="toy",
        description="tool names test",
        metadata={"required_tool_names": names},
    )


def _tool_event(tool_name: str, step: int = 0) -> TraceEvent:
    return TraceEvent(
        run_id=RunId("r1"),
        step_index=step,
        kind=TraceEventKind.AGENT_TOOL_CALL,
        payload={"tool_name": tool_name, "args": {}},
    )


def test_required_tool_name_present_passes() -> None:
    ev = DeterministicEvaluator()
    task = _task_with_required_tools(["search"])
    trace = [_tool_event("search")]
    result = asyncio.run(ev.evaluate(task, _completed_output(), trace))
    assert result.passed is True


def test_required_tool_name_absent_fails() -> None:
    ev = DeterministicEvaluator()
    task = _task_with_required_tools(["search"])
    trace = [_tool_event("fetch")]  # wrong tool
    result = asyncio.run(ev.evaluate(task, _completed_output(), trace))
    assert result.passed is False


def test_required_tool_name_absent_notes_missing() -> None:
    ev = DeterministicEvaluator()
    task = _task_with_required_tools(["search"])
    result = asyncio.run(ev.evaluate(task, _completed_output(), []))
    assert "missing" in result.notes


def test_required_tool_name_no_tool_events_fails() -> None:
    ev = DeterministicEvaluator()
    task = _task_with_required_tools(["search"])
    trace = [_event(TraceEventKind.AGENT_PLAN)]
    result = asyncio.run(ev.evaluate(task, _completed_output(), trace))
    assert result.passed is False


# ---------------------------------------------------------------------------
# Combined checks
# ---------------------------------------------------------------------------


def test_all_new_checks_pass_together() -> None:
    ev = DeterministicEvaluator()
    task = TaskSpec(
        task_id=TaskId("t-all"),
        benchmark_id="toy",
        description="all checks",
        metadata={
            "expected_output": "hello world",
            "match_mode": "exact",
            "expected_substring": "world",
            "required_trace_kinds": [TraceEventKind.AGENT_TOOL_CALL.value],
            "required_tool_names": ["search"],
        },
    )
    output = AgentOutput(
        success=True,
        termination_reason=TerminationReason.COMPLETED,
        final_answer="hello world",
    )
    trace = [_tool_event("search")]
    result = asyncio.run(ev.evaluate(task, output, trace))
    assert result.passed is True


def test_one_failing_check_fails_overall() -> None:
    ev = DeterministicEvaluator()
    task = TaskSpec(
        task_id=TaskId("t-one-fail"),
        benchmark_id="toy",
        description="one failing check",
        metadata={
            "expected_output": "hello world",
            "match_mode": "exact",
            "required_tool_names": ["search"],  # not in trace → fail
        },
    )
    output = AgentOutput(
        success=True,
        termination_reason=TerminationReason.COMPLETED,
        final_answer="hello world",
    )
    result = asyncio.run(ev.evaluate(task, output, []))
    assert result.passed is False
