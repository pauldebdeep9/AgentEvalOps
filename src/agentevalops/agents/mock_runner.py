"""MockAgentRunner — deterministic agent runner for tests and the toy demo."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, AsyncIterator, Mapping, Sequence
from typing import Any

from agentevalops.core.schemas import (
    AgentInput,
    ResourceLimits,
    TerminationReason,
    TraceEvent,
    TraceEventKind,
)
from agentevalops.core.scripts import ScriptStep, parse_script
from agentevalops.core.types import AgentId, RunId
from agentevalops.environments.toy import toy_environment

logger = logging.getLogger(__name__)

#: Re-exported: the script grammar moved to ``core.scripts`` in R24 so that
#: domain packs can validate their trajectories without importing this family.
__all__ = ["MockAgentRunner", "ScriptStep", "parse_script"]


class MockAgentRunner:
    """Deterministic runner that never calls any model or external API.

    In the default (happy-path) mode it emits three or four events per task:

    1. ``AGENT_PLAN``          — records the plan text.
    2. ``AGENT_TOOL_CALL``     — *optional*; emitted only when the task
       metadata key ``emit_fake_tool_call`` is truthy.  The tool name is
       taken from ``fake_tool_name`` (defaults to ``"mock-tool"``).
    3. ``AGENT_FINAL_ANSWER``  — records the answer.
    4. ``AGENT_TERMINAL``      — signals completion; payload carries the
       fields needed to reconstruct ``AgentOutput``.

    So the happy path yields exactly **3 events** normally, or **4 events**
    when ``emit_fake_tool_call`` is set in the task metadata.

    Failure-mode (``should_fail=True``) emits exactly **2 events** per task:

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
    scripts:
        Scripted trajectories keyed by task id (docs/adr/0006 D11). A task
        with a script runs it -- every ``call`` through the task's real tool
        environment, so the harness records are genuine -- instead of the
        metadata-driven mode above. Validated when the runner is built.
    """

    agent_id: AgentId = AgentId("mock-agent-v1")

    def __init__(
        self,
        should_fail: bool = False,
        cost_per_task_usd: float = 0.0,
        scripts: (
            Mapping[str, Sequence[Mapping[str, Any] | ScriptStep]] | None
        ) = None,
    ) -> None:
        self._should_fail = should_fail
        self._cost_per_task_usd = cost_per_task_usd
        self._scripts = {
            str(task_id): parse_script(steps)
            for task_id, steps in (scripts or {}).items()
        }

    async def run(
        self,
        input: AgentInput,
        resource_limits: ResourceLimits,
    ) -> AsyncIterator[TraceEvent]:
        """Return an async iterator that yields the fixed demo events."""
        run_id = RunId(str(input.context.get("run_id", "unknown")))
        script = self._scripts.get(str(input.task.task_id))
        if script is not None:
            return self._scripted(run_id, input, script)
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

    async def _scripted(
        self, run_id: RunId, input: AgentInput, script: tuple[ScriptStep, ...]
    ) -> AsyncGenerator[TraceEvent, None]:
        environment = input.environment
        if environment is None:
            logger.warning(
                "MockAgentRunner: no tool environment given; using the toy "
                "environment"
            )
            environment = toy_environment(input.task.task_id)
        cost = self._cost_per_task_usd
        step = 0

        def _event(kind: TraceEventKind, payload: dict[str, Any]) -> TraceEvent:
            return TraceEvent(
                run_id=run_id, step_index=step, kind=kind, payload=payload
            )

        def _terminal(success: bool, answer: str | None) -> TraceEvent:
            reason = TerminationReason.AGENT_ERROR
            if success:
                reason = TerminationReason.COMPLETED
            return _event(
                TraceEventKind.AGENT_TERMINAL,
                {
                    "success": success,
                    "termination_reason": reason.value,
                    "final_answer": answer,
                    "total_cost_usd": cost,
                    "total_tokens": 0,
                    "wall_seconds": 0.0,
                },
            )

        yield _event(
            TraceEventKind.AGENT_PLAN, {"plan": f"Solve: {input.task.description}"}
        )
        step += 1
        for s in script:
            if s.kind == "say":
                yield _event(TraceEventKind.AGENT_PLAN, {"plan": s.text})
                step += 1
            elif s.kind == "observe":
                yield _event(TraceEventKind.AGENT_OBSERVATION, {"observation": s.text})
                step += 1
            elif s.kind == "call":
                yield _event(
                    TraceEventKind.AGENT_TOOL_CALL,
                    {"tool_name": s.report_as or s.tool, "args": dict(s.arguments)},
                )
                step += 1
                result = await environment.call(s.tool, dict(s.arguments))
                yield _event(
                    TraceEventKind.AGENT_TOOL_RESULT,
                    {"tool_name": s.tool, "result": result.content},
                )
                step += 1
            else:
                yield _event(TraceEventKind.AGENT_FINAL_ANSWER, {"answer": s.text})
                step += 1
                yield _terminal(True, s.text)
                return
        # A script that never finishes is the agent's failure, as in a real run.
        yield _terminal(False, None)
