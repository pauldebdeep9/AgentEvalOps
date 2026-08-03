"""OpenAIRunner — a second, OpenAI-backed agent runner.

Scope decision
--------------
This is a NEW adapter alongside :class:`~agentevalops.agents.langgraph_runner.
LangGraphRunner`, not a replacement or a fix for it.  It exists to make live
platform verification cheaper (see ``docs/adr/0001-openai-runner-chat-
completions.md``).  It does **not** close ROADMAP W8 — that milestone's exit
criteria are about the Anthropic runner specifically and are unaffected by
this file existing.

API choice
----------
Uses the Chat Completions API (``client.chat.completions.create``), not the
newer Responses API.  Reasoning recorded in ``docs/adr/0001-openai-runner-
chat-completions.md`` — in short: Chat Completions is a direct port of this
runner's message-list loop shape (the same shape ``LangGraphRunner`` already
uses), is the stable/mature surface, and keeps every input that determines
behaviour inside the messages list the bundle can capture, rather than
server-side state referenced by an opaque response id.

No shared base class with LangGraphRunner
------------------------------------------
Deliberate, per the same ADR: two providers is not enough evidence to design
the right shared seam, and the two differ in kind (not just naming) at the
exact two places that matter most — tool arguments arrive as a JSON
**string** here, not a parsed dict, and must be parsed defensively; and every
``tool_calls`` entry in one message needs its own matching ``role: "tool"``
reply before the next request, mirroring how ``LangGraphRunner`` already
replies to every ``tool_use`` block in a turn.  Do not "tidy" the two loops
into one without new evidence from a third provider.

Import safety / optional dependency pattern
-------------------------------------------
Same discipline as ``langgraph_runner.py``: CI installs only the core deps;
``openai`` lives in the optional ``llm`` extra and is imported **lazily
inside methods**, never at module top level, so the registry can reference
this class without importing ``openai``.  Unit tests inject a fake client;
the lazy-import and real-client-construction branches carry
``# pragma: no cover``.
"""

from __future__ import annotations

import json
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

#: Default model used only by direct/programmatic construction with no
#: ``model=`` argument (e.g. tests, or a script that builds ``OpenAIRunner``
#: without going through the CLI).  The CLI path never reaches this default:
#: ``config/loader.py`` requires model-backed run configs to specify
#: ``model`` explicitly and fails at load time otherwise (docs/adr/0001-...
#: §3, revised 2026-08-04 -- there is no environment-variable fallback of
#: any kind here anymore).  gpt-4.1-mini is the cost-efficient choice for
#: the toy Q&A tasks, mirroring why LangGraphRunner defaults to Haiku.
DEFAULT_MODEL: str = "gpt-4.1-mini-2025-04-14"

#: Per-model USD price per **single** token, as ``(input, output)`` tuples.
#:
#: Source: https://developers.openai.com/api/docs/pricing (the current
#: canonical URL — https://platform.openai.com/docs/pricing 301-redirects
#: there), standard (non-batch, non-"fast mode") tier. Checked 2026-08-03:
#: gpt-4.1-mini = $0.40 / 1M input tokens, $1.60 / 1M output tokens.
#: Confirmed this is the *base* model's rate, not the fine-tuning page's
#: separate (higher) per-token rate for fine-tuned gpt-4.1-mini-2025-04-14
#: variants, which is a different, higher-priced row on the same page.
#: Divided by 1e6 below to match this table's per-single-token convention —
#: do NOT drop a per-1M figure in directly, that is a 10**6 error.
_PRICE_PER_1M_USD: dict[str, tuple[float, float]] = {
    # model id                     (input $/1M, output $/1M)
    "gpt-4.1-mini-2025-04-14": (0.40, 1.60),
}

#: Fallback price used when a model id is not in the table above.  Only one
#: model is priced today, so the fallback is that same (cheapest available)
#: rate — same "never over-report cost for an unknown model" reasoning as
#: LangGraphRunner's fallback.
_FALLBACK_PRICE_PER_1M_USD: tuple[float, float] = _PRICE_PER_1M_USD[
    "gpt-4.1-mini-2025-04-14"
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
# NOTE (docs/adr/0004-cloud-backend-two-axes.md, Axis B / D2): a single
# ``model`` string used to serve three jobs at once -- the API's ``model=``
# parameter, the pricing-table key, and the ``agent_id`` provenance string.
# Azure OpenAI keys requests by *deployment name*, not model id, splitting
# the first job from the other two. ``OpenAIRunner`` below is refactored to
# carry ``_api_model``/``_model_id`` as two attributes (both set to the same
# string here, so behaviour is unchanged) precisely so ``AzureOpenAIRunner``
# (``azure_openai_runner.py``) can override each independently without
# duplicating this class's loop.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Lightweight tools (same tool set as langgraph_runner._TOOLS, re-expressed in
# OpenAI's {"type": "function", "function": {...}} shape)
# ---------------------------------------------------------------------------

_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": (
                "Evaluate a simple arithmetic expression using +, -, *, /, "
                "parentheses and numbers. Returns the numeric result as a "
                "string."
            ),
            "parameters": {
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
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": (
                "Return the final answer to the user's task. Call this "
                "exactly once when you are confident in the answer."
            ),
            "parameters": {
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
    },
]

# Characters permitted in a calculator expression.  Anything else is rejected
# rather than passed to ``eval`` — the tool never executes arbitrary code.
_SAFE_EXPR_CHARS = set("0123456789+-*/(). ")


def _run_calculator(expression: str) -> str:
    """Evaluate a trivial arithmetic *expression*, safely.

    Only digits, whitespace and the operators ``+ - * / ( ) .`` are allowed.
    Any other character (or an evaluation error) yields an error string that
    is handed back to the model as the tool result — this never raises.
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

    The real ``openai`` SDK returns a ``ChatCompletion`` with
    ``choices[0].message``; the injected fake in tests returns the same
    duck-typed shape.  Tool call arguments are read as the raw JSON
    **string** the API sends (``function.arguments``) — parsing happens in
    the loop, defensively, because malformed JSON from the model is normal,
    not exceptional (see module docstring).
    """

    __slots__ = (
        "text",
        "tool_calls",
        "input_tokens",
        "output_tokens",
        "finish_reason",
    )

    def __init__(self, response: Any) -> None:
        usage = getattr(response, "usage", None)
        self.input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        self.output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)

        choices = getattr(response, "choices", None) or []
        choice = choices[0] if choices else None
        message = getattr(choice, "message", None)
        self.finish_reason = getattr(choice, "finish_reason", None)
        self.text = str(getattr(message, "content", None) or "").strip()

        tool_calls: list[tuple[str, str, str]] = []
        for tc in getattr(message, "tool_calls", None) or []:
            func = getattr(tc, "function", None)
            tool_calls.append(
                (
                    str(getattr(tc, "id", "")),
                    str(getattr(func, "name", "")),
                    str(getattr(func, "arguments", "") or ""),
                )
            )
        self.tool_calls = tool_calls


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class OpenAIRunner:
    """Real, model-backed :class:`~agentevalops.core.protocols.AgentRunner`
    on the OpenAI Chat Completions API.

    Streams ``AGENT_PLAN`` -> (``AGENT_TOOL_CALL`` / ``AGENT_TOOL_RESULT``)* ->
    ``AGENT_FINAL_ANSWER`` -> ``AGENT_TERMINAL`` while a minimal tool-calling
    agent works a single toy Q&A task — the same contract and the same two
    tools (``calculator``, ``finish``) as ``LangGraphRunner``.

    Parameters
    ----------
    model:
        OpenAI model id.  ``None`` falls back to :data:`DEFAULT_MODEL` --
        this fallback is for direct/programmatic construction only (tests,
        scripts).  There is no environment-variable fallback: an earlier
        ``OPENAI_MODEL`` env-var path was removed 2026-08-04 after it was
        found to be exactly the kind of second, ambiguous source
        ``docs/adr/0001-...`` §3 already argued against -- a run started via
        the CLI now fails loudly at config-load time
        (``config/loader.py``) if a model-backed run config omits ``model``,
        rather than silently resolving it from the environment.  Whichever
        model is actually resolved is still encoded in :attr:`agent_id`.
    agent_factory:
        Optional zero-argument callable returning an *agent client* object
        with a ``chat.completions.create(...)`` method (the shape of
        ``openai.OpenAI``).  Injecting one lets unit tests run with no
        network and no ``llm`` extra.  When ``None`` (the default) the real
        ``openai`` client is built lazily on first use — which requires
        ``OPENAI_API_KEY``.
    max_steps:
        Hard ceiling on agent loop iterations, independent of resource
        limits.  Prevents an unbounded loop if the model never calls
        ``finish``.
    system_prompt:
        Overrides the default system prompt (used mainly by tests).

    Notes
    -----
    * The API key is read from ``OPENAI_API_KEY`` and is never logged or
      placed in any trace payload.
    * Agent-level failure (wrong / empty answer, step ceiling, malformed tool
      arguments) is captured in the terminal event with ``success=False`` —
      never raised.
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
        resolved_model = model or DEFAULT_MODEL
        self._api_model = resolved_model  # -> the API's `model=` parameter
        self._model_id = resolved_model  # -> pricing table key + agent_id
        self._agent_factory = agent_factory
        self._max_steps = max(1, int(max_steps))
        self._system_prompt = system_prompt or (
            "You are a precise assistant solving a short question-answering "
            "task. Use the calculator tool only when arithmetic is needed. "
            "When you know the answer, call the finish tool with the exact "
            "answer string and nothing else."
        )
        self._client: Any = None
        self.agent_id = AgentId(f"openai-chat:{self._model_id}")

    # ------------------------------------------------------------------
    # Client construction
    # ------------------------------------------------------------------

    def _build_real_client(self) -> Any:  # pragma: no cover - live path only
        """Construct the real ``openai`` client (lazy import).

        Raises
        ------
        ConfigurationError
            If ``OPENAI_API_KEY`` is not set.
        InfrastructureError
            If the ``openai`` SDK is not installed or the client cannot be
            constructed.
        """
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ConfigurationError(
                "OPENAI_API_KEY is not set; the openai runner needs an "
                "OpenAI API key to build its real client. Set the "
                "environment variable or inject an agent_factory."
            )
        try:
            import openai  # lazy: only present with the 'llm' extra
        except ImportError as exc:
            raise InfrastructureError(
                "The 'openai' package is required for the openai runner. "
                "Install it with: pip install 'agentevalops[llm]'"
            ) from exc
        try:
            return openai.OpenAI(api_key=api_key)
        except Exception as exc:  # noqa: BLE001 - surface as infra failure
            raise InfrastructureError(
                f"Failed to construct the OpenAI client: {exc}"
            ) from exc

    def _get_client(self) -> Any:
        """Return the agent client, building/injecting it once.

        Missing key (real path) surfaces as :class:`ConfigurationError`; this
        is checked eagerly so the CLI reports a clear error before any events
        are emitted.
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
            response = client.chat.completions.create(
                model=self._api_model,
                messages=messages,
                tools=_TOOLS,
            )
        except (ConfigurationError, InfrastructureError):
            raise
        except Exception as exc:  # noqa: BLE001 - model API / transport failure
            raise InfrastructureError(
                f"Model API call failed: {exc}"
            ) from exc
        return _ParsedResponse(response)

    def _compute_step_cost(
        self, model_id: str, input_tokens: int, output_tokens: int
    ) -> float:
        """Return the USD cost of one model response.

        Overridden by :class:`~agentevalops.agents.azure_openai_runner.
        AzureOpenAIRunner`, which prices against its own Azure table instead
        of this module's OpenAI-direct one (docs/adr/0004, D3) -- the seam
        that lets a subclass repriced a call without touching :meth:`_gen`.
        """
        return _cost_usd(model_id, input_tokens, output_tokens)

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
        model_id = self._model_id
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
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": user_content},
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
            step_cost = self._compute_step_cost(
                model_id, parsed.input_tokens, parsed.output_tokens
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
            # Every tool_calls entry in this message needs its own matching
            # role:"tool" reply before the next request is accepted — mirror
            # LangGraphRunner's per-block iteration, one call at a time.
            assistant_tool_calls: list[dict[str, Any]] = []
            tool_messages: list[dict[str, Any]] = []
            finished_answer: str | None = None

            for tool_id, tool_name, args_raw in parsed.tool_calls:
                assistant_tool_calls.append(
                    {
                        "id": tool_id,
                        "type": "function",
                        "function": {"name": tool_name, "arguments": args_raw},
                    }
                )

                # Tool arguments arrive as a JSON string, not a parsed dict —
                # malformed JSON from the model is normal, not exceptional.
                # Parse defensively; never raise on bad model output.
                parse_error: str | None = None
                tool_input: dict[str, Any] = {}
                try:
                    decoded = json.loads(args_raw) if args_raw else {}
                    if not isinstance(decoded, dict):
                        raise ValueError(
                            "tool arguments JSON did not decode to an object"
                        )
                    tool_input = decoded
                except (json.JSONDecodeError, ValueError) as exc:
                    parse_error = str(exc)

                yield TraceEvent(
                    run_id=run_id,
                    step_index=step,
                    kind=TraceEventKind.AGENT_TOOL_CALL,
                    payload={"tool_name": tool_name, "args": tool_input},
                )
                step += 1

                if parse_error is not None:
                    tool_output = (
                        f"error: malformed tool arguments JSON: {parse_error}"
                    )
                elif tool_name == "finish":
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
                tool_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_id,
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
            messages.append(
                {
                    "role": "assistant",
                    "content": parsed.text or None,
                    "tool_calls": assistant_tool_calls,
                }
            )
            messages.extend(tool_messages)

        # Loop ran out of steps without the agent finishing.
        yield _terminal(
            success=False,
            reason=TerminationReason.AGENT_ERROR,
            answer=None,
        )
