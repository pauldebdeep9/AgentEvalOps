# Using AgentEvalOps

The detailed reference that used to follow the README's quickstart,
moved here verbatim on 2026-11-09 when the README was shortened; only
relative link targets changed, to resolve from `docs/`.

---

## Running the agentic baseline

Every task passes because `mock_trajectory: known_good` plays each
scenario's known-good trajectory; set it to `known_bad` and every task
fails under each evaluator that applies, and the gate exits 1 naming the
three hard gates. The declaration also sets `repetitions`; at k = 2 the
category lines read `pass^2`. `gate` exits 0 when every hard gate is met
over scenarios that were all evaluated, 1 when any hard gate is below its
threshold or has a not-evaluated scenario, and 2 when it cannot judge (a
run that declared no system, an invalid or tampered bundle). A comparative threshold is
reported and never gates. `report --run-id <id>` prints a bundle's
`report.md`; `replay` is a deprecated alias of `validate-bundle`.
`triage --bundle <dir>` walks a seeded sample of a bundle's task runs and
records a reviewer's verdict on each beside the bundle, never in it
([docs/triage.md](triage.md)).

The other toy configs show one outcome each: `toy_failure.yaml` (wrong
answers, evaluations fail), `toy_policy_violation.yaml` (cost ceiling,
policy fails), `toy_trace_limit.yaml` (trace-event ceiling, policy fails),
`toy_mixed.yaml` (two of three pass). The CLI exits 0 on a failed
evaluation or policy verdict; only `gate` turns a verdict into an exit
code. `toy_langgraph.yaml`, `toy_openai.yaml` and `toy_azure_openai.yaml`
call a real model, cost money, and need `ANTHROPIC_API_KEY` or
`OPENAI_API_KEY`.

---

## The bundle

`--output <dir>` writes a bundle at format 0.6: thirteen content files and
`manifest.json`. Content files are written first and `manifest.json` last,
so its checksums cover them all.

| File | Holds |
|------|-------|
| `metadata.json` | Format version, run id, timestamp, task count, status (`sealed`, `interrupted`, `partial`), platform version, the registered `test_plan_sha256`; `package_sha256`, the hash of the package's `.py` and `.yaml` files; `git_sha` and `git_dirty` of the package's own source tree (an editable install's checkout, else `unavailable`); `invocation_git_sha` and `invocation_git_dirty` of the directory the run started in |
| `config.json` | The resolved run config, the policy spec, benchmark id and options; `runner_id`, `mock_trajectory` (set when the mock runner played a script) and the evaluators configured |
| `traces.jsonl` | Every trace event, one JSON object per line, each stamped with its task and repetition; each task run's stream ends with one `evaluator.score` event per evaluation result |
| `tool_calls.jsonl` | Every tool call as the environment recorded it: arguments, result, approval, fault, injections |
| `end_states.jsonl` | Each task run's end state, snapshotted before the evaluators ran |
| `evaluations.json` | One entry per evaluator per task run, with the records it cites |
| `policy.json` | The run-level policy verdict |
| `summary.json` | Task results with their derived verdicts, counts, not-evaluated causes, preflight results, pass^k per scenario and per category |
| `report.md` | Task results, repeatability, outcomes by kind of harm, where runs first fail (a matrix of each failed run's last completed stage against the stage of its first failure), the pre-registered plan, policy checks, a trace summary |
| `replay_command.txt` | The invocation that produced the bundle and the code provenance, for a human to judge drift |
| `manifest.json` | Format version, the required-file list, each file's size and sha256, writer version |
| `test_plan.json` | Registered before the first task: the declaration, the selected and excluded scenarios with reasons, thresholds with sources, every seed, the pack's scenario and data hashes. A run with no declaration registers a plan that says so (`declared: false`), which the gate cannot judge |
| `controls.json` | Every catalogue control in one of five states (covered, partly covered, uncovered, not in plan, not in pack) with each citing scenario's pass^k and its records, never an aggregate; the sources and catalogue files it was resolved against; every pack scenario, selected or excluded |
| `evidence_report.md` | The testing framework's 112 checks by principle, with evidence pointers where a citing scenario ran and the organisation's Implemented and Elaboration columns left blank; a Scenarios table; the MGF-Agentic and Starter Kit controls; the pinned sources |
| `manifest.json.sig` | Optional: a detached `ssh-keygen` signature over `manifest.json` (see [Signing a bundle](#signing-a-bundle)) |

Bundles at formats 0.1 to 0.5 are still read and validated; a reader
treats what they lack as the documented default, and a 0.5 bundle's
`git_sha` keeps its old meaning, the invocation's checkout.

`validate-bundle` checks: the path is a directory; the required files for
the bundle's declared format are present; `manifest.json` parses and
declares a supported format; every file the manifest lists exists with the
recorded size and sha256, and every required file is listed; the JSON files
parse; `traces.jsonl`, `tool_calls.jsonl` and `end_states.jsonl` parse line
by line; the trace count matches `summary.json`; `test_plan.json` still
hashes to the value `metadata.json` registered before the first task; the
manifest's required-file list agrees with the format; and the files agree
with each other (run id in metadata and summary, non-empty traces and
evaluations, per-task event counts summing to the trace count, the task
count matching the task runs evaluated). At 0.6 it also checks that
`controls.json` is tied to the registered plan and selects exactly the
plan's scenarios, that every end-state pointer in it is a real line of
`end_states.jsonl`, and that there is one `evaluator.score` event per
entry in `evaluations.json`; that the three records of each evaluation
result -- its entry in `evaluations.json`, its place in `summary.json` and
its `evaluator.score` event -- agree; and that what a run carries fits its
records: a task run stopped by its scenario's failed preflight has that
failure recorded and no agent event, tool call or end state, a run not
evaluated for the preflight or the infrastructure is not evaluated by any
evaluator and its output says `infra_failure`, and an evaluated run has its
end state. Stopped or finished is read from the records, not the summary: a
run stopped before any evaluator judged it has no `agent.terminal` event,
and every other run's output is what that event gives. A malformed file is a finding, never a crash. A code-drift warning
is printed when the code differs from the code that wrote the bundle --
for a 0.6 bundle, the running package's hash against `package_sha256`; for
older ones, the current checkout against the recorded `git_sha` -- and a
`manifest.json.sig` is noted as present but not verified unless
`--allowed-signers` is given. Neither ever fails the bundle. Checksums
detect tampering and corruption; they are not a signature.

## Signing a bundle

`sign` signs a bundle's `manifest.json` with `ssh-keygen -Y sign` (OpenSSH
8.2 or newer), in the namespace `agentevalops-bundle`, and writes the
detached signature to `manifest.json.sig`. The key is never defaulted; a
bundle that does not validate is not signed, and an existing signature is
kept unless you pass `--force`.

```bash
agentevalops sign --bundle runs/seed --key release_ed25519
```

`sign` never prompts for a passphrase. For a key that has one, load it into
`ssh-agent` and pass its public half; `ssh-keygen` then signs with the
agent's copy:

```bash
ssh-add release_ed25519
agentevalops sign --bundle runs/seed --key release_ed25519.pub
```

To verify, list the keys you trust in an allowed-signers file (see
`ssh-keygen(1)`, ALLOWED SIGNERS), one line per key, each limited to this
tool's namespace. The identity is a name you choose for the key; the key
is the public key's type and base64 text:

```
release@example.org namespaces="agentevalops-bundle" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA...
```

```bash
agentevalops validate-bundle --bundle runs/seed --allowed-signers allowed_signers
agentevalops gate --bundle runs/seed --require-signature --allowed-signers allowed_signers
```

With `--allowed-signers`, `validate-bundle` verifies the signature and
prints the identity and the key's fingerprint; a missing or bad signature,
or no usable `ssh-keygen`, fails validation. `gate --require-signature`
cannot judge (exit 2) a bundle whose signature does not verify, or when no
`--allowed-signers` file is given.

**What a verified signature says.** That `manifest.json` is byte for byte
the file the holder of a key in your allowed-signers file signed, for this
tool's namespace, and -- with the checksums `validate-bundle` checks --
that every file the manifest lists is unchanged since then. It does not say
who ran the agent or that the run happened as recorded, that the code or
model was what the bundle names, that the key was not lost or stolen, or
anything about how well the system behaves; it is only as trustworthy as
the allowed-signers file you chose to trust.

## Rescoring a bundle

`rescore` derives a bundle's verdicts again from its own records with the
code now installed, and compares them with what the bundle records
(ADR-0008). It is offline: it never runs the agent, calls a model or uses a
tool, and it never writes into the bundle.

```bash
agentevalops rescore --bundle runs/seed
agentevalops rescore --bundle runs/seed --json rescore.json --output recomputed/
```

It validates the bundle first, with every check. Then it rebuilds the run
from `config.json` and the plan, and judges each task run again from its
recorded tool calls, end state and agent events, through the run's own
code. A run whose agent finished (it has an `agent.terminal` event) is
judged again; one stopped before that is made again from the reason it
records when its infrastructure failed, and carried as recorded, and
named, when its scenario's preflight or its configuration failed. Each
scenario's preflight is carried too. Rescore then derives the summary, the
policy verdict and the control coverage, renders every file in memory with
the writer's own renderer and the bundle's own dates, and compares.

| Outcome | Exit | Means |
|---|---|---|
| `MATCH` | 0 | The running code is the code that wrote the bundle, and every evaluation, the summary's counts and pass^k, the policy verdict, each control's state and verdicts, and both reports byte for byte, are what the bundle records |
| `DIFFER` | 1 | The code is the same and something is not; each difference is named, and a report's first differing line |
| cannot rescore | 2 | The bundle does not validate, predates format 0.6, or names a benchmark, pack or evaluator that is not installed |
| `CODE_CHANGED` | 3 | The package hash, the pack's scenarios or data hash, the catalogue files or an evaluator's id differ from the code that wrote the bundle; the verdict line still says whether every verdict reproduces under the running code, and names the ones that moved |

An evaluation's refs, and a control verdict's, decide for a bundle written
from R42 on (its `metadata.json` records `refs_version`) and for any bundle
the running code wrote; an older bundle's refs are compared and shown as
"refs (evidence)", so it rescores `CODE_CHANGED` with its verdicts
reproducing. Both commands print the era: `rescore`'s `Refs:` line says
whether the refs decided and why -- `deciding (refs_version 1)`, `evidence
(pre-R42 bundle)`, or `deciding (no refs_version, but the running code
wrote this bundle)`, which only an edited bundle shows -- and
`validate-bundle`'s says what the bundle records. A bundle whose marker is
deleted and whose recorded code is edited reads as one written before R42,
and is trusted as one: its signature is the defence against tampering.

`--json FILE` writes the result as JSON: the outcome, the provenance
comparison and every difference, and the reason when the bundle cannot be
rescored. `--output DIR` writes the recomputed content files into a new or
empty folder, for diffing by hand; it is not a bundle (no plan, no
manifest). Neither may be inside the bundle, by any spelling or link. A
bundle written by a later commit than the one installed, or rescored after
the code changed, gives `CODE_CHANGED`, not `MATCH`, even when every
verdict reproduces.

Rescore builds the benchmark the bundle names from the installed packages,
with the bundle's own options: rescore a bundle only from a source you
would accept a run config from.

**What a `MATCH` says.** That the code now installed -- the same code, by
`package_sha256`, as the code that wrote the bundle -- derives every
recorded deterministic verdict, the summary's counts and pass^k, the policy
verdict and the control coverage again from the bundle's own recorded tool
calls, end states and agent events. It does not say that those records
truthfully describe what the agent did (the harness recorded them; a
signature says they are unchanged since signing), that the agent would act
the same way again (the behavioural layer, not built), anything about
model-judged results, or anything beyond the tested domain pack. Nor does
it compare everything that could change a result: the versions of the
package's dependencies and of Python are not compared, and an evaluator
installed as a plugin whose code changed without a new `evaluator_id` is
not detected.

---

## Config reference

Every key the loader accepts, with its default. Keys not listed are
ignored.

```yaml
run_id: "unnamed-run"           # the bundle's run id
agent_id: "mock-agent-v1"       # a label recorded in the bundle; runner_id picks the runner
backend_id: "local"             # the only value accepted
max_concurrent_tasks: 1

runner_id: mock                 # mock | langgraph | openai | azure-openai
model: null                     # required for every runner except mock; no default, no env fallback
deployment: null                # azure-openai only, required there, rejected elsewhere
api_version: null               # azure-openai only, required there, rejected elsewhere

benchmark_id: toy               # toy | agentic-baseline
benchmark_options: {}           # toy: scenario (smoke | failure | policy_violation | trace_limit | mixed)
                                # agentic-baseline: pack (a registered domain pack), scenarios (optional subset)
benchmark_scenario: smoke       # alias of benchmark_options.scenario

system: null                    # agentic-baseline: the system declaration, relative to this file; required
repetitions: 1                  # runs without a declaration only; the declaration sets both otherwise
seed: 0                         # base of every (task, repetition) seed
bootstrap_samples: 10000        # resamples per pass^k interval (95%, cluster bootstrap over scenarios); 0 turns
                                #   intervals off, else at least 100; runs without a declaration only, as above

evaluators: [deterministic]     # any of deterministic, tool_use, state_based, policy, in order

resource_limits:                # per task run, enforced by the runner during it
  max_tokens: 100000
  max_wall_seconds: 3600.0
  max_cost_usd: 10.0

run_limits:                     # the whole run's budget; off unless set; runs without a declaration only,
  max_run_cost_usd: null        #   the declaration sets it otherwise. The spend is what the task runs report
  max_run_tokens: null          #   on their results (a run its infrastructure stops reports none). Once it
                                #   reaches a cap no further task run starts; those running finish; the rest
                                #   are not evaluated (run_budget_exhausted) unless their preflight stopped
                                #   them first, and the summary records run_budget

policy:                         # checked after the run against the trace and the harness records
  policy_id: default
  max_cost_usd: null            # fail if total cost exceeds this
  max_trace_events: null        # fail if the trace has more events
  deny_tool_ids: []             # fail if any listed tool was called
  allowed_tool_ids: []          # when set, fail if any other tool was called; may not overlap deny_tool_ids

mock_fail: false                # mock runner only: simulate agent failure
mock_cost_per_task_usd: 0.0     # mock runner only: cost reported per task
mock_trajectory: null           # mock runner only: known_good | known_bad, played through the real environment
```

`configs/seed_system.yaml` is the system declaration the agentic baseline
runs under: the tools the system exposes with their permission class, data
class, reversibility and external exposure; the level of human involvement;
the intended use, out-of-scope uses and business metrics, which are recorded
and repeated in the report, never evaluated; repetitions, seed and
`bootstrap_samples`; an optional `run_limits` budget, as in the run config;
and optional thresholds that may tighten the pack's, never loosen them.

---

## Development

The gate, as CI runs it:

```bash
ruff check src/ tests/
mypy src
lint-imports                 # the console script; `python -m importlinter` checks nothing
pytest --cov=agentevalops --cov-report=term-missing --cov-fail-under=94
```

`make check` runs the first two and the tests; `make lint-imports` the
contracts; `make smoke` a run, `validate-bundle` and `replay`. import-linter
holds nine contracts: the four-tier layering above; the independence of the
adapter families from each other; `catalogue` and `environments` importing
only `core`; and the confinement of each optional SDK to the one package
that uses it, with the SWE-bench harness imported by none. CI runs on every push: the `CI` job on Python
3.10, 3.11 and 3.12 (ruff, mypy, lint-imports, pytest with a 94% coverage
floor, then a smoke run with `validate-bundle` and `replay`); `CI (min
deps)` on 3.10 at the declared dependency floor; and `Package` (build and
twine check). Nothing is tagged while CI is red.

See [CONTRIBUTING.md](../CONTRIBUTING.md) for local setup and scope discipline,
[CHANGELOG.md](../CHANGELOG.md) for what each release holds,
[ARCHITECTURE.md](../ARCHITECTURE.md) for the layers in detail, the ADRs in
[docs/adr/](adr/) for the decisions (ADR-0005 for the assurance
re-baseline, ADR-0006 for the tool environment), and
[docs/imda-alignment.md](imda-alignment.md) for how each check of the
testing framework maps to what a run can evidence. Third-party framework
text is never reproduced in this repository; the catalogue holds
identifiers and this project's own labels.

---

## License

Apache License 2.0. See [LICENSE](../LICENSE).
