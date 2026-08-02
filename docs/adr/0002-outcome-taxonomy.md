# 0002. A unified outcome taxonomy: task outcome, evaluation outcome, run aggregation, bundle integrity, and replay comparison are five different questions, not one enum

## Status

Proposed 2026-08-04 — **implemented 2026-08-05 (W12a)**, with the following
resolutions of what "Open questions" left open: `not_evaluated_tasks`, not
`infra_failed_tasks` (names the fact, not the inferred cause — decided
during implementation, applied consistently). `sealed: bool` kept alongside
`status`, always derived from it. `git_dirty` added alongside `git_sha`
(this ADR named `git_sha` alone; the implementing session found a clean SHA
against a dirty tree was the more likely false-provenance failure mode and
added the flag). The `agent_id`-fix-vs-`model_id`-field choice for Axis 4's
model provenance was resolved by neither: `RunConfig` gained a direct
`model` field (see `docs/adr/0001-openai-runner-chat-completions.md`'s
2026-08-05 closure). Axis 5 (`Verdict.WARN` does not generalize) confirmed,
unchanged. `TraceEventKind.EVALUATOR_SCORE`/`.POLICY_VERDICT` wiring
(mentioned in "A recurring pattern," not one of the five axes) assessed and
explicitly deferred past W12a — see `ROADMAP.md` §0's W12a entry for the
reasoning, including a real design question (no precedent for a run-level,
non-runner-emitted trace event) `POLICY_VERDICT` specifically surfaced.

## Context

Two pieces of work converged on the same underlying gap from different
directions and were deliberately combined into one design session so they
don't produce incompatible answers:

- **W12** (`ROADMAP.md`, row for Aug 3–9) needs a way to distinguish "bundle
  corrupt," "code changed since the bundle was written," and "results
  genuinely differ" once behavioral replay exists.
- **The 2026-08-03 infra-failure investigation** (`ROADMAP.md` §0) found a
  fourth, currently-uncategorized state — "the agent was never asked" — and
  a policy-checker fix that gave `Verdict.WARN` its first real meaning,
  narrowly scoped to one checker.

Six states were named as the minimum bar: agent succeeded, agent failed on
merits, infrastructure failed before evaluation, bundle corrupt or
incomplete, code changed since the bundle was written, replay results
genuinely differ.

**These six states do not belong in one enum.** They answer five different
questions at five different scopes:

1. *Why did this one task's agent loop stop?* — per-task, decided by the
   runner.
2. *Was this task's result actually evaluated for correctness?* — per-task,
   decided by the evaluator (or, per this ADR's decision, skipped
   entirely by the orchestrator for tasks with nothing to evaluate).
3. *How does a run's tasks aggregate into a headline?* — per-run.
4. *Is this bundle's own file set internally trustworthy?* — per-bundle,
   independent of what happened during the run it records.
5. *Does re-running this bundle's config today reproduce what it recorded?*
   — a comparison between two runs, not a property of either alone.

Forcing all six states into one closed enum would mean a bundle-integrity
check and a per-task outcome share a type, despite answering unrelated
questions at unrelated scopes — exactly the kind of collapse this whole
investigation keeps finding (see "A recurring pattern," below).

### A recurring pattern, found again while investigating this

This is not the first time this codebase has shipped a vocabulary slot with
no wiring behind it:

- `TerminationReason.INFRA_FAILURE` (`core/schemas.py:72`) — defined,
  asserted by exactly one isolated unit test, never constructed by any
  runner or orchestrator code. (Found 2026-08-03.)
- `Verdict.WARN` (`core/schemas.py:58`) — defined since the schema's
  original authoring, unused anywhere until 2026-08-03's policy-checker fix
  gave it its first real meaning, scoped to one checker.
- `ResultBundleMetadata.sealed` (`core/schemas.py:303`) — defaults to
  `False` on the dataclass, but `bundles/writer.py:121` hardcodes
  `sealed=True` on every single bundle ever written, unconditionally.
  `grep -rn "\.sealed" src/` outside `writer.py`'s write site and the
  dataclass definition itself returns nothing — nothing anywhere reads it
  back. It has never varied and nothing depends on it varying.

Three for three: this project defines the right field, then ships the write
path without the corresponding logic, and nothing catches it because the
field's mere presence looks like the feature is done. This ADR's own
proposed fields (below) are written with that history in mind — each comes
with an explicit statement of what must *also* change (orchestrator logic,
not just a schema field) for it to mean anything, so a future session
doesn't repeat the pattern a fourth time.

### Material available

- A real, preserved partial bundle:
  `tests/fixtures/bundles/partial-infra-failure-2026-08-03/` — only
  `traces.jsonl` exists (two `agent.plan` events, no `agent.terminal`, no
  cost). Confirmed this session: it isn't merely `LocalOrchestrator` failing
  to catch `InfrastructureError` — **no failure path in `cli.py` ever calls
  `BundleWriter.write()` at all**, including the one path
  `orchestration/local.py` *does* handle (`KeyboardInterrupt`/
  `CancelledError`, lines 135-149). `cli.py`'s `except (KeyboardInterrupt,
  asyncio.CancelledError)` and `except InfrastructureError` blocks (lines
  113-132) both print a message and `raise typer.Exit(...)` — neither
  reaches the `BundleWriter(...).write(...)` call, which sits *after* the
  whole `try/except`, reachable only on success. `ARCHITECTURE.md` §5's
  "best-effort finalize... `metadata.json.status = 'interrupted'`" promise
  is **0% built for every failure mode**, not partially built for some —
  `LocalTraceStore`'s per-event flush-to-disk is *necessary* for even
  `traces.jsonl` to survive, but finalizing the trace store was never
  sufficient on its own, and nothing upstream of it was ever attempted.
- Two fresh mock-runner bundles generated this session
  (`configs/toy_smoke.yaml`, run twice, ~1 second apart) and diffed file by
  file — the empirical basis for the nondeterminism enumeration in ADR
  0003.
- A real, confirmed pre-existing inconsistency: `bundles/constants.py`'s
  `BUNDLE_FORMAT_VERSION = "0.1"` (used in `manifest.json`, validated by
  `bundles/validator.py`'s `SUPPORTED_FORMAT_VERSIONS` check) versus
  `bundles/writer.py:115`'s `schema_version="0.1.0"` (a separate hardcoded
  literal in `metadata.json`, never validated against anything, never
  cross-checked against the manifest's version string). Two different
  strings, two different files, both claiming to be "the bundle format
  version," already inconsistent before this ADR proposes touching either.

## Decision

Five axes, each with its own small vocabulary, each recorded at the scope
where the question it answers actually applies.

### Axis 1 — Task outcome (`TerminationReason`, wire what exists, add nothing)

`COMPLETED | LIMIT_TOKENS | LIMIT_TIME | LIMIT_COST | AGENT_ERROR |
INFRA_FAILURE`. All six members already exist in the enum. **No schema
change.** The only work is behavioral: the orchestrator must catch
`InfrastructureError`/`ConfigurationError` per task (not let it abort
`asyncio.gather()` for the whole run) and construct an `AgentOutput` with
`termination_reason=TerminationReason.INFRA_FAILURE`. This is the
"orchestrator" layer of the three-layer fix already recorded in
`ROADMAP.md` §0 — this ADR doesn't change that plan, it fixes the taxonomy
the plan's schema layer was missing a design for.

Recorded: per task, in `AgentOutput.termination_reason` and each task's
`AGENT_TERMINAL` trace-event payload. Already flows into `summary.json`
today for the five members already wired; `infra_failure` needs no new
field to also flow there, only the orchestrator change above.

### Axis 2 — Evaluation outcome (`EvaluationResult`, one new field)

Add `evaluated: bool = True`. Default `True` is correct for every existing
bundle — evaluation always ran before this axis existed, so the absence of
the field on an old bundle means exactly what the default says.

**Decision: the orchestrator, not the evaluator, decides this.** When a
task's `termination_reason == INFRA_FAILURE`, the orchestrator should
*skip calling `Evaluator.evaluate()` entirely* for that task and construct
`EvaluationResult(evaluated=False, passed=False, score=0.0, notes="not
evaluated: infrastructure failure before completion")` directly. Rejected
alternative: have `DeterministicEvaluator.evaluate()` special-case
`INFRA_FAILURE` internally. Rejected because the evaluator's whole
contract (DESIGN.md §9: "Evaluators are pure functions of (task, result,
trace)") is to judge a completed result against criteria — a task that
never produced a result isn't a case for the evaluator to *handle*, it's a
case where there's nothing to hand the evaluator at all. Keeping the
"was this ever evaluated" decision in the orchestrator, upstream of every
evaluator, means every future evaluator (LLM-judge, tool-use, etc.)
inherits the correct behavior for free instead of each having to
reimplement the same check.

Every other `termination_reason` (`COMPLETED`, `AGENT_ERROR`,
`LIMIT_TOKENS`, `LIMIT_TIME`, `LIMIT_COST`) still means the task ran to
some conclusion and *is* evaluable — `evaluated` stays `True` for all of
them, `passed`/`score` still come from the real evaluator as today.

### Axis 3 — Run aggregation (`RunSummary`, one new field, one redefinition)

Add `infra_failed_tasks: int = 0`. Redefine `failed_tasks` to mean
`evaluated=True and passed=False` only — genuine merit-based failures.
`total_tasks = passed_tasks + failed_tasks + infra_failed_tasks` becomes a
clean three-way partition, replacing today's implicit two-way split where
`failed_tasks` silently absorbs everything that isn't `passed_tasks`
(exactly what let an infra-failed task read as a wrong answer, if the
orchestrator ever got fixed to catch it without this axis also existing).

**Headline aggregation — needs sign-off, not decided unilaterally here (see
Open Questions):** my recommendation is that the primary reported "pass
rate" stays `passed_tasks / total_tasks` (i.e. infra-failed tasks count
against the rate, never silently excluded from the denominator — a run that
half-crashed should not look better than it was), with
`infra_failed_tasks` always displayed alongside as its own number, never
folded into "failed" silently. A secondary, explicitly-labeled "of tasks
actually evaluated" rate (`passed_tasks / (total_tasks - infra_failed_tasks)`)
may also be shown, but never as the unlabeled default — collapsing to a
single number by *either* convention without labeling it is a milder
version of the exact bug this whole investigation started from.

### Axis 4 — Bundle self-description (`ResultBundleMetadata`, three new fields, one field finally wired)

- `status: str = "sealed"` (values: `"sealed" | "interrupted" | "partial"`).
  This is the field `ARCHITECTURE.md` §5 already promised
  (`metadata.json.status = "interrupted"`) and that was never built.
  **Finally makes `sealed: bool` mean something**: instead of the dead,
  always-`True` boolean, `status` distinguishes a normal complete bundle
  from one where `cli.py` had to write *something* after a partial/aborted
  run. Whether the existing `sealed: bool` field is kept alongside `status`
  (redundant, `sealed == (status == "sealed")`) or removed in favor of
  `status` alone is an open question below — removing a field is more
  disruptive to old readers than adding one, so there's a real tradeoff,
  not an obvious answer.
- `git_sha: str | None = None`. `ARCHITECTURE.md` §5 already claims this is
  captured ("including the platform version, the git SHA if available") —
  it is not; no such field exists on `ResultBundleMetadata` today. This is
  the single most load-bearing addition for ADR 0003's replay work: without
  it, "code changed since this bundle was written" (Part A's fifth state)
  has no evidence to check against. `platform_version` alone is too coarse
  — every bundle produced by this repo to date records the same
  `"0.1.0.dev0"` regardless of which of dozens of intervening commits
  actually produced it (confirmed: `runs/design-diff-*/metadata.json` this
  session, both generated from current `main`, both under a version string
  that hasn't been bumped since early WBS work).
- A fix, not strictly a new field: **`RunConfig.agent_id`, as written into
  `config.json`, currently records the *static YAML-declared* value
  (`configs/toy_openai.yaml`'s `agent_id: "openai-chat"`), never the
  *runtime-resolved* value the runner itself computes**
  (`OpenAIRunner.agent_id = f"openai-chat:{self._model}"`,
  `agents/openai_runner.py:326`). `cli.py`'s `_build_runner()` constructs
  the runner (which computes the model-qualified `agent_id`) but never
  writes that value back onto `loaded.run_config` before
  `LocalOrchestrator`/`BundleWriter` consume it — the two `agent_id`s exist
  side by side in memory for the length of one CLI invocation and only the
  less-informative one gets persisted. **No model-backed run's bundle
  records which model actually ran, anywhere, today.** Recommended fix:
  after constructing the runner, set `run_config.agent_id = runner.agent_id`
  before passing `run_config` onward — this repairs what the field is
  already documented to mean, rather than adding a parallel `model_id`
  field that would leave the stale `agent_id` in place as a trap for the
  next reader. (Alternative — add a dedicated `model_id` field instead —
  considered and listed as an open question, not decided here, because it
  changes matching/display logic anywhere `agent_id` is currently assumed
  to be the static config value.)

### Axis 5 — Policy compliance (`Verdict`) — does NOT generalize

**Resolved: `Verdict.WARN`'s new meaning stays scoped to `PolicyChecker`. It
does not become the vocabulary for Axis 2's "not evaluated," nor for any
other axis.** `Verdict` answers "did this run comply with organizational
policy" — `PolicySpec`/`PolicyChecker` (DESIGN.md §6, §9) is explicitly a
distinct protocol from `Evaluator` for exactly this reason: an evaluator
judges task-correctness against benchmark criteria, a policy checker judges
compliance against organizational rules, and DESIGN.md deliberately keeps
these separate ("the platform never produces a single composite score by
default, because composite scores hide the disagreements that matter").
Reusing `Verdict` for `EvaluationResult.evaluated` would need `passed: bool`
retyped to a three-state type shared with an unrelated protocol — a real
schema change for no benefit over a plain new boolean field, and a
precedent for blurring exactly the protocol boundary DESIGN.md draws on
purpose.

**Rejected alternative: one shared three-state enum used everywhere
(`Verdict` extended, or a new `Outcome{OK, PROBLEM, UNKNOWN}` used by every
axis).** Rejected for the same reason a single flat enum was rejected in
"Context" above: it would erase the distinction between "this task wasn't
evaluated" (Axis 2, per-task), "this run has some unevaluated tasks" (Axis
3, per-run aggregate), "this bundle's files don't check out" (Axis 4,
per-bundle, orthogonal to what happened during the run), and "we couldn't
compare because the code moved" (replay, a property of a *comparison*, not
of any one bundle) — four genuinely different claims that happen to share
the English word "unknown," which is exactly how `Verdict.PASS` came to
mean "found nothing to flag" instead of "the agent behaved," the bug this
whole investigation started from.

### Bundle-format implications

- Axis 1 needs **no schema change** — `INFRA_FAILURE` already exists in the
  shipped enum; using it is a behavior change, not a format change. Old and
  new readers already tolerate any string in this closed set, since bundle
  readers (`bundles/reader.py`) parse JSON into plain dicts and never
  strictly validate `termination_reason` against the Python enum.
- Axes 2-4's new fields (`EvaluationResult.evaluated`,
  `RunSummary.infra_failed_tasks`, `ResultBundleMetadata.status`/`git_sha`)
  are **additive with safe defaults** — backward-compatible under
  `DESIGN.md` §9's rule ("versioned, documented, and backwards-compatible
  within a major version"). Old bundles simply lack the fields; a reader
  must treat their absence as the stated default, not as an error.
- **The pre-existing `"0.1"` vs `"0.1.0"` inconsistency (see Context) should
  be resolved as part of this change, not compounded by it.** Recommendation:
  make `ResultBundleMetadata.schema_version` read from
  `bundles.constants.BUNDLE_FORMAT_VERSION` instead of a separately
  hardcoded literal, so there is exactly one source of truth for "the
  bundle format version" instead of two that happen to agree today by
  coincidence and could silently diverge tomorrow. Bump
  `BUNDLE_FORMAT_VERSION` from `"0.1"` to `"0.2"` to mark the point these
  fields become part of the documented contract — a minor bump, since
  nothing existing breaks, matching `DESIGN.md`'s "backwards-compatible
  within a major version" framing.
- **What happens to existing bundles, including the preserved fixture:**
  old bundles (the `toy-smoke*` bundles under `runs/`, dated as early as
  2026-05-26) simply predate these fields; any future reader must treat
  their absence as the documented default, not fail validation over it.
  `tests/fixtures/bundles/partial-infra-failure-2026-08-03/` is
  **unaffected in a different way**: its regression test
  (`tests/test_bundle_fixtures.py`) asserts a specific list of *missing
  required files* — `metadata.json`, `config.json`, `evaluations.json`,
  `policy.json`, `summary.json`, `report.md`, `manifest.json`. None of
  those files exist in that fixture at all, so no field *within* them can
  be added, removed, or reinterpreted by this change. The fixture's
  assertion is about structural completeness, orthogonal to schema
  content — this ADR requires no change to that fixture or its test.

## Alternatives considered and rejected

- **One unified enum for all six states.** Rejected — see "Context" and
  Axis 5's rejected-alternative paragraph. The states answer different
  questions at different scopes; a shared type would make the type
  system's clean-looking "impossible states" claim false — states from
  different axes genuinely *can* coexist (a bundle can be structurally
  valid *and* record one infra-failed task *and* later fail replay
  comparison because code changed — three axes, three simultaneous, mutually
  compatible values), which a single enum, closed and mutually exclusive by
  construction, cannot represent.
- **Reuse `Verdict` for evaluation outcome.** Rejected — see Axis 5.
- **Have `Evaluator.evaluate()` special-case `INFRA_FAILURE` itself.**
  Rejected — see Axis 2. Wrong layer; every future evaluator would need to
  reimplement the same check.
- **Add a `model_id` field instead of fixing `agent_id`.** Not rejected,
  left open — see Axis 4's fix. Listed as an open question, not decided.

## Consequences

- Four small, additive, backward-compatible schema changes
  (`EvaluationResult.evaluated`, `RunSummary.infra_failed_tasks`,
  `ResultBundleMetadata.status`, `ResultBundleMetadata.git_sha`), one field
  repair (`RunConfig.agent_id` reflecting the resolved runtime value), and
  one consolidation (`schema_version` sourced from the single
  `BUNDLE_FORMAT_VERSION` constant) — none implemented by this ADR.
- The orchestrator must change to (a) catch per-task infra/config errors
  instead of letting them abort the whole `gather()`, (b) skip the
  evaluator for infra-failed tasks and synthesize `evaluated=False`
  directly, (c) partition `RunSummary`'s three buckets correctly. This is
  the same "orchestrator" and "schema" layers already named in
  `ROADMAP.md` §0's three-layer fix — this ADR gives that plan the taxonomy
  it was missing, not a new plan.
- `cli.py` must gain a real best-effort bundle-write path for the
  `KeyboardInterrupt`/`InfrastructureError` cases, using the new `status`
  field — today neither path calls `BundleWriter` at all (see "Material
  available"). Once the orchestrator change in the previous bullet lands,
  most infra failures won't need this path at all (they'll come back as a
  normal `RunSummary` with some `INFRA_FAILURE` tasks) — this path only
  remains necessary for `KeyboardInterrupt` and any failure severe enough
  that the orchestrator itself can't return normally.
- `BasicPolicyChecker`'s `WARN` logic (shipped 2026-08-03) is unaffected —
  it already uses `output.success`/trace inspection, not any of these new
  fields, and this ADR confirms (Axis 5) that it should stay exactly that
  narrowly scoped rather than being generalized.
