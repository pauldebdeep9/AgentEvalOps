# DESIGN.md — minieval

> **Status:** v0.1 · **Last updated:** 2026-08-26
> Source of truth for *what* minieval is and *what it deliberately is not*.
> Anything here overrides code comments, README, or chat threads. Build
> sequencing lives in `WBS.md`.

---

## 1. One-line definition

**minieval is a deliberately small, offline-first harness for evaluating
tool-using AI agents: a typed trace, a deterministic evaluator, a post-hoc
policy check, repeat runs with stability classification, and a result bundle
on disk.**

It is a learning artifact, reduced from
[AgentEvalOps](https://github.com/) (6,684 lines of package source, 10,600 of
test) to roughly 1,100 and 900. It is not production-grade and does not claim
to be.

---

## 2. Why reduce rather than read

The source repo is well-built and its `DESIGN.md` is better than most. But
reading a 6,700-line platform teaches you its *structure*; rebuilding a
1,100-line version teaches you which parts of that structure were load-bearing
and which were scaffolding. The reduction ratio is not the point. The argument
for each cut is the point, which is why §5 is longer than §4.

---

## 3. The problem, stated narrowly

An agent that produces a plausible-looking answer has not been evaluated. The
questions that matter are:

- Did it actually satisfy the task's criteria, or just terminate?
- Which tool calls did it make, and were the required ones among them?
- **How often is it correct** — not "did it work once"?
- When it failed, did it fail on merit, or did the harness break?
- Did it do anything the organisation forbids?

The fourth is the one most harnesses collapse. The third is the one the source
repo does not answer at all.

---

## 4. What is retained, and why

Four ideas. Everything else in the source repo is scaffolding around these.

**The typed trace event is the substrate.** `TraceEvent(run_id, task_id,
attempt, step_index, kind, payload, timestamp, cost_usd)`, with `kind` a closed
`StrEnum`. Evaluation, policy checking, cost accounting, and reporting are all
pure functions over `list[TraceEvent]`. A closed enum makes trace analysis a
typed problem rather than a regex problem.

**The runner streams events and never returns a verdict.** `AgentRunner.run()`
yields `TraceEvent`s and terminates with `AGENT_TERMINAL`, whose payload
reconstructs the `AgentOutput`. The runner *never raises on agent failure* —
only on infrastructure failure. That contract is what keeps "the agent was
wrong" and "the harness broke" from collapsing into one signal.

**`evaluated` is separate from `passed`.** `EvaluationResult.evaluated=False`
means the attempt was never judged, because there was nothing to judge.
`passed=False` still holds — there is no basis to call it a pass — but the two
facts are different and the partition is exhaustive:

```
total_attempts == passed_attempts + failed_attempts + not_evaluated_attempts
```

`failed_attempts` is a derived property precisely so it can never silently
absorb what was never judged. The orchestrator sets `evaluated`, not the
evaluator: an evaluator's job is to grade a result, not to special-case the
absence of one.

**Post-hoc policy is distinct from in-run limits.** `ResourceLimits` is
enforced by the orchestrator mid-stream and stops an attempt. `PolicySpec` is
graded after the attempt ends and produces `PASS`/`FAIL`/`WARN` with citations
to `step_index`. A denied tool call still happens; the point is that the trace
proves it happened. Two different questions, deliberately not fused.

---

## 5. What is cut, and why

This is the important section. Line counts are from the source repo.

**The registry (331 lines).** Entry-point plugin discovery, duplicate-key
guards, and a `KIND_*` taxonomy, to dispatch two runner names. A module-level
dict is five lines. Reinstate a registry when there is a third-party
implementation to discover, which for a learning repo is never.

**Seven of nine protocols (~200 of 253 lines).** `TraceStore`, `ArtifactStore`,
`Scorer`, `PolicyChecker`, `ReportGenerator`, `BenchmarkAdapter`, and
`CloudBackend` are all defined as Protocols in the source. Six have exactly one
implementation; `CloudBackend` has **zero** — the source's own `DESIGN.md`
admits its portability claim is untested because nothing implements it. A
Protocol with one implementation is a comment with syntax. `AgentRunner` and
`Evaluator` keep theirs, because those are the two axes actually swapped.

**Bundle format machinery (~1,380 lines: constants, manifest, reader, writer,
validator, serializers).** Checksums, `BUNDLE_FORMAT_VERSION`,
`LEGACY_BUNDLE_FORMAT_VERSIONS`, `required_files_for_version()`, and a
structural validator. All of it exists to read bundles written by older code.
That problem is real when bundles outlive the code that wrote them and
imaginary before then. Five flat files, no manifest, ~80 lines.

Retained from that family: `replay_command.txt`, about fifteen lines, recording
the invocation, `git_sha`, `git_dirty`, and the resolved model id. That is the
whole provenance lesson.

**Concurrency (semaphore, `gather`, cancellation drain).** Sequential
execution. The async iterator shape stays because it is what real model clients
look like and cannot be retrofitted cheaply; concurrency is ten lines whenever
it is wanted.

**OpenTelemetry (148 lines).** Teaches nothing about evaluation and adds
dependency weight.

**Two of three model runners (~900 lines).** One OpenAI-compatible runner.
`base_url` reaches Azure OpenAI, so the source's separate 316-line
`AzureOpenAIRunner` subclass is a configuration value here, not a class.
LangGraph (581 lines) is dropped entirely.

**Four of nine trace event kinds.** `EVALUATOR_SCORE` and `POLICY_VERDICT` are
never constructed in the source repo — its own `ROADMAP.md` lists them as an
open instance of a pattern it has hit three times. `COST_TICK` duplicates a
fact `TraceEvent.cost_usd` already carries. `AGENT_OBSERVATION` is redundant
with `AGENT_TOOL_RESULT` at this scale.

**Config loader (427 → ~50 lines).** YAML to dataclass, with required-field
checks and *rejection* of unknown keys. Rejecting matters: a typo'd `repeat:`
that quietly runs once makes a measurement wrong without making it look wrong.

---

## 6. What is added

**Repeat runs and stability classification.** The source repo has no
repeat-run support. `pass@k` appears three times, all in docstrings describing
what a `Scorer` *could* do; nothing constructs it. Every task runs exactly
once, which measures close to nothing — a model that is 95% reliable per call
is ~60% reliable across ten.

`RunConfig.repeats` drives a loop; `TaskResult` holds a list of `Attempt`s; and
`k`, `n`, `evaluated`, and `stability` are all `@property`, derived from that
list on demand. Nothing stored can disagree with what it was counted from.

`Stability` (`STABLE_PASS` / `FLAKY` / `STABLE_FAIL` / `NOT_EVALUATED`) exists
because "five scenarios failed stably" is a more useful sentence than "mean
6.4/12". `tasks_by_stability()` returns names, not counts.

One judgement encoded there, worth knowing about because it may be wrong for
some purposes: `stability` **ignores unevaluated attempts when classifying**.
Two judged passes plus one infrastructure failure is `STABLE_PASS`, not
`FLAKY` — absent evidence is not contrary evidence.

**A finding from building it, not in the plan.** `AgentOutput.steps` needs a
value on both of the mock runner's terminal branches (completed,
`agent_error`). A first pass counted it separately on each — two counters,
each internally consistent with its own tests, silently free to disagree
with the other's definition of what counts as a step. They were replaced
with one computation: `steps` is membership in a closed set of
turn-initiating trace-event kinds (`AGENT_PLAN`, `AGENT_FINAL_ANSWER`),
counted once from whatever the branch actually emitted. Two branches that
each agree with themselves are not the same as two branches that agree with
each other; only deriving the count from the trace made those the same
claim. The same argument as `evaluated`/`passed` above, discovered a second
time while writing the code rather than designed in ahead of it.

---

## 7. Vocabulary ledger

The rule this project is built around: **do not add an enum member, field,
protocol, or config key that nothing in the package constructs.** Applying it
honestly to itself, the following are declared in `schemas.py` but not yet
constructed anywhere, each with the item that will construct it:

| Vocabulary | Constructed by |
|---|---|
| `TerminationReason.AGENT_ERROR` | ✅ closed — W3, `MockRunner`'s `mock_fail="agent_error"` branch |
| `TerminationReason.LIMIT_STEPS` / `LIMIT_COST` | W6 (orchestrator limits) |
| `TerminationReason.INFRA_FAILURE` | W6 (per-attempt catch of `InfrastructureError` only — `ConfigurationError`, defined alongside it in W3's `errors.py`, is deliberately *not* caught per attempt; it aborts the run instead of producing N `evaluated=False` results) |
| `Verdict.FAIL` / `Verdict.WARN` | W5 (policy checker) |
| `Stability.*` | W6 (orchestrator), W8 (report) |
| `PolicySpec.max_cost_usd` | W5 |

**Checked at W10.** Anything still unconstructed then gets deleted, not
deferred. A ledger that is never audited is how the source repo accumulated
three open instances of the same defect.

---

## 8. Non-goals

**Not a platform.** No dashboard, no API service, no cloud backend, no
deployment story. Local execution is the only execution.

**Not multi-benchmark.** One toy benchmark of eight hand-written tasks. No
SWE-bench, no MLE-bench, no adapter family. The source repo's own SWE-bench
spike took two sessions and surfaced live-network calls inside supposedly
frozen containers; that is a genuine research problem and out of scope.

**Not an agent framework.** minieval evaluates agents; it does not provide
abstractions for writing them.

**Not a runtime safety system.** Policy is graded after the fact. Nothing is
intercepted in flight.

**Not production-grade, and not pretending.** No retries, no rate-limit
handling, no partial-failure recovery beyond the per-attempt catch, no
backwards compatibility for bundles.

**Not benchmarking model quality.** The measurements this produces are about
*the harness working*, not about any model being good. The one live run in W10
is evidence that the pipeline measures something, not a model evaluation.

---

## 9. What success looks like

**Mechanically:** a clean clone runs `minieval run --config
configs/mock_smoke.yaml` with no API key and no network, produces a bundle on
disk, and prints a report naming which tasks passed k/n.

**Substantively:** the harness detects failure correctly. The toy benchmark
includes a task that fails on merit, one that trips the policy deny-list, one
that exceeds the step budget, and one that raises `InfrastructureError` — and
the last lands in `NOT_EVALUATED`, not `STABLE_FAIL`. A harness that only ever
proves things pass has not been tested.

**Honestly:** the W10 live run's results are recorded before any tuning,
reported as k/n against named tasks, with cost figures marked as structurally
unvalidated until reconciled against an actual invoice. The source repo shipped
three model-backed runners and never once checked a reported `total_cost_usd`
against what a provider billed. Do not inherit that claim.

---

## 10. Document hygiene

Update this document when the answer to *what is this*, *what does it keep*,
*what did it cut*, or *what does it not do* changes. Implementation drift does
not require an update; positioning drift does.

§7's ledger is the exception — it is a live checklist, updated as each item
lands, and audited at W10.
