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
from typing import Any, Literal

from agentevalops.core.types import AgentId, BackendId, RunId, TaskId

# ---------------------------------------------------------------------------
# Shared payload key constants
# ---------------------------------------------------------------------------

#: Canonical key for the tool name in AGENT_TOOL_CALL event payloads.
#: Both the producer (MockAgentRunner) and consumer (BasicPolicyChecker,
#: DeterministicEvaluator) must use this key so they can never drift apart.
TOOL_NAME_PAYLOAD_KEY: str = "tool_name"

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
    """Top-level configuration for a single evaluation run.

    ``model``: the model id passed to a model-backed runner (e.g.
    ``"gpt-4.1-mini-2025-04-14"``). ``None`` for the mock runner, which
    ignores it. This is the single source of truth for which model produced
    a run — ``config/loader.py`` rejects a model-backed ``runner_id`` config
    that omits it, and because the value lives here (not on a separate
    loader-only wrapper), ``config.json`` records it by construction: the
    whole ``RunConfig`` object is what gets serialized, so a new field here
    needs no separate write-back path to reach the bundle (docs/adr/0001 §3,
    closed 2026-08-05).

    ``deployment``/``api_version``: Azure OpenAI-only fields (``runner_id:
    azure-openai``, docs/adr/0004-cloud-backend-two-axes.md, Axis B).
    ``deployment`` is the Azure resource's deployment name -- Azure OpenAI
    keys API requests by deployment name, not model id, so this is a
    different value from ``model`` above, not a duplicate of it.
    ``api_version`` is the Azure OpenAI API version, which has no
    OpenAI-API equivalent default. Both additive, default ``None``, same
    shape ``model`` took in W12a: ``config/loader.py`` requires both when
    ``runner_id == "azure-openai"`` and rejects them for every other
    ``runner_id`` rather than silently ignoring stale state.
    """

    run_id: RunId
    agent_id: AgentId
    backend_id: BackendId
    max_concurrent_tasks: int = 1
    resource_limits: ResourceLimits = field(default_factory=ResourceLimits)
    model: str | None = None
    deployment: str | None = None
    api_version: str | None = None


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

    ``evaluated``: ``False`` means this task was never handed to the
    evaluator at all -- there was nothing to judge (e.g. the runner raised
    an infrastructure/configuration error before producing a result). The
    orchestrator decides this, not the evaluator (docs/adr/0002, Axis 2):
    an evaluator's contract is to judge a completed result against
    criteria, not to special-case "there is no result." ``passed=False``
    still holds in this case (there is no basis to call it a pass), but
    ``evaluated=False`` is what actually distinguishes "we judged this and
    it failed" from "we never got to judge it" -- collapsing the two into
    one boolean is the bug this field exists to fix.
    """

    evaluator_id: str
    # One of: "deterministic", "llm_judge", "tool_use", "state_based", "trace_quality"
    evaluator_kind: str
    score: float
    passed: bool
    evaluated: bool = True
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
# Run results
# ---------------------------------------------------------------------------
#
# These live in core (not the orchestration package) because ReportGenerator
# (a core protocol) needs to reference them. Moving them here keeps core a
# leaf: it depends on nothing outside itself, and orchestration/reports/
# bundles all import these types from core instead of from each other.


@dataclass
class TaskRunResult:
    """Outcome of a single task within a run."""

    task_id: TaskId
    output: AgentOutput
    evaluation: EvaluationResult
    event_count: int


@dataclass
class RunSummary:
    """Aggregated result of a complete orchestrator run.

    Three-way partition of ``total_tasks`` (docs/adr/0002, Axis 3):
    ``total_tasks == passed_tasks + failed_tasks + not_evaluated_tasks``.
    ``failed_tasks`` means a genuine, evaluated merit-based failure
    (``evaluated=True``, ``passed=False``) -- it no longer silently absorbs
    tasks that were never evaluated at all. ``not_evaluated_tasks`` names
    the observable fact (nothing was judged), not the inferred cause:
    infrastructure failure is one reason evaluation didn't happen, not the
    only conceivable one, so the field is not called ``infra_failed_tasks``.
    """

    run_id: RunId
    task_results: list[TaskRunResult] = field(default_factory=list)
    policy_verdict: PolicyVerdict | None = None
    score_result: ScoreResult | None = None
    total_tasks: int = 0
    passed_tasks: int = 0
    failed_tasks: int = 0
    not_evaluated_tasks: int = 0
    trace_event_count: int = 0
    total_cost_usd: float = 0.0
    total_tokens: int = 0


# ---------------------------------------------------------------------------
# Result bundle metadata
# ---------------------------------------------------------------------------


@dataclass
class ResultBundleMetadata:
    """Top-level metadata written to a result bundle's ``metadata.json``.

    ``schema_version`` is the contract version of the bundle format itself,
    not the platform version.  Increment it when bundle layout changes.
    Sourced from a single constant (``bundles.constants.BUNDLE_FORMAT_VERSION``)
    -- this field and ``manifest.json``'s ``bundle_format_version`` used to
    be two independently hardcoded literals (``"0.1.0"`` vs ``"0.1"``) that
    happened to agree by coincidence, not construction; see
    ``docs/adr/0002-outcome-taxonomy.md``.

    ``status`` (docs/adr/0002, Axis 4): ``"sealed"`` for a normal, complete
    run (every exit path that reaches the end of ``LocalOrchestrator.run()``
    normally -- including one with some ``not_evaluated_tasks``, since the
    *run* completed even if not every task did). ``"interrupted"`` for
    ``KeyboardInterrupt``/task cancellation. ``"partial"`` for anything else
    that stopped the CLI from reaching a normal return (an unexpected
    exception, or an infrastructure/configuration failure the orchestrator
    itself could not recover from). ``sealed: bool`` is kept for backward
    compatibility with any reader still checking the old boolean and is
    always derived from this field (``sealed == (status == "sealed")``), not
    set independently -- avoiding a second, driftable source of the same
    fact was the entire point of adding ``status``.

    ``git_sha`` / ``git_dirty`` (docs/adr/0002, Axis 4): what code produced
    this bundle. ``git_sha`` is never omitted and never fabricated -- outside
    a git checkout (or if ``git`` itself is unavailable) it is the literal
    sentinel string ``"unavailable"``, never ``None`` and never a blank or
    made-up value. ``git_dirty`` is ``True``/``False`` only when ``git_sha``
    is a real commit SHA and the working-tree state was actually determined;
    ``None`` whenever it wasn't (no checkout, or the sentinel case) --
    downstream consumers must treat ``git_sha == "unavailable"`` and
    ``git_dirty is True`` identically, as *cannot determine a match*, never
    as a match by omission. A clean SHA recorded against a dirty working
    tree would be a false provenance claim, which is the more likely
    failure mode than running entirely outside a checkout.
    """

    schema_version: str
    run_id: RunId
    created_at: datetime
    platform_version: str
    backend_id: BackendId
    task_count: int = 0
    sealed: bool = False
    status: Literal["sealed", "interrupted", "partial"] = "sealed"
    git_sha: str = "unavailable"
    git_dirty: bool | None = None
