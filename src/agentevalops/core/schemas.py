"""Core schemas for AgentEvalOps.

All schema objects are plain Python dataclasses — no Pydantic, no ORM.
This keeps the core layer free of heavy dependencies and lets adapter
layers add their own serialisation on top.

Enum values are part of the public contract and MUST NOT be renamed
without a schema_version bump.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from agentevalops.core.types import AgentId, BackendId, RunId, TaskId

if TYPE_CHECKING:  # protocols.py imports this module; avoid the cycle
    from agentevalops.core.protocols import ToolEnvironment

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
    schema_version bump; so does removing one. ``policy.verdict`` was
    removed at format 0.6 (docs/adr/0007 D3): a policy verdict is one
    judgement over the whole run, ``policy.json`` holds it, and no bundle
    ever carried one as an event.
    """

    AGENT_PLAN = "agent.plan"
    AGENT_TOOL_CALL = "agent.tool_call"
    AGENT_TOOL_RESULT = "agent.tool_result"
    AGENT_OBSERVATION = "agent.observation"
    AGENT_FINAL_ANSWER = "agent.final_answer"
    AGENT_TERMINAL = "agent.terminal"
    EVALUATOR_SCORE = "evaluator.score"
    COST_TICK = "cost.tick"


#: Events the harness writes about a task run, not the agent's activity
#: (docs/adr/0007 D3): one ``evaluator.score`` per evaluation result, from
#: format 0.6. The policy's trace ceiling does not count them.
HARNESS_EVENT_KINDS: frozenset[TraceEventKind] = frozenset(
    {TraceEventKind.EVALUATOR_SCORE}
)


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


#: Field metadata: the serialiser leaves the field out of a record while its
#: value is None, so an optional field added later changes no existing
#: record (R35: ``run_limits``, ``run_budget``). A reader takes the default.
OMIT_WHEN_NONE: dict[str, bool] = {"omit_when_none": True}


@dataclass(frozen=True)
class RunLimits:
    """Caps on a whole run's spend, summed over every task run (R35; ROADMAP
    §0, external review finding 9). ``None`` means off.

    Unlike ``ResourceLimits``, which each runner checks for its own task,
    the orchestrator enforces these between task runs: once a cap is
    reached it starts no further task run, lets those already running
    finish, and records each run it did not start as not evaluated, with
    the cause ``run_budget_exhausted``.
    """

    max_run_cost_usd: float | None = None
    max_run_tokens: int | None = None

    def reached(self, spent_usd: float, spent_tokens: int) -> bool:
        """Whether the spend has reached a cap that is set: the one rule the
        orchestrator asks before each task run starts, validate-bundle holds
        a budget stop to, and the run's budget line reads at its end. A cost
        within float rounding of its cap has reached it: ten task runs at
        $0.10 add up to 0.9999999999999999."""
        cost, tokens = self.max_run_cost_usd, self.max_run_tokens
        return (
            cost is not None
            and (spent_usd >= cost or math.isclose(spent_usd, cost, rel_tol=1e-9))
        ) or (tokens is not None and spent_tokens >= tokens)

    def label(self) -> str:
        """The caps that are set, as the budget line and a stopped task
        run's detail name them: ``$0.5000, 20 tokens``."""
        caps = []
        if self.max_run_cost_usd is not None:
            caps.append(f"${self.max_run_cost_usd:.4f}")
        if self.max_run_tokens is not None:
            caps.append(f"{self.max_run_tokens} tokens")
        return ", ".join(caps)


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
    #: docs/adr/0006 D18: the orchestrator runs each task this many times.
    #: Always 1 until R27 gives it a YAML path.
    repetitions: int = 1
    #: docs/adr/0006 D6: base of the per-(task, repetition) seeds. Unused
    #: until R26's perturbations; recorded so a run is reproducible.
    seed: int = 0
    #: R35: the run-level budget, from the run config or the system
    #: declaration; None (off) is left out of config.json.
    run_limits: RunLimits | None = field(default=None, metadata=OMIT_WHEN_NONE)
    #: R36: resamples per pass^k interval, from the run config or the system
    #: declaration; the loader always sets it (10,000 unless set; 0 turns
    #: intervals off). None -- a record from before R36, or a config built
    #: without the loader -- derives no interval, and is left out.
    bootstrap_samples: int | None = field(default=None, metadata=OMIT_WHEN_NONE)


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
    #: docs/adr/0006 D13: the evaluator kinds that apply to this task.
    #: Empty means every evaluator the run configures. Not serialised.
    applicable_evaluators: tuple[str, ...] = ()


@dataclass(frozen=True)
class Scenario:
    """One scenario of a domain pack (docs/adr/0006 D11, D23).

    ``known_good`` and ``known_bad`` are scripted trajectories as data --
    steps like ``{"call": tool, "arguments": {...}}``, ``{"say": text}``,
    ``{"finish": answer}`` -- which ``MockAgentRunner`` runs. ``expected``
    is what the evaluators check (copied into the task's metadata), and
    ``control_ids`` are the catalogue controls the scenario evidences.
    ``environment`` configures the scenario's environment declaratively:
    ``approvals`` -- the simulated approver's answers per irreversible
    tool, per call (D5) -- and ``faults`` and ``injections``, the R26
    schedules (D6). The pack reads it in ``build_environment``.
    ``outcome_group`` is the MGF-Agentic §1.2.2 kind of harm the scenario's
    failure would be (``NEGATIVE_OUTCOMES``); the report groups by it.
    ``variant`` says how a derived scenario varies its base (R26): a
    ``counterfactual`` -- the base's case with one protected attribute
    changed -- or a ``perturbation`` of the task text. Copied into the
    task's metadata for the report.
    """

    scenario_id: str
    description: str
    category: str
    known_good: tuple[dict[str, Any], ...]
    known_bad: tuple[dict[str, Any], ...] = ()
    applicable_evaluators: tuple[str, ...] = ()
    control_ids: tuple[str, ...] = ()
    expected: dict[str, Any] = field(default_factory=dict)
    environment: dict[str, Any] = field(default_factory=dict)
    outcome_group: str = ""
    variant: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EnvHandle:
    """Opaque handle to a task's prepared workspace (docs/benchmark_adapters.md D5).

    Returned by ``BenchmarkAdapter.prepare_environment`` and consumed by
    ``BenchmarkAdapter.teardown``. ``workspace_path=None`` means this
    adapter has no filesystem workspace for the task, not that preparation
    was skipped or failed.
    """

    task_id: TaskId
    workspace_path: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    #: The task's tool environment (docs/adr/0006 D1), returned by
    #: ``prepare_environment`` and handed to the runner by the orchestrator.
    tools: ToolEnvironment | None = None


@dataclass
class AgentInput:
    """Everything the agent receives when starting a task.

    ``environment`` (docs/adr/0006 D1): where the runner gets its tool specs
    and sends its tool calls. The orchestrator always sets it; ``None`` only
    happens when a runner is called directly, as the runner tests do.
    """

    task: TaskSpec
    context: dict[str, Any] = field(default_factory=dict)
    environment: ToolEnvironment | None = None


# ---------------------------------------------------------------------------
# Tools (docs/adr/0006-tool-environment-and-observation-seam.md)
# ---------------------------------------------------------------------------

#: Permission class of a tool (D3). ``irreversible`` calls go through the
#: environment's approval gate (D5, built in R25).
ToolPermission = Literal["read", "write", "irreversible"]

#: Sensitivity of the data a tool returns or exports (D3).
ToolDataClass = Literal["normal", "restricted"]

#: Approval state of one recorded call (D4, D5).
ToolApproval = Literal["not_required", "approved", "denied", "timed_out"]

#: The faults a scenario may schedule on a tool call (D6; ROADMAP R26).
FAULT_KINDS: frozenset[str] = frozenset(
    {"timeout", "transient_error", "malformed_result"}
)

#: MGF-Agentic §1.2.2's five kinds of harm, as this project labels them
#: (docs/imda-alignment.md §6), in the section's order. Every scenario
#: declares the group its failure would fall in; the report groups by it.
NEGATIVE_OUTCOMES: tuple[str, ...] = (
    "erroneous_action",
    "unauthorised_action",
    "biased_action",
    "data_breach",
    "disruption",
)

#: A tool environment's end state (D7): a JSON object whose ``schema`` key is
#: chosen by the domain pack.
EndState = dict[str, Any]


@dataclass(frozen=True)
class ToolSpec:
    """One tool, in one provider-neutral shape (D3).

    ``input_schema`` is a JSON Schema object. ``permission`` and
    ``data_class`` are harness-side and never sent to a model provider; each
    runner converts the rest to its own provider format (D9).
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    permission: ToolPermission = "read"
    data_class: ToolDataClass = "normal"


@dataclass(frozen=True)
class ToolResult:
    """What the agent sees for one tool call (D2)."""

    content: str
    error: bool = False


@dataclass(frozen=True)
class ToolCallRecord:
    """The harness's own record of one tool call (D4).

    Written only by the environment, never by the agent. ``index`` orders
    the calls within one task run; ``timestamp`` is information only and is
    never compared by ``rescore``.
    """

    task_id: TaskId
    index: int
    tool_name: str
    arguments: dict[str, Any]
    permission: ToolPermission | None
    data_class: ToolDataClass | None
    executed: bool
    result: str
    error: bool = False
    approval: ToolApproval = "not_required"
    fault: str | None = None
    injections: tuple[str, ...] = ()
    repetition: int = 0
    timestamp: datetime = field(default_factory=lambda: _utcnow())


#: Names no environment may declare (D3): ``finish`` is the runners' own
#: control, never an environment tool.
RESERVED_TOOL_NAMES: frozenset[str] = frozenset({"finish"})

#: The ``finish`` control's spec (D9). Every runner offers it after the
#: environment's tools and handles the call itself.
FINISH_TOOL_SPEC = ToolSpec(
    name="finish",
    description=(
        "Return the final answer to the user's task. Call this exactly "
        "once when you are confident in the answer."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "answer": {
                "type": "string",
                "description": "The final answer string.",
            }
        },
        "required": ["answer"],
    },
)


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
# System declaration (docs/adr/0005 D6; ROADMAP R27)
# ---------------------------------------------------------------------------

#: MGF-Agentic §2.3.2's four categories, as this project labels them
#: (docs/imda-alignment.md), in the section's order. Every scenario of a
#: domain pack is in one; the plan's thresholds are per category.
MGF_CATEGORIES: tuple[str, ...] = (
    "task_execution",
    "tool_calling",
    "policy_compliance",
    "robustness",
)

#: The level of human involvement a system declares. MGF-Agentic §1.1.3
#: describes levels of human involvement in an agent's actions; these three
#: labels are this project's own words for them, not the framework's
#: (docs/adr/0005 D5). ``human_approves``: a person answers each approval
#: request before an irreversible action. ``human_monitors``: a person can
#: see the actions but answers no approval request. ``autonomous``: no
#: person in the loop.
InvolvementLevel = Literal["human_approves", "human_monitors", "autonomous"]
INVOLVEMENT_LEVELS: tuple[str, ...] = ("human_approves", "human_monitors", "autonomous")

#: How a threshold is used (docs/adr/0005 D6): a ``hard`` gate fails the
#: run; a ``comparative`` threshold is reported and never gates.
GateKind = Literal["hard", "comparative"]
GATE_KINDS: tuple[str, ...] = ("hard", "comparative")


@dataclass(frozen=True)
class DeclaredTool:
    """One tool the system under test exposes, as its owner declares it.

    ``permission`` and ``data_class`` use the harness's own vocabulary
    (``ToolSpec``); the plan checks them against the pack's specs.
    ``reversible`` is ``False`` exactly when ``permission`` is
    ``irreversible``: a declaration that says otherwise contradicts the
    approval gate (docs/adr/0006 D5) and is rejected. ``external`` says
    whether the tool reaches outside the system (a notice sent, a record
    exported); the pack has no view of it, so it is the owner's fact.
    """

    name: str
    permission: ToolPermission
    data_class: ToolDataClass = "normal"
    reversible: bool = True
    external: bool = False


@dataclass(frozen=True)
class BusinessMetric:
    """A metric the owner names (name and definition), recorded in the plan
    and repeated in the report, never computed (testing-framework 3.7.1)."""

    name: str
    definition: str


@dataclass(frozen=True)
class ThresholdOverride:
    """The owner's threshold for one category, from ``system.yaml``. Either
    field may be omitted; the plan fills the rest from the pack's and
    records each value's source (testing-framework 3.6.1)."""

    category: str
    min_pass_k: float | None = None
    gate: GateKind | None = None


@dataclass(frozen=True)
class SystemDeclaration:
    """What the owner declares about the system under test (``system.yaml``):
    the scoping step, from which the plan selects scenarios and thresholds.

    ``intended_use``, ``out_of_scope`` and ``business_metrics`` are recorded
    and repeated, never evaluated. ``repetitions`` and ``seed`` are the
    run's (docs/adr/0006 D18, D6). ``sha256`` is of the YAML bytes the
    declaration was loaded from, empty when it was built in code.
    """

    system_id: str
    pack_id: str
    involvement: InvolvementLevel
    tools: tuple[DeclaredTool, ...]
    intended_use: str
    out_of_scope: tuple[str, ...] = ()
    business_metrics: tuple[BusinessMetric, ...] = ()
    repetitions: int = 1
    seed: int = 0
    thresholds: tuple[ThresholdOverride, ...] = ()
    #: R36: resamples per pass^k interval, declared here like repetitions
    #: and seed; None (not declared: the loader's default) is left out.
    bootstrap_samples: int | None = field(default=None, metadata=OMIT_WHEN_NONE)
    #: R35: the run-level budget, declared here like repetitions and seed;
    #: None (off) is left out of the plan.
    run_limits: RunLimits | None = field(default=None, metadata=OMIT_WHEN_NONE)
    sha256: str = ""


@dataclass(frozen=True)
class CategoryThreshold:
    """A domain pack's threshold for one category (docs/adr/0005 D6):
    ``min_pass_k`` is the least fraction of the category's scenarios that
    must pass every repetition (pass^k); ``gate`` says whether falling
    short fails the run. The owner may tighten either in ``system.yaml``."""

    category: str
    min_pass_k: float
    gate: GateKind


#: Who set a threshold's value in the plan: the domain pack, or the owner
#: in ``system.yaml`` (testing-framework 3.6.1).
ThresholdSource = Literal["pack", "owner"]


@dataclass(frozen=True)
class PlanThreshold:
    """One category's threshold as the plan fixes it, each value with its
    source."""

    category: str
    min_pass_k: float
    min_pass_k_source: ThresholdSource
    gate: GateKind
    gate_source: ThresholdSource


@dataclass(frozen=True)
class PlanExclusion:
    """A candidate scenario the declaration left out, and the rule that
    excluded it (``plans/builder.py``)."""

    scenario_id: str
    reason: str


@dataclass(frozen=True)
class RunPlan:
    """The pre-registered test plan, ``test_plan.json`` (docs/adr/0005 D6):
    written and hashed before the first task runs, so what the run was
    going to test, and against what, is fixed before it tests it.

    Plan version "2" (docs/adr/0007 D2, D9): every 0.6 bundle has a plan.
    ``declared`` is ``False`` for a run without a system declaration, whose
    plan has no ``system``, ``system_path`` or pack, selects the tasks the
    run executes and sets no threshold. ``data_sha256`` is the pack's data
    hash (``DataHashed``), ``None`` when the pack names none.

    ``system`` is the declaration as loaded; ``scenarios_sha256`` is the
    hash of the pack's scenarios as loaded, so a pack edit after the plan
    shows. ``seeds`` holds every (task, repetition) seed D6 derives.
    ``requested`` is the run config's own subset, or ``None`` for the whole
    pack; ``selected`` and ``excluded`` are the declaration's choice over
    it, in the pack's order. ``uncovered_tools`` are declared tools the
    pack does not model: recorded, since a real system may have more tools
    than a pack exercises, never an error.
    """

    plan_version: str
    run_id: str
    config_name: str
    created_at: str
    platform_version: str
    system: SystemDeclaration | None
    system_path: str | None
    pack_id: str | None
    pack_version: str | None
    scenarios_sha256: str | None
    repetitions: int
    seed: int
    evaluators: tuple[str, ...]
    requested: tuple[str, ...] | None
    selected: tuple[str, ...]
    excluded: tuple[PlanExclusion, ...]
    seeds: dict[str, tuple[int, ...]]
    thresholds: tuple[PlanThreshold, ...]
    uncovered_tools: tuple[str, ...]
    declared: bool = True
    data_sha256: str | None = None


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
    #: docs/adr/0006 D17 (format 0.5): which task run emitted this event.
    #: Stamped by the orchestrator, not the runner; ``step_index`` restarts
    #: at 0 for every task run, so it is unique only together with these.
    task_id: TaskId | None = None
    repetition: int = 0


@dataclass(frozen=True)
class Ref:
    """Addresses one trace event or one harness call record across a whole
    run (docs/adr/0006 D17, format 0.5)."""

    stream: Literal["trace", "tool_calls"]
    task_id: TaskId | None
    repetition: int
    index: int


#: Why a task run was not evaluated (docs/adr/0006 D16, format 0.5).
#: ``run_budget_exhausted`` (R35): the run's budget was reached before this
#: task run started. A reader older than R35 refuses a record that holds it.
NotEvaluatedCause = Literal[
    "infrastructure", "configuration", "environment_defect", "run_budget_exhausted"
]

#: R35: the cause of a task run the run's budget stopped before it started.
BUDGET_STOP: NotEvaluatedCause = "run_budget_exhausted"

#: The policy rules a scenario may declare for the ``policy`` evaluator
#: (ROADMAP R25). A rule names tool *roles* -- lookup, decide, notify,
#: refer, export -- which the domain pack maps to its tools, and looks only
#: at the harness records on the scenario's case:
#: ``lookup_before_decision`` (the case was looked up before it was decided
#: or referred); ``decision_before_notice`` (every notice attempt follows a
#: decision that executed); ``no_retry_after_denial`` (a tool the approver
#: refused on the case is not tried again); ``refer_before_finish`` (an
#: ambiguous case is referred to an officer and never decided);
#: ``least_privilege`` (every recorded call, run or not, names a tool in
#: the scenario's ``allowed_tools``); ``restricted_export_destination``
#: (every export attempt names a destination inside the pack's
#: ``export_domains`` -- the restricted-data boundary, R25);
#: ``notice_transparency`` (every notice that went out on the case says an
#: AI prepared it and lists exactly the case's seeded reason codes --
#: testing-framework 1.1.3 and 1.1.4, R25); ``no_pii_leak`` (no notice
#: that went out carries an identifier that is not the addressee's own, or
#: the addressee's own in full where the pack requires masking; exports and
#: the final answer are reported, not failed -- testing-framework 8.3.2);
#: ``call_budget`` (the run makes at most the scenario's ``call_budget``
#: tool calls, attempts included -- a retry storm counts as disruption to
#: connected systems, R26); ``counterfactual_consistency`` (a counterfactual
#: variant makes exactly the calls its base's known-good makes -- the same
#: tools with the same arguments in the same order -- so a pair may differ
#: in the swapped attribute only, R26).
POLICY_RULES: frozenset[str] = frozenset(
    {
        "lookup_before_decision",
        "decision_before_notice",
        "no_retry_after_denial",
        "refer_before_finish",
        "least_privilege",
        "restricted_export_destination",
        "notice_transparency",
        "no_pii_leak",
        "call_budget",
        "counterfactual_consistency",
    }
)


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
    # One of: "deterministic", "llm_judge", "tool_use", "state_based",
    # "trace_quality", "policy" (the sixth kind, ROADMAP R25)
    evaluator_kind: str
    score: float
    passed: bool
    evaluated: bool = True
    confidence: float = 1.0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    citations: list[int] = field(default_factory=list)
    notes: str = ""
    #: docs/adr/0006 D13: ``False`` when the evaluator found none of the
    #: inputs it needs in this task; such a result is not counted.
    applicable: bool = True
    #: docs/adr/0006 D16: set when ``evaluated`` is ``False``.
    not_evaluated_cause: NotEvaluatedCause | None = None
    #: docs/adr/0006 D17: run-wide addresses of what the score rests on.
    #: ``citations`` stays, for 0.1-0.4 readers.
    refs: list[Ref] = field(default_factory=list)


@dataclass
class ScoreResult:
    """Aggregated evaluation results across tasks within a run."""

    scorer_id: str
    run_id: RunId
    task_scores: list[EvaluationResult] = field(default_factory=list)
    aggregate_score: float = 0.0
    total_tasks: int = 0
    passed_tasks: int = 0
    #: Evaluated and failed (docs/adr/0006 D20, format 0.5). Before 0.5 it
    #: also counted not-evaluated tasks.
    failed_tasks: int = 0
    pass_rate: float = 0.0
    baseline_delta: float | None = None
    not_evaluated_tasks: int = 0


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
    #: docs/adr/0006 D17: run-wide addresses of the cited events.
    refs: list[Ref] = field(default_factory=list)


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
    """Outcome of a single task run (one repetition of one task).

    ``evaluation`` is the task run's verdict (docs/adr/0006 D13): the one
    counted result when a single evaluator applies, otherwise derived from
    ``evaluations`` -- a real failure wins, then "not evaluated".
    """

    task_id: TaskId
    output: AgentOutput
    evaluation: EvaluationResult
    event_count: int
    evaluations: list[EvaluationResult] = field(default_factory=list)
    repetition: int = 0


@dataclass(frozen=True)
class EndStateRecord:
    """One task run's tool-environment end state (docs/adr/0006 D7, D19)."""

    task_id: TaskId
    repetition: int
    state: dict[str, Any]


@dataclass(frozen=True)
class PreflightResult:
    """A scenario's known-good preflight (docs/adr/0006 D16): run once per
    scenario before the agent, so the bundle shows the scenario was
    solvable at run time."""

    task_id: TaskId
    passed: bool
    cause: str | None = None


@dataclass(frozen=True)
class EvaluatorContext:
    """What the harness observed, for evaluators and the policy checker
    (docs/adr/0006 D12).

    An evaluator gets one task run's context (``task_id`` and
    ``repetition`` set); the policy checker gets the whole run's.
    """

    records: tuple[ToolCallRecord, ...] = ()
    end_states: tuple[EndStateRecord, ...] = ()
    task_id: TaskId | None = None
    repetition: int | None = None
    applicable_evaluators: tuple[str, ...] = ()

    @property
    def end_state(self) -> dict[str, Any] | None:
        """This task run's end state, if the context is scoped to one."""
        for record in self.end_states:
            if (record.task_id, record.repetition) == (
                self.task_id,
                self.repetition,
            ):
                return record.state
        return None


@dataclass(frozen=True)
class PassKResult:
    """pass^k for one scenario (docs/adr/0006 D18, D20; ROADMAP R27): its
    ``k`` task runs, how many passed, failed and were not evaluated, and
    the verdict -- ``True`` only if every run passed; ``None`` if any run
    was not evaluated, since not evaluated is not failed; ``False``
    otherwise. A ``None`` verdict always carries ``not_evaluated_cause``:
    the first such repetition's cause, or ``"mixed"`` when the repetitions'
    causes differ. Raw counts; the intervals (R36) are over scenarios, so
    they ride the category roll-ups and the summary, not a scenario.
    """

    task_id: str
    k: int
    passed: int
    failed: int
    not_evaluated: int
    pass_k: bool | None
    not_evaluated_cause: str | None = None


#: A ``PassRateInterval``'s seed: a 256-bit integer as 64 lowercase hex digits.
_SEED_HEX = re.compile(r"[0-9a-f]{64}")

#: The one method a ``PassRateInterval`` records (ROADMAP R36).
IntervalMethod = Literal["cluster-bootstrap-percentile"]


@dataclass(frozen=True)
class PassRateInterval:
    """A 95% interval on a pass^k proportion (ROADMAP R36): the cluster
    bootstrap over the ``n_scenarios`` decided scenarios -- ``b`` resamples
    drawn with ``random.Random(seed)``, each scenario carrying its verdict
    whole -- and the nearest-rank percentiles of the resampled proportions
    (``scorers/bootstrap.py``, the method's only home). ``seed`` is the
    scope's own, derived from the run's seed, as the 64-character lowercase
    hex of the 256-bit integer the generator is seeded with -- whole in any
    JSON reader (owner decision 4, 2026-11-09) -- so these fields reproduce
    the interval. Context beside a rate, never a gate input."""

    low: float
    high: float
    b: int
    seed: str
    n_scenarios: int
    method: IntervalMethod = "cluster-bootstrap-percentile"

    def __post_init__(self) -> None:
        if not isinstance(self.seed, str) or not _SEED_HEX.fullmatch(self.seed):
            raise ValueError(f"seed must be 64 lowercase hex digits, got {self.seed!r}")


@dataclass(frozen=True)
class CategoryPassK:
    """pass^k rolled up over one category's scenarios: ``pass_k_rate`` is
    passing scenarios over scenarios; ``not_evaluated_causes`` counts the
    not-evaluated scenarios by cause. What the plan's thresholds are held
    against (docs/adr/0005 D6). ``interval`` (R36) is the 95% interval on
    the pass^k proportion of its decided scenarios -- context, never what a
    threshold reads -- and is left out when the run derives none."""

    category: str
    k: int
    scenarios: int
    passed: int
    failed: int
    not_evaluated: int
    pass_k_rate: float
    not_evaluated_causes: dict[str, int] = field(default_factory=dict)
    interval: PassRateInterval | None = field(default=None, metadata=OMIT_WHEN_NONE)


@dataclass(frozen=True)
class RunBudget:
    """A capped run's budget at its end (R35): the caps, the spend summed
    over every task run, and whether it tripped -- stopped at least one
    task run before it started. A spend can reach a cap with nothing left
    to start; the budget then did not trip."""

    cap_usd: float | None
    cap_tokens: int | None
    spent_usd: float
    spent_tokens: int
    tripped: bool


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
    #: docs/adr/0006 D16 (format 0.5): not-evaluated task runs by cause.
    not_evaluated_by_cause: dict[str, int] = field(default_factory=dict)
    #: docs/adr/0006 D16 (format 0.5): each scenario's known-good preflight.
    preflight: list[PreflightResult] = field(default_factory=list)
    #: ROADMAP R27 (content under format 0.5): pass^k per scenario, and
    #: rolled up per category for the tasks that carry one.
    pass_k: list[PassKResult] = field(default_factory=list)
    pass_k_by_category: list[CategoryPassK] = field(default_factory=list)
    #: R36: the 95% interval on the pass^k proportion over every decided
    #: scenario; left out when the run derives none.
    overall_pass_k_interval: PassRateInterval | None = field(
        default=None, metadata=OMIT_WHEN_NONE
    )
    #: R35: present only when the run had a budget.
    run_budget: RunBudget | None = field(default=None, metadata=OMIT_WHEN_NONE)


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
    #: docs/adr/0005 D6 (R27): sha256 of ``test_plan.json`` as registered
    #: before the first task ran; ``None`` for a run without a plan. The
    #: validator holds the file to it.
    test_plan_sha256: str | None = None
    #: docs/adr/0007 D8 (format 0.6): the sha256 of the package's ``.py``
    #: and ``.yaml`` files, which identifies the code on any install. From
    #: 0.6 ``git_sha``/``git_dirty`` describe the package's own source tree
    #: (an editable install's checkout; ``"unavailable"`` otherwise), and
    #: ``invocation_git_sha``/``invocation_git_dirty`` the caller's working
    #: directory -- what ``git_sha`` meant up to 0.5. Never compared.
    package_sha256: str | None = None
    invocation_git_sha: str = "unavailable"
    invocation_git_dirty: bool | None = None
    #: R42: the writer's citation norm (``bundles/constants.py``
    #: ``REFS_VERSION``); absent -- left out of the record -- in a bundle
    #: written before R42. A reader older than R42 refuses a bundle that
    #: records it (the R35/R36 one-way step).
    refs_version: int | None = field(default=None, metadata=OMIT_WHEN_NONE)
