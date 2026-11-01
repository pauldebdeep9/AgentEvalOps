# AgentEvalOps

**v0.1.0** · Local-first agent evaluation platform · Apache-2.0

Run evaluation loops against toy benchmarks, write structured result bundles, and replay bundles for structural verification — all offline, no model API required.

---

## Current capabilities

AgentEvalOps v0.1 provides a local-first evaluation skeleton for tool-using agents:

- Deterministic mock agent runner — no model API required
- Toy benchmark scenarios (smoke, failure, policy violation, trace limit, mixed)
- Deterministic evaluator, scorer, and policy checker
- Replayable result bundles with `manifest.json` and SHA-256 checksums
- `validate-bundle` and `replay` CLI commands
- A system declaration that scopes a run into a pre-registered `test_plan.json`, hashed before the first task runs; repetitions with pass^k; `agentevalops gate --bundle`, which fails closed (ROADMAP R27; ADR-0005 D6)
- CI-backed quality gates (ruff, mypy, pytest, multi-Python matrix)

## Not implemented in v0.1

- AWS / Bedrock / cloud backends
- LangGraph, OpenAI Agents SDK, or any real agent runner
- LLM-as-judge evaluators
- SWE-bench or any external benchmark
- FastAPI dashboard or web UI
- Runtime tool execution or sandboxing

See [ROADMAP.md](ROADMAP.md) for planned delivery order, and [ADR-0005](docs/adr/0005-agentic-assurance-rebaseline.md) and [docs/imda-alignment.md](docs/imda-alignment.md) for where v0.5 is headed: testing tool-using agents against Singapore's agentic-AI guidance, with evidence for an AI Verify Testing Framework report.

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

Five toy configs ship in `configs/`, each demonstrating a distinct outcome
from the local pipeline, plus one that runs the agentic-baseline benchmark
on the seed domain pack (last below).  A toy config's scenario is selected
with the `benchmark_scenario` key in the YAML config.

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

### The agentic baseline on the seed domain (`agentic_baseline_mock.yaml`)

```bash
agentevalops run --config configs/agentic_baseline_mock.yaml --output runs/agentic-baseline
agentevalops validate-bundle --bundle runs/agentic-baseline
agentevalops gate --bundle runs/agentic-baseline
```

The `agentic-baseline` benchmark runs the `seed` domain pack -- synthetic
public-sector case handling with six tools, eight seeded cases and
twenty-six scenarios across task execution, tool calling, policy
compliance and robustness (ROADMAP R24–R26; ADR-0006) -- under four evaluators
(`evaluators: [deterministic, tool_use, state_based, policy]`), each
scenario declaring which apply. The `policy` evaluator judges the pack's
rules per task from the harness records: the approval gate on every
irreversible tool, the order of work, no retry after a denial, least
privilege, the restricted-data boundary on exports, notice transparency
and personal data in notices; `policy.allowed_tool_ids` is the run-level
least-privilege rule. `mock_trajectory: known_good` plays every
scenario's known-good trajectory back through the mock runner, so every
task passes and the summary shows the verdicts by category; change it to
`known_bad` and every task fails under each evaluator that applies. The
bundle carries the harness's own `tool_calls.jsonl` and
`end_states.jsonl` and each scenario's preflight result. No model is
called.

The run config's `system:` key names a system declaration
(`configs/seed_system.yaml`; ADR-0005 D6; ROADMAP R27): the tools the
system exposes with their permission class, data class, reversibility and
external exposure; the level of human involvement; the intended use,
out-of-scope uses and the owner's business metrics, which are recorded and
repeated in the report and never evaluated; repetitions and seed; and
optional thresholds, which may tighten the pack's and never loosen them.
From it the run selects the scenarios (every exclusion with its reason),
fixes each threshold with its source, and writes `test_plan.json` before
the first task runs, hashing it into `metadata.json`; the agentic-baseline
benchmark refuses to start without one, and `validate-bundle` fails a
bundle whose plan was edited afterwards. Each scenario runs `repetitions`
times and passes only if every repetition passed (pass^k; a scenario with
a repetition that was not evaluated is not evaluated, with its cause).
`agentevalops gate --bundle` then holds the bundle to its plan and fails
closed: exit 0 when every hard gate is met over scenarios that were all
evaluated, 1 when any hard gate is below its threshold or has a
not-evaluated scenario, 2 when it cannot judge (no registered plan, an
invalid or tampered bundle). A comparative threshold is reported and never
gates.

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

## Development commands

A `Makefile` provides common tasks:

```bash
make install    # pip install -e ".[dev]"
make lint       # ruff check src/ tests/
make typecheck  # mypy src
make test       # pytest
make test-cov   # pytest --cov=agentevalops --cov-report=term-missing
make check      # lint + typecheck + test
make smoke      # run + validate-bundle + replay + cleanup
make build      # python -m build
make clean      # remove caches, dist/, build/
```

Or run the tools directly:

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
backend_id: "local"       # only "local"; "aws" is rejected (AWS left v0.5, ADR-0005 D3)
max_concurrent_tasks: 1
benchmark_id: "toy"       # "toy", or "agentic-baseline" with benchmark_options.pack
system: seed_system.yaml  # agentic-baseline only, and required for it: the system
                          #   declaration, relative to this file (ADR-0005 D6; R27)
repetitions: 1            # toy runs only; a declaration sets both for its run
seed: 0

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

## Contributing and changelog

See [CONTRIBUTING.md](CONTRIBUTING.md) for local setup, coding style, and scope
discipline. See [CHANGELOG.md](CHANGELOG.md) for what has changed in each WBS.

---

## License

Apache License 2.0. See [LICENSE](LICENSE).
