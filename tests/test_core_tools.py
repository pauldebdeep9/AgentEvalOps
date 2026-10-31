"""Tests for the tool types and protocol in core (docs/adr/0006 D1-D4)."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from typing import Any

import pytest

from agentevalops.core.protocols import ToolEnvironment
from agentevalops.core.schemas import (
    FINISH_TOOL_SPEC,
    RESERVED_TOOL_NAMES,
    AgentInput,
    EndState,
    EnvHandle,
    TaskSpec,
    ToolCallRecord,
    ToolResult,
    ToolSpec,
)
from agentevalops.core.types import TaskId


class _MinimalEnvironment:
    async def list_tools(self) -> Sequence[ToolSpec]:
        return ()

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(content="ok")

    async def snapshot(self) -> EndState:
        return {}

    def records(self) -> Sequence[ToolCallRecord]:
        return ()


def _task() -> TaskSpec:
    return TaskSpec(task_id=TaskId("t-1"), benchmark_id="toy", description="d")


def test_tool_spec_defaults_and_is_frozen() -> None:
    spec = ToolSpec(name="lookup", description="d", input_schema={"type": "object"})
    assert (spec.permission, spec.data_class) == ("read", "normal")
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.name = "other"  # type: ignore[misc]


def test_finish_is_reserved_and_has_its_own_spec() -> None:
    assert FINISH_TOOL_SPEC.name == "finish"
    assert "finish" in RESERVED_TOOL_NAMES
    assert FINISH_TOOL_SPEC.input_schema["required"] == ["answer"]


def test_agent_input_and_env_handle_default_to_no_environment() -> None:
    assert AgentInput(task=_task()).environment is None
    assert EnvHandle(task_id=TaskId("t-1")).tools is None


def test_agent_input_and_env_handle_carry_an_environment() -> None:
    env = _MinimalEnvironment()
    assert AgentInput(task=_task(), environment=env).environment is env
    assert EnvHandle(task_id=TaskId("t-1"), tools=env).tools is env


def test_tool_call_record_defaults() -> None:
    record = ToolCallRecord(
        task_id=TaskId("t-1"),
        index=0,
        tool_name="lookup",
        arguments={},
        permission="read",
        data_class="normal",
        executed=True,
        result="ok",
    )
    assert record.approval == "not_required"
    assert (record.repetition, record.fault, record.injections) == (0, None, ())
    assert record.timestamp.tzinfo is not None


def test_tool_environment_protocol_is_runtime_checkable() -> None:
    assert isinstance(_MinimalEnvironment(), ToolEnvironment)
    assert not isinstance(object(), ToolEnvironment)
