"""Unit tests for LangGraphRunner (ROADMAP W8).

All non-live tests inject a FAKE anthropic-style client, so they require no
network and no ``llm`` extra — the real import / client-construction path is
never hit.  The single ``@pytest.mark.live`` test is skipped unless
``ANTHROPIC_API_KEY`` is set and is never selected by the default pytest run.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

from agentevalops.agents.langgraph_runner import (
    DEFAULT_MODEL,
    LangGraphRunner,
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
    TerminationReason,
    TraceEvent,
    TraceEventKind,
)
from agentevalops.core.types import TaskId

# ---------------------------------------------------------------------------
# Fake anthropic-style client
# ---------------------------------------------------------------------------


class _FakeBlock:
    """Duck-typed content block (text or tool_use)."""

    def __init__(
        self,
        type: str,
        *,
        text: str = "",
        id: str = "",
        name: str = "",
        input: dict[str, Any] | None = None,
    ) -> None:
        self.type = type
        self.text = text
        self.id = id
        self.name = name
        self.input = input or {}


class _FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeResponse:
    def __init__(
        self,
        content: list[_FakeBlock],
        input_tokens: int = 10,
        output_tokens: int = 5,
        stop_reason: str = "end_turn",
    ) -> None:
        self.content = content
        self.usage = _FakeUsage(input_tokens, output_tokens)
        self.stop_reason = stop_reason


class _FakeMessages:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeClient:
    """Mimics ``anthropic.Anthropic`` closely enough for the runner."""

    def __init__(self, responses: list[Any]) -> None:
        self.messages = _FakeMessages(responses)


def _finish_response(answer: str, **usage: int) -> _FakeResponse:
    return _FakeResponse(
        [
            _FakeBlock(
                "tool_use",
                id="tool-finish-1",
                name="finish",
                input={"answer": answer},
            )
        ],
        stop_reason="tool_use",
        **usage,
    )


def _calc_then_finish(
    expression: str, answer: str
) -> list[_FakeResponse]:
    return [
        _FakeResponse(
            [
                _FakeBlock(
                    "tool_use",
                    id="tool-calc-1",
                    name="calculator",
                    input={"expression": expression},
                )
            ],
            input_tokens=12,
            output_tokens=8,
            stop_reason="tool_use",
        ),
        _finish_response(answer, input_tokens=6, output_tokens=4),
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


def _collect(runner: LangGraphRunner, inp: AgentInput, limits: ResourceLimits):
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
    in_per, out_per = _model_price_per_token("claude-haiku-4-5")
    assert in_per == pytest.approx(1.0 / 1_000_000)
    assert out_per == pytest.approx(5.0 / 1_000_000)
    # 1000 input + 1000 output tokens on Haiku = (1000*1 + 1000*5)/1e6.
    assert _cost_usd("claude-haiku-4-5", 1000, 1000) == pytest.approx(0.006)


def test_price_table_unknown_model_uses_fallback() -> None:
    assert _model_price_per_token("made-up-model") == _model_price_per_token(
        "claude-haiku-4-5"
    )


# ---------------------------------------------------------------------------
# Construction / identity
# ---------------------------------------------------------------------------


def test_default_model_and_agent_id() -> None:
    runner = LangGraphRunner(agent_factory=_factory([]))
    assert runner._model == DEFAULT_MODEL
    assert runner.agent_id == f"langgraph-claude:{DEFAULT_MODEL}"


def test_explicit_model_in_agent_id() -> None:
    runner = LangGraphRunner(
        model="claude-opus-4-8", agent_factory=_factory([])
    )
    assert runner.agent_id == "langgraph-claude:claude-opus-4-8"


def test_none_model_falls_back_to_default() -> None:
    runner = LangGraphRunner(model=None, agent_factory=_factory([]))
    assert runner._model == DEFAULT_MODEL


def test_protocol_conformance() -> None:
    runner = LangGraphRunner(agent_factory=_factory([]))
    assert isinstance(runner, AgentRunner)


# ---------------------------------------------------------------------------
# Happy path — direct finish
# ---------------------------------------------------------------------------


def test_happy_path_finish_tool() -> None:
    responses = [_finish_response("hello world", input_tokens=10, output_tokens=5)]
    runner = LangGraphRunner(agent_factory=_factory(responses))
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
    # 10 input + 5 output tokens accounted for.
    assert terminal.payload["total_tokens"] == 15
    assert terminal.payload["total_cost_usd"] == pytest.approx(
        _cost_usd(DEFAULT_MODEL, 10, 5)
    )
    assert terminal.payload["wall_seconds"] >= 0.0


def test_happy_path_tool_call_payload_key() -> None:
    responses = [_finish_response("hi")]
    runner = LangGraphRunner(agent_factory=_factory(responses))
    events = _collect(runner, _make_input(), ResourceLimits())
    tool_calls = [
        e for e in events if e.kind == TraceEventKind.AGENT_TOOL_CALL
    ]
    assert tool_calls[0].payload["tool_name"] == "finish"


def test_cost_tick_carries_deltas() -> None:
    responses = [_finish_response("hi", input_tokens=20, output_tokens=10)]
    runner = LangGraphRunner(agent_factory=_factory(responses))
    events = _collect(runner, _make_input(), ResourceLimits())
    tick = next(e for e in events if e.kind == TraceEventKind.COST_TICK)
    assert tick.tokens_delta == 30
    assert tick.cost_delta_usd == pytest.approx(_cost_usd(DEFAULT_MODEL, 20, 10))
    assert tick.payload["cumulative_tokens"] == 30


def test_run_id_propagated() -> None:
    responses = [_finish_response("hi")]
    runner = LangGraphRunner(agent_factory=_factory(responses))
    events = _collect(runner, _make_input(run_id="run-99"), ResourceLimits())
    for e in events:
        assert e.run_id == "run-99"


# ---------------------------------------------------------------------------
# Happy path — with a calculator tool step
# ---------------------------------------------------------------------------


def test_happy_path_with_calculator() -> None:
    responses = _calc_then_finish("40 + 2", "42")
    runner = LangGraphRunner(agent_factory=_factory(responses))
    events = _collect(
        runner, _make_input("Return the integer 42."), ResourceLimits()
    )
    kinds = [e.kind for e in events]
    # plan, tick, calc call, calc result, tick, finish call, finish result,
    # final answer, terminal
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
    calc_result = [
        e for e in events if e.kind == TraceEventKind.AGENT_TOOL_RESULT
    ][0]
    assert calc_result.payload["result"] == "42"
    assert events[-1].payload["final_answer"] == "42"
    # tokens from both model calls: (12+8) + (6+4) = 30.
    assert events[-1].payload["total_tokens"] == 30


def test_second_model_call_receives_tool_result_message() -> None:
    responses = _calc_then_finish("1 + 1", "2")
    client = _FakeClient(responses)
    runner = LangGraphRunner(agent_factory=lambda: client)
    _collect(runner, _make_input(), ResourceLimits())
    # The second create() call must include the assistant tool_use turn and the
    # user tool_result turn appended after the initial user message.
    second_call_messages = client.messages.calls[1]["messages"]
    roles = [m["role"] for m in second_call_messages]
    assert roles == ["user", "assistant", "user"]
    assert second_call_messages[-1]["content"][0]["type"] == "tool_result"


# ---------------------------------------------------------------------------
# Plain-text answer (no tool call)
# ---------------------------------------------------------------------------


def test_plain_text_answer_completes() -> None:
    responses = [
        _FakeResponse([_FakeBlock("text", text="hello world")])
    ]
    runner = LangGraphRunner(agent_factory=_factory(responses))
    events = _collect(runner, _make_input(), ResourceLimits())
    assert events[-1].payload["success"] is True
    assert events[-1].payload["final_answer"] == "hello world"
    assert any(
        e.kind == TraceEventKind.AGENT_FINAL_ANSWER for e in events
    )


def test_empty_text_no_tool_is_agent_error() -> None:
    responses = [_FakeResponse([_FakeBlock("text", text="")])]
    runner = LangGraphRunner(agent_factory=_factory(responses))
    events = _collect(runner, _make_input(), ResourceLimits())
    terminal = events[-1]
    assert terminal.payload["success"] is False
    assert terminal.payload["termination_reason"] == "agent_error"


# ---------------------------------------------------------------------------
# Wrong / empty answer -> AGENT_ERROR terminal (no raise)
# ---------------------------------------------------------------------------


def test_finish_with_empty_answer_is_agent_error() -> None:
    responses = [_finish_response("")]
    runner = LangGraphRunner(agent_factory=_factory(responses))
    events = _collect(runner, _make_input(), ResourceLimits())
    terminal = events[-1]
    assert terminal.kind == TraceEventKind.AGENT_TERMINAL
    assert terminal.payload["success"] is False
    assert terminal.payload["termination_reason"] == "agent_error"
    assert terminal.payload["final_answer"] is None


def test_wrong_answer_still_reports_completed_success() -> None:
    # A *wrong but non-empty* answer is an agent success at the runner level;
    # evaluation (not the runner) decides correctness.  This documents that.
    responses = [_finish_response("wrong answer")]
    runner = LangGraphRunner(agent_factory=_factory(responses))
    events = _collect(runner, _make_input(), ResourceLimits())
    assert events[-1].payload["success"] is True
    assert events[-1].payload["final_answer"] == "wrong answer"


def test_step_ceiling_exhausted_is_agent_error() -> None:
    # Model keeps calling the calculator, never finishes.
    calc = _FakeResponse(
        [
            _FakeBlock(
                "tool_use",
                id="c",
                name="calculator",
                input={"expression": "1 + 1"},
            )
        ],
        input_tokens=1,
        output_tokens=1,
        stop_reason="tool_use",
    )
    runner = LangGraphRunner(
        agent_factory=_factory([calc] * 3), max_steps=3
    )
    events = _collect(runner, _make_input(), ResourceLimits())
    terminal = events[-1]
    assert terminal.payload["success"] is False
    assert terminal.payload["termination_reason"] == "agent_error"


# ---------------------------------------------------------------------------
# Resource limit breaches
# ---------------------------------------------------------------------------


def test_limit_tokens_breach() -> None:
    responses = [_finish_response("hi", input_tokens=1000, output_tokens=1000)]
    runner = LangGraphRunner(agent_factory=_factory(responses))
    limits = ResourceLimits(max_tokens=100)
    events = _collect(runner, _make_input(), limits)
    terminal = events[-1]
    assert terminal.payload["termination_reason"] == "limit_tokens"
    assert terminal.payload["success"] is False


def test_limit_cost_breach() -> None:
    responses = [
        _finish_response("hi", input_tokens=1_000_000, output_tokens=1_000_000)
    ]
    runner = LangGraphRunner(agent_factory=_factory(responses))
    limits = ResourceLimits(max_tokens=10_000_000, max_cost_usd=0.001)
    events = _collect(runner, _make_input(), limits)
    terminal = events[-1]
    assert terminal.payload["termination_reason"] == "limit_cost"
    assert terminal.payload["success"] is False


def test_limit_time_breach() -> None:
    responses = [_finish_response("hi", input_tokens=1, output_tokens=1)]
    runner = LangGraphRunner(agent_factory=_factory(responses))
    # A zero wall-clock budget forces the elapsed-time check to trip.
    limits = ResourceLimits(
        max_tokens=10_000, max_cost_usd=10.0, max_wall_seconds=0.0
    )
    events = _collect(runner, _make_input(), limits)
    terminal = events[-1]
    assert terminal.payload["termination_reason"] == "limit_time"
    assert terminal.payload["success"] is False


# ---------------------------------------------------------------------------
# Adapter / block-parsing edge cases
# ---------------------------------------------------------------------------


def test_unknown_tool_name_yields_error_result() -> None:
    unknown = _FakeResponse(
        [_FakeBlock("tool_use", id="u", name="mystery", input={})],
        stop_reason="tool_use",
    )
    runner = LangGraphRunner(
        agent_factory=_factory([unknown, _finish_response("done")])
    )
    events = _collect(runner, _make_input(), ResourceLimits())
    results = [
        e for e in events if e.kind == TraceEventKind.AGENT_TOOL_RESULT
    ]
    assert results[0].payload["result"].startswith("error: unknown tool")
    assert events[-1].payload["final_answer"] == "done"


def test_text_alongside_tool_call_is_preserved() -> None:
    # A response that has BOTH a leading text block and a tool_use block; the
    # text is recorded on the assistant turn sent back to the model.
    mixed = _FakeResponse(
        [
            _FakeBlock("text", text="Let me compute."),
            _FakeBlock(
                "tool_use",
                id="c",
                name="calculator",
                input={"expression": "2 + 2"},
            ),
        ],
        stop_reason="tool_use",
    )
    client = _FakeClient([mixed, _finish_response("4")])
    runner = LangGraphRunner(agent_factory=lambda: client)
    events = _collect(runner, _make_input(), ResourceLimits())
    assistant_turn = client.messages.calls[1]["messages"][1]
    assert assistant_turn["content"][0] == {
        "type": "text",
        "text": "Let me compute.",
    }
    assert events[-1].payload["final_answer"] == "4"


def test_unknown_block_type_is_ignored() -> None:
    # A block whose type is neither 'text' nor 'tool_use' is skipped.
    weird = _FakeResponse(
        [
            _FakeBlock("thinking"),
            _FakeBlock(
                "tool_use",
                id="f",
                name="finish",
                input={"answer": "ok"},
            ),
        ],
        stop_reason="tool_use",
    )
    runner = LangGraphRunner(agent_factory=_factory([weird]))
    events = _collect(runner, _make_input(), ResourceLimits())
    assert events[-1].payload["final_answer"] == "ok"


# ---------------------------------------------------------------------------
# Infrastructure / configuration errors
# ---------------------------------------------------------------------------


def test_configuration_error_from_client_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A client whose create() raises ConfigurationError must propagate as-is
    # (not be re-wrapped as InfrastructureError).
    class _RaisingClient:
        def __init__(self) -> None:
            self.messages = self

        def create(self, **kwargs: Any) -> Any:
            raise ConfigurationError("bad config mid-run")

    runner = LangGraphRunner(agent_factory=lambda: _RaisingClient())

    async def _run() -> None:
        trace_iter = await runner.run(_make_input(), ResourceLimits())
        async for _ in trace_iter:
            pass

    with pytest.raises(ConfigurationError):
        asyncio.run(_run())


def test_model_api_error_raises_infrastructure_error() -> None:
    responses = [RuntimeError("500 server error")]
    runner = LangGraphRunner(agent_factory=_factory(responses))

    async def _run() -> None:
        trace_iter = await runner.run(_make_input(), ResourceLimits())
        async for _ in trace_iter:
            pass

    with pytest.raises(InfrastructureError):
        asyncio.run(_run())


def test_missing_api_key_raises_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # No agent_factory -> real client path -> missing key -> ConfigurationError.
    runner = LangGraphRunner()

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

    # Smoke scenario has two tasks (expect 'hello world' and '42'); the fake
    # client returns the correct finish answer regardless, keyed off metadata.
    class _MetadataAwareClient:
        """Returns a finish tool call echoing the task's expected_output."""

        def __init__(self) -> None:
            self.messages = self

        def create(self, **kwargs: Any) -> Any:
            text = kwargs["messages"][0]["content"]
            answer = "hello world" if "hello world" in text else "42"
            return _finish_response(answer)

    run_config = RunConfig(
        run_id=RunId("orch-run"),
        agent_id=AgentId("langgraph-claude:test"),
        backend_id=BackendId("local"),
    )
    orch = LocalOrchestrator(
        run_config=run_config,
        benchmark=ToyBenchmarkAdapter(scenario="smoke"),
        runner=LangGraphRunner(agent_factory=lambda: _MetadataAwareClient()),
        trace_store=InMemoryTraceStore(),
        evaluator=DeterministicEvaluator(),
        policy_checker=BasicPolicyChecker(),
    )
    summary = asyncio.run(orch.run())
    assert summary.total_tasks == 2
    assert summary.passed_tasks == 2
    assert summary.failed_tasks == 0
    assert summary.total_tokens > 0


# ---------------------------------------------------------------------------
# Opt-in live test (skipped unless ANTHROPIC_API_KEY is set; never in CI)
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="requires ANTHROPIC_API_KEY and the 'llm' extra",
)
def test_live_real_model_completes() -> None:  # pragma: no cover - live path
    runner = LangGraphRunner()
    inp = _make_input("Return the string 'hello world'.")
    events = _collect(runner, inp, ResourceLimits(max_tokens=5000))
    terminal = events[-1]
    assert terminal.kind == TraceEventKind.AGENT_TERMINAL
    assert terminal.payload["success"] is True
    assert TerminationReason(terminal.payload["termination_reason"]) in (
        TerminationReason.COMPLETED,
    )
    assert "hello world" in str(terminal.payload["final_answer"]).lower()
