"""Fake provider clients shared by the runner tests (R24 slice 6).

Moved, unchanged in behaviour, from ``test_openai_runner.py`` (the
OpenAI-style fakes, which ``test_azure_openai_runner.py`` mirrored) and
``test_langgraph_runner.py`` (the Anthropic-style fakes), so that
``test_runner_seam.py`` and ``test_runner_concurrency.py`` import them from
one place rather than from other test files. The runner test files alias
these back to their old private names, so their tests read as before.

Not a test module: pytest collects only ``test_*.py``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from agentevalops.core.schemas import AgentInput, ResourceLimits, TaskSpec, TraceEvent
from agentevalops.core.types import TaskId

# ---------------------------------------------------------------------------
# OpenAI-style client (``openai.OpenAI``; Azure's client has the same shape)
# ---------------------------------------------------------------------------


class OpenAIFakeFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class OpenAIFakeToolCall:
    def __init__(self, id: str, name: str, arguments: str) -> None:
        self.id = id
        self.function = OpenAIFakeFunction(name, arguments)


class OpenAIFakeMessage:
    def __init__(
        self,
        content: str | None = None,
        tool_calls: list[OpenAIFakeToolCall] | None = None,
    ) -> None:
        self.content = content
        self.tool_calls = tool_calls or []


class OpenAIFakeChoice:
    def __init__(self, message: OpenAIFakeMessage, finish_reason: str = "stop") -> None:
        self.message = message
        self.finish_reason = finish_reason


class OpenAIFakeUsage:
    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class OpenAIFakeResponse:
    def __init__(
        self,
        message: OpenAIFakeMessage,
        prompt_tokens: int = 10,
        completion_tokens: int = 5,
        finish_reason: str = "stop",
    ) -> None:
        self.choices = [OpenAIFakeChoice(message, finish_reason)]
        self.usage = OpenAIFakeUsage(prompt_tokens, completion_tokens)


class OpenAIFakeCompletions:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class OpenAIFakeChat:
    def __init__(self, responses: list[Any]) -> None:
        self.completions = OpenAIFakeCompletions(responses)


class OpenAIFakeClient:
    """Mimics ``openai.OpenAI`` closely enough for the runner."""

    def __init__(self, responses: list[Any]) -> None:
        self.chat = OpenAIFakeChat(responses)


def openai_finish_response(answer: str, **usage: int) -> OpenAIFakeResponse:
    msg = OpenAIFakeMessage(
        content=None,
        tool_calls=[
            OpenAIFakeToolCall(
                "tool-finish-1", "finish", json.dumps({"answer": answer})
            )
        ],
    )
    return OpenAIFakeResponse(msg, finish_reason="tool_calls", **usage)


def openai_calc_then_finish(expression: str, answer: str) -> list[OpenAIFakeResponse]:
    calc_msg = OpenAIFakeMessage(
        content=None,
        tool_calls=[
            OpenAIFakeToolCall(
                "tool-calc-1", "calculator", json.dumps({"expression": expression})
            )
        ],
    )
    return [
        OpenAIFakeResponse(
            calc_msg, prompt_tokens=12, completion_tokens=8, finish_reason="tool_calls"
        ),
        openai_finish_response(answer, prompt_tokens=6, completion_tokens=4),
    ]


def openai_factory(responses: list[Any]):  # type: ignore[no-untyped-def]
    return lambda: OpenAIFakeClient(responses)


# ---------------------------------------------------------------------------
# Anthropic-style client (``anthropic.Anthropic``)
# ---------------------------------------------------------------------------


class AnthropicFakeBlock:
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


class AnthropicFakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class AnthropicFakeResponse:
    def __init__(
        self,
        content: list[AnthropicFakeBlock],
        input_tokens: int = 10,
        output_tokens: int = 5,
        stop_reason: str = "end_turn",
    ) -> None:
        self.content = content
        self.usage = AnthropicFakeUsage(input_tokens, output_tokens)
        self.stop_reason = stop_reason


class AnthropicFakeMessages:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class AnthropicFakeClient:
    """Mimics ``anthropic.Anthropic`` closely enough for the runner."""

    def __init__(self, responses: list[Any]) -> None:
        self.messages = AnthropicFakeMessages(responses)


def anthropic_finish_response(answer: str, **usage: int) -> AnthropicFakeResponse:
    return AnthropicFakeResponse(
        [
            AnthropicFakeBlock(
                "tool_use",
                id="tool-finish-1",
                name="finish",
                input={"answer": answer},
            )
        ],
        stop_reason="tool_use",
        **usage,
    )


def anthropic_calc_then_finish(
    expression: str, answer: str
) -> list[AnthropicFakeResponse]:
    return [
        AnthropicFakeResponse(
            [
                AnthropicFakeBlock(
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
        anthropic_finish_response(answer, input_tokens=6, output_tokens=4),
    ]


def anthropic_factory(responses: list[Any]):  # type: ignore[no-untyped-def]
    return lambda: AnthropicFakeClient(responses)


# ---------------------------------------------------------------------------
# Shared by every runner test: the input and the event collector
# ---------------------------------------------------------------------------


def make_input(
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


def collect(runner: Any, inp: AgentInput, limits: ResourceLimits) -> list[TraceEvent]:
    async def _run() -> list[TraceEvent]:
        trace_iter = await runner.run(inp, limits)
        return [e async for e in trace_iter]

    return asyncio.run(_run())
