"""The nine core Protocol definitions for AgentEvalOps.

These are PEP 544 structural protocols.  Implementations do NOT need to
inherit from these classes — they only need to match the declared shape.
This makes it trivial to wrap third-party agent frameworks without
modifying them.

All protocols are decorated with ``@runtime_checkable`` so that
``isinstance`` checks work in tests.

Protocol method signatures reference only schemas defined in WBS 1.
Richer parameter types (EnvHandle, EvaluatorContext, etc.) will be added
in later WBS phases alongside their implementations.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Sequence
from typing import Any, Protocol, runtime_checkable

from agentevalops.core.schemas import (
    AgentInput,
    AgentOutput,
    EvaluationResult,
    PolicySpec,
    PolicyVerdict,
    ResourceLimits,
    ScoreResult,
    TaskSpec,
    TraceEvent,
)
from agentevalops.core.types import AgentId, BackendId, RunId


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

    Five canonical kinds, all behind this same interface:
    - ``deterministic``: unit tests, exact match, state assertions
    - ``llm_judge``: quality/relevance via a model (optional in v0.1)
    - ``tool_use``: tool-call validity and argument correctness
    - ``state_based``: final filesystem/db state vs. target
    - ``trace_quality``: loops, hallucinated outputs, dead-ends

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
    ) -> EvaluationResult:
        """Return a score for this evaluator's dimension."""
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
    ) -> PolicyVerdict:
        """Return PASS / FAIL / WARN with citations to specific events."""
        ...


@runtime_checkable
class ReportGenerator(Protocol):
    """Renders human-readable reports from a completed run."""

    report_id: str

    async def render(
        self,
        result: ScoreResult,
        trace: list[TraceEvent],
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
