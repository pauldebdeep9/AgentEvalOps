"""Model calls run off the event loop, with a bounded request (ADR-0006 D10;
finding 7 of the 2026-10-31 external review). R23 slice 4."""

from __future__ import annotations

import asyncio
import sys
import threading
import types
from typing import Any

import pytest
import test_langgraph_runner as lg_t
import test_openai_runner as oa_t

from agentevalops.agents.azure_openai_runner import AzureOpenAIRunner
from agentevalops.agents.langgraph_runner import LangGraphRunner
from agentevalops.agents.openai_runner import (
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    MAX_RETRIES,
    OpenAIRunner,
)
from agentevalops.benchmarks.toy.adapter import ToyBenchmarkAdapter
from agentevalops.core.schemas import (
    ResourceLimits,
    RunConfig,
    TerminationReason,
)
from agentevalops.core.types import BACKEND_LOCAL, AgentId, RunId
from agentevalops.evaluators.deterministic import DeterministicEvaluator
from agentevalops.orchestration.local import LocalOrchestrator
from agentevalops.policy.basic_checker import BasicPolicyChecker
from agentevalops.stores.memory import InMemoryTraceStore

# ---------------------------------------------------------------------------
# Two concurrent model calls must both be in flight at once
# ---------------------------------------------------------------------------


class _Rendezvous:
    """Each call waits until the other call has started. If calls run on
    the event-loop thread, the first blocks the loop, the second never
    starts, and the barrier times out (5 s) instead of the test hanging."""

    def __init__(self, respond: Any) -> None:
        self._barrier = threading.Barrier(2, timeout=5)
        self._respond = respond

    def create(self, **kwargs: Any) -> Any:
        self._barrier.wait()
        return self._respond()


def _run_two_toy_tasks(runner: Any) -> list[Any]:
    orch = LocalOrchestrator(
        run_config=RunConfig(
            run_id=RunId("conc"),
            agent_id=AgentId("x"),
            backend_id=BACKEND_LOCAL,
            resource_limits=ResourceLimits(),
            max_concurrent_tasks=2,
        ),
        benchmark=ToyBenchmarkAdapter(scenario="smoke"),
        runner=runner,
        trace_store=InMemoryTraceStore(),
        evaluator=DeterministicEvaluator(),
        policy_checker=BasicPolicyChecker(),
    )
    return asyncio.run(orch.run()).task_results


def test_openai_calls_do_not_block_each_other() -> None:
    meet = _Rendezvous(lambda: oa_t._FakeResponse(oa_t._FakeMessage(content="4")))
    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=meet))
    results = _run_two_toy_tasks(OpenAIRunner(agent_factory=lambda: client))
    assert len(results) == 2
    for result in results:
        assert result.output.termination_reason == TerminationReason.COMPLETED


def test_langgraph_calls_do_not_block_each_other() -> None:
    meet = _Rendezvous(lambda: lg_t._FakeResponse([lg_t._FakeBlock("text", text="4")]))
    client = types.SimpleNamespace(messages=meet)
    results = _run_two_toy_tasks(LangGraphRunner(agent_factory=lambda: client))
    assert len(results) == 2
    for result in results:
        assert result.output.termination_reason == TerminationReason.COMPLETED


# ---------------------------------------------------------------------------
# The real clients get the request timeout and one retry
# ---------------------------------------------------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    def __call__(self, **kwargs: Any) -> object:
        self.kwargs = kwargs
        return object()


def _fake_sdk(
    monkeypatch: pytest.MonkeyPatch, name: str, **classes: _Recorder
) -> None:
    monkeypatch.setitem(sys.modules, name, types.SimpleNamespace(**classes))


def test_defaults_are_sixty_seconds_and_one_retry() -> None:
    assert (DEFAULT_REQUEST_TIMEOUT_SECONDS, MAX_RETRIES) == (60.0, 1)
    assert OpenAIRunner()._request_timeout_seconds == 60.0
    assert LangGraphRunner()._request_timeout_seconds == 60.0


def test_openai_real_client_gets_timeout_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    _fake_sdk(monkeypatch, "openai", OpenAI=recorder)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    OpenAIRunner(request_timeout_seconds=120)._build_real_client()
    assert recorder.kwargs["timeout"] == 120.0
    assert recorder.kwargs["max_retries"] == 1


def test_anthropic_real_client_gets_timeout_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    _fake_sdk(monkeypatch, "anthropic", Anthropic=recorder)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    LangGraphRunner(request_timeout_seconds=90)._build_real_client()
    assert recorder.kwargs["timeout"] == 90.0
    assert recorder.kwargs["max_retries"] == 1


def test_azure_real_client_gets_timeout_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    _fake_sdk(monkeypatch, "openai", AzureOpenAI=recorder)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.test")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key-not-real")
    AzureOpenAIRunner(
        model="gpt-4.1-mini-2025-04-14",
        deployment="d",
        api_version="2024-10-21",
        request_timeout_seconds=45,
    )._build_real_client()
    assert recorder.kwargs["timeout"] == 45.0
    assert recorder.kwargs["max_retries"] == 1
