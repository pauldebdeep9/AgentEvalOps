# Changelog

All notable changes to AgentEvalOps are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

### Added

- **W8 — real model-backed `LangGraphRunner`** (post-v0.1; enables v0.2). A
  registered `AgentRunner` (`runner_id: langgraph`) that drives a minimal
  ReAct-style agent (tools: `calculator`, `finish`) against the toy Q&A tasks
  using the Anthropic API directly, with per-response token/cost accounting
  (single-source price table sourced from the Anthropic model docs) and
  `ResourceLimits` enforcement (tokens/cost/wall-time → matching
  `TerminationReason`). The `anthropic` SDK is an optional `llm` extra imported
  lazily, so core stays installable without it and CI is unaffected; unit tests
  inject a fake client (no network), with one opt-in `@pytest.mark.live` test.
  Config gains `runner_id` (mock|langgraph) and `model`; the CLI selects the
  runner via the registry and surfaces a missing `ANTHROPIC_API_KEY` as a clean
  exit. Ships with `configs/toy_langgraph.yaml`.
  - *Scope/fallback:* file/sandbox tools deferred to a code-editing benchmark;
    runner is implemented on the `anthropic` SDK directly (pre-authorized
    fallback) under the `langgraph` name, with the `langgraph`/
    `langchain-anthropic` pins declared for a future port.

---

## [0.1.0] — 2026-07-05

### Added

- **WBS 0** — Package scaffold: hatchling build, `src/` layout, Typer CLI stub,
  ruff + mypy + pytest quality gates, GitHub Actions CI.
- **WBS 1** — Core types, schemas (`RunConfig`, `TraceEvent`, `EvaluationResult`,
  `PolicyVerdict`, `RunSummary`), protocols, and error hierarchy.
- **WBS 2** — Local orchestration skeleton: `InMemoryTraceStore`,
  `MockAgentRunner`, `ToyBenchmarkAdapter`, `DeterministicEvaluator`,
  `BasicPolicyChecker`; `agentevalops run` CLI command.
- **WBS 3** — Result bundle writer: `BundleWriter` produces `metadata.json`,
  `config.json`, `traces.jsonl`, `evaluations.json`, `policy.json`,
  `summary.json`, `report.md`; `--output` flag on `run`.
- **WBS 4** — Bundle reader, local replay verifier, `agentevalops replay` CLI;
  read-only structural validation.
- **WBS 5** — Stronger `DeterministicEvaluator`, `SimpleScorer`,
  `BasicPolicyChecker`; improved `RunSummary` and `report.md`; pass-rate in CLI
  summary; bundle and replay compatibility.
- **WBS 6** — Centralised config loader with validation; five toy configs
  (`smoke`, `failure`, `policy_violation`, `trace_limit`, `mixed`); README and
  local-demo hardening; generated `runs/` git-ignored.
- **WBS 7** — Mature toy benchmark scenarios with per-scenario task definitions;
  improved `ToyBenchmarkAdapter`; richer deterministic evaluator behaviour.
- **WBS 8** — Bundle manifest (`manifest.json`): deterministic SHA-256
  checksums, file sizes, format version, run metadata, writer info;
  `BundleValidator` with 12 integrity checks; `agentevalops validate-bundle`
  CLI (exits 0/1); tamper detection; replay surfaces manifest status.
- **WBS 9** — CI and release hygiene: multi-Python CI matrix with mypy and CLI
  smoke; packaging build check workflow; `Makefile` developer commands;
  `.pre-commit-config.yaml`; `CONTRIBUTING.md`; `CHANGELOG.md`; release
  checklist; GitHub PR and issue templates; `SECURITY.md`; `CODE_OF_CONDUCT.md`;
  pyproject.toml classifiers/keywords/coverage config; pytest-cov support.
- **WBS 10** — v0.1 release candidate readiness: Apache-2.0 `LICENSE`; version
  bumped to `0.1.0`; pyproject.toml license metadata and classifier; README
  public-readiness pass; CHANGELOG, CONTRIBUTING, SECURITY, release-checklist
  finalized; `.gitignore` extended; `__init__.py` docstring corrected.
- **WBS 11** — Orchestrator concurrency + release hardening: bounded task
  concurrency via `asyncio.Semaphore(max_concurrent_tasks)` with
  deterministic result ordering regardless of concurrency; best-effort
  trace-store finalize on `KeyboardInterrupt`/task cancellation; disk-backed
  `LocalTraceStore` (flush+fsync per event) so `run --output` survives a
  hard process kill, not just an in-process interrupt; `agentevalops report
  --run-id` CLI command; interrupted-bundle recovery test coverage
  (`strict=False` reads an unsealed bundle).
- **WBS 12** — `v0.1.0` tag cut and pushed to `origin/main`. `ReportGenerator`
  protocol given its first real (non-mock) implementation:
  `RunSummary`/`TaskRunResult` moved from `orchestration/local.py` into
  `core/schemas.py` (pure data, no adapter deps — this is what let a core
  protocol reference them without a circular import); `ReportGenerator.render()`
  widened from `(ScoreResult, trace)` to `(RunConfig, RunSummary, trace,
  config_name)`, since a useful report needs cost/tokens/per-task
  results/policy verdict, none of which fit in a bare score aggregate;
  `MarkdownReportGenerator` added and wired into `BundleWriter._write_report`
  in place of the direct `render_report()` call. `ArtifactStore`/`CloudBackend`
  left intentionally mock-only (no caller exists for either yet). Audited
  ARCHITECTURE.md and ROADMAP.md against running code and fixed drift:
  corrected `src/` directory tree (WBS packages don't nest under `core/` the
  way early drafts described), replaced stale `pyright` references with the
  actual `mypy --strict` gate, and added explicit status callouts marking
  "replay" as bundle verification (not recorded-output re-execution) and
  OpenTelemetry/`CloudBackend`/`ArtifactStore` as not yet implemented.

### Not included in v0.1

- AWS / Bedrock backend.
- SWE-bench adapter.
- LLM-as-judge evaluator.
- Model-provider runners (LangGraph, Ollama, OpenAI Agents SDK).
- FastAPI dashboard or web UI.
- Remote artifact storage.
