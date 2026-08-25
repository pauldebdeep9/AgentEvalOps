"""Core data types for minieval.

Everything downstream — evaluator, policy checker, scorer, report writer —
is a pure function over the types defined here.  There is no I/O in this
module and it imports nothing from the rest of the package: it is a leaf.

Two rules govern what lives here:

1. **No vocabulary without a constructor.**  Every enum member and every
   field below is produced by real code somewhere in the package.  An enum
   member that nothing ever emits is a lie that reads as a feature.

2. **One fact, one field.**  If two fields could disagree about the same
   fact, one of them is derived (a property) or absent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, NewType

# ---------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------

RunId = NewType("RunId", str)
TaskId = NewType("TaskId", str)


# ---------------------------------------------------------------------------
# Payload keys
# ---------------------------------------------------------------------------

TOOL_NAME_PAYLOAD_KEY: str = "tool_name"
"""Key a runner writes the tool name under in an ``AGENT_TOOL_CALL`` payload.

The evaluator and the policy checker both read this key back out — the
former to check ``required_tool_names``, the latter to check
``deny_tool_names``. A hand-typed string on both the producer and consumer
side can drift silently: a runner that writes ``"tool"`` while a checker
reads ``"tool_name"`` would make every deny-list check pass, not because the
tool was allowed, but because the checker never found the key at all. Import
this constant on both sides instead of retyping the string.
"""


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TraceEventKind(StrEnum):
    """Closed set of trace-event types.

    Closed, not free-form, so that trace analysis is a typed problem rather
    than a regex problem.  Five members, all of which the runners actually
    emit.  Deliberately absent: ``evaluator.score`` and ``policy.verdict``
    (evaluation results are their own types, not trace events — putting
    them in the trace creates a second source of the same fact), and
    ``cost.tick`` (cost is carried on every event by ``cost_usd``;
    a separate cost event would be that same second source).
    """

    AGENT_PLAN = "agent.plan"
    AGENT_TOOL_CALL = "agent.tool_call"
    AGENT_TOOL_RESULT = "agent.tool_result"
    AGENT_FINAL_ANSWER = "agent.final_answer"
    AGENT_TERMINAL = "agent.terminal"


class TerminationReason(StrEnum):
    """Why an attempt stopped.

    ``AGENT_ERROR`` is the agent failing at its task.  ``INFRA_FAILURE`` is
    the harness or the model API failing — a different fact, and the one
    that produces ``EvaluationResult.evaluated=False``.
    """

    COMPLETED = "completed"
    LIMIT_STEPS = "limit_steps"
    LIMIT_COST = "limit_cost"
    AGENT_ERROR = "agent_error"
    INFRA_FAILURE = "infra_failure"


class Verdict(StrEnum):
    """Post-hoc policy verdict."""

    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"


class Stability(StrEnum):
    """How a task behaved across its repeats.

    The reason ``repeats`` exists.  A task run once has no stability, only
    an outcome; the interesting question is whether a task passes *every*
    time, *never*, or *sometimes* — and only the third case is a flake.
    ``NOT_EVALUATED`` means no attempt was ever judged (see
    ``EvaluationResult.evaluated``); it is not the same as failing.
    """

    STABLE_PASS = "stable_pass"
    FLAKY = "flaky"
    STABLE_FAIL = "stable_fail"
    NOT_EVALUATED = "not_evaluated"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResourceLimits:
    """Ceilings enforced by the orchestrator *during* an attempt.

    Distinct from ``PolicySpec``, which is checked *after* the attempt ends.
    Limits stop a run; policy grades one.

    ``max_steps`` counts **model turns**, not tool calls.  A model that
    batches four parallel tool calls into one turn spends one step, not
    four — so a step budget does not bound tool-call volume and must not be
    described as if it does.
    """

    max_steps: int = 12
    max_cost_usd: float = 1.0


@dataclass(frozen=True)
class PolicySpec:
    """Organisational constraints graded after an attempt completes.

    This is never enforced in flight.  A denied tool call still happens;
    the point is that the trace proves it happened.
    """

    policy_id: str = "default"
    deny_tool_names: tuple[str, ...] = ()
    max_cost_usd: float | None = None


@dataclass(frozen=True)
class RunConfig:
    """Everything needed to reproduce a run, and nothing else.

    Serialised verbatim into the bundle's ``config.json``, so a new field
    here reaches the bundle by construction — no separate write-back path
    to keep in sync.
    """

    run_id: RunId
    runner: str = "mock"
    model: str | None = None
    repeats: int = 1
    limits: ResourceLimits = field(default_factory=ResourceLimits)
    policy: PolicySpec = field(default_factory=PolicySpec)


# ---------------------------------------------------------------------------
# Task and attempt I/O
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskSpec:
    """One evaluation task.

    ``criteria`` holds the evaluator's expectations (expected substring,
    required tool names, required trace kinds).  Keeping them on the task
    rather than in evaluator config means a task carries its own definition
    of success.
    """

    task_id: TaskId
    description: str
    criteria: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentOutput:
    """What one attempt produced.

    Reconstructed from the ``AGENT_TERMINAL`` event's payload, so the trace
    remains the single source of truth about what happened.
    """

    success: bool
    termination_reason: TerminationReason
    final_answer: str | None = None
    steps: int = 0
    cost_usd: float = 0.0
    error: str | None = None


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


@dataclass
class TraceEvent:
    """One timestamped event emitted during an attempt.

    The substrate.  Every evaluator, policy check, and report in this
    package reads a ``list[TraceEvent]`` and nothing else.
    """

    run_id: RunId
    task_id: TaskId
    attempt: int
    step_index: int
    kind: TraceEventKind
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=_utcnow)
    cost_usd: float = 0.0

    def to_json(self) -> str:
        """Serialise to one JSONL line."""
        return json.dumps(
            {
                "run_id": str(self.run_id),
                "task_id": str(self.task_id),
                "attempt": self.attempt,
                "step_index": self.step_index,
                "kind": self.kind.value,
                "payload": self.payload,
                "timestamp": self.timestamp.isoformat(),
                "cost_usd": self.cost_usd,
            },
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, line: str) -> TraceEvent:
        """Parse one JSONL line.  Raises on malformed input, by design."""
        d = json.loads(line)
        return cls(
            run_id=RunId(d["run_id"]),
            task_id=TaskId(d["task_id"]),
            attempt=int(d["attempt"]),
            step_index=int(d["step_index"]),
            kind=TraceEventKind(d["kind"]),
            payload=dict(d.get("payload", {})),
            timestamp=datetime.fromisoformat(d["timestamp"]),
            cost_usd=float(d.get("cost_usd", 0.0)),
        )


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


@dataclass
class EvaluationResult:
    """One evaluator's judgement of one attempt.

    ``evaluated=False`` means the attempt was never judged — there was
    nothing to judge, because the runner failed before producing a result.
    ``passed=False`` still holds (there is no basis to call it a pass), but
    collapsing "judged and failed" into "never judged" is the bug this
    field exists to prevent.  The orchestrator sets it, not the evaluator:
    an evaluator's job is to grade a result, not to special-case the
    absence of one.

    ``citations`` are ``step_index`` values pointing at the events the
    judgement rests on.
    """

    evaluator_id: str
    passed: bool
    evaluated: bool = True
    citations: list[int] = field(default_factory=list)
    notes: str = ""


@dataclass
class PolicyVerdict:
    """Result of grading one attempt against a ``PolicySpec``."""

    policy_id: str
    verdict: Verdict
    citations: list[int] = field(default_factory=list)
    notes: str = ""


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class Attempt:
    """One execution of one task, with its own trace.

    Events live here rather than in a run-wide list so the orchestrator
    never has to thread a parallel sequence alongside its results.  The
    bundle flattens them at write time.
    """

    index: int
    output: AgentOutput
    evaluation: EvaluationResult
    policy: PolicyVerdict
    events: list[TraceEvent] = field(default_factory=list)


@dataclass
class TaskResult:
    """All attempts at one task.

    Every aggregate below is a property, derived from ``attempts`` on
    demand.  There is no stored count that could disagree with the list it
    was counted from.
    """

    task: TaskSpec
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def n(self) -> int:
        """Attempts made."""
        return len(self.attempts)

    @property
    def k(self) -> int:
        """Attempts that passed.  Report as ``k/n``, never as a percentage."""
        return sum(1 for a in self.attempts if a.evaluation.passed)

    @property
    def evaluated(self) -> int:
        """Attempts that were actually judged."""
        return sum(1 for a in self.attempts if a.evaluation.evaluated)

    @property
    def stability(self) -> Stability:
        """Classify behaviour across repeats."""
        if self.evaluated == 0:
            return Stability.NOT_EVALUATED
        if self.k == self.evaluated:
            return Stability.STABLE_PASS
        if self.k == 0:
            return Stability.STABLE_FAIL
        return Stability.FLAKY

    @property
    def cost_usd(self) -> float:
        return sum(a.output.cost_usd for a in self.attempts)


@dataclass
class RunSummary:
    """Aggregated outcome of a whole run.

    The attempt-level partition holds by construction:
    ``total_attempts == passed + failed + not_evaluated``.  It is asserted
    in the tests, not merely intended.
    """

    run_id: RunId
    config: RunConfig
    task_results: list[TaskResult] = field(default_factory=list)

    @property
    def total_attempts(self) -> int:
        return sum(t.n for t in self.task_results)

    @property
    def passed_attempts(self) -> int:
        return sum(t.k for t in self.task_results)

    @property
    def not_evaluated_attempts(self) -> int:
        return sum(t.n - t.evaluated for t in self.task_results)

    @property
    def failed_attempts(self) -> int:
        """Judged and found wanting — never absorbs unevaluated attempts."""
        return self.total_attempts - self.passed_attempts - self.not_evaluated_attempts

    @property
    def total_cost_usd(self) -> float:
        return sum(t.cost_usd for t in self.task_results)

    def tasks_by_stability(self, stability: Stability) -> list[TaskId]:
        """Named task ids for one stability class, for ``k/n`` reporting."""
        return [t.task.task_id for t in self.task_results if t.stability is stability]
