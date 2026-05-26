"""Tests for MockAgentRunner."""

from __future__ import annotations

import asyncio

from agentevalops.agents.mock_runner import MockAgentRunner
from agentevalops.core.schemas import (
    AgentInput,
    ResourceLimits,
    TaskSpec,
    TraceEventKind,
)
from agentevalops.core.types import TaskId


def _make_input(run_id: str = "test-run") -> AgentInput:
    task = TaskSpec(
        task_id=TaskId("t-1"),
        benchmark_id="toy",
        description="test task",
        metadata={},
    )
    return AgentInput(task=task, context={"run_id": run_id})


async def _collect_events(run_id: str = "test-run") -> list[object]:
    runner = MockAgentRunner()
    inp = _make_input(run_id)
    trace_iter = await runner.run(inp, ResourceLimits())
    return [e async for e in trace_iter]


def test_mock_runner_agent_id() -> None:
    assert MockAgentRunner.agent_id == "mock-agent-v1"


def test_mock_runner_emits_three_events() -> None:
    events = asyncio.run(_collect_events())
    assert len(events) == 3


def test_mock_runner_event_kinds() -> None:
    from agentevalops.core.schemas import TraceEvent

    events = asyncio.run(_collect_events())
    kinds = [e.kind for e in events if isinstance(e, TraceEvent)]
    assert kinds == [
        TraceEventKind.AGENT_PLAN,
        TraceEventKind.AGENT_FINAL_ANSWER,
        TraceEventKind.AGENT_TERMINAL,
    ]


def test_mock_runner_terminal_payload_success() -> None:
    from agentevalops.core.schemas import TraceEvent

    events = asyncio.run(_collect_events())
    terminal = events[-1]
    assert isinstance(terminal, TraceEvent)
    assert terminal.payload["success"] is True
    assert terminal.payload["termination_reason"] == "completed"
    assert terminal.payload["final_answer"] == "mock answer"


def test_mock_runner_run_id_propagated() -> None:
    from agentevalops.core.schemas import TraceEvent

    events = asyncio.run(_collect_events("my-run-42"))
    for e in events:
        assert isinstance(e, TraceEvent)
        assert e.run_id == "my-run-42"


# ---------------------------------------------------------------------------
# WBS 6: should_fail and cost_per_task_usd parameters
# ---------------------------------------------------------------------------


async def _collect_fail_events() -> list[object]:
    runner = MockAgentRunner(should_fail=True)
    inp = _make_input()
    trace_iter = await runner.run(inp, ResourceLimits())
    return [e async for e in trace_iter]


def test_mock_runner_fail_emits_two_events() -> None:
    events = asyncio.run(_collect_fail_events())
    assert len(events) == 2


def test_mock_runner_fail_event_kinds() -> None:
    from agentevalops.core.schemas import TraceEvent

    events = asyncio.run(_collect_fail_events())
    kinds = [e.kind for e in events if isinstance(e, TraceEvent)]
    assert kinds == [TraceEventKind.AGENT_PLAN, TraceEventKind.AGENT_TERMINAL]


def test_mock_runner_fail_terminal_success_false() -> None:
    from agentevalops.core.schemas import TraceEvent

    events = asyncio.run(_collect_fail_events())
    terminal = events[-1]
    assert isinstance(terminal, TraceEvent)
    assert terminal.payload["success"] is False
    assert terminal.payload["termination_reason"] == "agent_error"


def test_mock_runner_cost_in_terminal_payload() -> None:
    from agentevalops.core.schemas import TraceEvent

    async def _run() -> list[object]:
        runner = MockAgentRunner(cost_per_task_usd=0.5)
        inp = _make_input()
        return [e async for e in await runner.run(inp, ResourceLimits())]

    events = asyncio.run(_run())
    terminal = events[-1]
    assert isinstance(terminal, TraceEvent)
    assert terminal.payload["total_cost_usd"] == 0.5


def test_mock_runner_default_cost_is_zero() -> None:
    from agentevalops.core.schemas import TraceEvent

    events = asyncio.run(_collect_events())
    terminal = events[-1]
    assert isinstance(terminal, TraceEvent)
    assert terminal.payload["total_cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# Helper for parameterised task execution
# ---------------------------------------------------------------------------


async def _collect_task_events(
    runner: MockAgentRunner, task: TaskSpec
) -> list[object]:
    inp = AgentInput(task=task, context={"run_id": "t-run"})
    trace_iter = await runner.run(inp, ResourceLimits())
    return [e async for e in trace_iter]


def _run(runner: MockAgentRunner, task: TaskSpec) -> list[object]:
    return asyncio.run(_collect_task_events(runner, task))


# ---------------------------------------------------------------------------
# mock_answer metadata key
# ---------------------------------------------------------------------------


def test_mock_answer_overrides_expected_output() -> None:
    """MockAgentRunner should use mock_answer instead of expected_output."""
    task = TaskSpec(
        task_id=TaskId("ma-001"),
        benchmark_id="toy",
        description="test mock_answer",
        metadata={
            "expected_output": "correct answer",
            "mock_answer": "wrong answer",
        },
    )
    runner = MockAgentRunner()
    events = _run(runner, task)
    final_answer_events = [
        e for e in events if e.kind == TraceEventKind.AGENT_FINAL_ANSWER
    ]
    assert len(final_answer_events) == 1
    assert final_answer_events[0].payload["answer"] == "wrong answer"


def test_no_mock_answer_uses_expected_output() -> None:
    """Without mock_answer, runner uses expected_output as the answer."""
    task = TaskSpec(
        task_id=TaskId("ma-002"),
        benchmark_id="toy",
        description="test default answer",
        metadata={"expected_output": "the right answer"},
    )
    runner = MockAgentRunner()
    events = _run(runner, task)
    final_answer_events = [
        e for e in events if e.kind == TraceEventKind.AGENT_FINAL_ANSWER
    ]
    assert len(final_answer_events) == 1
    assert final_answer_events[0].payload["answer"] == "the right answer"


# ---------------------------------------------------------------------------
# emit_fake_tool_call metadata key
# ---------------------------------------------------------------------------


def test_emit_fake_tool_call_adds_tool_call_event() -> None:
    task = TaskSpec(
        task_id=TaskId("tc-001"),
        benchmark_id="toy",
        description="use a tool",
        metadata={"emit_fake_tool_call": True, "expected_output": "ok"},
    )
    runner = MockAgentRunner()
    events = _run(runner, task)
    tool_events = [
        e for e in events if e.kind == TraceEventKind.AGENT_TOOL_CALL
    ]
    assert len(tool_events) == 1


def test_emit_fake_tool_call_total_event_count() -> None:
    task = TaskSpec(
        task_id=TaskId("tc-002"),
        benchmark_id="toy",
        description="use a tool",
        metadata={"emit_fake_tool_call": True, "expected_output": "ok"},
    )
    runner = MockAgentRunner()
    events = _run(runner, task)
    assert len(events) == 4  # plan + tool_call + final_answer + terminal


def test_fake_tool_name_metadata_sets_payload_tool_name() -> None:
    task = TaskSpec(
        task_id=TaskId("tc-003"),
        benchmark_id="toy",
        description="use fetch",
        metadata={
            "emit_fake_tool_call": True,
            "fake_tool_name": "fetch",
            "expected_output": "ok",
        },
    )
    runner = MockAgentRunner()
    events = _run(runner, task)
    tool_events = [
        e for e in events if e.kind == TraceEventKind.AGENT_TOOL_CALL
    ]
    assert tool_events[0].payload["tool_name"] == "fetch"


def test_default_fake_tool_name_is_mock_tool() -> None:
    """When emit_fake_tool_call=True but fake_tool_name absent, default name used."""
    task = TaskSpec(
        task_id=TaskId("tc-004"),
        benchmark_id="toy",
        description="use default tool",
        metadata={"emit_fake_tool_call": True, "expected_output": "ok"},
    )
    runner = MockAgentRunner()
    events = _run(runner, task)
    tool_events = [
        e for e in events if e.kind == TraceEventKind.AGENT_TOOL_CALL
    ]
    assert tool_events[0].payload["tool_name"] == "mock-tool"
