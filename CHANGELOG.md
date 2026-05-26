# Changelog

All notable changes to AgentEvalOps are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [0.1.0] — Unreleased

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

### Not included in v0.1

- AWS / Bedrock backend.
- SWE-bench adapter.
- LLM-as-judge evaluator.
- Model-provider runners (LangGraph, Ollama, OpenAI Agents SDK).
- FastAPI dashboard or web UI.
- Remote artifact storage.
