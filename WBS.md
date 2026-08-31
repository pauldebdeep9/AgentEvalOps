# WBS — minieval

A minimal agent-evaluation harness, reduced from AgentEvalOps (6,684 lines of
package source, 10,600 of test) to roughly 1,100 and 900. Each work item below
is sized to 3–7 prompts against Claude Code, ending at a green toolchain run
and a commit.

**Definition of done, every item, no exceptions:**

```bash
ruff check . && ruff format --check . && mypy --strict src tests && pytest -q
```

Chained with `&&` deliberately — a green pytest after a red ruff is not a green
build. Run it *before* the commit, and `git diff` before staging so nothing
lands unread.

**Git is deliberately loose: single branch, commit straight to `main`. Testing
is not.** Every item ships tests in the same commit, covering the happy path,
each failure mode by name, the boundary cases, and the distinction the module
exists to preserve — with at least three tests per item fail-first verified
(delete the check, watch it go red, restore it). See `CLAUDE.md`.

**The standing constraint, restated in every item's first prompt:** do not add
an enum member, dataclass field, protocol, or config key that nothing in the
package constructs. The source repo's own `ROADMAP.md` records this failure
three separate times (`INFRA_FAILURE`, `Verdict.WARN`,
`EVALUATOR_SCORE`/`POLICY_VERDICT` — vocabulary defined, nothing emitting it).
Subtraction is the whole exercise; the agent's default gradient is addition.

---

## Sequencing

```
W1 ──▶ W2 ──▶ W3 ──▶ W4 ──▶ W5 ──▶ W6 ──▶ W7 ──▶ W8   ✅ complete
                                                        │
                                                        ▼
                                         W9 CLI/config ──▶ W10 runner
                                             next           optional
```

W1–W8 need no network and no API key. That is deliberate: prove the harness
detects failure correctly before spending a single real token. W9 is the only
item that can fail for reasons outside the repo.

---

## W1 — Core schemas ✅ done

`src/minieval/schemas.py` (375 lines, 264 non-blank non-comment) and
`tests/test_schemas.py` (246 lines). 12 tests, `mypy --strict` clean.

Carries the four load-bearing ideas: typed trace events as substrate, the
`evaluated` vs `passed` distinction, the three-way attempt partition, and
`Stability` across repeats. Every aggregate is a `@property` derived from
`attempts` — nothing stored can disagree with what it was counted from.

---

## W2 — DESIGN.md and CLAUDE.md ✅ done · 3 prompts · ~0 lines of code

Do this before any more code. These two files are what stop the agent from
rebuilding AgentEvalOps.

1. Draft `DESIGN.md`: one-line definition, the four ideas retained and why,
   and — the important half — an explicit non-goals list naming what was cut
   (registry, 7 of 9 protocols, OTel, bundle versioning, LangGraph runner,
   concurrency) with the reason for each.
2. Draft `CLAUDE.md`: line budget per module, no new runtime dependencies
   beyond `pyyaml` and `openai`, the verification chain above, the
   no-uncalled-vocabulary rule, and "ask before adding a module."
3. Review both against the actual `schemas.py` on disk; fix anything the docs
   claim that the code doesn't do.

**Exit:** a reader who has never seen AgentEvalOps can tell from `DESIGN.md`
what was deliberately left out.

**Risk:** low. **Do not skip.** This is the highest-leverage item in the WBS
and the easiest to rationalise away.

---

## W3 — Protocols and the mock runner ✅ done · 5 prompts · ~110 lines

`src/minieval/protocols.py` (~30) and `src/minieval/runners/mock.py` (~80).

Two Protocols only — `AgentRunner` and `Evaluator`. Everything else stays a
concrete function until there is a second implementation to justify a seam.

The mock runner is the harness's own test double. It is written *before* the
real runner so every downstream test is deterministic and offline. It reads
`TaskSpec.criteria` to decide what to emit: the right answer, a wrong answer,
a denied tool call, a step-limit overrun, or an `InfrastructureError`.

1. `protocols.py` — `AgentRunner.run(task, limits) -> AsyncIterator[TraceEvent]`
   and `Evaluator.evaluate(...) -> EvaluationResult`, both
   `@runtime_checkable`. State the contract in the docstring: the runner
   *never* raises on agent failure, only on infrastructure failure.
2. `errors.py` — `InfrastructureError`, `ConfigurationError`. Two classes.
3. Mock runner: happy path only. Emits plan → tool_call → tool_result →
   final_answer → terminal.
4. Mock runner: the failure modes, driven by `criteria` keys.
5. Tests: `isinstance` checks against both Protocols, plus one test per
   failure mode asserting the emitted event sequence.

**Exit:** `MockRunner` satisfies `isinstance(r, AgentRunner)`; each failure
mode produces a distinguishable trace.

**Risk:** low.

---

## W4 — Toy benchmark ✅ done · 3 prompts · ~110 lines

`src/minieval/benchmarks/toy.py`. A plain function returning
`list[TaskSpec]` — not a Protocol, not an adapter class.

Nine tasks, and the distribution matters more than the count:

| Task | What it proves |
|---|---|
| T01–T03 | pass cleanly |
| T04–T05 | fail on merit (wrong answer, missing required tool) |
| T06 | trips the policy deny-list |
| T07 | exceeds `max_steps` |
| T08 | raises `InfrastructureError` → `not_evaluated` |
| T09 | flaky across repeats (`mock_pass_on`) |

T08 is the one that earns the whole three-way partition. Without it the
partition is untested vocabulary. T09 needs its own slot rather than
riding on an existing task: folding `mock_pass_on` into T01–T03 would
consume a clean pass, leaving fewer than three tasks that reliably pass;
folding it into T04 or T05 would give a task that fails for two
independent reasons at once — the same defect that makes a test stop
testing anything specific.

1. T01–T05.
2. T06–T09.
3. Tests: task ids unique, every `criteria` key is one the mock runner reads
   (guards against criteria the runner silently ignores).

**Exit:** running all nine through the mock runner produces at least one task
in each of the four `Stability` classes.

**Risk:** low.

---

## W5 — Evaluator and policy checker ✅ done · 5 prompts · ~120 lines

`src/minieval/evaluate.py` (~70) and `src/minieval/policy.py` (~40).

Pure functions over `(task, output, trace)`. No I/O, no model, no network —
which means they are fully testable against hand-built `list[TraceEvent]`
without running an agent at all. Write those tests that way; it is faster and
it isolates the failure.

1. Deterministic evaluator: completion check plus `expected_substring`.
2. Add `required_tool_names` and `required_trace_kinds`. All checks
   AND-combined; `citations` populated with real `step_index` values.
3. Policy checker: tool deny-list and cost ceiling, returning
   `PASS`/`FAIL`/`WARN`.
4. Tests for the evaluator against synthetic traces, including the case where
   the answer is right but a required tool was never called.
5. Tests for the policy checker, including a `FAIL` that cites the exact step.

**Exit:** every check has a test that fails when the check is removed. Verify
that by actually removing one and watching it go red — assertion is not
evidence.

**Risk:** low. **Note:** the evaluator never sets `evaluated=False`. That is
the orchestrator's call, in W6.

---

## W6 — Orchestrator ✅ done · 6 prompts · ~120 lines

`src/minieval/orchestrator.py`. The hard one, and the one where a subtly
wrong implementation still produces green tests — because the same process
wrote both.

**Write the assertions yourself before letting Claude Code write the loop.**
Specifically the partition identity and the repeat counts. This is a fifteen-
minute investment against the failure mode where the harness and its tests
agree with each other and both are wrong.

Sequential execution — no semaphore, no `gather`, no cancellation drain. Async
iterator, yes; concurrency, no. The semaphore is ten lines whenever you want it.

1. Single task, single attempt, end to end. Reconstruct `AgentOutput` from the
   `AGENT_TERMINAL` payload.
2. Catch `InfrastructureError`/`ConfigurationError` per attempt → a well-formed
   `evaluated=False` result, and the run continues. Catch nothing else: any
   other exception is a bug, not a modelled failure mode, and should propagate.
3. `repeats` loop, building `TaskResult.attempts`.
4. Enforce `max_steps` and `max_cost_usd` mid-stream, terminating with the
   right `TerminationReason`.
5. Integration test: all eight toy tasks × 3 repeats, asserting exact
   partition counts and per-task `k/n`.
6. Adversarial test: a runner that never emits `AGENT_TERMINAL`.

**Exit:** `passed + failed + not_evaluated == total_attempts` asserted against
real numbers, and T08 lands in `NOT_EVALUATED` rather than `STABLE_FAIL`.

**Risk:** **high** — the highest in the WBS. Budget an extra prompt.

---

## W7 — Bundle writer ✅ done · 4 prompts · ~155 lines

`src/minieval/bundle.py`. Five machine/provenance files landed in W7; W8 adds
`report.md` as the sixth current bundle file. The directory stays flat, with no
manifest, checksums, format version, legacy-version table, or validator. That
family is ~1,380 lines in AgentEvalOps.

```
runs/<run_id>/
├── metadata.json        run_id, created_at, git_sha, git_dirty
├── config.json          the RunConfig verbatim
├── traces.jsonl         every attempt's events, flattened
├── results.json         task results, k/n, stability
├── report.md            human view of the same RunSummary (added in W8)
└── replay_command.txt   invocation + git_sha + resolved model id
```

`replay_command.txt` is the one piece of the original's 1,380 lines worth
keeping, and it is about fifteen of them. `git_sha` is never fabricated: the
literal string `"unavailable"` outside a checkout, never `None`, never blank.

1. Writer for the four JSON/JSONL files.
2. Git provenance helper — SHA, dirty flag, the `"unavailable"` sentinel.
3. `replay_command.txt`.
4. Round-trip test: write a bundle from a real `RunSummary`, read
   `traces.jsonl` back through `TraceEvent.from_json`, assert event count and
   ordering.

**Exit:** a bundle on disk from an actual run; every trace line parses.

**Risk:** low.

---

## W8 — Markdown report ✅ done · 3 prompts · ~125 lines

`src/minieval/report.py`.

Report `k/n` against *named* tasks, never a percentage. With n=8 and
repeats=3, "67%" is noise dressed as a measurement. Group by `Stability` and
name the members: stable pass, flaky, stable fail, not evaluated. Report
not-evaluated separately from failed — the report is where the three-way
partition either pays off or gets quietly re-collapsed into two numbers.

1. Header block: run id, runner, model, repeats, the full attempt partition,
   and cost explicitly labelled as not invoice-verified.
2. Per-task table with `k/n`, evaluated count, stability, and policy-verdict
   counts; all four stability groups name their tasks.
3. Attempt-level findings preserve evaluator/policy notes and their trace-step
   citations, including policy failures on tasks that passed evaluation.
4. `write_bundle()` writes the rendered document as `report.md`, from the same
   `RunSummary` used for `results.json`.
5. Eight direct report tests plus bundle integration coverage exercise all
   stability classes, the zero-task boundary, Markdown escaping, citations,
   the absence of percentages, and overwrite behavior.

The increase from the original ~70-line estimate buys the explicitly requested
policy/citation detail and Markdown-safe rendering. It adds no schema, protocol,
configuration key, or runtime dependency.

**Exit:** the report of an all-mock run is something you would actually paste
into a decision memo.

**Risk:** low. Higher value than its size suggests — this is the artifact a
funding decision reads.

**Fail-first verified:** unevaluated attempts are not absorbed into failures;
task results remain `k/n`; trace-step citations cannot disappear silently.

---

## W9 — CLI and config loader · 5 prompts · ~110 lines

`src/minieval/config.py` (~50) and `src/minieval/cli.py` (~60).

YAML → `RunConfig`, with required-field checks and rejection of unknown keys.
Reject, do not silently ignore: a typo'd `repeat:` that quietly runs once is
exactly the bug that makes a measurement wrong without making it look wrong.

Runner dispatch is a module-level dict, five lines. Not a registry, not entry
points, not plugin discovery.

1. `config.py` — load, validate, construct.
2. Unknown-key and missing-field rejection, with useful messages.
3. `cli.py` — `run --config <path>` writing a bundle and printing the report.
4. `cli.py` — `report <bundle_dir>` re-rendering from a bundle on disk.
5. Two example configs (`configs/mock_smoke.yaml`, `configs/mock_repeats.yaml`)
   plus a test that every shipped config actually loads.

**Exit:** `minieval run --config configs/mock_smoke.yaml` produces a bundle and
a report from a clean clone, no API key.

**Risk:** medium — CLI tests drift toward testing the framework rather than
your code. Test `config.py` hard and `cli.py` lightly.

---

## W10 — OpenAI-compatible runner · 6 prompts · ~130 lines

`src/minieval/runners/openai.py`. Last, and optional.

One runner, not three. `base_url` covers Azure OpenAI, so the original's
separate 316-line `AzureOpenAIRunner` subclass is unnecessary — note in
`DESIGN.md` that Azure is reachable by configuration rather than by a class.

Take the client as a constructor argument so tests inject a fake. That single
seam is what makes every test in this item offline.

1. Constructor, client injection seam, `agent_id`.
2. The turn loop: model call → tool calls → tool results → repeat, emitting
   `TraceEvent`s throughout.
3. Tool definitions and dispatch for the toy tasks' tools.
4. Token and cost accounting onto `cost_usd`.
5. Offline tests against a fake client covering the loop, the step limit under
   batched parallel tool calls, and a malformed tool-call response.
6. One live run, `repeats=5`, results recorded in the README as `k/n`.

**Exit:** offline tests green with no network. Live run recorded honestly,
including whatever it shows.

**Risk:** **high**, and mostly outside the code. Two things to write down
plainly rather than tune away:

- **Cost figures are structurally unvalidated** until reconciled against an
  actual invoice. AgentEvalOps shipped three model-backed runners and never
  once checked a reported `total_cost_usd` against what a provider billed —
  their `ROADMAP.md` names this as a single repeating gap, not three separate
  blockers. Do not inherit the same claim.
- **Temperature 0 is not deterministic.** You measured this in P2. `repeats=5`
  exists because of it. Report the spread; do not average it away.

---

## Totals

| | Prompts | Source |
|---|---|---|
| W2–W10 | ~40 | ~980 |
| W1 (done) | — | 264 |
| **Total** | **~40** | **~1,245** |

Against 6,684. Roughly a 6× reduction — less than the 10× first estimated,
because `repeats`/`Stability` is a genuine addition rather than a retained
feature. That trade is worth naming: the source repo has no repeat-run support
at all (`pass@k` appears three times, all in docstrings describing what a
`Scorer` *could* do; nothing constructs it), which means every task runs once
and a single run per task measures close to nothing.

---

## The two places to spend real attention

**W6.** Write the assertions before the implementation. It is the only item
where the tests and the code can be wrong together and still look right.

**W10's live run.** Report what happened before any tuning. A stably failing
scenario named in the README is worth more than a tuned number, and it is the
only part of this repo that constitutes evidence rather than construction.
