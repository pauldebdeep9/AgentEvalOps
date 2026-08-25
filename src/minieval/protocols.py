"""The two seams minieval actually swaps.

Six of the source repo's other Protocols — ``TraceStore``, ``ArtifactStore``,
``Scorer``, ``PolicyChecker``, ``ReportGenerator``, ``BenchmarkAdapter`` —
had exactly one implementation each, and ``CloudBackend`` had zero. A
Protocol with one implementation is a comment with syntax. ``AgentRunner``
(mock now, an OpenAI-compatible client later) and ``Evaluator`` are the two
that are actually swapped, so they are the two that stay Protocols.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from minieval.schemas import (
    AgentOutput,
    EvaluationResult,
    ResourceLimits,
    TaskSpec,
    TraceEvent,
)


@runtime_checkable
class AgentRunner(Protocol):
    """Executes one attempt at one task, streaming its trace.

    The contract the rest of the design rests on:

    - The runner **never raises on agent failure**. A wrong answer, a
      denied tool call, a step-limit overrun — none of these are exceptional
      conditions. The runner emits ``AGENT_TERMINAL`` with ``success=False``
      like any other outcome, and the trace records what happened.
    - The runner raises **only** on infrastructure failure: the model API
      erroring, a network failure, a malformed response from the underlying
      client. That is ``InfrastructureError`` (see ``errors.py``), and it is
      the one thing allowed to interrupt the stream instead of terminating
      it normally.
    - ``AGENT_TERMINAL`` is always the last event on a normal return, and
      its payload must carry enough to reconstruct an ``AgentOutput``
      (``success``, ``termination_reason``, ``final_answer``, ``steps``,
      ``cost_usd``, ``error``) — the trace stays the single source of truth
      about what happened, rather than a return value that could disagree
      with it.

    Whether the attempt was *evaluated* is deliberately not part of this
    contract. That is decided after the runner has finished or raised, by
    the orchestrator — the only party that knows whether a result ever
    reached an evaluator at all. A runner does not know, and should not
    guess, whether its own output will end up judged.
    """

    agent_id: str

    def run(self, task: TaskSpec, limits: ResourceLimits) -> AsyncIterator[TraceEvent]:
        """Stream the trace of one attempt at ``task`` under ``limits``."""
        ...


@runtime_checkable
class Evaluator(Protocol):
    """Judges one attempt's output against its task.

    A pure function over ``(task, output, trace)``: no I/O, no model calls,
    fully testable against hand-built traces without running an agent.
    """

    evaluator_id: str

    def evaluate(
        self, task: TaskSpec, output: AgentOutput, trace: list[TraceEvent]
    ) -> EvaluationResult:
        """Grade ``output`` against ``task.criteria``, citing ``trace``."""
        ...
