"""Tests for agentevalops.environments (docs/adr/0006 D2-D4, D8)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agentevalops.agents import langgraph_runner, openai_runner
from agentevalops.core.errors import ConfigurationError
from agentevalops.core.protocols import ToolEnvironment
from agentevalops.core.schemas import ToolSpec
from agentevalops.core.types import TaskId
from agentevalops.environments import (
    CALCULATOR_TOOL_SPEC,
    InProcessToolEnvironment,
    ToolEnvironmentError,
    run_calculator,
    toy_environment,
)


def _spec(name: str, **kw: Any) -> ToolSpec:
    return ToolSpec(name=name, description=f"{name} tool", input_schema={}, **kw)


async def _echo(arguments: dict[str, Any]) -> str:
    return f"echo {arguments.get('x', '')}"


async def _boom(arguments: dict[str, Any]) -> str:
    raise RuntimeError("handler failed")


def _env(**kw: Any) -> InProcessToolEnvironment:
    return InProcessToolEnvironment(
        TaskId("t-1"),
        (_spec("echo", permission="write"), _spec("boom")),
        {"echo": _echo, "boom": _boom},
        **kw,
    )


# ---------------------------------------------------------------------------
# Construction guards (each seen failing with its check removed)
# ---------------------------------------------------------------------------


def test_reserved_tool_name_is_rejected() -> None:
    """ADR-0006 D3: a domain pack cannot define ``finish``."""
    with pytest.raises(ToolEnvironmentError, match="reserved"):
        InProcessToolEnvironment(TaskId("t"), (_spec("finish"),), {"finish": _echo})


def test_duplicate_tool_name_is_rejected() -> None:
    with pytest.raises(ToolEnvironmentError, match="duplicate"):
        InProcessToolEnvironment(
            TaskId("t"), (_spec("echo"), _spec("echo")), {"echo": _echo}
        )


def test_tool_without_a_handler_is_rejected() -> None:
    with pytest.raises(ToolEnvironmentError, match="no handler"):
        InProcessToolEnvironment(TaskId("t"), (_spec("echo"),), {})


def test_environment_error_is_a_configuration_error() -> None:
    assert issubclass(ToolEnvironmentError, ConfigurationError)


# ---------------------------------------------------------------------------
# Calls and records
# ---------------------------------------------------------------------------


def test_every_call_is_recorded_in_order() -> None:
    env = _env(repetition=2)

    async def scenario() -> None:
        assert (await env.call("echo", {"x": 1})).content == "echo 1"
        await env.call("echo", {"x": 2})

    asyncio.run(scenario())
    records = env.records()
    assert [r.index for r in records] == [0, 1]
    assert [r.arguments for r in records] == [{"x": 1}, {"x": 2}]
    first = records[0]
    assert (first.task_id, first.repetition) == ("t-1", 2)
    assert (first.permission, first.data_class) == ("write", "normal")
    assert (first.executed, first.error, first.result) == (True, False, "echo 1")


def test_unknown_tool_is_an_error_result_and_recorded() -> None:
    env = _env()
    result = asyncio.run(env.call("nope", {"a": 1}))
    assert result.error and "unknown tool 'nope'" in result.content
    (record,) = env.records()
    assert (record.executed, record.error, record.permission) == (False, True, None)


def test_failing_handler_is_an_error_result_and_never_raises() -> None:
    env = _env()
    result = asyncio.run(env.call("boom", {}))
    assert result.error and "handler failed" in result.content
    (record,) = env.records()
    assert (record.executed, record.error) == (True, True)


def test_records_cannot_be_edited_through_the_returned_view() -> None:
    env = _env()
    asyncio.run(env.call("echo", {}))
    assert isinstance(env.records(), tuple)


def test_caller_mutating_arguments_does_not_change_the_record() -> None:
    env = _env()
    arguments: dict[str, Any] = {"x": 1}
    asyncio.run(env.call("echo", arguments))
    arguments["x"] = 99
    assert env.records()[0].arguments == {"x": 1}


def test_snapshot_defaults_to_the_call_count_or_uses_the_given_function() -> None:
    env = _env()
    asyncio.run(env.call("echo", {}))
    assert asyncio.run(env.snapshot()) == {"schema": "inprocess-calls-v0", "calls": 1}
    custom = _env(end_state=lambda: {"schema": "custom", "ok": True})
    assert asyncio.run(custom.snapshot()) == {"schema": "custom", "ok": True}


def test_in_process_environment_satisfies_the_protocol() -> None:
    assert isinstance(_env(), ToolEnvironment)
    assert isinstance(toy_environment(), ToolEnvironment)


# ---------------------------------------------------------------------------
# The toy environment
# ---------------------------------------------------------------------------


def test_toy_environment_offers_only_the_calculator() -> None:
    specs = asyncio.run(toy_environment().list_tools())
    assert [s.name for s in specs] == ["calculator"]
    assert specs[0] is CALCULATOR_TOOL_SPEC


def test_toy_calculator_call_is_recorded() -> None:
    env = toy_environment(TaskId("toy-1"))
    assert asyncio.run(env.call("calculator", {"expression": "6 * 7"})).content == "42"
    assert env.records()[0].task_id == "toy-1"


@pytest.mark.parametrize(
    "expression", ["2 + 2", "(3 * 4) - 1", "1 / 0", "2 ** 10", "", "__import__('os')"]
)
def test_toy_calculator_matches_both_runners(expression: str) -> None:
    """Same behaviour as each runner's own calculator, until slice 3 makes
    the runners use this one."""
    assert run_calculator(expression) == openai_runner._run_calculator(expression)
    assert run_calculator(expression) == langgraph_runner._run_calculator(expression)
