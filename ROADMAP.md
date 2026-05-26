# ROADMAP.md — AgentEvalOps Platform

> **Status:** Draft v0.1 · **Owner:** *(your name)* · **Last updated:** 2026-05-15
> **Target release:** v0.5 in the **week of January 4, 2027** (Week 33).
> Available: 33 calendar weeks, ~12 productive hours/week, ~2 weeks holiday buffer = **~372 engineering hours** total.
> This document is the source of truth for *when*. The *what* lives in [`DESIGN.md`](./DESIGN.md), the *how* in [`ARCHITECTURE.md`](./ARCHITECTURE.md).

---

## 1. v0.5 scope (what ships on January 4, 2027)

**In scope:**
- All nine protocols defined, typed strictly (`pyright --strict` passes on `src/agentevalops/core/`).
- Core orchestration loop functional end-to-end.
- One real benchmark adapter: **SWE-bench Verified** (wrapping the official upstream harness, not reimplementing). One toy adapter for tests and the v0.1 demo.
- Three of five evaluators working: **deterministic** (pytest-based), **tool-use validator**, and optionally **LLM-as-judge** (Claude via Bedrock, if time permits; not required for a credible release).
- Two agent runners: **mock** (for tests) and **LangGraph + Claude via Bedrock**.
- Two cloud backends: **LocalBackend** (local processes / docker-compose) and **AwsBackend** (ECS Fargate).
- Result-bundle format frozen and versioned; bundle-replay CI test passing.
- Reproducible AWS deployment via **CDK (Python)**.
- One published end-to-end run: SWE-bench Verified subset (20 instances) on AWS Fargate, real result bundle checked into `reports/`.
- README, DESIGN.md, ARCHITECTURE.md, ROADMAP.md, SECURITY.md current.
- CI green: lint, `pyright --strict` on core, unit + integration + smoke + replay tests, import-linter enforcement of layer boundaries.

**Out of scope (deferred to v0.7/v1.0):**
MLE-bench, MLGym, Terminal-Bench adapters · Bedrock AgentCore runner · Azure and GCP backends · State-based and trace-quality evaluators · Dashboard (CLI report only) · FastAPI service · Multi-tenant control plane · SaaS or marketplace packaging.

---

## 2. Weekly work breakdown structure

Phase legend: **F** Foundation · **C** Core protocols · **V1** First vertical slice (local) · **A** AWS deployment · **S** SWE-bench integration · **H** Hardening · **P** Polish & release

| Wk | Calendar | Phase | Primary deliverable | Sub-tasks | Exit criteria | Deps | Risk |
|---|---|---|---|---|---|---|---|
| 1 | May 18–24 | F | Repo skeleton + tooling | Init repo with `uv`, `ruff`, `pyright`, `pytest`, `pre-commit`. Add DESIGN.md/ARCHITECTURE.md/ROADMAP.md. Configure `pyproject.toml`. Set up GitHub Actions skeleton (lint + type-check, no tests yet). Add `import-linter` config (layer rules, no enforcement yet). Write CONTRIBUTING.md stub. | `make install && make lint && make type-check` all green on fresh clone | – | L |
| 2 | May 25–31 | C | `schemas.py` v1 | Pydantic v2 models for: `TaskSpec`, `AgentConfig`, `RunConfig`, `EnvHandle`, `TraceEvent` (with closed `kind` enum), `AgentResult`, `GradeReport`, `EvaluatorScore`, `RunSummary`, `ResourceLimits`. Add `schema_version` field. Unit tests for serialization round-trips. | All schemas import cleanly, round-trip serialization tests pass, `pyright --strict` clean | W1 | L |
| 3 | Jun 1–7 | C | `protocols.py` — all 9 protocols | Define `AgentRunner`, `BenchmarkAdapter`, `TraceStore`, `ArtifactStore`, `Evaluator`, `Scorer`, `PolicyChecker`, `ReportGenerator`, `CloudBackend` as `Protocol` classes. Add docstrings matching ARCHITECTURE.md. Write `MockAgentRunner` and `MockBenchmarkAdapter` for tests. | `pyright --strict` clean; mock implementations satisfy protocols | W2 | L |
| 4 | Jun 8–14 | C | `bundle.py` — result-bundle read/write | Implement bundle dir creation, `metadata.json`/`config.yaml`/`trace.jsonl`/`scores.json` writers, lifecycle (create → append → finalize → seal). Bundle reader. Version compatibility check. Unit tests cover partial-write recovery (interrupted bundle still readable). | Bundle round-trip test passes; interrupted-bundle test passes | W2 | L |
| 5 | Jun 15–21 | C | `orchestrator.py` — minimal loop | Implement the orchestration loop from ARCHITECTURE §5, but only against mock protocols. Async, with `max_concurrent_tasks` semaphore. Capture `replay_command` before execution. Best-effort finalize on KeyboardInterrupt. | Orchestrator runs 5 mock tasks end-to-end against mock runner + mock evaluator, produces valid bundle | W3, W4 | M |
| 6 | Jun 22–28 | C | Registry + CLI skeleton | `registry.py` with entry-point discovery (`importlib.metadata.entry_points("agentevalops.adapters")`). `cli.py` with `agentevalops run --config X` and `agentevalops report --run-id Y` commands using `typer`. Wire registry to mock adapters. | `agentevalops run --config configs/toy_smoke.yaml` works end-to-end with mock everything | W5 | M |
| 7 | Jun 29–Jul 5 | V1 | Toy benchmark adapter + deterministic evaluator | Implement `ToyBenchmarkAdapter` (3 trivial "fix the bug" Python tasks with embedded pytest). Implement `DeterministicEvaluator` (runs pytest, returns pass/fail). Wire into registry. Add `LocalTraceStore` (JSONL) and `LocalArtifactStore` (disk). | Toy benchmark runs against mock agent, real deterministic eval scores it, bundle is inspectable | W6 | M |
| 8 | Jul 6–12 | V2 | Real agent runner — LangGraph + Claude *(post-v0.1; enables v0.2)* | Implement `LangGraphRunner` wrapping a basic LangGraph agent with file-read/file-write/run-tests tools. Use Anthropic API directly (Bedrock comes in W17). Cost + token accounting in trace events. Respect `ResourceLimits`. **Note: this work happens before the v0.1 tag to get feedback early, but the v0.1 milestone does not require it — v0.1 demo path uses MockAgentRunner only.** | LangGraph runner solves at least 1/3 toy tasks; cost report accurate within 5% of API receipt | W7 | **H** — first integration with real LLM, framework version pinning, prompt iteration |
| 9 | Jul 13–19 | V1 | OpenTelemetry wiring | `otel_setup.py` configures tracer. Orchestrator + runner + evaluators emit spans per ARCHITECTURE §8 hierarchy. Local OTel exporter writes to JSONL alongside the trace. Span attributes carry cost/tokens/latency. | OTel spans appear in bundle's `trace.jsonl`; span hierarchy matches spec | W8 | M |
| 10 | Jul 20–26 | V1 | LLM-as-judge evaluator (optional) | `LLMJudgeEvaluator` using Claude. Define 2 judge prompts: relevance + adherence-to-task. Citations: judge must point at specific trace events. Cost accounted as evaluator cost in `EvaluatorScore`. **This week is optional: if behind schedule, skip and keep deterministic evaluator only.** | If implemented: judge runs against toy bundle, produces scores with non-empty citations; cost report includes judge spend. If skipped: v0.1 ships with deterministic evaluator only, LLM-judge deferred to v0.3. | W9 | M |
| 11 | Jul 27–Aug 2 | V1 | Policy checker + v0.1 release | `BasicPolicyChecker`: validates run against `PolicySpec` (cost ceiling, allowlist). Wire into orchestrator. Markdown/CLI report via `ReportGenerator`. **Tag v0.1.** Demo path: `make install && make test && agentevalops run --config configs/toy_smoke.yaml`. Update README with demo path. | v0.1 git tag pushed; fresh-clone demo path works in <5 min on a laptop; result bundle on disk; replay command works; policy verdict in bundle | W10 | M |
| 12 | Aug 3–9 | H | Replay system | `ReplayRunner` that re-runs from a recorded bundle by substituting recorded model outputs. `replay_command.txt` generation. CI test in `tests/replay/` that replays a checked-in fixture bundle and asserts behavioral match. | Replay CI test green; replay produces identical `tool_calls` order and `terminated_by` | W11 | **H** — critical for the reproducibility story; subtle bugs likely |
| 13 | Aug 10–16 | H | Import-linter enforcement + layer boundaries | Turn on `import-linter` enforcement in CI. Add `boto3`-not-outside-`cloud/aws/` grep check. Add per-layer test files. Refactor any leaks discovered. | `make lint` fails if a core module imports an adapter; `grep -r boto3 src/` returns hits only in `src/agentevalops/cloud/aws/` (currently empty, prep for W17) | W11 | L |
| 14 | Aug 17–23 | S | SWE-bench upstream harness vendoring decision + spike | Read upstream SWE-bench code. Decide: pip-install vs. vendor vs. submodule. Spike: get the official harness running locally against one Verified instance (no agent yet — just the grading half). Document decision in `docs/benchmark_adapters.md`. | Can grade a hand-written patch against one SWE-bench Verified instance using upstream harness; decision recorded | W11 | **H** — upstream API drift, Docker-in-Docker complications |
| 15 | Aug 24–30 | S | `SWEBenchAdapter.list_tasks` + `prepare_environment` | Implement task enumeration from SWE-bench Verified. Sandbox preparation: clone repo at the right SHA, apply test patch, set up Docker container per upstream conventions. Use `docker-py` for sandbox lifecycle. | Can enumerate 500 SWE-bench Verified tasks; can prepare environment for 5 sample instances | W14 | H — sandboxing is fiddly |
| 16 | Aug 31–Sep 6 | S | `SWEBenchAdapter.grade` + `teardown` | Wire upstream harness as the `grade` implementation. Implement `teardown` (container cleanup). Integration test: agent applies a known-good patch, adapter grades it as pass. | Known-good patch graded PASS; known-bad patch graded FAIL; no container leaks | W15 | M |
| 17 | Sep 7–13 | A | `AwsBackend` skeleton + Bedrock runner | Implement `AwsBackend.fetch_secret` (Secrets Manager) and `submit_job` stub. Add `BedrockRunner` (same LangGraph agent, but model via Bedrock instead of Anthropic API). Test Bedrock auth from local. | LangGraph agent runs against toy benchmark using Claude-via-Bedrock from local machine | W11 | M |
| 18 | Sep 14–20 | A | CDK skeleton in blueprints repo | Spin up `agent-evalops-aws-blueprints` repo. CDK Python project. Stacks: `NetworkStack` (VPC + subnets), `StorageStack` (S3 bucket for bundles, DynamoDB table for run index), `IamStack` (Fargate task role). `cdk deploy` works. | `cdk deploy` provisions VPC + S3 + DDB + IAM in dev account; smoke check via aws-cli | W17 | M |
| 19 | Sep 21–27 | A | Fargate eval runner stack | `EvalRunnerStack`: ECR repo, Fargate task definition pointing at AgentEvalOps image, ECS cluster. Dockerfile for the eval runner. Push image to ECR. | Manually submitted Fargate task pulls image, runs `agentevalops --version`, exits clean | W18 | M |
| 20 | Sep 28–Oct 4 | A | `AwsBackend.submit_job` end-to-end | Wire `submit_job` to RunTask API. Pass `RunConfig` via S3 + env var. Implement `job_status` polling. Implement `cancel_job`. Tee Fargate stdout to CloudWatch Logs. | `agentevalops run --backend aws --config toy.yaml` launches Fargate task, polls to completion, produces bundle in S3 | W19 | **H** — IAM scoping, networking, image-pull issues |
| 21 | Oct 5–11 | A | OTel → CloudWatch via ADOT | Add AWS Distro for OpenTelemetry sidecar to task definition. Span export to CloudWatch Logs. Cost/token attributes emitted as CloudWatch Metrics via embedded metric format. | Spans visible in CloudWatch Logs Insights; `agentevalops.cost_usd` metric appears in CloudWatch Metrics | W20 | M |
| 22 | Oct 12–18 | A | v0.2 release: toy benchmark on AWS | Polish AWS path. Update README with AWS demo. **Tag v0.2.** Demo path: `cdk deploy` then `agentevalops run --backend aws --config configs/toy_aws.yaml`. | v0.2 tag pushed; second demo path works end-to-end on AWS; cost <$1 per run | W21 | M |
| 23 | Oct 19–25 | S | SWE-bench on Fargate — first run | Adjust Fargate task definition for SWE-bench memory/disk needs. Run SWE-bench adapter on 3 Verified instances via AwsBackend. Debug Docker-in-Docker on Fargate (or switch to a sandbox-on-Fargate approach without nested Docker). | At least 1/3 SWE-bench Verified instances produces a graded bundle on AWS | W16, W22 | **H** — Docker-in-Docker on Fargate is the single most likely blocker |
| 24 | Oct 26–Nov 1 | S | SWE-bench reliability pass | Fix top 3 failure modes from W23. Add retries for transient sandbox failures. Tune resource limits. Run 10 instances. | 7+/10 instances grade cleanly (PASS or legit FAIL, not infra error) | W23 | H |
| 25 | Nov 2–8 | S | v0.3 release: SWE-bench on AWS works | **Tag v0.3.** Run 20 Verified instances; commit the result bundle to `reports/swebench_verified_20_v0.3.md`. Update README with real numbers. | v0.3 tag; real result bundle in repo; pass rate documented (whatever it is) | W24 | M |
| 26 | Nov 9–15 | H | Policy checker + cost ceiling | `AllowlistChecker`, `CostCeilingChecker`. Wire into orchestrator. Add `PolicySpec` to `RunConfig`. Tests for policy violation → bundle marked WARN/FAIL. | Run with cost ceiling $0.50 against a task expected to cost $1 → policy verdict FAIL with citation | W25 | L |
| 27 | Nov 16–22 | H | Scorers + regression comparison | `PassAtKScorer`, `CostSummaryScorer`, `RegressionScorer` (compares two `RunSummary`s and emits delta). DynamoDB run index for finding baseline runs. | `agentevalops compare --baseline <run_id> --candidate <run_id>` prints regression report | W25 | M |
| 28 | Nov 23–29 | H | Report generators | `FailureAnalysisLLM` (uses Claude to summarize what went wrong on a failed task, citing trace events). `CostBreakdownReport` (markdown table by step). Wire into orchestrator. | Failed task produces `failure_analysis.md` that cites at least 3 specific trace events; manual sanity-check on 5 examples | W27 | M |
| 29 | Nov 30–Dec 6 | H | Bundle-replay CI hardening + SECURITY.md | Add 3 more fixture bundles to `tests/replay/fixtures/` (one passing, one failing, one infra-error). Write SECURITY.md: threat model, IAM scoping, secret handling, sandboxing assumptions. | Replay CI test runs all 4 fixtures; SECURITY.md reviewed against ARCHITECTURE.md non-goals | W12 | M |
| 30 | Dec 7–13 | H | COST_MODEL.md + cost guardrails doc | Write a lightweight `COST_MODEL.md` documenting cost-estimation assumptions: observed per-task spend on toy / SWE-bench, per-run overhead, S3 storage cost over time. This is a notes document, not a pricing engine. Also provision a CloudWatch alarm CDK construct for budget overrun. | `COST_MODEL.md` exists with measured numbers from W25; CDK alarm provisioned and tested | W25 | L |
| 31 | Dec 14–20 | P | v0.4 release: full v0.5 feature freeze | **Tag v0.4.** Feature complete. No new modules after this week. Run full SWE-bench Verified subset (20 instances) once more to refresh the canonical result bundle. | v0.4 tag; 20-instance run completes; remaining work is docs + polish only | W29, W30 | M |
| 32 | Dec 21–27 | — | **Holiday buffer week 1** | Light touch. If ahead: nothing. If behind: catch-up on whichever of W30–W31 slipped. | n/a | – | – |
| 33 | Dec 28–Jan 3 | — | **Holiday buffer week 2** | Same. Use this week to draft the v0.5 release notes mentally, not to write code. | n/a | – | – |
| 34 | Jan 4–10 | P | **v0.5 RELEASE** | Final README pass with sample bundle output and CLI report screenshot. Final ARCHITECTURE.md/DESIGN.md/ROADMAP.md sync. Write the v0.5 release-notes post (target: a blog post or GitHub release body, ~1500 words). Record a 3-minute demo (loom or asciinema). **Tag v0.5. Publish.** | v0.5 tag pushed; release notes published; demo linked from README; one external reviewer can run the demo path successfully | W31 | M |

---

## 3. Milestone ladder

| Tag | Week | Scope | Demo path | Unlocks |
|---|---|---|---|---|
| **v0.1** | W11 (Aug 2) | Local platform: protocols, orchestrator, toy benchmark, mock runner, deterministic evaluator, basic policy checker, result bundle writer, replay command, markdown/CLI report. No dashboard, no FastAPI, no AWS, no real SWE-bench, LLM-as-judge deferred. | `make install && make test && agentevalops run --config configs/toy_smoke.yaml && agentevalops report --run-id latest` | Replay (W12), AWS work (W17+) — protocols stable enough to build cloud layer |
| **v0.2** | W22 (Oct 18) | AWS deployment of toy benchmark via Fargate; CDK provisions full stack; CloudWatch observability live | `cdk deploy && agentevalops run --backend aws --config configs/toy_aws.yaml` | SWE-bench-on-AWS work (W23) |
| **v0.3** | W25 (Nov 8) | SWE-bench Verified running on AWS Fargate; first real result bundle committed | `agentevalops run --backend aws --config configs/swebench_verified_20.yaml` | Hardening phase (policy, scorers, reports) on a real workload |
| **v0.4** | W31 (Dec 20) | Feature freeze; policy + scorers + reports + cost model complete | Full demo path with regression comparison: `agentevalops compare --baseline v0.3-run --candidate v0.4-run` | Final polish (W34) — no new code, only docs/release |
| **v0.5** | **W34 (Jan 4–10, 2027)** | Public release with release notes, demo video, polished docs, external-reviewer-tested | Public README's demo path; reviewer-friendly | v0.7 roadmap (MLE-bench, AgentCore, state-based + trace-quality evaluators) |

---

## 4. Critical path

These five items, if they slip, cascade into the v0.5 date slipping. Everything else has slack.

**Critical path item 1: Schemas + protocols (W2–W3).** Earliest start W2, latest finish W4. Slippage cost: every downstream week is built on these signatures. Mitigation: don't over-engineer in W2–W3 — get to compilable-and-pyright-clean fast, iterate the schemas in subsequent weeks while implementations are still thin. The bundle-version compatibility test means schema evolution is survivable.

**Critical path item 2: Real LangGraph + Claude runner (W8).** Earliest start W8, latest finish W16. Slippage cost: all AWS work (W17+) depends on a working real runner. **This is NOT required for v0.1 — v0.1 uses MockAgentRunner.** Mitigation: if LangGraph proves painful, fall back to a hand-rolled minimal agent loop (tool-call → execute → observe) using the Anthropic SDK directly. The architecture treats runners as pluggable; "LangGraph specifically" is not a requirement, "a real runner for v0.2" is.

**Critical path item 3: SWE-bench harness vendoring decision (W14).** Earliest start W14, latest finish W15. Slippage cost: this is the highest-uncertainty technical investigation in the whole plan. Mitigation: if upstream proves intractable (Docker-in-Docker on Fargate, harness API mismatches), descope to "SWE-bench Lite" instead of Verified, which has simpler grading. The result bundle is more impressive with Verified, but Lite still ships a real benchmark.

**Critical path item 4: Fargate eval runner end-to-end (W20).** Earliest start W19, latest finish W22. Slippage cost: this is the make-or-break for the AWS story. Mitigation: if Fargate proves stubborn, the fallback is "AWS Batch" or "EC2 with launch templates" — same `CloudBackend` interface, different implementation. The plan does not assume Fargate-specifically.

**Critical path item 5: SWE-bench on Fargate (W23–W24).** Earliest start W23, latest finish W25. Slippage cost: this is the v0.3 release and the centerpiece result bundle. Mitigation: if Docker-in-Docker on Fargate is blocked, switch to a "sandbox-as-Fargate-task" approach where each SWE-bench instance is its own Fargate task instead of a nested container. More expensive per run but unblocks the architecture.

The non-critical-path items — policy checker, scorers, report generators, COST_MODEL.md — have multi-week slack and can move around freely between W26 and W31.

---

## 5. Risk register

| # | Risk | Prob | Impact | Mitigation | Trigger condition |
|---|---|---|---|---|---|
| 1 | Docker-in-Docker on Fargate doesn't work cleanly for SWE-bench sandboxes | H | H | Pre-commit to fallback architecture: one Fargate task per SWE-bench instance, no nested containers. Spike the decision in W14, not W23. | W14 spike fails to grade a single instance in the harness, or W23 first attempt produces 0/3 graded bundles |
| 2 | LangGraph or LangChain breaking-change releases mid-build | M | M | Pin exact versions in `pyproject.toml`. Treat runner code as wrapping a snapshot, not following HEAD. Vendor minimal subset if needed. | A `pip install` in CI fails between W11 and W30 due to upstream version churn |
| 3 | Personal bandwidth drops below 12 hr/week (work crunch, illness, life) | M | H | Scope-cut ladder pre-committed (§6). Re-baseline every 4 weeks (W4, W8, W12, ...) — if you're behind two weeks, execute the next scope cut immediately, don't wait. | Any 4-week window where commit cadence drops below half the planned weekly deliverables |
| 4 | SWE-bench Verified upstream changes API/format | L | H | Pin a specific upstream commit SHA in the adapter. The benchmark version is part of the result bundle metadata. If upstream changes, that's a v0.7 problem. | A test that was green in W16 starts failing because of an upstream re-release |
| 5 | AWS cost overrun (Fargate + Bedrock spend) | M | M | Budget alarm at $50/month in W18; hard cap at $200. CloudWatch alarm + SNS to email. Move to smaller instance counts if alarm fires. | Monthly AWS bill exceeds $50 in any month |
| 6 | Bundle-replay determinism is harder than expected (model-side non-determinism) | M | M | Recorded replay (substituting model outputs) is the v0.5 requirement; live replay is not required. If even recorded replay drifts, scope the determinism test to "behavioral structure" not "exact output." | W12 replay test fails after one round of fixing |
| 7 | Protocol design has a load-bearing flaw discovered mid-build | L | H | Schemas have `schema_version`; one breaking-change pivot is survivable. Reserve 2 weeks of slack in the plan (the holiday buffer doubles as protocol-revision buffer if needed). | A new evaluator or runner in W20+ can't be expressed in the existing protocol shape |
| 8 | Scope creep — "while I'm here, let me also add MLE-bench / dashboard / AgentCore / FastAPI" | **H** | H | DESIGN.md non-goals are the answer; re-read them at the start of every month. If a feature isn't on this roadmap, it doesn't get built before v0.5. | Any week's commits include code in `benchmarks/mlebench/`, `dashboard/`, `api/`, or `cloud/aws/agentcore_jobs.py` |

Risk 8 is the highest-probability risk by a wide margin. The single most common way this plan fails is not a technical blocker but discipline failure — building the dashboard "because it's only a weekend" and then losing three weekends.

---

## 6. Scope-cut ladder

Re-baseline every 4 weeks. If behind schedule, execute the corresponding cut immediately, don't hope to catch up.

**If 2 weeks behind by Week 20 (Oct 4):** Cut the LLM-as-judge evaluator from v0.5 (defer to v0.7). Keep deterministic only. Removes ~1 week of effort. Minimum viable v0.5 still includes SWE-bench-on-AWS, which is the headline.

**If 4 weeks behind by Week 28 (Nov 29):** Cut the scorers (regression comparison) and the LLM-driven failure-analysis report. Replace with a simple cost-and-pass-rate markdown report. Remove the DynamoDB run index — bundles in S3 are enough. Removes ~2 weeks. Minimum viable v0.5 still includes SWE-bench-on-AWS with a real result bundle and policy checker.

**If 6 weeks behind by Week 32 (Dec 27):** Cut the AWS deployment entirely from v0.5 scope. Reposition v0.5 as "local-only platform with real SWE-bench Verified subset run locally," and pull AWS into v0.6 (target: March 2027). Removes ~6 weeks of AWS work. Minimum viable v0.5 still ships on Jan 4 with a real benchmark and real result bundle — just on a laptop, not on Fargate. **This is the load-bearing cut. AWS is the most visually impressive feature but is also the most slippable.** A v0.5 with no AWS is still a credible portfolio artifact; a v0.5 that misses Jan 4 to keep AWS in scope is not.

**If 8+ weeks behind:** Acknowledge the plan was wrong and re-baseline. Don't ship a v0.5 you're embarrassed by to hit a date.

---

## 7. Re-baselining cadence

The roadmap is checked against reality every 4 weeks, on the same day. Re-baseline checkpoints fall on:

- **W4 checkpoint (mid-Jun):** Are protocols + schemas + orchestrator on track? If W5 isn't running a mock task end-to-end, scope-cut now.
- **W8 checkpoint (mid-Jul):** Is the real LangGraph runner working? This is post-v0.1 work — it enables v0.2. If it's blocked, note the blocker and continue toward v0.1 (W11) using MockAgentRunner. Switch to a hand-rolled SDK loop if LangGraph proves intractable.
- **W12 checkpoint (mid-Aug):** Is replay working? Is v0.1 tagged? If v0.1 isn't tagged by W12, execute the 2-weeks-behind cut.
- **W16 checkpoint (mid-Sep):** Is SWE-bench grading working locally? Critical path checkpoint.
- **W20 checkpoint (early Oct):** Is `AwsBackend.submit_job` working? If not, execute the 4-weeks-behind cut.
- **W24 checkpoint (late Oct):** Is SWE-bench running on Fargate? Last chance to execute the "drop AWS from v0.5" cut while still maintaining a credible release.
- **W28 checkpoint (late Nov):** Is v0.4 feature-freeze achievable in 3 weeks? If not, lock scope now.

Each checkpoint is one hour of reflection, not a meeting. Write a one-paragraph note in `ROADMAP.md`'s changelog ("W12 checkpoint: replay working, v0.1 tagged on time, no cut needed"). That note is itself a portfolio artifact — it shows the discipline of a project that was planned, not just executed.

---

## 8. What this roadmap does not promise

This roadmap promises that *if the assumptions hold*, v0.5 ships in the week of January 4, 2027. The assumptions are: ~12 productive hours per week sustained, no major life disruption, AWS account access stays valid, Anthropic and Bedrock APIs remain available, SWE-bench Verified upstream remains usable.

It does not promise that the SWE-bench pass rate will be impressive. The portfolio value is in *the platform*, not in any one agent's score on any one benchmark. A v0.5 release that ships on time with a 12% pass rate is more credible than a delayed release with a 40% pass rate, because the platform is what's being demonstrated — the agent is just the most convenient thing to point it at.

It does not promise that the architecture won't need revisions in v0.6+. The schema versioning and the protocol-based extension model are designed for evolution. The roadmap is for *getting to a credible v0.5*, not for getting to the platonic final form.

If any of these assumptions changes, this document is updated and re-baselined. The roadmap is a tool, not a contract.
