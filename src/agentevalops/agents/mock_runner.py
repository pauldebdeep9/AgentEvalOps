"""MockAgentRunner — deterministic agent runner for tests and the toy demo."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator

from agentevalops.core.schemas import (
    AgentInput,
    ResourceLimits,
    TerminationReason,
    TraceEvent,
    TraceEventKind,
)
from agentevalops.core.types import AgentId, RunId


class MockAgentRunner:
    """Deterministic runner that never calls any model or external API.

    In the default (happy-path) mode it emits between three and four events
    per task:

    1. ``AGENT_PLAN``          — records the plan text.
    2. ``AGENT_TOOL_CALL``     — *optional*; emitted only when the task
       metadata key ``emit_fake_tool_call`` is truthy.  The tool name is
       taken from ``fake_tool_name`` (defaults to ``"mock-tool"``).
    3. ``AGENT_FINAL_ANSWER``  — records the answer.
    4. ``AGENT_TERMINAL``      — signals completion; payload carries the
       fields needed to reconstruct ``AgentOutput``.

    Failure-mode (``should_fail=True``) emits two events per task:

    1. ``AGENT_PLAN``      — planning step begins.
    2. ``AGENT_TERMINAL``  — signals ``AGENT_ERROR`` / ``success=False``.

    The answer returned in happy-path mode is determined by task metadata:

    * If the task metadata contains ``mock_answer``, that value is used.
      This lets specific tasks always return a *wrong* answer to exercise
      evaluation failure without using the global ``should_fail`` flag.
    * Otherwise ``expected_output`` is used.
    * If neither key is present the literal string ``"mock answer"`` is used.

    The ``run_id`` is read from ``input.context["run_id"]`` so that the
    orchestrator can associate events with the correct run.

    Parameters
    ----------
    should_fail:
        When ``True`` every task returns ``success=False`` with
        ``termination_reason=AGENT_ERROR``.  Used by scenarios where the
        agent itself crashes rather than returning a wrong answer.
    cost_per_task_usd:
        Cost each task reports in its ``AGENT_TERMINAL`` payload.  Defaults
        to ``0.0``.  Used by the ``toy_policy_violation`` demo scenario.
    """

    agent_id: AgentId = AgentId("mock-agent-v1")

    def __init__(
        self,
        should_fail: bool = False,
        cost_per_task_usd: float = 0.0,
    ) -> None:
        self._should_fail = should_fail
        self._cost_per_task_usd = cost_per_task_usd

    async def run(
        self,
        input: AgentInput,
        resource_limits: ResourceLimits,
    ) -> AsyncIterator[TraceEvent]:
        """Return an async iterator that yields the fixed demo events."""
        run_id = RunId(str(input.context.get("run_id", "unknown")))
        should_fail = self._should_fail
        cost = self._cost_per_task_usd
        metadata = input.task.metadata

        async def _gen() -> AsyncGenerator[TraceEvent, None]:
            if should_fail:
                yield TraceEvent(
                    run_id=run_id,
                    step_index=0,
                    kind=TraceEventKind.AGENT_PLAN,
                    payload={"plan": f"Solve: {input.task.description}"},
                )
                yield TraceEvent(
                    run_id=run_id,
                    step_index=1,
                    kind=TraceEventKind.AGENT_TERMINAL,
                    payload={
                        "success": False,
                        "termination_reason": (
                            TerminationReason.AGENT_ERROR.value
                        ),
                        "final_answer": None,
                        "total_cost_usd": cost,
                        "total_tokens": 0,
                        "wall_seconds": 0.01,
                    },
                )
            else:
                # Determine the answer.  ``mock_answer`` lets individual tasks
                # force a wrong answer without using the global should_fail.
                answer = str(
                    metadata.get(
                        "mock_answer",
                        metadata.get("expected_output", "mock answer"),
                    )
                )

                step = 0
                yield TraceEvent(
                    run_id=run_id,
                    step_index=step,
                    kind=TraceEventKind.AGENT_PLAN,
                    payload={"plan": f"Solve: {input.task.description}"},
                )
                step += 1

                # Optional fake tool call — used by tool_required task types.
                if metadata.get("emit_fake_tool_call"):
                    tool_name = str(
                        metadata.get("fake_tool_name", "mock-tool")
                    )
                    yield TraceEvent(
                        run_id=run_id,
                        step_index=step,
                        kind=TraceEventKind.AGENT_TOOL_CALL,
                        payload={"tool_name": tool_name, "args": {}},
                    )
                    step += 1

                yield TraceEvent(
                    run_id=run_id,
                    step_index=step,
                    kind=TraceEventKind.AGENT_FINAL_ANSWER,
                    payload={"answer": answer},
                )
                step += 1

                yield TraceEvent(
                    run_id=run_id,
                    step_index=step,
                    kind=TraceEventKind.AGENT_TERMINAL,
                    payload={
                        "success": True,
                        "termination_reason": TerminationReason.COMPLETED.value,
                        "final_answer": answer,
                        "total_cost_usd": cost,
                        "total_tokens": 10,
                        "wall_seconds": 0.01,
                    },
                )

        return _gen()
