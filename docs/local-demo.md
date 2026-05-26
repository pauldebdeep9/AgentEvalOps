# Local Demo Guide

This document explains how to run the five local demo scenarios and what to
expect from each one.

---

## What the local demo does

The local pipeline runs toy evaluation tasks through a mock agent, scores
the results, and optionally writes a result bundle to disk.  No model,
API key, or network connection is needed.

```
config.yaml
    → config/loader.py       (validation + scenario selection)
    → LocalOrchestrator      (task loop)
    → ToyBenchmarkAdapter    (scenario-specific task list)
    → MockAgentRunner        (deterministic events, no model call)
    → DeterministicEvaluator (exact/substring match + trace checks)
    → SimpleScorer           (pass rate, aggregate score)
    → BasicPolicyChecker     (post-run cost/trace/tool checks)
    → BundleWriter           (optional: writes 8 files to disk, including manifest.json)
    → report.md              (generated as part of the bundle)
```

---

## Running the five demo scenarios

### 1. Smoke (happy path)

```bash
agentevalops run --config configs/toy_smoke.yaml --output runs/toy-smoke
agentevalops replay --bundle runs/toy-smoke
```

**Expected outcome:**
- Both evaluations: PASS (score 1.0)
- Policy: PASS
- Replay: PASS

---

### 2. Evaluation failure

```bash
agentevalops run --config configs/toy_failure.yaml --output runs/toy-failure
agentevalops replay --bundle runs/toy-failure
```

`benchmark_scenario: failure` loads tasks whose `mock_answer` deliberately
does not satisfy the evaluation criterion.  The agent runs normally
(`success=True`, `termination_reason=COMPLETED`) but
`DeterministicEvaluator` rejects both answers.  This demonstrates
evaluation failure without an agent crash.

**Expected outcome:**
- Both evaluations: FAIL (score 0.0)
- Policy: PASS (cost $0.00 < $1.00 limit)
- CLI exit code: 0 (the run completed; failure is an evaluation result)
- Replay: PASS

---

### 3. Policy violation — cost ceiling

```bash
agentevalops run --config configs/toy_policy_violation.yaml --output runs/policy-violation
agentevalops replay --bundle runs/policy-violation
```

`mock_cost_per_task_usd: 0.5` × 2 tasks = $1.00 total.
`policy.max_cost_usd: 0.25` → $1.00 > $0.25 → policy FAIL.

**Expected outcome:**
- Both evaluations: PASS
- Policy: FAIL (cost ceiling exceeded)
- CLI exit code: 0
- Replay: PASS

---

### 4. Policy violation — trace event limit

```bash
agentevalops run --config configs/toy_trace_limit.yaml --output runs/trace-limit
agentevalops replay --bundle runs/trace-limit
```

Default mock runner emits 3 events/task × 2 tasks = 6 events.
`policy.max_trace_events: 5` → 6 > 5 → policy FAIL.

**Expected outcome:**
- Both evaluations: PASS
- Policy: FAIL (trace limit exceeded)
- CLI exit code: 0
- Replay: PASS

---

### 5. Mixed pass/fail

```bash
agentevalops run --config configs/toy_mixed.yaml --output runs/toy-mixed
agentevalops replay --bundle runs/toy-mixed
```

`benchmark_scenario: mixed` loads three tasks with different acceptance
criteria:

| Task | Strategy | Result |
|------|----------|--------|
| `toy-001` | exact-match | PASS |
| `toy-fail-001` | exact-match + wrong `mock_answer` | FAIL |
| `toy-substr-001` | substring-match | PASS |

**Expected outcome:**
- 2 / 3 evaluations: PASS (pass rate ≈ 67%)
- Policy: PASS ($0.00 cost)
- CLI exit code: 0
- Replay: PASS

---

## What files appear in a result bundle

```
runs/toy-smoke/
├── metadata.json      schema version, run ID, created_at, task count
├── config.json        RunConfig (run_id, agent_id, backend_id, limits)
├── traces.jsonl       all trace events (one JSON object per line)
├── evaluations.json   per-task EvaluationResult list
├── policy.json        PolicySpec + PolicyVerdict
├── summary.json       RunSummary (pass rate, score, totals)
├── report.md          human-readable Markdown report
└── manifest.json      bundle format version, file sizes, SHA-256 checksums
```

`manifest.json` is written **last** so its checksums cover all content files.
It contains:

- `bundle_format_version` — format version of the bundle layout
- `generated_at` — ISO-8601 timestamp
- `required_files` — list of expected content files
- `files` — per-file `size_bytes` and `sha256` (SHA-256 hex digest)
- `run` — high-level run metadata (run_id, config_name, benchmark, scenario)
- `writer` — package name and version that produced the bundle

SHA-256 checksums are for tamper detection and accidental corruption
detection.  This is **not** cryptographic signing; there is no key management,
remote attestation, or governance service.

### Validating a bundle

```bash
agentevalops validate-bundle --bundle runs/toy-smoke
```

`validate-bundle` checks:
- all required files are present
- `manifest.json` parses and has a supported `bundle_format_version`
- every manifest-listed file exists, matches expected size, and SHA-256
- all JSON files parse cleanly
- `traces.jsonl` parses line-by-line
- `trace_event_count` in `summary.json` agrees with the number of trace lines

Exit code 0 = all checks passed. Exit code 1 = one or more failures.

---

## How replay works

```bash
agentevalops replay --bundle runs/toy-smoke
```

Replay reads the bundle, runs bundle validation (manifest + checksums), and
then checks internal consistency:

- `traces.jsonl` parses without errors
- `evaluations.json` count matches `summary.json` → `total_tasks`
- `policy.json` verdict is a known string (`pass` / `fail` / `warn`)
- `metadata.json` `sealed` field is `true`
- `summary.json` task result count matches metadata
- manifest checksums all pass

Replay exits 0 if all checks pass, 1 otherwise.  It shows:
- bundle format version (from manifest)
- manifest validation status
- trace event count, evaluation count, policy verdict
- replay status

### What replay does NOT do

- It does not re-run any agent, model, or tool.
- It does not re-evaluate task answers.
- It does not compare two runs.
- It does not check whether evaluation scores are "correct".

Replay is a consistency and integrity verifier, not a correctness verifier.

---

## Cleaning up

Generated bundles are git-ignored.  Remove them when you are done:

```bash
rm -rf runs/
```
