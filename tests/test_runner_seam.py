"""The runner tool seam (docs/adr/0006 D1, D9; R23 slice 3).

Uses the fake clients the runner tests share through ``fake_clients.py``
(R24 slice 6; before that it imported them from the runner test files).
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import fake_clients as fakes
import pytest

from agentevalops.agents import langgraph_runner, openai_runner
from agentevalops.agents.langgraph_runner import LangGraphRunner
from agentevalops.agents.mock_runner import MockAgentRunner
from agentevalops.agents.openai_runner import OpenAIRunner
from agentevalops.core.schemas import (
    FINISH_TOOL_SPEC,
    EnvHandle,
    ResourceLimits,
    RunConfig,
    TaskSpec,
    ToolSpec,
    TraceEventKind,
)
from agentevalops.core.types import BACKEND_LOCAL, AgentId, RunId, TaskId
from agentevalops.environments import (
    CALCULATOR_TOOL_SPEC,
    InProcessToolEnvironment,
    toy_environment,
)
from agentevalops.evaluators.deterministic import DeterministicEvaluator
from agentevalops.orchestration.local import LocalOrchestrator
from agentevalops.policy.basic_checker import BasicPolicyChecker
from agentevalops.stores.memory import InMemoryTraceStore

_GOLDEN = Path(__file__).parent / "fixtures" / "tool_payloads"


def _golden(name: str) -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = json.loads((_GOLDEN / name).read_text())
    return data


def _same(sent: Any, expected: Any) -> bool:
    """Equal, and serialised identically (key order included), so the request
    body a provider SDK builds from it is byte-identical."""
    return bool(sent == expected and json.dumps(sent) == json.dumps(expected))


def _toy_payload(convert: Any) -> list[dict[str, Any]]:
    return [convert(s) for s in (CALCULATOR_TOOL_SPEC, FINISH_TOOL_SPEC)]


# ---------------------------------------------------------------------------
# Golden payloads: today's _TOOLS, pinned before the refactor
# ---------------------------------------------------------------------------


def test_openai_conversion_reproduces_the_pinned_payload() -> None:
    expected = _golden("openai.json")
    assert _same(_toy_payload(openai_runner._to_provider_tool), expected)


def test_anthropic_conversion_reproduces_the_pinned_payload() -> None:
    expected = _golden("anthropic.json")
    assert _same(_toy_payload(langgraph_runner._to_provider_tool), expected)


@pytest.mark.parametrize("with_env", [False, True])
def test_openai_runner_sends_the_pinned_payload(with_env: bool) -> None:
    client = fakes.OpenAIFakeClient([fakes.openai_finish_response("x")])
    runner = OpenAIRunner(agent_factory=lambda: client)
    inp = fakes.make_input()
    if with_env:
        inp.environment = toy_environment(TaskId("t-1"))
    fakes.collect(runner, inp, ResourceLimits())
    sent = client.chat.completions.calls[0]["tools"]
    assert _same(sent, _golden("openai.json"))


@pytest.mark.parametrize("with_env", [False, True])
def test_langgraph_runner_sends_the_pinned_payload(with_env: bool) -> None:
    client = fakes.AnthropicFakeClient([fakes.anthropic_finish_response("x")])
    runner = LangGraphRunner(agent_factory=lambda: client)
    inp = fakes.make_input()
    if with_env:
        inp.environment = toy_environment(TaskId("t-1"))
    fakes.collect(runner, inp, ResourceLimits())
    sent = client.messages.calls[0]["tools"]
    assert _same(sent, _golden("anthropic.json"))


# ---------------------------------------------------------------------------
# Specs come from the environment; calls go to it and are recorded there
# ---------------------------------------------------------------------------


async def _lookup(arguments: dict[str, Any]) -> str:
    return f"case {arguments.get('case_id')}: open"


def _lookup_env() -> InProcessToolEnvironment:
    spec = ToolSpec(
        name="case_lookup",
        description="Look up a case.",
        input_schema={"type": "object", "properties": {"case_id": {"type": "string"}}},
    )
    return InProcessToolEnvironment(TaskId("t-1"), (spec,), {"case_lookup": _lookup})


def test_openai_runner_uses_the_environments_tools_and_records() -> None:
    env = _lookup_env()
    call = fakes.OpenAIFakeMessage(
        tool_calls=[fakes.OpenAIFakeToolCall("c1", "case_lookup", '{"case_id": "A7"}')]
    )
    client = fakes.OpenAIFakeClient(
        [
            fakes.OpenAIFakeResponse(call, finish_reason="tool_calls"),
            fakes.openai_finish_response("ok"),
        ]
    )
    inp = fakes.make_input()
    inp.environment = env
    runner = OpenAIRunner(agent_factory=lambda: client)
    events = fakes.collect(runner, inp, ResourceLimits())

    sent = client.chat.completions.calls[0]["tools"]
    assert [t["function"]["name"] for t in sent] == ["case_lookup", "finish"]
    (record,) = env.records()  # finish is the runner's, never recorded
    assert (record.tool_name, record.arguments) == ("case_lookup", {"case_id": "A7"})
    results = [e for e in events if e.kind == TraceEventKind.AGENT_TOOL_RESULT]
    assert results[0].payload["result"] == "case A7: open"


def test_langgraph_runner_uses_the_environments_tools_and_records() -> None:
    env = _lookup_env()
    call = fakes.AnthropicFakeResponse(
        [
            fakes.AnthropicFakeBlock(
                "tool_use", id="c1", name="case_lookup", input={"case_id": "A7"}
            )
        ],
        stop_reason="tool_use",
    )
    client = fakes.AnthropicFakeClient([call, fakes.anthropic_finish_response("ok")])
    inp = fakes.make_input()
    inp.environment = env
    events = fakes.collect(
        LangGraphRunner(agent_factory=lambda: client), inp, ResourceLimits()
    )

    sent = client.messages.calls[0]["tools"]
    assert [t["name"] for t in sent] == ["case_lookup", "finish"]
    (record,) = env.records()
    assert (record.tool_name, record.arguments) == ("case_lookup", {"case_id": "A7"})
    results = [e for e in events if e.kind == TraceEventKind.AGENT_TOOL_RESULT]
    assert results[0].payload["result"] == "case A7: open"


def test_fallback_to_the_toy_environment_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = fakes.OpenAIFakeClient([fakes.openai_finish_response("x")])
    with caplog.at_level(logging.WARNING):
        fakes.collect(
            OpenAIRunner(agent_factory=lambda: client),
            fakes.make_input(),
            ResourceLimits(),
        )
    assert any("no tool environment" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# D1: a benchmark that hands back no environment is a wiring bug
# ---------------------------------------------------------------------------


class _NoToolsBenchmark:
    benchmark_id = "no-tools"
    benchmark_version = "0"

    def __init__(self) -> None:
        self.torn_down: list[str] = []

    def list_tasks(self, filter_spec=None):  # type: ignore[no-untyped-def]
        return [
            TaskSpec(task_id=TaskId("t-1"), benchmark_id="no-tools", description="d")
        ]

    def grade(self, task, output):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def prepare_environment(self, task):  # type: ignore[no-untyped-def]
        return EnvHandle(task_id=task.task_id)  # no tools: the wiring bug

    def teardown(self, handle):  # type: ignore[no-untyped-def]
        self.torn_down.append(str(handle.task_id))


def test_handle_without_an_environment_is_not_evaluated_and_torn_down() -> None:
    """Fail-first: with the orchestrator's check removed, the mock runner
    would run the task and it would pass, hiding the bug."""
    benchmark = _NoToolsBenchmark()
    orch = LocalOrchestrator(
        run_config=RunConfig(
            run_id=RunId("seam"), agent_id=AgentId("mock"), backend_id=BACKEND_LOCAL
        ),
        benchmark=benchmark,  # type: ignore[arg-type]
        runner=MockAgentRunner(),
        trace_store=InMemoryTraceStore(),
        evaluator=DeterministicEvaluator(),
        policy_checker=BasicPolicyChecker(),
    )
    summary = asyncio.run(orch.run())
    (result,) = summary.task_results
    assert summary.not_evaluated_tasks == 1
    assert result.evaluation.evaluated is False
    assert "no tool environment" in (result.output.error or "")
    assert benchmark.torn_down == ["t-1"]
