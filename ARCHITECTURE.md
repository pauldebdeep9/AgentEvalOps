# ARCHITECTURE.md — AgentEvalOps Platform

> **Status:** Draft v0.1 · **Owner:** *(your name)* · **Last updated:** 2026-05-10
> This document specifies *how* AgentEvalOps is built. The *what* and *why* live in [`DESIGN.md`](./DESIGN.md). When this document and `DESIGN.md` disagree, `DESIGN.md` wins and this one is wrong.

---

## 1. Overview

AgentEvalOps is a Python package (`agentevalops`) with nine core protocols, a set of pluggable implementations behind each, a portable result-bundle format, and a planned AWS cloud backend. Local execution is the default; AWS is an optional extension behind the `CloudBackend` protocol.

The architecture has three layers:

The **core layer** (`src/agentevalops/core/`) defines protocols, schemas, and the result-bundle format. It has no dependencies on any cloud provider, model provider, or agent framework. It is pure Python plus Pydantic plus OpenTelemetry.

The **adapter layer** (`src/agentevalops/agents/`, `benchmarks/`, `evaluators/`, `cloud/`, `observability/`) contains concrete implementations of the core protocols. Adapters depend on the core; the core never depends on adapters. New benchmarks, agents, clouds, and evaluators are added by writing new adapters.

The **edge layer** (`src/agentevalops/cli.py`) is what users interact with — a CLI that composes adapters. The edge layer never reaches into adapter internals. A FastAPI service and a dashboard are out of scope for v0.1 and explicitly deferred.

This three-layer separation is the load-bearing architectural commitment. Everything else in this document is detail.

---

## 2. The nine protocols

These are defined in `src/agentevalops/core/protocols.py`. They are Python `Protocol` classes (PEP 544), not abstract base classes, because we want structural subtyping — an implementation does not need to inherit from the protocol, only match its shape. This makes it trivial to wrap third-party agent frameworks without modifying them.

All protocols are typed in strict mode. `pyright --strict` and `mypy --strict` pass on `src/agentevalops/core/` as a CI gate.

### 2.1 `AgentRunner`

Executes an agent against a prepared environment and emits a trace.

```python
from typing import Protocol, AsyncIterator
from agentevalops.core.schemas import (
    AgentConfig, EnvHandle, TaskSpec, TraceEvent, AgentResult, ResourceLimits,
)

class AgentRunner(Protocol):
    """Executes an agent against a single task in a prepared environment.

    Implementations are responsible for invoking the agent (LangGraph, Bedrock,
    OpenAI Agents SDK, replay from a recorded bundle, etc.) and emitting trace
    events as they occur. The runner does NOT score the result; that is the
    Evaluator's job.

    Implementations MUST:
      - Emit at least one TraceEvent per step the agent takes.
      - Respect resource_limits.max_tokens, .max_wall_seconds, .max_cost_usd.
      - Stop the agent and emit a terminal TraceEvent if any limit is reached.
      - Never raise on agent-level failure; capture it in AgentResult.error.
      - Raise only on infrastructure failure (sandbox died, model API 5xx).
    """

    runner_id: str  # stable identifier, e.g. "langgraph-claude-4-7"

    async def run(
        self,
        task: TaskSpec,
        env: EnvHandle,
        agent_config: AgentConfig,
        resource_limits: ResourceLimits,
    ) -> AsyncIterator[TraceEvent]:
        """Yield trace events as the agent runs. The final event MUST have
        kind='agent.terminal' and carry the AgentResult in its payload."""
        ...
```

The `AsyncIterator` return type is deliberate: it lets the platform stream traces to disk and to OpenTelemetry as they happen, rather than buffering an entire run before persisting anything. For long-running ML-engineering tasks this is the difference between losing a four-hour run on a sandbox crash and losing the last thirty seconds.

### 2.2 `BenchmarkAdapter`

Translates a benchmark (SWE-bench, MLE-bench, an internal workflow suite) into the platform's task model.

```python
from typing import Protocol, Iterable
from agentevalops.core.schemas import TaskSpec, EnvHandle, AgentResult, GradeReport

class BenchmarkAdapter(Protocol):
    """Bridges an external benchmark into the platform.

    One adapter per benchmark family. The adapter owns the mapping from the
    benchmark's native format (SWE-bench instances, MLE-bench competitions,
    Terminal-Bench tasks) into TaskSpecs, AND owns the upstream-blessed
    grading logic. The platform does NOT reimplement benchmark scoring.
    """

    benchmark_id: str  # e.g. "swebench-verified", "mlebench-lite"
    benchmark_version: str  # pinned upstream version

    def list_tasks(self, filter_spec: dict | None = None) -> Iterable[TaskSpec]:
        """Enumerate tasks. filter_spec is benchmark-specific (e.g. difficulty,
        repo subset, competition list)."""
        ...

    async def prepare_environment(self, task: TaskSpec) -> EnvHandle:
        """Set up the sandbox/repo/dataset state the agent will operate on.
        Returns a handle the AgentRunner uses to interact with the environment."""
        ...

    def grade(self, task: TaskSpec, result: AgentResult, env: EnvHandle) -> GradeReport:
        """Apply the benchmark's official grading. This is deterministic
        scoring only — soft criteria (LLM-judge, trace quality) are separate
        Evaluators, not the adapter's job."""
        ...

    async def teardown(self, env: EnvHandle) -> None:
        """Release sandbox resources. MUST be idempotent."""
        ...
```

The split between `grade` (deterministic, benchmark-native) and `Evaluator` (everything else) is deliberate. SWE-bench's "did the patch pass the hidden test suite?" is a `BenchmarkAdapter.grade` concern. "Did the agent take 47 unnecessary steps before getting there?" is a `TraceQualityEvaluator` concern. They run independently and produce independent scores.

### 2.3 `Evaluator`

Scores a completed run on a single dimension. Five evaluator types are first-class; the protocol is uniform across all five.

```python
from typing import Protocol
from agentevalops.core.schemas import (
    TaskSpec, AgentResult, Trace, EvaluatorScore, EvaluatorContext,
)

class Evaluator(Protocol):
    """Scores one dimension of a completed run.

    Five canonical kinds, all behind this same interface:
      - DeterministicEvaluator: unit tests, exact match, state assertions
      - LLMJudgeEvaluator: quality, relevance, policy compliance via a model
        (optional; not required for v0.1)
      - ToolUseEvaluator: tool-call validity, argument correctness
      - StateBasedEvaluator: final filesystem/db/cloud state vs target
      - TraceQualityEvaluator: loops, hallucinated tool outputs, dead-ends

    Evaluators are pure functions of (task, result, trace). They MUST NOT
    mutate the environment. They MAY make outbound LLM calls (for judges)
    but MUST report those calls' cost in the EvaluatorScore.
    """

    evaluator_id: str  # e.g. "swebench-pytest", "claude-judge-relevance"
    evaluator_kind: str  # one of: "deterministic", "llm_judge", "tool_use",
                         #         "state_based", "trace_quality"

    async def evaluate(
        self,
        task: TaskSpec,
        result: AgentResult,
        trace: Trace,
        context: EvaluatorContext,
    ) -> EvaluatorScore:
        """Return a score on this evaluator's dimension. Does not raise on
        agent failure — a failed agent produces a low score, not an exception."""
        ...
```

A run is graded by *all* configured evaluators in parallel, and their scores are reported separately. The platform never collapses them into a single composite score by default — that decision belongs to the consumer of the bundle.

### 2.4 `Scorer`

Aggregates per-task scores into per-run summaries. Distinct from `Evaluator` because it operates over collections.

```python
from typing import Protocol, Sequence
from agentevalops.core.schemas import EvaluatorScore, RunSummary

class Scorer(Protocol):
    """Aggregates scores across tasks within a run.

    Examples: pass@1, pass@k, mean cost per task, p95 latency, regression
    delta vs a baseline run.
    """

    scorer_id: str

    def summarize(
        self,
        scores: Sequence[EvaluatorScore],
        baseline: RunSummary | None = None,
    ) -> RunSummary:
        """Aggregate. If baseline is provided, include comparison fields."""
        ...
```

### 2.5 `TraceStore`

Persists trace events. The platform writes traces locally to JSONL by default. In future AWS-enabled runs, traces may additionally be exported to CloudWatch via OpenTelemetry; the local JSONL file always exists in the bundle regardless.

> **Note on `TraceEvent.kind` vs. OTel span names.** `TraceEvent.kind` is a *semantic event type* from a closed enum (e.g. `agent.tool_call`, `agent.final_answer`). OpenTelemetry span names (e.g. `agent.execute`, `benchmark.prepare`) are *observability labels* used for dashboards and traces in external systems. They are parallel concepts, not the same field.

```python
from typing import Protocol, AsyncIterator
from agentevalops.core.schemas import TraceEvent, RunId

class TraceStore(Protocol):
    """Persists and retrieves trace events."""

    async def append(self, run_id: RunId, event: TraceEvent) -> None:
        """Append a single event. MUST be safe to call concurrently for the
        same run_id."""
        ...

    async def stream(self, run_id: RunId) -> AsyncIterator[TraceEvent]:
        """Yield all events for a run in append order."""
        ...

    async def finalize(self, run_id: RunId) -> None:
        """Close the trace. Subsequent appends MUST raise. Idempotent."""
        ...
```

### 2.6 `ArtifactStore`

Persists files the agent produced (patches, model checkpoints, generated reports).

```python
from typing import Protocol, BinaryIO
from agentevalops.core.schemas import RunId, ArtifactRef

class ArtifactStore(Protocol):
    """Stores binary artifacts produced during a run."""

    async def put(self, run_id: RunId, path: str, content: BinaryIO) -> ArtifactRef:
        """Store an artifact under (run_id, path). Returns a ref usable by get()."""
        ...

    async def get(self, ref: ArtifactRef) -> BinaryIO:
        """Retrieve by reference."""
        ...

    async def list(self, run_id: RunId) -> list[ArtifactRef]:
        """Enumerate artifacts for a run."""
        ...
```

`ArtifactStore` is separate from `TraceStore` because artifact lifecycle is different — traces are append-only event logs, artifacts are immutable blobs that may be large (multi-GB ML model checkpoints). Local implementation writes to disk; AWS implementation writes to S3 with lifecycle rules.

### 2.7 `ReportGenerator`

Produces the human-readable artifacts in the result bundle: `failure_analysis.md`, the CLI/markdown summary, the cost-report rendering.

```python
from typing import Protocol
from agentevalops.core.schemas import RunSummary, Trace, ReportArtifact

class ReportGenerator(Protocol):
    """Renders human-readable reports from a completed run."""

    report_id: str  # e.g. "failure-analysis-llm", "cost-breakdown-html"

    async def render(self, summary: RunSummary, trace: Trace) -> ReportArtifact:
        """Produce a report. ReportArtifact contains the file content,
        suggested filename, and content type."""
        ...
```

`ReportGenerator` implementations may use LLMs (the failure-analysis-llm generator does), in which case the cost is captured the same way evaluator costs are.

### 2.8 `CloudBackend`

The cloud-neutrality boundary. All cloud-specific operations go through this protocol; everything else in the codebase is cloud-agnostic. The `LocalBackend` implementation (using local processes or docker-compose) is the default for v0.1 and requires no cloud credentials. The `AwsBackend` is planned for a later phase.

> **Note on `ResourceLimits` vs. `PolicySpec`.** `ResourceLimits` (on `AgentConfig`) are *runtime controls* — the runner enforces them during execution and will stop the agent if a limit is reached. `PolicySpec` is a *post-run compliance check* — the `PolicyChecker` evaluates the completed trace after the fact and produces a verdict. They are separate concerns with separate enforcement points.

```python
from typing import Protocol
from agentevalops.core.schemas import (
    JobSpec, JobHandle, JobStatus, ContainerSpec, SecretRef,
)

class CloudBackend(Protocol):
    """Abstracts the cloud primitives the platform actually needs.

    Deliberately small. The five primitives below are all that distinguishes
    a 'local laptop run' from a 'production AWS run' from a future cloud run.
    Anything cloud-specific beyond these (IAM policies, VPC config, IaC) is
    out of scope for the runtime and lives in infra/.
    """

    backend_id: str  # "local", "aws" (future: "azure", "gcp")

    async def submit_job(self, spec: JobSpec) -> JobHandle:
        """Launch a containerized job. Returns a handle for status/logs/cancel."""
        ...

    async def job_status(self, handle: JobHandle) -> JobStatus:
        """Poll job status. Includes resource usage and cost-to-date."""
        ...

    async def cancel_job(self, handle: JobHandle) -> None:
        """Stop the job. Idempotent."""
        ...

    async def fetch_secret(self, ref: SecretRef) -> str:
        """Read a secret. Implementations MUST NOT log secret values, MUST NOT
        write them to traces, MUST NOT include them in artifacts."""
        ...

    async def export_telemetry(self, run_id: str) -> None:
        """Forward this run's traces and metrics to the cloud's observability
        surface (e.g. CloudWatch for AwsBackend). Local backend is a no-op."""
        ...
```

The protocol is deliberately small. It is the answer to the question *"what does AgentEvalOps actually need from a cloud?"* and the answer is: launch containers, read secrets, ship telemetry. Anything more (IAM, VPC, autoscaling policies) belongs in IaC, not in the runtime.

### 2.9 `PolicyChecker`

Evaluates a completed run against organizational policy. Distinct from `Evaluator` because policy is a binary compliance question, not a graded dimension, and because the consequences of policy violation (block publication, alert security team) are different from low scores.

```python
from typing import Protocol
from agentevalops.core.schemas import (
    PolicySpec, Trace, AgentResult, PolicyVerdict,
)

class PolicyChecker(Protocol):
    """Evaluates a run against a PolicySpec.

    Operates post-hoc on the completed trace. Does NOT intercept tool calls
    in flight — runtime safety is a different concern with different latency
    requirements (see DESIGN.md non-goals).
    """

    checker_id: str

    async def check(
        self,
        policy: PolicySpec,
        result: AgentResult,
        trace: Trace,
    ) -> PolicyVerdict:
        """Return PASS / FAIL / WARN with citations to specific trace events."""
        ...
```

---

## 3. Schemas

All schemas are Pydantic v2 models defined in `src/agentevalops/core/schemas.py`. They are versioned via a top-level `schema_version` field on the result bundle's `metadata.json`, and a compatibility-test suite ensures bundles produced by older platform versions remain readable.

The schema set is deliberately flat — TaskSpec, AgentConfig, RunConfig, EnvHandle, TraceEvent, AgentResult, GradeReport, EvaluatorScore, EvaluatorContext, RunSummary, ArtifactRef, ReportArtifact, JobSpec, JobHandle, JobStatus, ContainerSpec, SecretRef, PolicySpec, PolicyVerdict, Trace, RunId, ResourceLimits. Anything more nested becomes hard to evolve.

A few schemas worth calling out specifically:

**`TraceEvent`** carries `run_id`, `step_index`, `timestamp`, `kind` (one of a closed enum: `agent.plan`, `agent.tool_call`, `agent.tool_result`, `agent.observation`, `agent.final_answer`, `agent.terminal`, `evaluator.score`, `policy.verdict`, `cost.tick`), `payload` (kind-specific), `cost_delta_usd`, `tokens_delta`, and an OpenTelemetry-compatible `span_context`. The closed enum is critical — open-ended `kind` strings make trace analysis a regex problem instead of a typed problem.

**`AgentResult`** carries `success: bool`, `final_answer: str | None`, `artifacts: list[ArtifactRef]`, `error: ErrorInfo | None`, `total_cost_usd`, `total_tokens`, `wall_seconds`, and `terminated_by` (one of `completed`, `limit_tokens`, `limit_time`, `limit_cost`, `infra_failure`, `agent_error`). The `terminated_by` field is what makes regression analysis tractable — knowing whether a run failed because the agent gave up vs. ran out of budget is more useful than the success boolean alone.

**`EvaluatorScore`** carries the evaluator id and kind, a `score` (float in [0,1] for graded evaluators, bool for deterministic), a `confidence` (for LLM judges), `cost_usd`, `latency_ms`, and `citations: list[TraceEventRef]` pointing at the specific events the score is based on. Citations are what let consumers of the bundle understand *why* a score was what it was, not just *what* the score was.

---

## 4. Directory layout

This is the v0.1 layout. Future-phase modules (AWS backend, additional benchmarks, dashboard) are noted as extension points.

```
agent-evalops/
├── README.md
├── DESIGN.md                       # what & why
├── ARCHITECTURE.md                 # this file
├── ROADMAP.md
├── SECURITY.md
├── Makefile
├── pyproject.toml
├── uv.lock
├── .github/
│   └── workflows/
│       ├── ci.yml                  # lint, type-check, test
│       ├── replay.yml              # bundle-replay regression test
│       └── release.yml
│
├── src/
│   └── agentevalops/
│       ├── __init__.py
│       ├── cli.py                  # entry point: `agentevalops`
│       │
│       ├── core/                   # LAYER 1: pure, no adapters
│       │   ├── __init__.py
│       │   ├── protocols.py        # the nine Protocol definitions
│       │   ├── schemas.py          # Pydantic models
│       │   ├── bundle.py           # result-bundle read/write
│       │   ├── replay.py           # replay_command construction & execution
│       │   ├── orchestrator.py     # runs the eval loop given protocol impls
│       │   ├── registry.py         # adapter registration & discovery
│       │   └── errors.py
│       │
│       ├── agents/                 # LAYER 2: AgentRunner adapters
│       │   ├── __init__.py
│       │   ├── mock_runner.py             # for tests and demo
│       │   └── replay_runner.py           # re-runs from a bundle
│       │   # Future: langgraph_runner.py, openai_compatible_runner.py, etc.
│       │
│       ├── benchmarks/             # LAYER 2: BenchmarkAdapter adapters
│       │   ├── __init__.py
│       │   └── toy/                       # for v0.1 demo
│       │       ├── adapter.py
│       │       └── tasks.py
│       │   # Future: swebench/, mlebench/, etc.
│       │
│       ├── evaluators/             # LAYER 2: Evaluator adapters
│       │   ├── __init__.py
│       │   └── deterministic.py
│       │   # Future: llm_judge.py (optional), tool_use.py, etc.
│       │
│       ├── scorers/                # LAYER 2: Scorer adapters
│       │   ├── __init__.py
│       │   └── pass_at_k.py
│       │
│       ├── stores/                 # LAYER 2: TraceStore + ArtifactStore
│       │   ├── __init__.py
│       │   ├── local_trace_store.py       # JSONL on disk (default)
│       │   └── local_artifact_store.py   # files on disk (default)
│       │   # Future: s3_trace_store.py, s3_artifact_store.py
│       │
│       ├── reports/                # LAYER 2: ReportGenerator adapters
│       │   ├── __init__.py
│       │   └── markdown_report.py
│       │
│       ├── policy/                 # LAYER 2: PolicyChecker adapters
│       │   ├── __init__.py
│       │   ├── allowlist_checker.py
│       │   └── cost_ceiling_checker.py
│       │
│       ├── cloud/                  # LAYER 2: CloudBackend adapters
│       │   ├── __init__.py
│       │   ├── local_backend.py           # local processes / docker-compose (default)
│       │   └── aws/                       # Future (planned, not v0.1)
│       │       # backend.py, fargate_jobs.py, s3_io.py, secrets.py, etc.
│       │
│       └── observability/          # LAYER 2: OTel wiring
│           ├── __init__.py
│           ├── otel_setup.py
│           └── span_emitter.py
│
├── configs/
│   └── toy_smoke.yaml
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── smoke/
│   └── replay/
│       └── fixtures/
│
├── examples/
│   └── 01_local_toy_eval/
│
└── docs/
    ├── benchmark_adapters.md
    ├── writing_an_evaluator.md
    └── images/
```

A few rules about this layout that exist for specific reasons:

`src/agentevalops/core/` imports nothing from any adapter module. This is enforced by an `import-linter` config in CI. The core layer's import graph is a leaf.

Adapter modules import from the core, optionally from each other within the same adapter family, and from third-party libraries. No adapter module imports from a different adapter family.

`grep -r "boto3" src/` returns hits only in `src/agentevalops/cloud/aws/` (which is empty until the AWS phase). CI enforces this.

Tests mirror the source tree. There is one test file per source module.

---

## 5. The orchestration loop

The orchestrator (`src/agentevalops/core/orchestrator.py`) is the only place that knows about all nine protocols simultaneously. It is the conductor; everything else is an instrument.

For a single run, the orchestrator does the following, in this order:

It loads the `RunConfig` and resolves it into concrete protocol implementations via a registry. It generates a `run_id` (UUIDv7, so it sorts by time) and a result-bundle directory. It captures the `replay_command` *before* executing anything — including the platform version, the git SHA if available, and the resolved config — so that even runs that crash partway through produce a partial bundle that can be inspected.

It calls `BenchmarkAdapter.list_tasks()` to enumerate tasks, applying any filters from the config. For each task in parallel (bounded by the configured concurrency limit), it calls `BenchmarkAdapter.prepare_environment()`, then `AgentRunner.run()`, streaming `TraceEvent`s into the `TraceStore` as they arrive. The `AgentRunner` is responsible for enforcing `ResourceLimits` (tokens, wall-clock seconds, cost) *during* execution — if a limit is reached, the runner stops the agent and emits a terminal `TraceEvent` with the appropriate `terminated_by` reason. The orchestrator does not intercept mid-run. When the agent emits a terminal event, the orchestrator captures the `AgentResult`.

It then calls `BenchmarkAdapter.grade()` to get the deterministic score, runs all configured `Evaluator`s in parallel against the trace, and runs all configured `PolicyChecker`s against the completed trace and `AgentResult`. `PolicyChecker`s evaluate post-run compliance against a `PolicySpec`; they do not affect execution. All results are written to `scores.json`. It calls `BenchmarkAdapter.teardown()` regardless of outcome.

After all tasks complete, it runs the configured `Scorer`s to produce a `RunSummary`, then runs the configured `ReportGenerator`s to produce the human-readable artifacts. Finally it finalizes the `TraceStore`, writes `metadata.json`, and the bundle is sealed.

The whole loop is async. Concurrency is bounded at the task level by a semaphore whose size comes from `RunConfig.max_concurrent_tasks`. The default is 1 (run tasks serially) because most failure modes show up with concurrency, and we want the unsurprising default. Production AWS runs use higher values.

If the orchestrator is interrupted (Ctrl-C, OOM, sandbox died), it makes a best effort to finalize the partial bundle so what *did* complete is still inspectable. Bundles produced this way have `metadata.json.status = "interrupted"` and are excluded from regression baselines by default.

---

## 6. AWS topology (planned — not required for v0.1)

AWS is the planned cloud deployment. The topology described here guides the runtime architecture so the interface design and future IaC stay aligned. Local execution requires none of this infrastructure.

The runtime communicates with AWS exclusively through the `CloudBackend` protocol. The `AwsBackend` implementation (planned for a post-v0.1 phase) will use:
- **ECS Fargate** for eval runner jobs
- **S3** for result bundles and artifacts
- **DynamoDB** as a run index
- **Secrets Manager** for credentials
- **CloudWatch via ADOT** for observability

Specific choices and the reasons for them:

**ECS Fargate over Lambda for the eval runner.** Eval runs commonly exceed Lambda's 15-minute ceiling. Fargate also gives a real Linux environment for sandboxed code execution.

**S3 as the artifact and bundle store.** Bundles benefit from S3's lifecycle rules. Bundles are stored at `s3://{bucket}/runs/{run_id}/` so a run is a single prefix and IAM policies can be scoped to a single run.

**DynamoDB as the run index, not the bundle store.** DynamoDB holds the metadata that needs to be queried (run id, agent id, benchmark, timestamp, summary stats, S3 pointer). The bundles themselves live in S3.

**OpenTelemetry → CloudWatch via ADOT.** Spans become CloudWatch Logs entries; selected span attributes (cost, latency, tokens) become CloudWatch Metrics via the embedded metric format.

**Secrets Manager, never task-definition env vars.** API keys are fetched at runtime by the runner via `CloudBackend.fetch_secret()`. Task definitions reference the secret ARN, not the secret value.

**Bedrock AgentCore** is future work beyond the initial AWS backend phase and is not part of the `AwsBackend` planned implementation. It will be added as a separate `AgentRunner` adapter when relevant.

**Azure and GCP** are future roadmap items. The `CloudBackend` protocol is the extension point; they are not active development and have no placeholder modules in the current codebase.

---

## 7. Replay determinism

Replay is a tested property of the platform, not a hope. The CI pipeline includes a job that takes a checked-in bundle from `tests/replay/fixtures/`, runs `replay_command.txt`, and asserts the new bundle matches the original on a defined schema:

The new bundle must produce the same `AgentResult.success`, the same set of tool calls in the same order, the same `terminated_by` reason, and `total_cost_usd` within ±5% (tolerating provider-side pricing drift). It need not produce identical model outputs token-for-token — that would require provider cooperation we don't have — but the *behavioral* trace must match.

This is achieved by capturing, in the trace, every input that determined behavior: the resolved prompt, the tool definitions, the temperature and seed (where supported), the model version, the random state at each sampling point. The `ReplayRunner` consumes a recorded bundle and substitutes recorded model outputs for live calls, which produces an exact match against the original behavior and is the strongest form of replay.

Live replay (re-running with the real model) is supported but not required for the regression test, because provider-side non-determinism is outside our control. Live replay is what users do to test "does this still work against the new model version?"; recorded replay is what CI does to test "did the platform itself break?"

---

## 8. Observability

The platform emits OpenTelemetry spans for every step of every run. Span hierarchy:

```
eval.run                        (root span, run_id)
├── eval.task                   (one per task)
│   ├── benchmark.prepare
│   ├── agent.execute           (the AgentRunner's span)
│   │   ├── agent.plan
│   │   ├── agent.tool_call     (one per tool call)
│   │   └── agent.final_answer
│   ├── benchmark.grade
│   ├── evaluator.deterministic
│   ├── evaluator.llm_judge
│   ├── evaluator.tool_use
│   ├── evaluator.state_based
│   ├── evaluator.trace_quality
│   ├── policy.check
│   └── benchmark.teardown
├── scorer.summarize
└── report.render
```

Span attributes carry `run_id`, `task_id`, `agent_id`, `model_id`, `cost_usd`, `tokens_in`, `tokens_out`, `latency_ms`. Cost and tokens are emitted as both span attributes and CloudWatch Metrics (via the embedded metric format), so dashboards can graph them without parsing logs.

Local runs export to a JSONL file in the bundle. AWS runs export to CloudWatch via ADOT, with the JSONL file still produced inside the bundle for portability — the bundle is the contract, the cloud surface is augmentation.

---

## 9. Configuration and the registry

`RunConfig` is YAML. The orchestrator resolves string references in the config (e.g. `runner: langgraph-claude-4-7`) against a registry defined in `src/agentevalops/core/registry.py`. The registry is populated at import time by adapter modules calling `register_runner`, `register_evaluator`, etc.

Users can register their own adapters by installing a package that exposes the `agentevalops.adapters` entry point group in its `pyproject.toml`. The platform discovers third-party adapters via `importlib.metadata.entry_points()` at startup and adds them to the registry. This is how external users contribute new benchmarks and runners without forking the platform.

A sample v0.1 `RunConfig` (local execution, toy benchmark):

```yaml
run_name: toy-mock-agent-local
benchmark:
  id: toy
agent:
  runner: mock
  resource_limits:
    max_tokens: 10_000
    max_wall_seconds: 60
    max_cost_usd: 0.0
evaluators:
  - id: deterministic-pytest     # deterministic (required for v0.1)
policy:
  spec_id: cost-ceiling-default
backend:
  id: local
storage:
  bundles: ./runs/
  trace_store: local
max_concurrent_tasks: 1
```

In a future phase, the `backend.id` can be changed to `aws`, `runner` to a real agent runner, and `benchmark.id` to a real benchmark — no orchestrator changes needed.

Every field maps to a protocol implementation registered in the system. Adding a new runner is one entry in the registry plus a YAML field, no orchestrator changes.

---

## 10. What this document does not specify

This document specifies interfaces, layout, topology, and the orchestration loop. It deliberately does not specify:

The exact implementation of any one adapter — those live in their respective modules and are documented in `docs/benchmark_adapters.md`, `docs/writing_an_evaluator.md`, etc. The IaC implementation details — those live in `infra/aws/`. The CI pipeline configuration beyond the rules it must enforce — that is in `.github/workflows/`.

If a future change touches interfaces, layout, or the orchestration contract, it requires an update to this document. If it only touches an adapter's internals, it does not.
