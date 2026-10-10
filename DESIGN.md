# DESIGN.md — AgentEvalOps Platform

> **Status:** Draft v0.2 — repositioned by [ADR-0005](docs/adr/0005-agentic-assurance-rebaseline.md) · **Owner:** *(your name)* · **Last updated:** 2026-10-10
> This document is the source of truth for *what* AgentEvalOps is, *why* it exists, and *what it is not*. Implementation details live in `ARCHITECTURE.md`. Anything in this document overrides anything in code comments, READMEs, or chat threads.

---

## 1. One-line definition

**AgentEvalOps is a local-first, open-source toolkit that tests tool-using AI agents against Singapore's agentic-AI testing guidance and produces signed evidence packs — mapped to the AI Verify Testing Framework — whose verdicts a third party can re-derive from the recorded runs.**

If you only read one sentence, read that one. Everything below is in service of it.

---

## 2. Problem

Modern AI agents are shipped on vibes. Teams build agents on top of LangGraph, MCP, or bespoke loops, demo them on cherry-picked tasks, and push them to production with no systematic answer to:

- *Did this agent actually solve the task, or did it just produce plausible-looking output?*
- *Which of its tool calls were valid, and which were hallucinated?*
- *When the next model version drops, did the agent get better, worse, or just differently broken?*
- *How much did this run cost, in tokens and in dollars, and where did the cost concentrate?*
- *When the agent fails in production, can we replay the failure deterministically?*
- *Is the agent's behavior compliant with the organization's policy and safety constraints?*

Each of these is a known problem with a partial solution somewhere — SWE-bench grades coding patches, MLE-bench grades ML submissions, LLM-as-judge frameworks grade soft criteria. **None of them compose.** A team that wants all of the above ends up duct-taping five tools together and inventing their own trace schema.

AgentEvalOps is the missing composition layer: a single platform with a stable trace schema, pluggable evaluators, pluggable benchmark adapters, pluggable agent runners, and a portable result bundle that captures everything needed to reproduce, compare, and audit an evaluation run.

---

## 3. Audience

The platform is designed for three personas, in priority order:

**The AI System Owner** is the primary user. They own an agent headed for production and need pre-deployment baseline tests, a fail-closed release gate, and evidence for the AI Verify Testing Framework's and MGF-Agentic's checks — produced from their own CI, on their own infrastructure.

**The Third-Party Tester** is the secondary user. They work for a testing firm, accredited or applying, and need a consistent, documented, reproducible method and portable evidence their clients can verify.

**The Reviewer** is the tertiary user: internal compliance, external auditors, procurement. They consume evidence packs and need to verify integrity and re-derive verdicts without re-running anyone's agent.

(The Applied Research Engineer persona was removed by ADR-0005: research benchmarks can still run through `BenchmarkAdapter`, but none is designed for.)

The platform is **not** designed for end users of agents (the citizen using a service agent, the data scientist using a coding assistant). Their experience is the agent's job, not ours.

---

## 4. Why this is hard

Agent evaluation is harder than model evaluation in five specific ways, and the platform must take each one seriously:

**Non-determinism compounds across steps.** A model that is 95% reliable per call is 60% reliable across ten calls. Naive scalar metrics hide this. The platform must capture per-step traces and support trace-level evaluators, not just final-answer scoring.

**The grader is often as fallible as the agent.** LLM-as-judge evaluators have their own biases, refusals, and inconsistencies. The platform must support multiple grader types running in parallel and surface their disagreements rather than averaging them away.

**Tool calls fail silently.** An agent can call the wrong tool, pass invalid arguments, or hallucinate a tool's response, and still produce a final answer that looks correct. Tool-use evaluators must be first-class, not an afterthought.

**Reproducibility requires more than seeds.** Replaying an agent run requires the same model version, the same prompt, the same tool definitions, the same environment state, *and* the same stochastic sampling. The platform must capture all of this in a single bundle, or replay is a lie.

**Cost is a real failure mode.** An agent that solves the task by burning $400 in tokens has not solved the task in any production-relevant sense. Cost and latency must be evaluator inputs, not metadata.

The platform's design choices — the trace schema, the result bundle, the five-evaluator taxonomy, the replay command — are direct responses to these five difficulties.

---

## 5. The smallest useful version

The temptation with platform projects is to build the platform forever and never ship a benchmark run. The initial release is deliberately scoped to be unimpressive in scope but unimpeachable in execution:

**v0.1 (Local AgentEvalOps Runner).** A user can clone the repo, run `make install && make test`, and execute `agentevalops run --config configs/toy_smoke.yaml` to evaluate a mock coding agent on a set of toy tasks. The run produces a result bundle on disk containing the trace, scores from a deterministic evaluator, a policy check result, and a `replay_command.txt`. The CLI prints a markdown report; no dashboard or API service is required.

**v0.1 includes:** installable Python package, CLI entry point, core schemas, core protocols, local orchestration loop, toy benchmark adapter, mock agent runner, deterministic evaluator, basic policy checker, result bundle writer, replay command, markdown or CLI report.

**v0.1 excludes:** dashboard, FastAPI service, AWS deployment, Bedrock AgentCore, real SWE-bench execution, required LLM-as-judge, required LangGraph or any real-model runner, ToolUseEvaluator as a required feature, multi-cloud, SaaS, marketplace packaging.

If a reviewer can clone the repo and reach a result bundle in under five minutes, the credibility threshold is crossed. Everything beyond v0.1 — real benchmark integration, AWS Fargate deployment, additional evaluators — is iteration on a foundation that already works.

**v0.5 (Agentic baseline + evidence pack).** MGF-Agentic §2.3.2's four test categories, run against a harness-owned tool environment on a seeded synthetic domain, scored deterministically, repeated k times, gated fail-closed, and packaged as a signed bundle a reviewer can validate and re-score on a fresh clone. One published run on one real model. See `docs/adr/0005-agentic-assurance-rebaseline.md`.

---

## 6. Inputs

The platform consumes the following input types, all defined as Pydantic schemas in `src/agentevalops/core/schemas.py`:

A **TaskSpec** describes a single evaluation task: identifier, domain, environment requirements, success criteria, expected artifacts, and any task-specific configuration. Tasks come from benchmark adapters; the adapter is responsible for translating SWE-bench instances, MLE-bench competitions, or enterprise workflow definitions into TaskSpecs.

An **AgentConfig** describes the agent under evaluation: runner type (LangGraph, Bedrock, OpenAI-compatible, Ollama, replay), model identifier, prompt version, tool definitions, resource limits, and policy constraints. Two AgentConfigs that differ only in model identifier produce two comparable runs — this is the foundation of regression testing.

A **RunConfig** binds a TaskSpec set to an AgentConfig and specifies which evaluators to apply, where to store the result bundle, what cost ceilings to enforce, and which cloud backend to use. A RunConfig is a YAML file checked into the repo or generated by the CLI.

A **PolicySpec** describes organizational constraints that an agent run must satisfy: prohibited tools, data residency requirements, maximum spend, prohibited content categories. PolicySpecs are inputs to the `PolicyChecker` protocol, which operates post-run on the completed trace and returns a PASS/FAIL/WARN verdict with citations. The platform does not enforce policy constraints at runtime; it evaluates them after the fact.

---

## 7. Outputs

Every evaluation run produces a **result bundle** — a self-contained, portable folder that is the platform's primary deliverable:

```
result_bundle/
├── metadata.json          # schema version, run_id, timestamps, platform version, git_sha/git_dirty, status
├── config.json            # the resolved RunConfig (and PolicySpec) that produced this run
├── traces.jsonl           # one TraceEvent per line
├── evaluations.json       # per-task EvaluationResult objects
├── policy.json            # PolicyVerdict, or null if no policy was specified
├── summary.json           # RunSummary: passed / failed / not_evaluated, cost, tokens
├── report.md              # human-readable report (same data as summary.json)
├── replay_command.txt     # reconstructable invocation + provenance (format 0.4+)
├── manifest.json          # format version, per-file size + sha256; written last
│
│   # added at format 0.5 — ADR-0005 D6; final names fixed by ADR-0007
├── test_plan.json         # system declaration, scenarios, k, thresholds; hashed before the run
├── controls.json          # control coverage, computed from executed scenarios
├── env_state/             # per-task tool-environment end states
├── evidence_report.md     # AI Verify Testing Framework-shaped: pre-filled vs organisational evidence
└── manifest.json.sig      # detached signature over manifest.json
```

(Before ADR-0005 this tree listed `config.yaml`, `trace.jsonl`, `scores.json`, `artifacts/`, `logs/`, `cost_report.json` and `failure_analysis.md` — a target that was never built. The list above is the bundle as built, plus format 0.5's planned additions.)

The bundle is the unit of portability. A user can hand the bundle to a colleague, attach it to a GitHub issue, store it in S3 for compliance, or feed it back into the platform for comparison against a later run. The bundle format is versioned and backwards-compatible within a major version.

Beyond the bundle, the platform produces aggregated views via the CLI — regression comparisons between agent versions and cost summaries. A dashboard (Streamlit or similar) is a future-work consumer of bundles, not a v0.1 deliverable; if such a UI breaks, the bundles still exist and remain analyzable.

---

## 8. Non-goals

This section is the most important section of this document, because it is where most platform projects die. AgentEvalOps explicitly does not do the following:

**It is not a universal AGI benchmark.** The platform aggregates and orchestrates existing benchmarks; it does not propose new ones. New benchmarks are research contributions; this is an engineering platform.

**It is not an agent framework.** The platform evaluates agents built on LangGraph, MCP, OpenAI Agents SDK, or hand-rolled loops. It does not provide its own agent abstractions beyond what is needed to run them under uniform observation. If you find yourself building "the AgentEvalOps way to write an agent," stop.

**It is not bound to one model provider.** Anthropic, OpenAI, Bedrock, Ollama, and OpenAI-compatible endpoints are all first-class. The platform does not optimize for any one provider's features at the cost of provider-neutrality in the core.

**It is not bound to one cloud provider.** No cloud backend is planned before v0.6 (ADR-0005 D3); when one comes, it is isolated behind the `CloudBackend` interface (AWS first, in `src/agentevalops/cloud/aws/`), and ADR-0004's two-axis framing stands. The core platform is cloud-neutral. "Cloud provider" in this paragraph means a `CloudBackend` implementation — the compute/secrets/telemetry plane a run executes on. Consuming a cloud vendor's *model endpoint* (Bedrock, Azure OpenAI) is a different concern, already governed by the "not bound to one model provider" non-goal directly above: it's a runner, not a backend, and admitting one requires no change to this paragraph's position. `CloudBackend` implementations for Azure and GCP are future roadmap items, not active development. AWS itself is not required for v0.1 — local execution is the default. See `docs/adr/0004-cloud-backend-two-axes.md` for the full two-axis split and why the distinction matters in practice.

**It is not Kubernetes-first.** ECS Fargate (and equivalent managed container services on other clouds) is the deployment target. Kubernetes is supported only via the same container interface; no Helm charts, no operators, no CRDs in the v1 timeframe.

**It is not a runtime safety system.** The platform grades policy compliance after a run completes; it does not intercept tool calls in flight or block agents from executing dangerous actions. Runtime safety is a different product with different latency and reliability constraints.

**It does not reimplement scoring logic for established benchmarks.** SWE-bench has an official harness; MLE-bench has an official harness; the platform wraps them and adds orchestration, observability, and cost — it does not reproduce their grading code. If the upstream harness is wrong, that is upstream's problem to fix.

**It does not store agent credentials.** API keys, IAM credentials, and tool-specific secrets are read from the user's environment or from AWS Secrets Manager (and equivalents). The platform never persists credentials to disk or to the result bundle.

**It is not a certification scheme or a compliance verdict.** It produces evidence. Accreditation applies to testers, not tools, and acceptance of residual risk stays with the deploying organisation (ADR-0005 D10).

**It is not a governance-workflow (GRC) product.** Documentary evidence is attached to the evidence report, not managed by it.

**It does not reproduce third-party framework text.** Identifiers and paraphrase only, unless the publisher grants permission (ADR-0005 D5).

**It is not a capability leaderboard.** Research benchmarks can run through `BenchmarkAdapter`, but none is on the roadmap.

**It does not fine-tune or remediate models.** That is Quench's side of the boundary.

---

## 9. Architectural commitments

These are the load-bearing decisions. They are not implementation details — changing them changes what the platform *is*.

**Interfaces before implementations.** Nine core protocols (`AgentRunner`, `BenchmarkAdapter`, `TraceStore`, `ArtifactStore`, `Evaluator`, `Scorer`, `PolicyChecker`, `ReportGenerator`, `CloudBackend`) are defined in `src/agentevalops/core/` and every concrete class implements one of them. New benchmarks, new agents, new clouds plug in by implementing protocols, never by modifying the core.

**Five evaluator types, all first-class.** Deterministic, LLM-as-judge, tool-use, state-based, and trace-quality evaluators are all supported and can run in parallel against the same trace. Scores are reported per-evaluator; the platform does not produce a single composite score by default, because composite scores hide the disagreements that matter.

**OpenTelemetry as the trace substrate.** The platform's trace events are OpenTelemetry spans, serialized to JSONL for the bundle. In future cloud-enabled runs they can be exported to CloudWatch (or other OTel-compatible backends) without changing the bundle format. The platform does not invent its own tracing format.

**The result bundle is the contract.** Every other surface — dashboards, comparisons, reports, future products — consumes bundles. The bundle format is versioned, documented, and backwards-compatible. If the platform's code disappeared tomorrow, bundles produced today would still be analyzable.

**Local-first; cloud deferred.** Local execution is the default and, through v0.5, the only mode (ADR-0005 D3). When a cloud backend arrives, AWS-specific code is quarantined to `src/agentevalops/cloud/aws/` and `infra/aws/`; a grep for `boto3` outside those directories is a build failure (the import-linter contract already exists, vacuously). The first port to another cloud should be a two-week project, not a rewrite — this claim is currently untested, since `CloudBackend` has no implementations at all yet (not even a `LocalBackend`); `docs/adr/0004-cloud-backend-two-axes.md` is what sets up the eventual measurement, by recording the Azure primitive mapping before `AwsBackend` is designed rather than after.

**Reproducibility is a tested property, not a hope.** The CI suite re-scores golden fixture bundles with `rescore` and requires `MATCH` (ADR-0005 D7); behavioural replay — re-running `replay_command.txt` against recorded model outputs — joins it when `ReplayRunner` exists. If re-scoring breaks, the build breaks.

---

## 10. What success looks like

At the v0.1 milestone, success is the demo path completing on a fresh clone in under five minutes with zero cloud credentials. The result bundle is on disk. The replay command works. A policy check runs and produces a verdict.

At the v0.5 milestone, success is a published live baseline run on one real model whose signed evidence pack validates, verifies and re-scores `MATCH` on a fresh clone, with cost reconciled against the provider.

At the v1.0 milestone, success is three properties simultaneously: (a) at least two domain packs; (b) buyer mode over MCP; (c) at least one evidence pack produced by someone other than the author, against their own agent — ideally inside the Global AI Assurance Sandbox.

The last property is the real bar. A platform that only its author has used is a portfolio piece. A platform with one external user is a product.

---

## 11. Open questions

These are deliberately unresolved and will be revisited as the platform matures. Listing them is part of the design discipline; pretending they are settled is how platforms accumulate hidden technical debt.

How much of the SWE-bench official harness should the platform vendor versus call out to? Vendoring is faster but couples versions; calling out is cleaner but adds a dependency the user must install. **Moot while SWE-bench is frozen (ADR-0005 D3).** If it returns, `docs/benchmark_adapters.md` D1–D2 stand: a pinned pip extra, invoked as a subprocess, never imported.

Should the platform define its own MCP server for agents to call into during evaluation (for sandboxed file ops, controlled web access), or should it remain transport-neutral and let agents bring their own tools? The MCP-server-as-platform-feature is appealing but increases scope. **Resolved by ADR-0005 D4:** yes — as the transport for the harness-owned tool environment, after v0.5. That is also what makes buyer mode possible. It is not a separate feature.

How should the LLM-judge evaluator handle disagreement between judges of different model families? Average, surface, vote, or refuse-to-score? Current intuition: surface. LLM-as-judge is optional in v0.1 (the deterministic evaluator is sufficient); this question is revisited once there is data from real runs. **Resolved by ADR-0005 D8:** surface, never average — and no judge feeds a gate without a stored human-agreement measurement.

What is the right cost model for the platform itself in a future hosted mode — per-run, per-token, per-task, or open-source-with-hosted-tier? This is a post-v1.0 question but worth flagging now so the architecture does not foreclose any of them.

---

## 12. Document hygiene

This document is updated when the answer to any of these questions changes: *what is this?*, *who is it for?*, *what does it consume?*, *what does it produce?*, *what does it explicitly not do?* Implementation drift does not require an update; positioning drift does. If a reader cannot tell from this document what AgentEvalOps is in 2026 versus 2027, the document has failed.
