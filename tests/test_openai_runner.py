"""Unit tests for OpenAIRunner (docs/adr/0001-openai-runner-chat-completions.md).

All tests inject a FAKE openai-style client, so they require no network and
no ``llm`` extra — the real import / client-construction path is never hit.
Unlike ``test_langgraph_runner.py`` there is no opt-in ``@pytest.mark.live``
test here: a live run is explicitly out of scope for this session (see the
ADR and the report that accompanies this change).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agentevalops.agents.openai_runner import (
    DEFAULT_MODEL,
    OpenAIRunner,
    _cost_usd,
    _model_price_per_token,
    _run_calculator,
)
from agentevalops.core.errors import ConfigurationError, InfrastructureError
from agentevalops.core.protocols import AgentRunner
from agentevalops.core.schemas import (
    AgentInput,
    ResourceLimits,
    TaskSpec,
    TraceEvent,
    TraceEventKind,
)
from agentevalops.core.types import TaskId

# ---------------------------------------------------------------------------
# Fake openai-style client
# ---------------------------------------------------------------------------


class _FakeFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, id: str, name: str, arguments: str) -> None:
        self.id = id
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(
        self,
        content: str | None = None,
        tool_calls: list[_FakeToolCall] | None = None,
    ) -> None:
        self.content = content
        self.tool_calls = tool_calls or []


class _FakeChoice:
    def __init__(self, message: _FakeMessage, finish_reason: str = "stop") -> None:
        self.message = message
        self.finish_reason = finish_reason


class _FakeUsage:
    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _FakeResponse:
    def __init__(
        self,
        message: _FakeMessage,
        prompt_tokens: int = 10,
        completion_tokens: int = 5,
        finish_reason: str = "stop",
    ) -> None:
        self.choices = [_FakeChoice(message, finish_reason)]
        self.usage = _FakeUsage(prompt_tokens, completion_tokens)


class _FakeCompletions:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeChat:
    def __init__(self, responses: list[Any]) -> None:
        self.completions = _FakeCompletions(responses)


class _FakeClient:
    """Mimics ``openai.OpenAI`` closely enough for the runner."""

    def __init__(self, responses: list[Any]) -> None:
        self.chat = _FakeChat(responses)


def _finish_response(answer: str, **usage: int) -> _FakeResponse:
    msg = _FakeMessage(
        content=None,
        tool_calls=[
            _FakeToolCall("tool-finish-1", "finish", json.dumps({"answer": answer}))
        ],
    )
    return _FakeResponse(msg, finish_reason="tool_calls", **usage)


def _calc_then_finish(expression: str, answer: str) -> list[_FakeResponse]:
    calc_msg = _FakeMessage(
        content=None,
        tool_calls=[
            _FakeToolCall(
                "tool-calc-1", "calculator", json.dumps({"expression": expression})
            )
        ],
    )
    return [
        _FakeResponse(
            calc_msg, prompt_tokens=12, completion_tokens=8, finish_reason="tool_calls"
        ),
        _finish_response(answer, prompt_tokens=6, completion_tokens=4),
    ]


def _make_input(
    description: str = "Return the string 'hello world'.",
    run_id: str = "test-run",
    metadata: dict[str, Any] | None = None,
) -> AgentInput:
    task = TaskSpec(
        task_id=TaskId("t-1"),
        benchmark_id="toy",
        description=description,
        metadata=metadata or {},
    )
    return AgentInput(task=task, context={"run_id": run_id})


def _collect(runner: OpenAIRunner, inp: AgentInput, limits: ResourceLimits):
    async def _run() -> list[TraceEvent]:
        trace_iter = await runner.run(inp, limits)
        return [e async for e in trace_iter]

    return asyncio.run(_run())


def _factory(responses: list[Any]):
    return lambda: _FakeClient(responses)


# ---------------------------------------------------------------------------
# Pure-function unit tests
# ---------------------------------------------------------------------------


def test_calculator_basic() -> None:
    assert _run_calculator("2 + 2") == "4"
    assert _run_calculator("(3 * 4) - 1") == "11"


def test_calculator_rejects_unsupported_characters() -> None:
    assert _run_calculator("__import__('os')").startswith("error:")
    assert _run_calculator("").startswith("error:")


def test_calculator_reports_arithmetic_error() -> None:
    assert _run_calculator("1 / 0").startswith("error:")


def test_price_table_and_cost() -> None:
    # Hand-computed from the price table: gpt-4.1-mini-2025-04-14 is
    # $0.40 / 1M input, $1.60 / 1M output (developers.openai.com/api/docs/
    # pricing, checked 2026-08-03). A per-1M figure pasted directly into this
    # per-token table (a 10**6 error) would make this assertion fail loudly.
    in_per, out_per = _model_price_per_token(DEFAULT_MODEL)
    assert in_per == pytest.approx(0.40 / 1_000_000)
    assert out_per == pytest.approx(1.60 / 1_000_000)
    # 1000 input + 1000 output tokens: (1000*0.40 + 1000*1.60) / 1e6 = 0.002.
    assert _cost_usd(DEFAULT_MODEL, 1000, 1000) == pytest.approx(0.002)


def test_price_table_unknown_model_uses_fallback() -> None:
    assert _model_price_per_token("made-up-model") == _model_price_per_token(
        DEFAULT_MODEL
    )


# ---------------------------------------------------------------------------
# Construction / identity
# ---------------------------------------------------------------------------


def test_default_model_and_agent_id() -> None:
    runner = OpenAIRunner(agent_factory=_factory([]))
    assert runner._model_id == DEFAULT_MODEL
    assert runner.agent_id == f"openai-chat:{DEFAULT_MODEL}"


def test_explicit_model_in_agent_id() -> None:
    runner = OpenAIRunner(model="gpt-4o-mini", agent_factory=_factory([]))
    assert runner.agent_id == "openai-chat:gpt-4o-mini"


def test_none_model_falls_back_to_default() -> None:
    runner = OpenAIRunner(model=None, agent_factory=_factory([]))
    assert runner._model_id == DEFAULT_MODEL


def test_openai_model_env_var_has_no_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # OPENAI_MODEL was removed as a fallback source entirely 2026-08-04
    # (docs/adr/0001 §3, revised): a second, environment-resolved path to
    # the model could silently diverge from what the run config says --
    # exactly the reproducibility failure the ADR already argued against.
    # The only paths left are the explicit `model` argument and
    # DEFAULT_MODEL; setting OPENAI_MODEL must do nothing at all.
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    runner = OpenAIRunner(agent_factory=_factory([]))
    assert runner._model_id == DEFAULT_MODEL
    assert runner.agent_id == f"openai-chat:{DEFAULT_MODEL}"


def test_protocol_conformance() -> None:
    runner = OpenAIRunner(agent_factory=_factory([]))
    assert isinstance(runner, AgentRunner)


# ---------------------------------------------------------------------------
# Happy path — direct finish
# ---------------------------------------------------------------------------


def test_happy_path_finish_tool() -> None:
    responses = [_finish_response("hello world", prompt_tokens=10, completion_tokens=5)]
    runner = OpenAIRunner(agent_factory=_factory(responses))
    events = _collect(runner, _make_input(), ResourceLimits())

    kinds = [e.kind for e in events]
    assert kinds == [
        TraceEventKind.AGENT_PLAN,
        TraceEventKind.COST_TICK,
        TraceEventKind.AGENT_TOOL_CALL,
        TraceEventKind.AGENT_TOOL_RESULT,
        TraceEventKind.AGENT_FINAL_ANSWER,
        TraceEventKind.AGENT_TERMINAL,
    ]
    terminal = events[-1]
    assert terminal.payload["success"] is True
    assert terminal.payload["termination_reason"] == "completed"
    assert terminal.payload["final_answer"] == "hello world"
    assert terminal.payload["total_tokens"] == 15
    assert terminal.payload["total_cost_usd"] == pytest.approx(
        _cost_usd(DEFAULT_MODEL, 10, 5)
    )
    assert terminal.payload["wall_seconds"] >= 0.0


def test_happy_path_tool_call_payload_key() -> None:
    responses = [_finish_response("hi")]
    runner = OpenAIRunner(agent_factory=_factory(responses))
    events = _collect(runner, _make_input(), ResourceLimits())
    tool_calls = [e for e in events if e.kind == TraceEventKind.AGENT_TOOL_CALL]
    assert tool_calls[0].payload["tool_name"] == "finish"
    assert tool_calls[0].payload["args"] == {"answer": "hi"}


def test_cost_tick_carries_deltas() -> None:
    responses = [_finish_response("hi", prompt_tokens=20, completion_tokens=10)]
    runner = OpenAIRunner(agent_factory=_factory(responses))
    events = _collect(runner, _make_input(), ResourceLimits())
    tick = next(e for e in events if e.kind == TraceEventKind.COST_TICK)
    assert tick.tokens_delta == 30
    assert tick.cost_delta_usd == pytest.approx(_cost_usd(DEFAULT_MODEL, 20, 10))
    assert tick.payload["cumulative_tokens"] == 30


def test_run_id_propagated() -> None:
    responses = [_finish_response("hi")]
    runner = OpenAIRunner(agent_factory=_factory(responses))
    events = _collect(runner, _make_input(run_id="run-99"), ResourceLimits())
    for e in events:
        assert e.run_id == "run-99"


def test_system_prompt_is_first_message() -> None:
    responses = [_finish_response("hi")]
    client = _FakeClient(responses)
    runner = OpenAIRunner(agent_factory=lambda: client)
    _collect(runner, _make_input(), ResourceLimits())
    first_call_messages = client.chat.completions.calls[0]["messages"]
    assert first_call_messages[0]["role"] == "system"
    assert first_call_messages[1]["role"] == "user"


# ---------------------------------------------------------------------------
# Happy path — with a calculator tool step
# ---------------------------------------------------------------------------


def test_happy_path_with_calculator() -> None:
    responses = _calc_then_finish("40 + 2", "42")
    runner = OpenAIRunner(agent_factory=_factory(responses))
    events = _collect(runner, _make_input("Return the integer 42."), ResourceLimits())
    kinds = [e.kind for e in events]
    assert kinds == [
        TraceEventKind.AGENT_PLAN,
        TraceEventKind.COST_TICK,
        TraceEventKind.AGENT_TOOL_CALL,
        TraceEventKind.AGENT_TOOL_RESULT,
        TraceEventKind.COST_TICK,
        TraceEventKind.AGENT_TOOL_CALL,
        TraceEventKind.AGENT_TOOL_RESULT,
        TraceEventKind.AGENT_FINAL_ANSWER,
        TraceEventKind.AGENT_TERMINAL,
    ]
    calc_result = [e for e in events if e.kind == TraceEventKind.AGENT_TOOL_RESULT][0]
    assert calc_result.payload["result"] == "42"
    assert events[-1].payload["final_answer"] == "42"
    assert events[-1].payload["total_tokens"] == 30


def test_second_model_call_receives_tool_result_message() -> None:
    responses = _calc_then_finish("1 + 1", "2")
    client = _FakeClient(responses)
    runner = OpenAIRunner(agent_factory=lambda: client)
    _collect(runner, _make_input(), ResourceLimits())
    second_call_messages = client.chat.completions.calls[1]["messages"]
    roles = [m["role"] for m in second_call_messages]
    # system, user, assistant (tool_calls), tool.
    assert roles == ["system", "user", "assistant", "tool"]
    assert second_call_messages[-1]["role"] == "tool"
    assert second_call_messages[-1]["tool_call_id"] == "tool-calc-1"
    assert second_call_messages[2]["tool_calls"][0]["function"]["name"] == "calculator"


def test_multi_tool_call_in_one_message_gets_one_reply_each() -> None:
    # The model emits calculator AND finish in the same turn. Every tool_call
    # id must get its own role:"tool" reply before the (never-made, here)
    # next request would be accepted -- confirming OpenAIRunner matches
    # LangGraphRunner's existing per-block iteration behaviour.
    msg = _FakeMessage(
        content=None,
        tool_calls=[
            _FakeToolCall(
                "call-calc", "calculator", json.dumps({"expression": "2 + 2"})
            ),
            _FakeToolCall("call-finish", "finish", json.dumps({"answer": "4"})),
        ],
    )
    response = _FakeResponse(msg, finish_reason="tool_calls")
    runner = OpenAIRunner(agent_factory=_factory([response]))
    events = _collect(runner, _make_input(), ResourceLimits())

    tool_calls = [e for e in events if e.kind == TraceEventKind.AGENT_TOOL_CALL]
    tool_results = [e for e in events if e.kind == TraceEventKind.AGENT_TOOL_RESULT]
    assert [t.payload["tool_name"] for t in tool_calls] == ["calculator", "finish"]
    assert len(tool_results) == 2
    assert tool_results[0].payload["result"] == "4"
    assert events[-1].payload["final_answer"] == "4"


# ---------------------------------------------------------------------------
# Malformed tool-argument JSON
# ---------------------------------------------------------------------------


def test_malformed_tool_arguments_json_is_handled_gracefully() -> None:
    bad_msg = _FakeMessage(
        content=None,
        tool_calls=[_FakeToolCall("bad-1", "calculator", "{not valid json")],
    )
    bad_response = _FakeResponse(bad_msg, finish_reason="tool_calls")
    runner = OpenAIRunner(
        agent_factory=_factory([bad_response, _finish_response("recovered")])
    )
    events = _collect(runner, _make_input(), ResourceLimits())

    tool_call = next(e for e in events if e.kind == TraceEventKind.AGENT_TOOL_CALL)
    assert tool_call.payload["args"] == {}
    tool_result = next(e for e in events if e.kind == TraceEventKind.AGENT_TOOL_RESULT)
    assert tool_result.payload["result"].startswith(
        "error: malformed tool arguments JSON"
    )
    # The run does not raise or terminate on malformed JSON -- the model gets
    # the error back and the loop continues to a normal completion.
    assert events[-1].payload["success"] is True
    assert events[-1].payload["final_answer"] == "recovered"


def test_malformed_tool_arguments_non_object_json_is_handled() -> None:
    # Valid JSON, but not an object (e.g. a bare string) -- also malformed
    # from the tool's point of view, and must not raise.
    bad_msg = _FakeMessage(
        content=None,
        tool_calls=[_FakeToolCall("bad-2", "calculator", json.dumps("2 + 2"))],
    )
    bad_response = _FakeResponse(bad_msg, finish_reason="tool_calls")
    runner = OpenAIRunner(
        agent_factory=_factory([bad_response, _finish_response("recovered")])
    )
    events = _collect(runner, _make_input(), ResourceLimits())
    tool_result = next(e for e in events if e.kind == TraceEventKind.AGENT_TOOL_RESULT)
    assert tool_result.payload["result"].startswith(
        "error: malformed tool arguments JSON"
    )


# ---------------------------------------------------------------------------
# Plain-text answer (no tool call)
# ---------------------------------------------------------------------------


def test_plain_text_answer_completes() -> None:
    responses = [_FakeResponse(_FakeMessage(content="hello world"))]
    runner = OpenAIRunner(agent_factory=_factory(responses))
    events = _collect(runner, _make_input(), ResourceLimits())
    assert events[-1].payload["success"] is True
    assert events[-1].payload["final_answer"] == "hello world"
    assert any(e.kind == TraceEventKind.AGENT_FINAL_ANSWER for e in events)


def test_empty_text_no_tool_is_agent_error() -> None:
    responses = [_FakeResponse(_FakeMessage(content=""))]
    runner = OpenAIRunner(agent_factory=_factory(responses))
    events = _collect(runner, _make_input(), ResourceLimits())
    terminal = events[-1]
    assert terminal.payload["success"] is False
    assert terminal.payload["termination_reason"] == "agent_error"


def test_none_content_no_tool_is_agent_error() -> None:
    # OpenAI represents "no text" as content=None (not ""); must be handled
    # the same as an empty string.
    responses = [_FakeResponse(_FakeMessage(content=None))]
    runner = OpenAIRunner(agent_factory=_factory(responses))
    events = _collect(runner, _make_input(), ResourceLimits())
    terminal = events[-1]
    assert terminal.payload["success"] is False
    assert terminal.payload["termination_reason"] == "agent_error"


# ---------------------------------------------------------------------------
# Wrong / empty answer -> AGENT_ERROR terminal (no raise)
# ---------------------------------------------------------------------------


def test_finish_with_empty_answer_is_agent_error() -> None:
    responses = [_finish_response("")]
    runner = OpenAIRunner(agent_factory=_factory(responses))
    events = _collect(runner, _make_input(), ResourceLimits())
    terminal = events[-1]
    assert terminal.kind == TraceEventKind.AGENT_TERMINAL
    assert terminal.payload["success"] is False
    assert terminal.payload["termination_reason"] == "agent_error"
    assert terminal.payload["final_answer"] is None


def test_wrong_answer_still_reports_completed_success() -> None:
    responses = [_finish_response("wrong answer")]
    runner = OpenAIRunner(agent_factory=_factory(responses))
    events = _collect(runner, _make_input(), ResourceLimits())
    assert events[-1].payload["success"] is True
    assert events[-1].payload["final_answer"] == "wrong answer"


def test_step_ceiling_exhausted_is_agent_error() -> None:
    calc_msg = _FakeMessage(
        content=None,
        tool_calls=[
            _FakeToolCall("c", "calculator", json.dumps({"expression": "1 + 1"}))
        ],
    )
    calc = _FakeResponse(
        calc_msg, prompt_tokens=1, completion_tokens=1, finish_reason="tool_calls"
    )
    runner = OpenAIRunner(agent_factory=_factory([calc] * 3), max_steps=3)
    events = _collect(runner, _make_input(), ResourceLimits())
    terminal = events[-1]
    assert terminal.payload["success"] is False
    assert terminal.payload["termination_reason"] == "agent_error"


# ---------------------------------------------------------------------------
# Resource limit breaches
# ---------------------------------------------------------------------------


def test_limit_tokens_breach() -> None:
    responses = [_finish_response("hi", prompt_tokens=1000, completion_tokens=1000)]
    runner = OpenAIRunner(agent_factory=_factory(responses))
    limits = ResourceLimits(max_tokens=100)
    events = _collect(runner, _make_input(), limits)
    terminal = events[-1]
    assert terminal.payload["termination_reason"] == "limit_tokens"
    assert terminal.payload["success"] is False


def test_limit_cost_breach() -> None:
    responses = [
        _finish_response("hi", prompt_tokens=1_000_000, completion_tokens=1_000_000)
    ]
    runner = OpenAIRunner(agent_factory=_factory(responses))
    limits = ResourceLimits(max_tokens=10_000_000, max_cost_usd=0.001)
    events = _collect(runner, _make_input(), limits)
    terminal = events[-1]
    assert terminal.payload["termination_reason"] == "limit_cost"
    assert terminal.payload["success"] is False


def test_limit_time_breach() -> None:
    responses = [_finish_response("hi", prompt_tokens=1, completion_tokens=1)]
    runner = OpenAIRunner(agent_factory=_factory(responses))
    limits = ResourceLimits(max_tokens=10_000, max_cost_usd=10.0, max_wall_seconds=0.0)
    events = _collect(runner, _make_input(), limits)
    terminal = events[-1]
    assert terminal.payload["termination_reason"] == "limit_time"
    assert terminal.payload["success"] is False


# ---------------------------------------------------------------------------
# Adapter / block-parsing edge cases
# ---------------------------------------------------------------------------


def test_unknown_tool_name_yields_error_result() -> None:
    unknown_msg = _FakeMessage(
        content=None,
        tool_calls=[_FakeToolCall("u", "mystery", json.dumps({}))],
    )
    unknown = _FakeResponse(unknown_msg, finish_reason="tool_calls")
    runner = OpenAIRunner(agent_factory=_factory([unknown, _finish_response("done")]))
    events = _collect(runner, _make_input(), ResourceLimits())
    results = [e for e in events if e.kind == TraceEventKind.AGENT_TOOL_RESULT]
    assert results[0].payload["result"].startswith("error: unknown tool")
    assert events[-1].payload["final_answer"] == "done"


def test_text_alongside_tool_call_is_preserved() -> None:
    mixed_msg = _FakeMessage(
        content="Let me compute.",
        tool_calls=[
            _FakeToolCall("c", "calculator", json.dumps({"expression": "2 + 2"}))
        ],
    )
    mixed = _FakeResponse(mixed_msg, finish_reason="tool_calls")
    client = _FakeClient([mixed, _finish_response("4")])
    runner = OpenAIRunner(agent_factory=lambda: client)
    events = _collect(runner, _make_input(), ResourceLimits())
    assistant_turn = client.chat.completions.calls[1]["messages"][2]
    assert assistant_turn["content"] == "Let me compute."
    assert events[-1].payload["final_answer"] == "4"


# ---------------------------------------------------------------------------
# Infrastructure / configuration errors
# ---------------------------------------------------------------------------


def test_configuration_error_from_client_propagates() -> None:
    class _RaisingCompletions:
        def create(self, **kwargs: Any) -> Any:
            raise ConfigurationError("bad config mid-run")

    class _RaisingChat:
        def __init__(self) -> None:
            self.completions = _RaisingCompletions()

    class _RaisingClient:
        def __init__(self) -> None:
            self.chat = _RaisingChat()

    runner = OpenAIRunner(agent_factory=lambda: _RaisingClient())

    async def _run() -> None:
        trace_iter = await runner.run(_make_input(), ResourceLimits())
        async for _ in trace_iter:
            pass

    with pytest.raises(ConfigurationError):
        asyncio.run(_run())


def test_model_api_error_raises_infrastructure_error() -> None:
    responses = [RuntimeError("500 server error")]
    runner = OpenAIRunner(agent_factory=_factory(responses))

    async def _run() -> None:
        trace_iter = await runner.run(_make_input(), ResourceLimits())
        async for _ in trace_iter:
            pass

    with pytest.raises(InfrastructureError):
        asyncio.run(_run())


def test_missing_api_key_raises_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runner = OpenAIRunner()

    async def _run() -> None:
        await runner.run(_make_input(), ResourceLimits())

    with pytest.raises(ConfigurationError):
        asyncio.run(_run())


# ---------------------------------------------------------------------------
# End-to-end through the LocalOrchestrator
# ---------------------------------------------------------------------------


def test_runs_through_local_orchestrator() -> None:
    from agentevalops.benchmarks.toy.adapter import ToyBenchmarkAdapter
    from agentevalops.core.schemas import RunConfig
    from agentevalops.core.types import AgentId, BackendId, RunId
    from agentevalops.evaluators.deterministic import DeterministicEvaluator
    from agentevalops.orchestration.local import LocalOrchestrator
    from agentevalops.policy.basic_checker import BasicPolicyChecker
    from agentevalops.stores.memory import InMemoryTraceStore

    class _MetadataAwareCompletions:
        def create(self, **kwargs: Any) -> Any:
            text = kwargs["messages"][1]["content"]
            answer = "hello world" if "hello world" in text else "42"
            return _finish_response(answer)

    class _MetadataAwareChat:
        def __init__(self) -> None:
            self.completions = _MetadataAwareCompletions()

    class _MetadataAwareClient:
        def __init__(self) -> None:
            self.chat = _MetadataAwareChat()

    run_config = RunConfig(
        run_id=RunId("orch-run"),
        agent_id=AgentId("openai-chat:test"),
        backend_id=BackendId("local"),
    )
    orch = LocalOrchestrator(
        run_config=run_config,
        benchmark=ToyBenchmarkAdapter(scenario="smoke"),
        runner=OpenAIRunner(agent_factory=lambda: _MetadataAwareClient()),
        trace_store=InMemoryTraceStore(),
        evaluator=DeterministicEvaluator(),
        policy_checker=BasicPolicyChecker(),
    )
    summary = asyncio.run(orch.run())
    assert summary.total_tasks == 2
    assert summary.passed_tasks == 2
    assert summary.failed_tasks == 0
    assert summary.total_tokens > 0
