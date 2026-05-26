"""Core schemas for AgentEvalOps.

All schema objects are plain Python dataclasses — no Pydantic, no ORM.
This keeps the core layer free of heavy dependencies and lets adapter
layers add their own serialisation on top.

Enum values are part of the public contract and MUST NOT be renamed
without a schema_version bump.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from agentevalops.core.types import AgentId, BackendId, RunId, TaskId

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TraceEventKind(str, Enum):
    """Closed set of semantic trace-event types.

    Using a closed enum rather than free-form strings makes trace analysis
    a typed problem rather than a regex problem.  New values require a
    schema_version bump.
    """

    AGENT_PLAN = "agent.plan"
    AGENT_TOOL_CALL = "agent.tool_call"
    AGENT_TOOL_RESULT = "agent.tool_result"
    AGENT_OBSERVATION = "agent.observation"
    AGENT_FINAL_ANSWER = "agent.final_answer"
    AGENT_TERMINAL = "agent.terminal"
    EVALUATOR_SCORE = "evaluator.score"
    POLICY_VERDICT = "policy.verdict"
    COST_TICK = "cost.tick"


class Verdict(str, Enum):
    """Binary-plus-warn verdict for post-run policy checks."""

    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"


class TerminationReason(str, Enum):
    """Reason an agent run stopped.

    Carried in ``AgentOutput.termination_reason``.  Knowing *why* a run
    stopped is more useful for regression analysis than the boolean alone.
    """

    COMPLETED = "completed"
    LIMIT_TOKENS = "limit_tokens"
    LIMIT_TIME = "limit_time"
    LIMIT_COST = "limit_cost"
    INFRA_FAILURE = "infra_failure"
    AGENT_ERROR = "agent_error"


# ---------------------------------------------------------------------------
# Runtime controls
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResourceLimits:
    """Hard ceilings enforced by the orchestrator *during* a run.

    Distinct from ``PolicySpec``: limits are enforced at execution time;
    policy is checked post-hoc on the completed trace.
    """

    max_tokens: int = 100_000
    max_wall_seconds: float = 3_600.0
    max_cost_usd: float = 10.0


# ---------------------------------------------------------------------------
# Run configuration
# ---------------------------------------------------------------------------


@dataclass
class RunConfig:
    """Top-level configuration for a single evaluation run."""

    run_id: RunId
    agent_id: AgentId
    backend_id: BackendId
    max_concurrent_tasks: int = 1
    resource_limits: ResourceLimits = field(default_factory=ResourceLimits)


# ---------------------------------------------------------------------------
# Task and agent I/O
# ---------------------------------------------------------------------------


@dataclass
class TaskSpec:
    """Describes a single evaluation task as provided by a BenchmarkAdapter."""

    task_id: TaskId
    benchmark_id: str
    description: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentInput:
    """Everything the agent receives when starting a task."""

    task: TaskSpec
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentOutput:
    """Result produced by an agent after completing or failing a task."""

    success: bool
    termination_reason: TerminationReason
    final_answer: str | None = None
    total_cost_usd: float = 0.0
    total_tokens: int = 0
    wall_seconds: float = 0.0
    error: str | None = None


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


@dataclass
class TraceEvent:
    """A single timestamped event emitted during a run.

    ``kind`` is a closed enum — open-ended strings make trace analysis
    a regex problem.  ``payload`` carries kind-specific structured data.
    """

    run_id: RunId
    step_index: int
    kind: TraceEventKind
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=_utcnow)
    cost_delta_usd: float = 0.0
    tokens_delta: int = 0


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


@dataclass
class EvaluationResult:
    """Score produced by one evaluator for one task.

    ``score`` is in [0.0, 1.0].  Deterministic evaluators use 0.0 or 1.0.
    ``citations`` are ``step_index`` values pointing at the specific events
    the score is based on.
    """

    evaluator_id: str
    # One of: "deterministic", "llm_judge", "tool_use", "state_based", "trace_quality"
    evaluator_kind: str
    score: float
    passed: bool
    confidence: float = 1.0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    citations: list[int] = field(default_factory=list)
    notes: str = ""


@dataclass
class ScoreResult:
    """Aggregated evaluation results across tasks within a run."""

    scorer_id: str
    run_id: RunId
    task_scores: list[EvaluationResult] = field(default_factory=list)
    aggregate_score: float = 0.0
    total_tasks: int = 0
    passed_tasks: int = 0
    failed_tasks: int = 0
    pass_rate: float = 0.0
    baseline_delta: float | None = None


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicySpec:
    """Declarative policy evaluated post-hoc against a completed trace.

    Distinct from ``ResourceLimits``: policy is a compliance question
    answered after the run; limits are enforced during the run.
    """

    policy_id: str
    max_cost_usd: float | None = None
    max_trace_events: int | None = None
    allowed_tool_ids: tuple[str, ...] = field(default_factory=tuple)
    deny_tool_ids: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class PolicyVerdict:
    """Result of a PolicyChecker evaluation.

    ``citations`` are ``step_index`` values pointing at the trace events
    that caused the verdict.
    """

    checker_id: str
    policy_id: str
    verdict: Verdict
    citations: list[int] = field(default_factory=list)
    notes: str = ""


# ---------------------------------------------------------------------------
# Result bundle metadata
# ---------------------------------------------------------------------------


@dataclass
class ResultBundleMetadata:
    """Top-level metadata written to a result bundle's ``metadata.json``.

    ``schema_version`` is the contract version of the bundle format itself,
    not the platform version.  Increment it when bundle layout changes.
    """

    schema_version: str
    run_id: RunId
    created_at: datetime
    platform_version: str
    backend_id: BackendId
    task_count: int = 0
    sealed: bool = False
