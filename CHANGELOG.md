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
- **W9 — OpenTelemetry warmup scaffold.** `observability/otel_setup.py`
  establishes the *seam* for OTel instrumentation, no more: span-name and
  attribute-key constants mirroring the ARCHITECTURE §8 hierarchy, a
  `NoOpTracer`/`NoOpSpan` pair so future instrumented call sites are safe and
  free whether or not the extra is installed, and a `get_tracer()` that
  currently always returns the no-op. `configure_tracer()` is an explicit
  `NotImplementedError` stub. **This scaffold emits no spans.** There is no
  `TracerProvider`, no exporter, no `spans.jsonl` output, and zero
  instrumentation call sites anywhere in the orchestrator, runners, or
  evaluators — real span emission is a dedicated future W9 session's work.
  Adds the optional `otel` extra (`opentelemetry-api`/`-sdk`, imported lazily,
  not installed in CI) and its own `mypy.overrides` block.
  - *Caveat 1:* `tests/test_observability.py` gives this module 100% coverage,
    but that is coverage of a no-op — it modestly inflates the repo's headline
    coverage percentage without exercising any real span-emission behavior.
  - *Caveat 2:* this ships alongside a fix for a related but separate defect —
    W8 shipped with `mypy --strict` red in CI for two commits because the dev
    environment had the `llm` extra installed and CI did not, and nothing
    checked that they agreed. That is a process gap (no dev/CI parity check),
    not a one-off missing config line; it can recur for any future optional
    extra, including this one's `opentelemetry.*` override, if the same
    unchecked assumption holds.
- **W13 — import-linter layer contracts.** `[tool.importlinter]` in
  `pyproject.toml`: 5 contracts derived from ARCHITECTURE.md §4 (not
  reverse-engineered from today's imports) — a layers contract (`cli` >
  adapters/orchestration/bundles/replay/registry/config > `core`), an
  independence contract (the 8 existing adapter families must not import each
  other), and 3 forbidden-import contracts confining `anthropic`/`langgraph`/
  `langchain_anthropic` to `agents/`, `opentelemetry` to `observability/`, and
  `boto3`/`botocore` to `cloud/` (the last is vacuous today — `cloud/` doesn't
  exist until W17). `agentevalops.cloud` is excluded from the independence
  contract for the same reason: import-linter errors, not just fails, if a
  listed module doesn't exist. Wired into `make lint-imports` and a new
  `Check import layer contracts` step in the `ci` job (not `ci-min-deps`).
  Every contract was proven to actually fail against a deliberately injected
  violation, then reverted, before being trusted — a contract that's never
  been shown to fail isn't evidence of anything. **5/5 pass.**
  - *Mis-specified contract, corrected same day — not a code violation.*
    The anthropic-confinement contract initially failed against existing
    code: `cli`/`registry` transitively reach `anthropic` via
    `agents/langgraph_runner.py` (registry imports the runner to register
    it; the runner imports `anthropic` lazily). Diagnosis: ARCHITECTURE.md
    §4's confinement rule constrains *where the import statement lives*
    (`agents/`), not how far a name can be transitively reached from — a
    registry that resolves adapters by name must reference them, which is
    correct composition, not a leak. Genuine layering leaks are already
    caught by the separate layers contract. Fix: `allow_indirect_imports =
    true` on all three forbidden-import contracts (anthropic, opentelemetry,
    boto3) — not just the one that failed today, since the other two pass
    only because `otel_setup.py` has zero call sites and `cloud/` doesn't
    exist, and would break identically the moment W9 or W17 wires anything
    through the registry. Not an `ignore_imports` exemption — the semantics
    of what each contract detects changed, deliberately, for all call sites,
    not one path around a single case. Re-proved after the fix: each
    forbidden contract still fails against a fresh direct violation, and the
    anthropic contract is confirmed KEPT with the registry chain no longer
    tripping it.
  - *Tooling gap found along the way:* `python -m importlinter.cli
    lint-imports` — the invocation import-linter's own docs suggest — silently
    no-ops (exit 0, no output) regardless of contract status: `importlinter/
    cli.py` has no `if __name__ == "__main__":` guard. Anyone reaching for
    that module form in CI would get a permanently green gate that checks
    nothing. Use the `lint-imports` console script instead (what
    `Makefile`/CI actually call).

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
