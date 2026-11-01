"""The core Protocol definitions for AgentEvalOps.

These are PEP 544 structural protocols.  Implementations do NOT need to
inherit from these classes — they only need to match the declared shape.
This makes it trivial to wrap third-party agent frameworks without
modifying them.

All protocols are decorated with ``@runtime_checkable`` so that
``isinstance`` checks work in tests.

Protocol method signatures reference only schemas defined in WBS 1, plus
``EnvHandle`` (docs/benchmark_adapters.md D5). Richer parameter types
(EvaluatorContext, etc.) will be added in later WBS phases alongside their
implementations.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Sequence
from typing import Any, Protocol, runtime_checkable

from agentevalops.core.schemas import (
    AgentInput,
    AgentOutput,
    EndState,
    EnvHandle,
    EvaluationResult,
    EvaluatorContext,
    PolicySpec,
    PolicyVerdict,
    ResourceLimits,
    RunConfig,
    RunSummary,
    Scenario,
    ScoreResult,
    TaskSpec,
    ToolCallRecord,
    ToolResult,
    ToolSpec,
    TraceEvent,
)
from agentevalops.core.types import AgentId, BackendId, RunId, TaskId


@runtime_checkable
class ToolEnvironment(Protocol):
    """A task's tool environment: the harness's observation point
    (docs/adr/0006-tool-environment-and-observation-seam.md, D2).

    Runners get tool specs from it and send every tool call except
    ``finish`` to it. It records each call itself, so verdicts can rest on
    the harness's record rather than on what the agent reports (D4, D14).
    Async so that an MCP transport (v0.6) is another implementation of the
    same protocol, not a change to it.
    """

    async def list_tools(self) -> Sequence[ToolSpec]:
        """The tools this environment offers, in a fixed order."""
        ...

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Run one tool call and record it. Never raises on a bad call: an
        unknown tool or a failed call is an error result, recorded."""
        ...

    async def snapshot(self) -> EndState:
        """The environment's end state (D7)."""
        ...

    def records(self) -> Sequence[ToolCallRecord]:
        """Every call so far, in order (D4)."""
        ...


@runtime_checkable
class AgentRunner(Protocol):
    """Executes an agent against a single task and streams trace events.

    Implementations are responsible for invoking the agent (LangGraph,
    Bedrock, OpenAI Agents SDK, replay from a bundle, etc.) and emitting
    trace events as they occur.

    Implementations MUST:
    - Emit at least one ``TraceEvent`` per step.
    - Respect all ``ResourceLimits`` fields and stop if any is exceeded.
    - Emit a final event with ``kind=TraceEventKind.AGENT_TERMINAL``.
    - Never raise on agent-level failure; capture it in ``AgentOutput``.
    - Raise only on infrastructure failure (sandbox crash, model API 5xx).
    """

    agent_id: AgentId

    async def run(
        self,
        input: AgentInput,
        resource_limits: ResourceLimits,
    ) -> AsyncIterator[TraceEvent]:
        """Stream trace events while the agent runs."""
        ...


@runtime_checkable
class BenchmarkAdapter(Protocol):
    """Bridges an external benchmark into the platform's task model.

    One adapter per benchmark family.  The adapter owns:
    - mapping from the benchmark's native format into ``TaskSpec`` objects
    - the upstream-blessed deterministic grading logic

    The platform does NOT reimplement benchmark scoring.
    """

    benchmark_id: str
    benchmark_version: str

    def list_tasks(
        self,
        filter_spec: dict[str, Any] | None = None,
    ) -> Iterable[TaskSpec]:
        """Enumerate tasks.  ``filter_spec`` is benchmark-specific."""
        ...

    def grade(self, task: TaskSpec, output: AgentOutput) -> EvaluationResult:
        """Apply the benchmark's official deterministic grading.

        Soft criteria (LLM-judge, trace quality) are separate ``Evaluator``
        concerns — not this method's responsibility.
        """
        ...

    def prepare_environment(self, task: TaskSpec) -> EnvHandle:
        """Prepare whatever this benchmark needs before the agent runs.

        docs/benchmark_adapters.md D5: for benchmarks with no filesystem
        workspace, return ``EnvHandle(task_id=task.task_id)`` (the default
        ``workspace_path=None``). On failure, remove any partial work this
        call created itself, then raise ``InfrastructureError`` — the
        orchestrator does not call ``teardown`` when no handle was returned.
        """
        ...

    def teardown(self, handle: EnvHandle) -> None:
        """Clean up whatever ``prepare_environment`` set up.

        docs/benchmark_adapters.md D5: MUST be safe to call more than once.
        """
        ...


@runtime_checkable
class KnownGoodSource(Protocol):
    """A benchmark that can say how each task is solved (docs/adr/0006 D16).

    Optional: the orchestrator checks for it with ``isinstance`` and runs
    the script once per task before the agent -- the known-good preflight.
    ``None`` means the task has no script, and no preflight.
    """

    def known_good_script(self, task: TaskSpec) -> Sequence[dict[str, Any]] | None:
        """The scripted trajectory that solves *task*, as data."""
        ...


@runtime_checkable
class Perturbing(Protocol):
    """A benchmark that can vary a task's text under a derived seed
    (docs/adr/0006 D6; ROADMAP R26). Optional, like ``KnownGoodSource``:
    the orchestrator checks for it with ``isinstance`` and, for each task
    run, hands the runner ``perturb(task, seed)`` with the seed D6 derives
    from ``RunConfig.seed``, the task id and the repetition. A task the
    benchmark does not vary comes back unchanged.
    """

    def perturb(self, task: TaskSpec, seed: int) -> TaskSpec:
        """*task* as the agent should see it for this seed."""
        ...


@runtime_checkable
class DomainPack(Protocol):
    """A domain's scenarios and the tool environment they run against
    (docs/adr/0006 D23). Discovered through the ``agentevalops.domain_packs``
    entry-point group (D24) and run by the ``agentic-baseline`` benchmark,
    one task per scenario.
    """

    pack_id: str
    pack_version: str

    def scenarios(self) -> Sequence[Scenario]:
        """Every scenario the pack offers, in a fixed order."""
        ...

    def build_environment(self, scenario: Scenario, task_id: TaskId) -> ToolEnvironment:
        """A fresh environment for one run of *scenario*."""
        ...


@runtime_checkable
class TraceStore(Protocol):
    """Persists and retrieves trace events for a run."""

    async def append(self, run_id: RunId, event: TraceEvent) -> None:
        """Append one event.  MUST be safe to call concurrently."""
        ...

    def stream(self, run_id: RunId) -> AsyncIterator[TraceEvent]:
        """Yield all events for a run in append order."""
        ...

    async def finalize(self, run_id: RunId) -> None:
        """Close the trace.  Subsequent appends MUST raise.  Idempotent."""
        ...


@runtime_checkable
class ArtifactStore(Protocol):
    """Stores binary artifacts produced during a run (patches, reports, etc.)."""

    async def put(self, run_id: RunId, path: str, content: bytes) -> str:
        """Store an artifact.  Returns an opaque reference string."""
        ...

    async def get(self, ref: str) -> bytes:
        """Retrieve an artifact by its reference string."""
        ...

    async def list(self, run_id: RunId) -> list[str]:
        """Enumerate artifact reference strings for a run."""
        ...


@runtime_checkable
class Evaluator(Protocol):
    """Scores one quality dimension of a completed run.

    Six canonical kinds, all behind this same interface:
    - ``deterministic``: unit tests, exact match, state assertions
    - ``llm_judge``: quality/relevance via a model (optional in v0.1)
    - ``tool_use``: tool-call validity and argument correctness
    - ``state_based``: final filesystem/db state vs. target
    - ``trace_quality``: loops, hallucinated outputs, dead-ends
    - ``policy``: a domain pack's policy rules, per task run, over the
      harness records (ROADMAP R25; the run-level ``PolicyChecker`` keeps
      the config's ``PolicySpec`` rules)

    Evaluators MUST NOT mutate the environment.  They do not raise on
    agent failure — a failed agent produces a low score, not an exception.
    """

    evaluator_id: str
    evaluator_kind: str

    async def evaluate(
        self,
        task: TaskSpec,
        output: AgentOutput,
        trace: list[TraceEvent],
        context: EvaluatorContext,
    ) -> EvaluationResult:
        """Return a score for this evaluator's dimension.

        ``context`` (docs/adr/0006 D12) carries the harness's own records
        and end state for this task run. An evaluator that finds none of
        the inputs it needs returns ``applicable=False`` rather than a
        failure (D13).
        """
        ...


@runtime_checkable
class Scorer(Protocol):
    """Aggregates per-task ``EvaluationResult`` objects into a run summary.

    Examples: pass@1, pass@k, mean cost per task, regression delta vs.
    a baseline run.
    """

    scorer_id: str

    def summarize(
        self,
        scores: Sequence[EvaluationResult],
        baseline: ScoreResult | None = None,
    ) -> ScoreResult:
        """Aggregate scores.  Include baseline comparison if provided."""
        ...


@runtime_checkable
class PolicyChecker(Protocol):
    """Evaluates a completed run against an organisational ``PolicySpec``.

    Operates post-hoc on the completed trace.  Does NOT intercept tool
    calls in flight — runtime safety is a separate concern with different
    latency requirements.
    """

    checker_id: str

    async def check(
        self,
        policy: PolicySpec,
        output: AgentOutput,
        trace: list[TraceEvent],
        context: EvaluatorContext,
    ) -> PolicyVerdict:
        """Return PASS / FAIL / WARN with citations to specific events."""
        ...


@runtime_checkable
class ReportGenerator(Protocol):
    """Renders human-readable reports from a completed run.

    Takes ``RunSummary`` (not just ``ScoreResult``) because a useful report
    needs cost/token totals, per-task pass/fail, and the policy verdict —
    none of which fit in a bare score aggregate.
    """

    report_id: str

    async def render(
        self,
        run_config: RunConfig,
        summary: RunSummary,
        trace: list[TraceEvent],
        config_name: str = "",
    ) -> str:
        """Produce a report.  Returns the report content as a string."""
        ...


@runtime_checkable
class CloudBackend(Protocol):
    """Abstracts the cloud primitives the platform needs to run jobs.

    Deliberately small: the five methods below are all that distinguishes
    a local-laptop run from a production AWS run.  Cloud-specific
    configuration (IAM, VPC, IaC) lives in ``infra/``, not here.

    ``backend_id`` MUST be one of ``BACKEND_LOCAL`` or ``BACKEND_AWS``
    from ``agentevalops.core.types``.  Azure and GCP are out of scope for v0.1.
    """

    backend_id: BackendId

    async def submit_job(self, spec: dict[str, Any]) -> str:
        """Launch a containerised job.  Returns an opaque handle string."""
        ...

    async def job_status(self, handle: str) -> dict[str, Any]:
        """Poll job status.  Returns a status mapping."""
        ...

    async def cancel_job(self, handle: str) -> None:
        """Stop the job.  Idempotent."""
        ...

    async def fetch_secret(self, ref: str) -> str:
        """Read a secret.  MUST NOT log the value or include it in traces."""
        ...

    async def export_telemetry(self, run_id: RunId) -> None:
        """Forward traces and metrics to the cloud observability surface.

        The local backend implementation is a no-op.
        """
        ...
