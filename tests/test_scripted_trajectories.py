"""Scripted trajectories and the self-report cross-check (docs/adr/0006 D11,
D14; R23 slice 5 and R23's exit criterion)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agentevalops.agents.mock_runner import MockAgentRunner, ScriptStep, parse_script
from agentevalops.core.errors import ConfigurationError
from agentevalops.core.schemas import (
    AgentInput,
    ResourceLimits,
    TaskSpec,
    ToolCallRecord,
    TraceEvent,
    TraceEventKind,
)
from agentevalops.core.types import RunId, TaskId
from agentevalops.environments import cross_check, toy_environment
from agentevalops.environments.inprocess import InProcessToolEnvironment


def _input(env: Any = None, task_id: str = "t-1") -> AgentInput:
    task = TaskSpec(task_id=TaskId(task_id), benchmark_id="toy", description="d")
    return AgentInput(task=task, context={"run_id": "r"}, environment=env)


def _run(runner: MockAgentRunner, inp: AgentInput) -> list[TraceEvent]:
    async def go() -> list[TraceEvent]:
        return [e async for e in await runner.run(inp, ResourceLimits())]

    return asyncio.run(go())


_GOOD = [
    {"say": "I will compute it."},
    {"call": "calculator", "arguments": {"expression": "6 * 7"}},
    {"finish": "42"},
]
_DIVERGENT = [
    # Calls the calculator but claims it called something else.
    {"call": "calculator", "arguments": {"expression": "6 * 7"}, "report_as": "lookup"},
    {"finish": "42"},
]


# ---------------------------------------------------------------------------
# R23's exit criterion
# ---------------------------------------------------------------------------


def test_matching_script_passes_the_cross_check() -> None:
    env = toy_environment(TaskId("t-1"))
    events = _run(MockAgentRunner(scripts={"t-1": _GOOD}), _input(env))
    (record,) = env.records()
    assert (record.tool_name, record.arguments) == (
        "calculator",
        {"expression": "6 * 7"},
    )
    assert record.result == "42"
    assert cross_check(env.records(), events) == []


def test_divergent_script_is_flagged() -> None:
    """Fail-first: with cross_check's comparison removed, this passed
    unflagged."""
    env = toy_environment(TaskId("t-1"))
    events = _run(MockAgentRunner(scripts={"t-1": _DIVERGENT}), _input(env))
    (mismatch,) = cross_check(env.records(), events)
    assert (mismatch.kind, mismatch.recorded, mismatch.reported) == (
        "different_call",
        "calculator",
        "lookup",
    )


# ---------------------------------------------------------------------------
# The scripted run itself
# ---------------------------------------------------------------------------


def test_scripted_run_emits_a_completed_trajectory() -> None:
    env = toy_environment(TaskId("t-1"))
    events = _run(MockAgentRunner(scripts={"t-1": _GOOD}), _input(env))
    kinds = [e.kind for e in events]
    assert kinds == [
        TraceEventKind.AGENT_PLAN,
        TraceEventKind.AGENT_PLAN,
        TraceEventKind.AGENT_TOOL_CALL,
        TraceEventKind.AGENT_TOOL_RESULT,
        TraceEventKind.AGENT_FINAL_ANSWER,
        TraceEventKind.AGENT_TERMINAL,
    ]
    assert [e.step_index for e in events] == list(range(6))
    terminal = events[-1].payload
    assert (terminal["success"], terminal["final_answer"]) == (True, "42")


def test_script_without_finish_is_an_agent_error() -> None:
    events = _run(
        MockAgentRunner(scripts={"t-1": [{"say": "thinking"}]}),
        _input(toy_environment()),
    )
    assert events[-1].payload["termination_reason"] == "agent_error"


def test_tasks_without_a_script_keep_the_default_mode() -> None:
    events = _run(MockAgentRunner(scripts={"other": _GOOD}), _input())
    assert [e.kind for e in events][-1] == TraceEventKind.AGENT_TERMINAL
    assert len(events) == 3  # plan, final answer, terminal: today's mode


def test_scripted_run_without_an_environment_falls_back_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING"):
        events = _run(MockAgentRunner(scripts={"t-1": _GOOD}), _input())
    assert events[-1].payload["success"] is True
    assert any("no tool environment" in r.message for r in caplog.records)


def test_scripted_call_to_an_unknown_tool_is_recorded_as_such() -> None:
    env = toy_environment(TaskId("t-1"))
    _run(
        MockAgentRunner(scripts={"t-1": [{"call": "nope"}, {"finish": "x"}]}),
        _input(env),
    )
    (record,) = env.records()
    assert (record.tool_name, record.executed, record.error) == ("nope", False, True)


# ---------------------------------------------------------------------------
# Script validation (a guard: a broken script fails when the runner is built)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "step",
    [
        {},
        {"call": "a", "finish": "b"},
        {"call": ""},
        {"call": 3},
        {"say": ["not", "text"]},
        {"call": "a", "arguments": ["x"]},
        {"jump": "x"},
    ],
)
def test_malformed_script_step_is_rejected(step: dict[str, Any]) -> None:
    with pytest.raises(ConfigurationError, match="script step 0"):
        MockAgentRunner(scripts={"t-1": [step]})


def test_parse_script_accepts_steps_and_data() -> None:
    step = ScriptStep(kind="say", text="hi")
    parsed = parse_script([step, {"finish": "done"}])
    assert parsed[0] is step
    assert parsed[1] == ScriptStep(kind="finish", text="done")


# ---------------------------------------------------------------------------
# cross_check over hand-made records and reports
# ---------------------------------------------------------------------------


def _record(
    index: int, tool: str, args: dict[str, Any] | None = None
) -> ToolCallRecord:
    return ToolCallRecord(
        task_id=TaskId("t"),
        index=index,
        tool_name=tool,
        arguments=args or {},
        permission="read",
        data_class="normal",
        executed=True,
        result="ok",
    )


def _report(tool: str, args: dict[str, Any] | None = None) -> TraceEvent:
    return TraceEvent(
        run_id=RunId("r"),
        step_index=0,
        kind=TraceEventKind.AGENT_TOOL_CALL,
        payload={"tool_name": tool, "args": args or {}},
    )


def test_cross_check_flags_unreported_and_unrecorded_calls() -> None:
    assert [m.kind for m in cross_check([_record(0, "a")], [])] == ["unreported_call"]
    assert [m.kind for m in cross_check([], [_report("a")])] == ["unrecorded_report"]


def test_cross_check_compares_arguments_and_ignores_finish() -> None:
    records = [_record(0, "a", {"x": 1})]
    events = [_report("a", {"x": 2}), _report("finish", {"answer": "y"})]
    (mismatch,) = cross_check(records, events)
    assert mismatch.kind == "different_call"
    assert cross_check(records, [_report("a", {"x": 1}), _report("finish")]) == []


def test_cross_check_orders_records_by_index() -> None:
    records = [_record(1, "b"), _record(0, "a")]
    assert cross_check(records, [_report("a"), _report("b")]) == []


def test_cross_check_of_a_real_runner_trace_with_a_custom_environment() -> None:
    async def lookup(arguments: dict[str, Any]) -> str:
        return "found"

    from agentevalops.core.schemas import ToolSpec

    env = InProcessToolEnvironment(
        TaskId("t-1"),
        (ToolSpec(name="lookup", description="d", input_schema={}),),
        {"lookup": lookup},
    )
    script = [{"call": "lookup", "arguments": {"id": "A"}}, {"finish": "found"}]
    events = _run(MockAgentRunner(scripts={"t-1": script}), _input(env))
    assert cross_check(env.records(), events) == []
