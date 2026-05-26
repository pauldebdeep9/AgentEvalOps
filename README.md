# AgentEvalOps

Local-first agent evaluation platform. Run evaluation loops against toy benchmarks, write structured result bundles, and replay bundles for structural verification — all offline, no model API required.

---

## Install for development

```bash
python -m pip install -e ".[dev]"
```

Requires Python 3.10+.

---

## Quickstart

```bash
# verify the install
agentevalops --help
agentevalops version
agentevalops doctor

# run the toy smoke evaluation
agentevalops run --config configs/toy_smoke.yaml

# write a result bundle to disk
agentevalops run --config configs/toy_smoke.yaml --output runs/toy-smoke

# replay and verify the bundle
agentevalops replay --bundle runs/toy-smoke
```

---

## Run the toy smoke evaluation

The smoke config runs two trivial tasks (`toy-001`, `toy-002`) through the
local pipeline and prints a summary:

```
Run ID  : toy-smoke-001
Tasks   : 2 / 2 passed
Failed  : 0
Rate    : 100%
Cost    : $0.0000
Tokens  : 20
Policy  : PASS  (basic-policy-v1)
```

---

## Write a result bundle

Pass `--output <dir>` to write a result bundle after the run:

```bash
agentevalops run --config configs/toy_smoke.yaml --output runs/toy-smoke
```

The bundle directory contains:

| File | Contents |
|------|----------|
| `metadata.json` | Schema version, run ID, timestamp, task count |
| `config.json` | `RunConfig` used for the run |
| `traces.jsonl` | All trace events, one JSON object per line |
| `evaluations.json` | Per-task `EvaluationResult` objects |
| `policy.json` | `PolicySpec` + `PolicyVerdict` |
| `summary.json` | Aggregated `RunSummary` |
| `report.md` | Human-readable Markdown report |
| `manifest.json` | Bundle format version, file sizes, SHA-256 checksums |

`manifest.json` is written **last** so its checksums cover all content files.

---

## Result bundle anatomy

Every bundle written by `BundleWriter` produces eight files.

### Content files (checksummed in manifest)

| File | Description |
|------|-------------|
| `metadata.json` | Run metadata: schema version, run ID, platform version, task count, timestamp |
| `config.json` | Resolved `RunConfig` + `PolicySpec` that produced the run |
| `traces.jsonl` | One JSON object per `TraceEvent`, newline-delimited |
| `evaluations.json` | List of per-task `EvaluationResult` objects |
| `policy.json` | Post-run `PolicyVerdict` (or JSON `null` if no policy was specified) |
| `summary.json` | Aggregated `RunSummary`: task counts, cost, tokens, per-task results |
| `report.md` | Human-readable Markdown report (same data as summary) |

### Manifest file

| File | Description |
|------|-------------|
| `manifest.json` | Bundle format version, `generated_at` timestamp, `required_files` list, per-file `size_bytes` and `sha256`, high-level `run` fields, writer name/version |

`manifest.json` is not checksummed inside itself (documented convention for
this bundle format). SHA-256 checksums are used for tamper detection and
accidental corruption detection — this is **not** cryptographic signing and
provides no remote attestation or governance guarantee.

### Validate and replay

```bash
agentevalops run --config configs/toy_smoke.yaml --output runs/toy-smoke
agentevalops validate-bundle --bundle runs/toy-smoke
agentevalops replay --bundle runs/toy-smoke
```

`validate-bundle` checks: required file presence, manifest parse,
`bundle_format_version`, file sizes, SHA-256 checksums, JSON parseability of
all JSON files, line-by-line parse of `traces.jsonl`, and
`trace_event_count` cross-check against `summary.json`.

`replay` checks internal consistency (run_id cross-match, non-empty traces
and evaluations, task count cross-check) and surfaces manifest validation
status in its output.

---

## Replay a result bundle

```bash
agentevalops replay --bundle runs/toy-smoke
```

Replay reads the bundle and checks its internal consistency (event counts,
evaluation counts, policy verdict format). It does **not** re-execute any
agent or model. Exit code is 0 if all checks pass, 1 otherwise.

---

## Example scenarios

Five example configs ship in `configs/`. Each demonstrates a distinct
outcome from the local pipeline.  The active scenario is selected with the
`benchmark_scenario` key in the YAML config.

### Happy path (`toy_smoke.yaml`)

```bash
agentevalops run --config configs/toy_smoke.yaml --output runs/toy-smoke
agentevalops replay --bundle runs/toy-smoke
```

Both tasks pass evaluation. Policy passes. Replay passes.

### Evaluation failure (`toy_failure.yaml`)

```bash
agentevalops run --config configs/toy_failure.yaml --output runs/toy-failure
agentevalops replay --bundle runs/toy-failure
```

The `failure` scenario loads tasks whose `mock_answer` differs from
`expected_output`, so the agent returns plausible-looking answers that the
evaluator rejects.  Both evaluations fail (score 0.0). The agent itself
completes without error (`success=True`). Policy still passes (cost $0.00
is within the $1.00 limit). CLI exits 0. Bundle and replay still work.

### Policy violation — cost ceiling (`toy_policy_violation.yaml`)

```bash
agentevalops run --config configs/toy_policy_violation.yaml --output runs/policy-violation
agentevalops replay --bundle runs/policy-violation
```

`mock_cost_per_task_usd: 0.5` makes each task report $0.50. Two tasks
produce $1.00 total cost, which exceeds the `policy.max_cost_usd: 0.25`
ceiling. Evaluations pass; policy fails. CLI exits 0. Bundle and replay
still work.

### Policy violation — trace event limit (`toy_trace_limit.yaml`)

```bash
agentevalops run --config configs/toy_trace_limit.yaml --output runs/trace-limit
agentevalops replay --bundle runs/trace-limit
```

The default mock runner emits 3 events per task × 2 tasks = 6 total events.
`policy.max_trace_events: 5` means 6 > 5 → policy fails. Evaluations pass.
CLI exits 0. Bundle and replay still work.

### Mixed pass/fail (`toy_mixed.yaml`)

```bash
agentevalops run --config configs/toy_mixed.yaml --output runs/toy-mixed
agentevalops replay --bundle runs/toy-mixed
```

The `mixed` scenario has 3 tasks using different evaluation strategies:

| Task | Strategy | Expected outcome |
|------|----------|-----------------|
| `toy-001` | `match_mode: exact` | Pass — runner returns exact answer |
| `toy-fail-001` | `match_mode: exact` + wrong `mock_answer` | Fail |
| `toy-substr-001` | `expected_substring` | Pass — answer contains substring |

Result: 2 / 3 tasks pass (67%). Policy passes ($0.00 cost).

## Toy benchmark scenario reference

| Scenario | Tasks | Evaluation result | Policy result |
|----------|-------|-------------------|---------------|
| `smoke` | 2 | All pass | PASS |
| `failure` | 2 | All fail (wrong answers) | PASS |
| `policy_violation` | 2 | All pass | FAIL (cost exceeded) |
| `trace_limit` | 2 | All pass | FAIL (trace count exceeded) |
| `mixed` | 3 | 2 pass, 1 fail | PASS |

Set the scenario in your config YAML:

```yaml
benchmark_scenario: smoke   # smoke | failure | policy_violation | trace_limit | mixed
```

---

## Lint, type-check, and test

```bash
ruff check src/ tests/
mypy src
pytest
```

---

## Config file reference

All fields except `run_id` and `benchmark_id` have defaults. Supported
top-level fields:

```yaml
run_id: "my-run"          # required; used as bundle folder key
agent_id: "mock-agent-v1" # currently only mock-agent-v1 is implemented
backend_id: "local"       # "local" or "aws" (aws = future, not implemented)
max_concurrent_tasks: 1
benchmark_id: "toy"       # only "toy" is implemented

resource_limits:          # enforced during the run
  max_tokens: 100000
  max_wall_seconds: 3600.0
  max_cost_usd: 10.0

policy:                   # checked post-run against the completed trace
  policy_id: "default"
  max_cost_usd: 1.0           # optional; fail if total cost exceeds this
  max_trace_events: 100       # optional; fail if trace has more events
  deny_tool_ids: []           # optional; fail if any listed tool was called

# demo/test knobs (mock runner only)
mock_fail: false              # set true to simulate agent failure
mock_cost_per_task_usd: 0.0   # per-task cost reported by mock runner
```

---

## Generated artifacts

Run outputs are written under `runs/` by default. This directory is
git-ignored. Do not commit generated run artifacts.

---

## What is intentionally not implemented yet

- AWS / Bedrock / cloud backends
- LangGraph, OpenAI Agents SDK, or any real agent runner
- LLM-judge evaluators
- SWE-bench or any external benchmark
- FastAPI dashboard or web UI
- Runtime tool execution or sandboxing
- Multi-run comparison or regression tracking
- Plugin or extension framework

See [ROADMAP.md](ROADMAP.md) for the planned delivery order.
