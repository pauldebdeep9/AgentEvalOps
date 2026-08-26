"""The harness's own deterministic test double for ``AgentRunner``.

Written before any real model-backed runner so every downstream test —
evaluator, policy checker, orchestrator — has a source of traces that is
fully offline and fully predictable. No randomness, no seeding: every
``mock_`` key maps to one fixed behaviour.

Reads only ``mock_``-prefixed keys off ``TaskSpec.criteria``: ``mock_answer``,
``mock_tools``, ``mock_fail``, ``mock_extra_steps``, ``mock_pass_on``. It
must never look at ``expected_substring``, ``required_tool_names``,
``required_trace_kinds``, or any other key an evaluator reads. Those are the
answer key. A runner that can see them would make every task pass by
construction, and the harness would measure nothing — the failure would be
invisible precisely because everything would look green. The prefix is the
enforcement mechanism: as long as every evaluator-side criterion stays
unprefixed and every mock-side one starts with ``mock_``, the boundary is
auditable by grep alone, without reading either side's implementation.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from minieval.errors import ConfigurationError, InfrastructureError
from minieval.schemas import (
    TOOL_NAME_PAYLOAD_KEY,
    AgentOutput,
    ResourceLimits,
    RunId,
    TaskSpec,
    TerminationReason,
    TraceEvent,
    TraceEventKind,
)

MOCK_EVENT_COST_USD: float = 0.01
"""Cost reported on every event the mock runner emits.

A fixed, named constant rather than a literal repeated at each call site,
so a task that chains enough ``mock_tools`` or ``mock_extra_steps`` has
something real to trip ``ResourceLimits.max_cost_usd`` against.
"""

_TURN_INITIATING_KINDS = (TraceEventKind.AGENT_PLAN, TraceEventKind.AGENT_FINAL_ANSWER)
"""A step is one model turn, and every model turn emits exactly one
turn-initiating event: ``AGENT_PLAN`` (the model chose to act) or
``AGENT_FINAL_ANSWER`` (the model chose to stop). ``AGENT_TOOL_RESULT`` is
environment output, never a turn; ``AGENT_TERMINAL`` is harness bookkeeping,
never a turn either. ``AgentOutput.steps`` is membership in this set,
counted once from the events actually emitted — never a separate counter
that could disagree with what the trace shows.
"""

MOCK_WRONG_ANSWER: str = "mock runner: deliberately incorrect answer"
"""Returned verbatim, ignoring ``mock_answer`` entirely, on a non-passing
``mock_pass_on`` attempt.

The wrong variant must not *contain* the right one. An earlier version
built it by appending to the correct answer (``f"{answer} (wrong)"``), but
appending can never remove a substring: any containment-based check (such
as the evaluator's ``expected_substring``) would still find the correct
text inside the "wrong" one and pass regardless of ``mock_pass_on``, which
makes ``Stability.FLAKY`` unreachable. A fixed, unrelated string closes
that gap unconditionally, for every task that uses ``mock_pass_on``, not
just the one that first exposed it.
"""

_PLACEHOLDER_ANSWER = "MOCK_ANSWER_NOT_SET"
_EXTRA_STEP_TOOL_NAME = "noop"
_RECOGNISED_FAIL_MODES = frozenset({"infra", "agent_error"})


class MockRunner:
    """Deterministic ``AgentRunner`` driven entirely by ``TaskSpec.criteria``.

    One instance handles one attempt: ``run_id`` and ``attempt`` are fixed
    at construction rather than passed to ``run()``, because every
    ``TraceEvent`` needs both and widening ``AgentRunner.run()``'s signature
    to carry them would push this bookkeeping into every real runner's
    interface too.
    """

    def __init__(self, run_id: RunId, attempt: int) -> None:
        self.agent_id: str = "mock"
        self._run_id: RunId = run_id
        self._attempt: int = attempt

    async def run(
        self, task: TaskSpec, limits: ResourceLimits
    ) -> AsyncGenerator[TraceEvent, None]:
        """Emit one AGENT_PLAN per tool round, immediately before that
        round's AGENT_TOOL_CALL — a task with no tools emits no plan at
        all, going straight to AGENT_FINAL_ANSWER, because that is what a
        real loop with nothing to do would produce.

        ``mock_fail="infra"`` raises right after the attempt's first event,
        whatever that is, guaranteeing a partial trace regardless of task
        shape. ``mock_fail="agent_error"`` reaches AGENT_TERMINAL with
        success=False instead of ever producing a final answer — a result,
        not an exception, because the agent failing at its task is not the
        harness failing. An unrecognised ``mock_fail`` is a configuration
        mistake, not a failure mode to simulate, so it raises
        ``ConfigurationError`` before anything is emitted.
        """
        fail_mode = task.criteria.get("mock_fail")
        if fail_mode is not None and fail_mode not in _RECOGNISED_FAIL_MODES:
            raise ConfigurationError(f"unrecognised mock_fail value: {fail_mode!r}")

        answer = task.criteria.get("mock_answer", _PLACEHOLDER_ANSWER)
        tools: list[str] = task.criteria.get("mock_tools", [])
        extra_steps: int = task.criteria.get("mock_extra_steps", 0)
        pass_on = task.criteria.get("mock_pass_on")
        if pass_on is not None and self._attempt not in pass_on:
            answer = MOCK_WRONG_ANSWER

        emitted: list[TraceEvent] = []

        def build(kind: TraceEventKind, payload: dict[str, Any]) -> TraceEvent:
            event = TraceEvent(
                run_id=self._run_id,
                task_id=task.task_id,
                attempt=self._attempt,
                step_index=len(emitted),
                kind=kind,
                payload=payload,
                cost_usd=MOCK_EVENT_COST_USD,
            )
            emitted.append(event)
            return event

        def turns() -> int:
            return sum(1 for e in emitted if e.kind in _TURN_INITIATING_KINDS)

        def cost_including_terminal() -> float:
            return sum(e.cost_usd for e in emitted) + MOCK_EVENT_COST_USD

        def maybe_fail_infra() -> None:
            if fail_mode == "infra" and len(emitted) == 1:
                raise InfrastructureError(
                    "mock runner: simulated infrastructure failure"
                )

        rounds = [*tools, *([_EXTRA_STEP_TOOL_NAME] * extra_steps)]
        for tool_name in rounds:
            yield build(TraceEventKind.AGENT_PLAN, {})
            maybe_fail_infra()
            yield build(
                TraceEventKind.AGENT_TOOL_CALL, {TOOL_NAME_PAYLOAD_KEY: tool_name}
            )
            yield build(
                TraceEventKind.AGENT_TOOL_RESULT, {TOOL_NAME_PAYLOAD_KEY: tool_name}
            )

        if fail_mode == "agent_error":
            yield build(
                TraceEventKind.AGENT_TERMINAL,
                AgentOutput(
                    success=False,
                    termination_reason=TerminationReason.AGENT_ERROR,
                    final_answer=None,
                    steps=turns(),
                    cost_usd=cost_including_terminal(),
                    error="mock runner: simulated agent error",
                ).to_payload(),
            )
            return

        yield build(TraceEventKind.AGENT_FINAL_ANSWER, {"final_answer": answer})
        maybe_fail_infra()

        yield build(
            TraceEventKind.AGENT_TERMINAL,
            AgentOutput(
                success=True,
                termination_reason=TerminationReason.COMPLETED,
                final_answer=answer,
                steps=turns(),
                cost_usd=cost_including_terminal(),
            ).to_payload(),
        )
