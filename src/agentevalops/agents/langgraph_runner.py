"""LangGraphRunner — a real, model-backed agent runner (ROADMAP W8).

Scope decision (minimum credible slice)
----------------------------------------
The current toy benchmark is string-answer Q&A (see
``agentevalops.benchmarks.toy.tasks``), *not* file-based.  This runner therefore
targets those Q&A tasks with a minimal ReAct / tool-calling agent and two
lightweight tools:

* ``calculator`` — evaluates a trivial arithmetic expression, so the
  ``AGENT_TOOL_CALL`` / ``AGENT_TOOL_RESULT`` trace path is genuinely exercised.
* ``finish`` — the agent calls this to return its final answer.

We deliberately do **not** build file-read / file-write / run-tests tools — those
wait for a code-editing benchmark (SWE-bench, later).

Fallback decision (LangGraph vs hand-rolled)
--------------------------------------------
The requirement is "a real runner", not "LangGraph specifically".  Because the
``langchain-anthropic`` binding was not available in the target environment and a
hand-rolled loop is simpler and has fewer moving parts, this runner implements a
minimal agent loop (plan -> tool_call -> execute -> observe -> final) directly on
the ``anthropic`` SDK.  It is registered under the runner name ``"langgraph"`` and
conforms to the identical trace / terminal contract, so config selection and the
orchestrator are unaffected.  The pinned ``langgraph`` / ``langchain-anthropic``
dependencies remain declared in the ``llm`` extra for a future LangGraph port.

Import safety / optional dependency pattern
-------------------------------------------
CI installs only the core deps (``typer`` + ``pyyaml``); the ``anthropic`` SDK is
in the optional ``llm`` extra.  This module MUST therefore import cleanly with
only the core deps present.  The ``anthropic`` SDK is imported **lazily inside
methods** (never at module top level), and the class can be registered in the
registry without importing it.  Unit tests inject a fake client so the real
import / network path is never hit; the lazy-import and real-client-construction
branches carry ``# pragma: no cover`` because they only run on the live path.
"""

from __future__ import annotations

import os
import time
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any, Callable

from agentevalops.core.errors import ConfigurationError, InfrastructureError
from agentevalops.core.schemas import (
    AgentInput,
    ResourceLimits,
    TerminationReason,
    TraceEvent,
    TraceEventKind,
)
from agentevalops.core.types import AgentId, RunId

# ---------------------------------------------------------------------------
# Model + pricing table
# ---------------------------------------------------------------------------

#: Default model used when the config does not specify one.  Haiku is the
#: cheapest current model and is well-suited to the toy Q&A tasks.
DEFAULT_MODEL: str = "claude-haiku-4-5"

#: Per-model USD price per **single** token, as ``(input, output)`` tuples.
#:
#: Source: the ``/claude-api`` skill "Current Models" table (cached 2026-06-24),
#: quoted there per 1M tokens; divided by 1e6 here.  Keep this the single source
#: of truth for cost accounting so a price change is a one-line edit.
_PRICE_PER_1M_USD: dict[str, tuple[float, float]] = {
    # model id            (input $/1M, output $/1M)
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
}

#: Fallback price used when a model id is not in the table above.  Uses the
#: Haiku (cheapest) rate so unknown models never over-report cost.
_FALLBACK_PRICE_PER_1M_USD: tuple[float, float] = _PRICE_PER_1M_USD[
    "claude-haiku-4-5"
]


def _model_price_per_token(model: str) -> tuple[float, float]:
    """Return ``(input_per_token, output_per_token)`` USD prices for *model*."""
    in_1m, out_1m = _PRICE_PER_1M_USD.get(model, _FALLBACK_PRICE_PER_1M_USD)
    return (in_1m / 1_000_000.0, out_1m / 1_000_000.0)


def _cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Compute the USD cost of a single model response from token usage."""
    in_per, out_per = _model_price_per_token(model)
    return input_tokens * in_per + output_tokens * out_per


# ---------------------------------------------------------------------------
# Lightweight tools
# ---------------------------------------------------------------------------

_TOOLS: list[dict[str, Any]] = [
    {
        "name": "calculator",
        "description": (
            "Evaluate a simple arithmetic expression using +, -, *, /, "
            "parentheses and numbers. Returns the numeric result as a string."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "e.g. '2 + 2' or '(3 * 4) - 1'",
                }
            },
            "required": ["expression"],
        },
    },
    {
        "name": "finish",
        "description": (
            "Return the final answer to the user's task. Call this exactly "
            "once when you are confident in the answer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "answer": {
                    "type": "string",
                    "description": "The final answer string.",
                }
            },
            "required": ["answer"],
        },
    },
]

# Characters permitted in a calculator expression.  Anything else is rejected
# rather than passed to ``eval`` — the tool never executes arbitrary code.
_SAFE_EXPR_CHARS = set("0123456789+-*/(). ")


def _run_calculator(expression: str) -> str:
    """Evaluate a trivial arithmetic *expression*, safely.

    Only digits, whitespace and the operators ``+ - * / ( ) .`` are allowed.
    Any other character (or an evaluation error) yields an error string that is
    handed back to the model as the tool result — this never raises.
    """
    expr = str(expression)
    if not expr or any(ch not in _SAFE_EXPR_CHARS for ch in expr):
        return "error: expression contains unsupported characters"
    try:
        # ``eval`` is bounded to arithmetic by the character allow-list above:
        # no names, calls, attribute access, or subscripts can appear.
        result = eval(expr, {"__builtins__": {}}, {})  # noqa: S307
    except Exception as exc:  # noqa: BLE001 - report any arithmetic failure
        return f"error: {exc}"
    return str(result)


# ---------------------------------------------------------------------------
# Response adapter
# ---------------------------------------------------------------------------


class _ParsedResponse:
    """Normalised view of one model response the loop needs to act on.

    The real ``anthropic`` SDK returns rich block objects; the injected fake in
    tests returns the same duck-typed shape.  This adapter reads only the
    attributes both share, so the loop code is provider-agnostic and fully
    covered by the fake path.
    """

    __slots__ = (
        "text",
        "tool_calls",
        "input_tokens",
        "output_tokens",
        "stop_reason",
    )

    def __init__(self, response: Any) -> None:
        usage = getattr(response, "usage", None)
        self.input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        self.output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        self.stop_reason = getattr(response, "stop_reason", None)

        text_parts: list[str] = []
        tool_calls: list[tuple[str, str, dict[str, Any]]] = []
        for block in getattr(response, "content", []) or []:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(str(getattr(block, "text", "")))
            elif btype == "tool_use":
                tool_calls.append(
                    (
                        str(getattr(block, "id", "")),
                        str(getattr(block, "name", "")),
                        dict(getattr(block, "input", {}) or {}),
                    )
                )
        self.text = " ".join(p for p in text_parts if p).strip()
        self.tool_calls = tool_calls


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class LangGraphRunner:
    """Real, model-backed :class:`~agentevalops.core.protocols.AgentRunner`.

    Streams ``AGENT_PLAN`` -> (``AGENT_TOOL_CALL`` / ``AGENT_TOOL_RESULT``)* ->
    ``AGENT_FINAL_ANSWER`` -> ``AGENT_TERMINAL`` while a minimal tool-calling
    agent works a single toy Q&A task.

    Parameters
    ----------
    model:
        Claude model id.  ``None`` selects :data:`DEFAULT_MODEL`.
    agent_factory:
        Optional zero-argument callable returning an *agent client* object with
        a ``messages.create(...)`` method (the shape of ``anthropic.Anthropic``).
        Injecting one lets unit tests run with no network and no ``llm`` extra.
        When ``None`` (the default) the real ``anthropic`` client is built
        lazily on first use — which requires ``ANTHROPIC_API_KEY``.
    max_steps:
        Hard ceiling on agent loop iterations, independent of resource limits.
        Prevents an unbounded loop if the model never calls ``finish``.
    system_prompt:
        Overrides the default system prompt (used mainly by tests).

    Notes
    -----
    * The API key is read from ``ANTHROPIC_API_KEY`` and is never logged or
      placed in any trace payload.
    * Agent-level failure (wrong / empty answer, step ceiling) is captured in
      the terminal event with ``success=False`` — never raised.
    * Only genuine infrastructure failures (model API / auth errors) raise
      :class:`InfrastructureError`.
    """

    agent_id: AgentId

    def __init__(
        self,
        model: str | None = None,
        *,
        agent_factory: Callable[[], Any] | None = None,
        max_steps: int = 6,
        system_prompt: str | None = None,
    ) -> None:
        self._model = model or DEFAULT_MODEL
        self._agent_factory = agent_factory
        self._max_steps = max(1, int(max_steps))
        self._system_prompt = system_prompt or (
            "You are a precise assistant solving a short question-answering "
            "task. Use the calculator tool only when arithmetic is needed. "
            "When you know the answer, call the finish tool with the exact "
            "answer string and nothing else."
        )
        self._client: Any = None
        self.agent_id = AgentId(f"langgraph-claude:{self._model}")

    # ------------------------------------------------------------------
    # Client construction
    # ------------------------------------------------------------------

    def _build_real_client(self) -> Any:  # pragma: no cover - live path only
        """Construct the real ``anthropic`` client (lazy import).

        Raises
        ------
        ConfigurationError
            If ``ANTHROPIC_API_KEY`` is not set.
        InfrastructureError
            If the ``anthropic`` SDK is not installed or the client cannot be
            constructed.
        """
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ConfigurationError(
                "ANTHROPIC_API_KEY is not set; the langgraph runner needs an "
                "Anthropic API key to build its real client. Set the "
                "environment variable or inject an agent_factory."
            )
        try:
            import anthropic  # lazy: only present with the 'llm' extra
        except ImportError as exc:
            raise InfrastructureError(
                "The 'anthropic' package is required for the langgraph runner. "
                "Install it with: pip install 'agentevalops[llm]'"
            ) from exc
        try:
            return anthropic.Anthropic(api_key=api_key)
        except Exception as exc:  # noqa: BLE001 - surface as infra failure
            raise InfrastructureError(
                f"Failed to construct the Anthropic client: {exc}"
            ) from exc

    def _get_client(self) -> Any:
        """Return the agent client, building/injecting it once.

        Missing key (real path) surfaces as :class:`ConfigurationError`; this is
        checked eagerly so the CLI reports a clear error before any events are
        emitted.
        """
        if self._client is not None:
            return self._client
        if self._agent_factory is not None:
            self._client = self._agent_factory()
        else:
            self._client = self._build_real_client()  # pragma: no cover
        return self._client

    # ------------------------------------------------------------------
    # Model call
    # ------------------------------------------------------------------

    def _call_model(
        self, client: Any, messages: list[dict[str, Any]]
    ) -> _ParsedResponse:
        """Call the model once; wrap API failures as infrastructure errors."""
        try:
            response = client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=self._system_prompt,
                tools=_TOOLS,
                messages=messages,
            )
        except (ConfigurationError, InfrastructureError):
            raise
        except Exception as exc:  # noqa: BLE001 - model API / transport failure
            raise InfrastructureError(
                f"Model API call failed: {exc}"
            ) from exc
        return _ParsedResponse(response)

    # ------------------------------------------------------------------
    # run()
    # ------------------------------------------------------------------

    async def run(
        self,
        input: AgentInput,
        resource_limits: ResourceLimits,
    ) -> AsyncIterator[TraceEvent]:
        """Stream trace events while the agent solves *input*.

        The client is resolved eagerly (so a missing API key raises
        :class:`ConfigurationError` before any event is streamed); the async
        generator then drives the loop.
        """
        run_id = RunId(str(input.context.get("run_id", "unknown")))
        client = self._get_client()

        return self._gen(run_id, input, resource_limits, client)

    async def _gen(
        self,
        run_id: RunId,
        input: AgentInput,
        limits: ResourceLimits,
        client: Any,
    ) -> AsyncGenerator[TraceEvent, None]:
        start = time.monotonic()
        model = self._model
        total_tokens = 0
        total_cost = 0.0
        step = 0

        def _elapsed() -> float:
            return time.monotonic() - start

        def _terminal(
            *,
            success: bool,
            reason: TerminationReason,
            answer: str | None,
        ) -> TraceEvent:
            return TraceEvent(
                run_id=run_id,
                step_index=step,
                kind=TraceEventKind.AGENT_TERMINAL,
                payload={
                    "success": success,
                    "termination_reason": reason.value,
                    "final_answer": answer,
                    "total_cost_usd": total_cost,
                    "total_tokens": total_tokens,
                    "wall_seconds": _elapsed(),
                },
            )

        task = input.task
        user_content = (
            f"Task: {task.description}\n"
            "Answer the task. Call the finish tool with your final answer."
        )
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": user_content}
        ]

        # Opening plan step (always at least one event before any tool work).
        yield TraceEvent(
            run_id=run_id,
            step_index=step,
            kind=TraceEventKind.AGENT_PLAN,
            payload={"plan": f"Solve: {task.description}"},
        )
        step += 1

        for _iteration in range(self._max_steps):
            parsed = self._call_model(client, messages)

            # --- cost / token accounting for this response ----------------
            step_tokens = parsed.input_tokens + parsed.output_tokens
            step_cost = _cost_usd(
                model, parsed.input_tokens, parsed.output_tokens
            )
            total_tokens += step_tokens
            total_cost += step_cost

            # A COST_TICK carries the per-response usage on its delta fields so
            # downstream cost analysis has an explicit datapoint per model call.
            yield TraceEvent(
                run_id=run_id,
                step_index=step,
                kind=TraceEventKind.COST_TICK,
                payload={
                    "input_tokens": parsed.input_tokens,
                    "output_tokens": parsed.output_tokens,
                    "cumulative_tokens": total_tokens,
                    "cumulative_cost_usd": total_cost,
                },
                cost_delta_usd=step_cost,
                tokens_delta=step_tokens,
            )
            step += 1

            # --- resource-limit checks (after each step) ------------------
            if total_tokens > limits.max_tokens:
                yield _terminal(
                    success=False,
                    reason=TerminationReason.LIMIT_TOKENS,
                    answer=None,
                )
                return
            if total_cost > limits.max_cost_usd:
                yield _terminal(
                    success=False,
                    reason=TerminationReason.LIMIT_COST,
                    answer=None,
                )
                return
            if _elapsed() > limits.max_wall_seconds:
                yield _terminal(
                    success=False,
                    reason=TerminationReason.LIMIT_TIME,
                    answer=None,
                )
                return

            # --- no tool call: model answered in plain text ---------------
            if not parsed.tool_calls:
                answer = parsed.text
                if answer:
                    yield TraceEvent(
                        run_id=run_id,
                        step_index=step,
                        kind=TraceEventKind.AGENT_FINAL_ANSWER,
                        payload={"answer": answer},
                    )
                    step += 1
                    yield _terminal(
                        success=True,
                        reason=TerminationReason.COMPLETED,
                        answer=answer,
                    )
                    return
                # Empty answer with no tool call — treat as agent error.
                yield _terminal(
                    success=False,
                    reason=TerminationReason.AGENT_ERROR,
                    answer=None,
                )
                return

            # --- process tool calls ---------------------------------------
            # Record the assistant turn (tool_use blocks) so the follow-up
            # user message with tool_result blocks is well-formed.
            assistant_blocks: list[dict[str, Any]] = []
            if parsed.text:
                assistant_blocks.append({"type": "text", "text": parsed.text})
            tool_result_blocks: list[dict[str, Any]] = []
            finished_answer: str | None = None

            for tool_id, tool_name, tool_input in parsed.tool_calls:
                assistant_blocks.append(
                    {
                        "type": "tool_use",
                        "id": tool_id,
                        "name": tool_name,
                        "input": tool_input,
                    }
                )
                yield TraceEvent(
                    run_id=run_id,
                    step_index=step,
                    kind=TraceEventKind.AGENT_TOOL_CALL,
                    payload={"tool_name": tool_name, "args": tool_input},
                )
                step += 1

                if tool_name == "finish":
                    finished_answer = str(tool_input.get("answer", ""))
                    tool_output = "ok"
                elif tool_name == "calculator":
                    tool_output = _run_calculator(
                        tool_input.get("expression", "")
                    )
                else:
                    tool_output = f"error: unknown tool '{tool_name}'"

                yield TraceEvent(
                    run_id=run_id,
                    step_index=step,
                    kind=TraceEventKind.AGENT_TOOL_RESULT,
                    payload={
                        "tool_name": tool_name,
                        "result": tool_output,
                    },
                )
                step += 1
                tool_result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": tool_output,
                    }
                )

            # --- finish tool ends the run --------------------------------
            if finished_answer is not None:
                if finished_answer:
                    yield TraceEvent(
                        run_id=run_id,
                        step_index=step,
                        kind=TraceEventKind.AGENT_FINAL_ANSWER,
                        payload={"answer": finished_answer},
                    )
                    step += 1
                    yield _terminal(
                        success=True,
                        reason=TerminationReason.COMPLETED,
                        answer=finished_answer,
                    )
                    return
                # finish called with an empty answer — agent-level failure.
                yield _terminal(
                    success=False,
                    reason=TerminationReason.AGENT_ERROR,
                    answer=None,
                )
                return

            # Continue the loop: feed the tool results back to the model.
            messages.append({"role": "assistant", "content": assistant_blocks})
            messages.append({"role": "user", "content": tool_result_blocks})

        # Loop ran out of steps without the agent finishing.
        yield _terminal(
            success=False,
            reason=TerminationReason.AGENT_ERROR,
            answer=None,
        )
