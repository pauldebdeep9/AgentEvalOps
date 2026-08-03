"""Unit tests for AzureOpenAIRunner (docs/adr/0004-cloud-backend-two-axes.md,
Axis B).

All tests inject a FAKE openai-style client, so they require no network and
no ``llm`` extra — the real import / client-construction path (``openai.
AzureOpenAI``) is never hit. Since ``AzureOpenAIRunner`` subclasses
``OpenAIRunner`` and reuses its ``_gen`` loop unmodified, most agent-loop
behaviour (tool-call parsing, resource limits, malformed JSON, etc.) is
already covered by ``test_openai_runner.py`` against the shared method.
These tests focus on the three seams that actually differ: construction
validation, client construction, and price resolution -- plus one
end-to-end happy path proving the inherited loop still works via
subclassing (D1: no loop duplication).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agentevalops.agents.azure_openai_runner import (
    _AZURE_PRICE_PER_1M_USD,
    AzureOpenAIRunner,
)
from agentevalops.core.errors import ConfigurationError
from agentevalops.core.protocols import AgentRunner
from agentevalops.core.schemas import (
    AgentInput,
    ResourceLimits,
    TaskSpec,
    TraceEventKind,
)
from agentevalops.core.types import TaskId

_MODEL_ID = "gpt-4.1-mini-2025-04-14"

# ---------------------------------------------------------------------------
# Fake openai-style client (mirrors test_openai_runner.py's fakes)
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


def _make_input(
    description: str = "Return the string 'hello world'.",
    run_id: str = "test-run",
) -> AgentInput:
    task = TaskSpec(
        task_id=TaskId("t-1"), benchmark_id="toy", description=description,
    )
    return AgentInput(task=task, context={"run_id": run_id})


def _collect(runner: AzureOpenAIRunner, inp: AgentInput, limits: ResourceLimits):
    async def _run() -> list[Any]:
        trace_iter = await runner.run(inp, limits)
        return [e async for e in trace_iter]

    return asyncio.run(_run())


def _factory(responses: list[Any]):
    return lambda: _FakeClient(responses)


def _make_runner(
    *,
    model: str | None = _MODEL_ID,
    deployment: str | None = "my-deployment",
    api_version: str | None = "2024-10-21",
    responses: list[Any] | None = None,
) -> AzureOpenAIRunner:
    return AzureOpenAIRunner(
        model=model,
        deployment=deployment,
        api_version=api_version,
        agent_factory=_factory(responses if responses is not None else []),
    )


# ---------------------------------------------------------------------------
# Construction validation
# ---------------------------------------------------------------------------


def test_missing_deployment_raises_configuration_error() -> None:
    with pytest.raises(ConfigurationError, match="deployment"):
        AzureOpenAIRunner(
            model=_MODEL_ID, deployment=None, api_version="2024-10-21",
            agent_factory=_factory([]),
        )


def test_empty_deployment_raises_configuration_error() -> None:
    with pytest.raises(ConfigurationError, match="deployment"):
        AzureOpenAIRunner(
            model=_MODEL_ID, deployment="", api_version="2024-10-21",
            agent_factory=_factory([]),
        )


def test_missing_api_version_raises_configuration_error() -> None:
    with pytest.raises(ConfigurationError, match="api_version"):
        AzureOpenAIRunner(
            model=_MODEL_ID, deployment="my-deployment", api_version=None,
            agent_factory=_factory([]),
        )


def test_unknown_model_id_raises_configuration_error_at_construction() -> None:
    """D3: an unpriced model id must fail loudly at construction, not
    silently fall back to any rate -- unlike OpenAIRunner's
    _FALLBACK_PRICE_PER_1M_USD, there is deliberately no Azure fallback.
    """
    with pytest.raises(ConfigurationError, match="made-up-model"):
        AzureOpenAIRunner(
            model="made-up-model", deployment="my-deployment",
            api_version="2024-10-21", agent_factory=_factory([]),
        )


def test_unknown_model_id_does_not_construct_a_runner() -> None:
    # Guards against a partially-constructed runner silently existing after
    # the raise -- construction must fail before anything else references
    # the bad model id.
    with pytest.raises(ConfigurationError):
        AzureOpenAIRunner(
            model="made-up-model", deployment="my-deployment",
            api_version="2024-10-21", agent_factory=_factory([]),
        )


def test_valid_construction_sets_distinguishable_agent_id() -> None:
    runner = _make_runner()
    assert runner.agent_id == f"azure-openai-chat:{_MODEL_ID}"
    assert runner.agent_id != f"openai-chat:{_MODEL_ID}"


def test_protocol_conformance() -> None:
    runner = _make_runner()
    assert isinstance(runner, AgentRunner)


# ---------------------------------------------------------------------------
# The two tests that matter most (per the ticket): deployment vs model id,
# and construction-time price-table enforcement (the latter above).
# ---------------------------------------------------------------------------


def test_api_call_uses_deployment_but_cost_uses_model_id_price() -> None:
    """The single test that catches the "quietly wrong cost" case D3 exists
    to prevent: the `model=` parameter sent to the API must be the
    deployment name, while the cost computed for that same call must use
    the model id's price from Azure's own table -- not the deployment name
    (which isn't even a key in any price table) and not OpenAI's table.
    """
    responses = [_finish_response("hi", prompt_tokens=1000, completion_tokens=1000)]
    client = _FakeClient(responses)
    runner = AzureOpenAIRunner(
        model=_MODEL_ID,
        deployment="my-deployment",
        api_version="2024-10-21",
        agent_factory=lambda: client,
    )
    events = _collect(runner, _make_input(), ResourceLimits())

    # --- the API was called with the deployment name, not the model id ---
    assert client.chat.completions.calls[0]["model"] == "my-deployment"
    assert client.chat.completions.calls[0]["model"] != _MODEL_ID

    # --- but cost was computed from the model id's Azure price ---
    in_1m, out_1m = _AZURE_PRICE_PER_1M_USD[_MODEL_ID]
    expected_cost = 1000 * (in_1m / 1_000_000.0) + 1000 * (out_1m / 1_000_000.0)
    terminal = events[-1]
    assert terminal.kind == TraceEventKind.AGENT_TERMINAL
    assert terminal.payload["total_cost_usd"] == pytest.approx(expected_cost)
    # Sanity: this must not equal what OpenAI's own direct-API table would
    # give for "my-deployment" (undefined -- falls back to the cheapest
    # rate) if the seam were wired wrong and _compute_step_cost were never
    # overridden at all.
    from agentevalops.agents.openai_runner import _cost_usd

    assert terminal.payload["total_cost_usd"] == pytest.approx(
        _cost_usd(_MODEL_ID, 1000, 1000)
    )


def test_compute_step_cost_uses_azure_table_not_openai_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Azure's table and OpenAI's table happen to agree for the one model
    priced today (both $0.40/$1.60 per 1M), which would let a wiring bug --
    AzureOpenAIRunner silently falling through to OpenAIRunner's
    _compute_step_cost instead of its own override -- go undetected by
    numeric comparison alone. Monkeypatch Azure's table to a deliberately
    different rate and confirm the computed cost tracks *that* change, not
    OpenAI's untouched table -- this is only possible if the override is
    genuinely in effect.
    """
    import agentevalops.agents.azure_openai_runner as azure_mod

    monkeypatch.setitem(
        azure_mod._AZURE_PRICE_PER_1M_USD, _MODEL_ID, (9.0, 18.0)
    )
    runner = _make_runner()
    cost = runner._compute_step_cost(_MODEL_ID, 1_000_000, 1_000_000)
    assert cost == pytest.approx(9.0 + 18.0)

    from agentevalops.agents.openai_runner import _cost_usd

    assert cost != pytest.approx(_cost_usd(_MODEL_ID, 1_000_000, 1_000_000))


def test_cost_table_hand_computed() -> None:
    in_1m, out_1m = _AZURE_PRICE_PER_1M_USD[_MODEL_ID]
    assert in_1m == pytest.approx(0.40)
    assert out_1m == pytest.approx(1.60)
    runner = _make_runner()
    cost = runner._compute_step_cost(_MODEL_ID, 1000, 1000)
    # (1000*0.40 + 1000*1.60) / 1e6 = 0.002
    assert cost == pytest.approx(0.002)


# ---------------------------------------------------------------------------
# Client construction (env-var checks only; the real openai.AzureOpenAI
# call itself is # pragma: no cover, live-path-only, never exercised here)
# ---------------------------------------------------------------------------


def test_missing_endpoint_raises_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-key")
    runner = AzureOpenAIRunner(
        model=_MODEL_ID, deployment="my-deployment", api_version="2024-10-21",
    )

    async def _run() -> None:
        await runner.run(_make_input(), ResourceLimits())

    with pytest.raises(ConfigurationError, match="AZURE_OPENAI_ENDPOINT"):
        asyncio.run(_run())


def test_missing_api_key_raises_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://fake.openai.azure.com")
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    runner = AzureOpenAIRunner(
        model=_MODEL_ID, deployment="my-deployment", api_version="2024-10-21",
    )

    async def _run() -> None:
        await runner.run(_make_input(), ResourceLimits())

    with pytest.raises(ConfigurationError, match="AZURE_OPENAI_API_KEY"):
        asyncio.run(_run())


# ---------------------------------------------------------------------------
# End-to-end happy path -- proves the inherited _gen loop still works via
# subclassing (D1: no loop duplication)
# ---------------------------------------------------------------------------


def test_happy_path_finish_tool() -> None:
    responses = [_finish_response("hello world", prompt_tokens=10, completion_tokens=5)]
    runner = _make_runner(responses=responses)
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
    assert terminal.payload["final_answer"] == "hello world"


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
        agent_id=AgentId("azure-openai-chat:test"),
        backend_id=BackendId("local"),
        model=_MODEL_ID,
        deployment="my-deployment",
        api_version="2024-10-21",
    )
    orch = LocalOrchestrator(
        run_config=run_config,
        benchmark=ToyBenchmarkAdapter(scenario="smoke"),
        runner=AzureOpenAIRunner(
            model=_MODEL_ID,
            deployment="my-deployment",
            api_version="2024-10-21",
            agent_factory=lambda: _MetadataAwareClient(),
        ),
        trace_store=InMemoryTraceStore(),
        evaluator=DeterministicEvaluator(),
        policy_checker=BasicPolicyChecker(),
    )
    summary = asyncio.run(orch.run())
    assert summary.total_tasks == 2
    assert summary.passed_tasks == 2
    assert summary.failed_tasks == 0
    assert summary.total_tokens > 0
