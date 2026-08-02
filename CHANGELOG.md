# Changelog

All notable changes to AgentEvalOps are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

### Added

- **OpenAI-backed `OpenAIRunner`** (`runner_id: "openai"`), a second, independent
  `AgentRunner` alongside `LangGraphRunner` — not a fix or replacement for it,
  and does not close W8 (see `docs/adr/0001-openai-runner-chat-completions.md`
  for the API-choice, model-config, and no-shared-abstraction decisions). Chat
  Completions API, same `calculator`/`finish` tool set re-expressed in
  OpenAI's function-calling shape, own price table (`gpt-4.1-mini-2025-04-14`),
  own `llm` extra pin, own mypy override, own import-linter contract (6th
  contract: "openai confined to agents/"). Config: `configs/toy_openai.yaml`.
  Offline unit tests only (`tests/test_openai_runner.py`), no network.
  - **Live verification attempted 2026-08-03, blocked before producing a
    result — an OpenAI account funding issue, not a code defect.** Every
    model call against `configs/toy_openai.yaml` failed with `429
    insufficient_quota`. 2 tasks attempted, 0 passed, `total_cost_usd = $0.00`
    actually incurred (OpenAI does not bill rejected 429s) — but this is a
    trivial zero-vs-zero, not a real cost-accounting check; that verification
    is still unperformed. `validate-bundle` and `replay` both correctly
    rejected the resulting incomplete bundle (missing required files).
    Recorded in full in ROADMAP.md §0; the bundle is kept at
    `runs/live-verify-openai-2026-08-03/`, not deleted.
  - **Parity bug found and fixed in the same session:** `config/loader.py`'s
    `_SUPPORTED_RUNNERS` allowlist had not been updated when `OpenAIRunner`
    was registered in `registry.py`, making `runner_id: "openai"` rejected
    from any YAML config even though the runner was otherwise fully wired
    up. Fixed; guarded by a new test
    (`test_supported_runners_matches_registry`) that asserts the allowlist
    equals the registry's registered runner names, confirmed to fail against
    the pre-fix state before being trusted.
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
- **`tests/test_enum_construction_coverage.py`** — a mechanical check that,
  for every member of `TraceEventKind`, `Verdict`, and `TerminationReason`
  (`core/schemas.py`), asserts the member is constructed somewhere in
  non-test `src/`, or is named in an allowlist with a comment pointing at
  the roadmap item that will wire it. Written because the same "vocabulary
  exists, never constructed" pattern had already surfaced twice
  independently (`TerminationReason.INFRA_FAILURE`, `Verdict.WARN` before
  2026-08-03) — this test, on first run, found it a **third and fourth**
  time: `TraceEventKind.AGENT_OBSERVATION` and `.EVALUATOR_SCORE`/
  `.POLICY_VERDICT` are also never constructed anywhere. All four are now
  allowlisted with a roadmap reference each (`INFRA_FAILURE` and
  `AGENT_OBSERVATION` had existing anchors; `EVALUATOR_SCORE`/
  `POLICY_VERDICT` did not — a new `ROADMAP.md` §0 entry records the
  finding since none existed to point at). Proved to actually catch drift,
  not just pass by construction: temporarily removed `INFRA_FAILURE` from
  the allowlist, confirmed the test fails naming exactly that member, then
  restored it.
  - **Does not cover `ResultBundleMetadata.sealed`** (hardcoded `True` at
    its one write site, read back nowhere) — a different shape, a
    constant-valued field rather than an unconstructed enum member. A
    narrow, single-field "is `.sealed` ever read outside its write site"
    check would be cheap to add, but would fail immediately (nothing reads
    it today, and fixing that is blocked on the same orchestrator
    prerequisite as `INFRA_FAILURE`) and doesn't generalize to a reusable
    "any write-only field" checker without real judgment about which
    fields are *supposed* to vary — not built; see the report accompanying
    this change for the full reasoning.

### Fixed

- **No model-backed bundle recorded which model produced it — `OPENAI_MODEL`
  environment-variable fallback deleted, not written back.** Investigated
  the full path a model id takes to reach a runner (`docs/adr/0001-...`
  §3, revised): `OpenAIRunner` resolved `model` from three sources (explicit
  config field → `OPENAI_MODEL` env var → `DEFAULT_MODEL` constant) and
  `LangGraphRunner` had the identical gap in miniature (config field →
  `DEFAULT_MODEL`, no env var, but the same silent-default behavior). Per
  `docs/adr/0002-outcome-taxonomy.md`'s finding that `RunConfig.agent_id`
  in `config.json` is the static YAML string, never the runner's
  model-qualified value, either fallback path let a config-driven run
  proceed with a model the bundle would never record and that couldn't be
  reconstructed from a second, potentially-divergent source. Fixed by
  *removing* the ambiguous path rather than adding a write-back (a
  write-back would leave two sources that can disagree, not one that
  can't): `OPENAI_MODEL` is deleted entirely from `OpenAIRunner`, and
  `config/loader.py` now raises `ConfigurationError` at load time if a
  model-backed `runner_id` (`langgraph` or `openai`) omits `model` —
  failing loudly instead of silently defaulting. `configs/toy_openai.yaml`
  and `configs/toy_langgraph.yaml` updated to document `model` as required,
  not merely recommended.
  - **Does not fully close the provenance gap, and isn't meant to.**
    `config.json` still doesn't record the model, because `RunConfig` has
    no `model` field and none was added — that's a config-schema change,
    explicitly deferred to the next, schema-focused session per
    `docs/adr/0002`'s open questions (fix `agent_id` to reflect the
    resolved model, vs. add a dedicated `model_id` field — undecided).
    What changed here is that there is now exactly one legal source for
    the model on any config-driven run, not that the bundle records it
    yet.

- **`BasicPolicyChecker` was a fail-open release gate.** `check()` only ever
  inspected cost, the tool deny-list, and trace length; it never asked
  whether anything was actually evaluated. A run with zero evaluated results
  (an empty benchmark) or where every task failed at the infrastructure
  layer before finishing (an API outage — see the 2026-08-03 infra-failure
  gap below) tripped none of the three checks and silently returned
  `Verdict.PASS`, `"all checks passed"` — the same class of bug as a
  deny-list silent failure: the check ran, reported success, and inspected
  nothing. Reproduced first (both a directly-constructed zero-results input
  and the real `tests/fixtures/bundles/partial-infra-failure-2026-08-03/`
  fixture's actual trace both returned `PASS` against the pre-fix code).
  Fixed by downgrading a would-be `PASS` to `Verdict.WARN` — the pre-existing,
  previously-unused third member of the `Verdict` enum
  (`core/schemas.py`'s own docstring: "binary-plus-warn") — whenever
  `output.success` is `False` **and** no `AGENT_TERMINAL` event appears
  anywhere in the trace (every runner yields one as its last event on every
  completion path; the only way to have zero across the whole trace is if no
  task's `run()` generator ever finished). Deliberately conservative: a
  genuine finding from the three existing checks is never downgraded, so
  real cost/deny-list/trace-count violations still surface even on a run
  that didn't finish cleanly. No `Verdict` member added, no bundle-contract
  or schema change — `WARN` already existed. Scoped narrowly to this one
  checker; the orchestrator's exception handling, `RunSummary`, and the
  report are untouched (that's the taxonomy-design work, still open — see
  `ROADMAP.md` §0).
  - **Known gap, not covered by this fix, reported not fixed:** a run where
    *some* tasks were genuinely evaluated (any completed, so the trace has
    at least one `AGENT_TERMINAL`) and others infra-failed still returns
    `PASS` with no signal that part of the run was never evaluated — the
    presence of even one real terminal event masks the absence for the rest,
    since `check()` only sees the whole run's combined output and trace, not
    a per-task breakdown. Not currently reachable through the real
    orchestrator (today, any infra failure aborts the whole run before
    `check()` is ever called at all, for every task — see the gap below), so
    this is a forward-looking limitation, not a live bug.

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
