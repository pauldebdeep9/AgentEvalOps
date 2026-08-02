# 0003. Replay semantics: two different promises sharing one ambiguous name, and what closing that gap requires

## Status

Proposed — 2026-08-04. Design only; nothing in this ADR is implemented yet.
Depends on [0002](0002-outcome-taxonomy.md) — the "code changed" and
"results differ" outcomes this ADR defines are two of that ADR's five axes.
**0002 was implemented 2026-08-05 (W12a)**, which unblocks this ADR's
prerequisites (`git_sha`/`git_dirty`, `RunConfig.model`) but this ADR's own
content — `replay_command.txt`, `ReplayRunner`, the fixture strategy — is
still entirely unbuilt, scoped to W12b/W12c. See "Open questions" at the
end.

This single ADR covers both the taxonomy-adjacent replay decisions and the
detailed, evidence-based answers to the six W12 design questions — a
separate "design section" file was considered and rejected: the six
questions are the technical grounding *for* this ADR's decisions, not a
separate narrative, and splitting them apart would just make a reader
cross-reference two files to understand one set of choices. Each question
below quotes real evidence (code line references, or output from real
generated/inspected bundles), not assumptions.

## Context

`replay/local.py`'s own docstring is honest about the gap: *"Replay ...
means 'bundle verification'. No agents, models, tools, or benchmarks are
executed."* `ARCHITECTURE.md` §7 confirms this is a narrowed, undocumented
scope: the original W12 target (`ROADMAP.md` row 12) was a `ReplayRunner`
that substitutes recorded model outputs and asserts behavioral match; what
shipped (WBS 4/8) is `LocalReplayVerifier`, a static, read-only
consistency checker over a bundle's own files.

Two *different* things are both currently called "replay" in this
codebase, and conflating them is itself a source of confusion this ADR
tries to stop compounding:

1. **Structural verification** (what exists): are this bundle's own files
   internally consistent — do checksums match, do cross-file counts agree,
   is required JSON parseable.
2. **Behavioral replay** (the unbuilt target): does re-executing this
   bundle's recorded run, with model calls replaced by the bundle's own
   recorded model responses, reproduce the same tool-call sequence,
   termination reasons, and pass/fail verdicts.

## Decision

### Q1 — What does replay assert? (two sentences, because there are two things)

**Structural verification** (already built, already true of
`LocalReplayVerifier`) asserts: *every cross-referential and checksum
invariant the bundle format guarantees still holds — this bundle has not
been corrupted, truncated, or internally contradicted since it was
written.* Falsifiable today: `tests/fixtures/bundles/partial-infra-failure-2026-08-03/`
is a real, checked-in example of a bundle that fails this assertion, and
`test_bundle_fixtures.py` pins the exact reason (missing required files).

**Behavioral replay** (the unbuilt `ReplayRunner`, W12's original target)
should assert: *substituting this bundle's own recorded model outputs for
what would otherwise be live model calls, and re-running the platform's
current orchestration, evaluation, and policy code, reproduces the same
per-task `termination_reason`, the same tool-call sequence (name and
order — not necessarily identical arguments), and the same evaluator
pass/fail verdict as the bundle originally recorded, with `total_cost_usd`
allowed to drift by a documented tolerance* (`ARCHITECTURE.md` §7 already
proposes ±5%, inherited here rather than re-derived).

**Recommendation, needs sign-off (see Open Questions):** stop sharing the
word "replay" between these. Keep `agentevalops replay` (or rename to
`verify`) for the structural check that already exists and works; reserve
"replay" for the behavioral feature once it exists, or pick two names now
so the CLI surface doesn't have to change meaning under users later.

### Q2 — Where is the determinism boundary?

**Does not re-execute:** the model API call. **What replaces it:** a
synthetic `agent_factory` — the exact same injection seam
`LangGraphRunner`/`OpenAIRunner` already accept for testing
(`agent_factory: Callable[[], Any] | None`, used today by every unit test's
`_FakeClient`). A `ReplayRunner` would not need to modify either runner at
all: construct the real runner with a synthetic factory that returns
recorded responses, reconstructed from the bundle's own `traces.jsonl`
(`AGENT_TOOL_CALL`/`AGENT_FINAL_ANSWER`/`COST_TICK` payloads carry tool
name, arguments, answer text, and token counts — enough semantic content to
re-synthesize a provider-shaped fake response object), in the order they
originally occurred. This is a real architectural finding, not a
guess: the seam that makes unit testing possible without the network is the
same seam that makes recorded replay possible without the network.

**Does re-execute:** `LocalOrchestrator`, `DeterministicEvaluator`,
`BasicPolicyChecker`, `SimpleScorer`, `MarkdownReportGenerator` — everything
except the model call. This is deliberate and matches
`ARCHITECTURE.md` §7's own framing: recorded replay tests *"did the
platform itself break,"* not *"did the model change."* `BenchmarkAdapter.
list_tasks()` also re-executes (cheap, deterministic for the toy benchmark)
rather than being read from the bundle — task definitions aren't currently
captured in the bundle at a level that would let them be replayed instead
of regenerated, and regenerating them from the same benchmark/scenario id
is simpler than adding that capture.

### Q3 — What is nondeterministic in a bundle today? (enumerated from real bundles, not assumed)

Empirical method: ran `configs/toy_smoke.yaml` (mock runner) twice, one
second apart, into fresh output directories, and diffed every file.

| File / field | Differed between two runs of the same config? | Disposition for recorded replay |
|---|---|---|
| `config.json` | No — byte-identical | Compare directly |
| `summary.json` (incl. `wall_seconds`) | No — byte-identical (mock runner reports a fixed `0.01`) | Compare directly for mock; see below for real runners |
| `evaluations.json` | No — byte-identical | Compare directly |
| `policy.json` | No — byte-identical | Compare directly |
| `metadata.json.created_at` | Yes | Exclude (wall-clock, not behavioral) |
| `traces.jsonl[].timestamp` (every event) | Yes | Exclude per-field; compare everything else in each event |
| `report.md`'s `Generated:` line | Yes | Exclude; don't diff `report.md` text at all — it's a derived artifact, re-rendering it from a matching `summary.json` is sufficient |
| `manifest.json.generated_at` | Yes | Exclude |
| `manifest.json`'s per-file `sha256` | Yes (derived from the two points above — the checksummed files themselves contain timestamps) | Don't compare manifests between original and replay at all; the comparison belongs at the semantic level (does task 1's `termination_reason` match), not the file-checksum level |

Not empirically captured this session (no successful real-model bundle
exists — the only real model-backed bundle available,
`tests/fixtures/bundles/partial-infra-failure-2026-08-03/`, never got past
`agent.plan`; a live call is explicitly out of scope per this session's
Non-goals). Reasoned from the runner code instead, flagged as such:

| Field | Nondeterministic for a real model-backed run? | Disposition |
|---|---|---|
| Model output text / tool-call argument values | Yes (genuine LLM sampling) | For **recorded** replay: moot — substituted, not regenerated, deterministic by construction. For **live** replay (optional, not CI-required): exclude, per `ARCHITECTURE.md` §7 — "need not produce identical model outputs token-for-token" |
| Token counts / `total_cost_usd` | Yes, follows from the above | Recorded replay: moot (substituted). Live replay: normalize within a tolerance (±5%, inherited from `ARCHITECTURE.md` §7) |
| `wall_seconds` | Yes — both real runners use `time.monotonic()` | Exclude always, recorded or live |
| Number of loop iterations / tool-call round-trips | Possibly, if the model's behavior itself varies | Recorded replay: moot (the recorded sequence *is* what gets replayed). Live replay: this is exactly what "behavioral structure, not exact output" (`ROADMAP.md` Risk #6's mitigation) is for |

**One more finding, not originally expected:** `run_id` is **not**
auto-generated today, contrary to `ARCHITECTURE.md` §5's claim ("generates
a `run_id` (UUIDv7...)"). `config/loader.py:226`:
`run_id=RunId(str(raw.get("run_id", "unnamed-run")))` — always taken
verbatim from the YAML, or a static default. Confirmed empirically: both
diffed bundles above have the identical `run_id`, `"toy-smoke-001"`. This
is currently *helpful* for replay comparison (one less nondeterministic
field to exclude) but is itself a documented-vs-actual mismatch worth
resolving deliberately — if `run_id` auto-generation is ever implemented
as `ARCHITECTURE.md` describes, the replay design must account for it
(compare using the bundle's *recorded* `run_id` as an input, not require a
freshly-generated one to match).

### Q4 — What does `replay_command.txt` contain, and what happens when code changed?

**It does not exist.** Confirmed by reading `bundles/writer.py`'s `write()`
end to end: it writes exactly `metadata.json`, `config.json`,
`traces.jsonl`, `evaluations.json`, `policy.json`, `summary.json`,
`report.md`, `manifest.json` — no ninth file, no `replay_command.txt`
anywhere in the call chain. `ARCHITECTURE.md` §7's callout already flags
this ("no `replay_command.txt` generation... exist") — confirmed still
true, not just still-documented-as-missing.

**What it needs to contain**, derived from what's actually needed to detect
"code changed since this bundle was written" (the task's own framing: *"A
replay that silently passes against a different codebase is worse than one
that fails"*):

- The exact CLI invocation, or enough to reconstruct it (`--config` path,
  `--output` target) — but a bare path is not self-contained if the YAML
  file itself is edited later; the *resolved* config content already lives
  in `config.json`, which `replay_command.txt` can point to rather than
  duplicate.
- `platform_version` — already captured (`metadata.json`), but confirmed
  this session to be too coarse on its own: every bundle produced by this
  repo to date (both bundles generated this session, on current `main`)
  records the identical `"0.1.0.dev0"`, unchanged across dozens of
  intervening commits. It cannot answer "did the code change."
- `git_sha` — **not captured anywhere today** (ADR 0002, Axis 4). This is
  the field that actually answers "did the code change," and it doesn't
  exist. Building `replay_command.txt` without it would ship a file whose
  entire stated purpose it can't fulfill.
- The resolved model id for model-backed runners — also not captured
  anywhere today (ADR 0002, Axis 4's `agent_id` fix). Without it,
  `replay_command.txt` can't even say what to substitute against for a
  *live* replay attempt.

**What happens when code has changed:** the comparison must not resolve to
a plain PASS or FAIL. Comparing the bundle's recorded `git_sha` (or a
"dirty working tree" flag, for uncommitted changes) against the current
checkout's `git_sha` is the mechanism; a mismatch must surface as its own,
explicit, third outcome — see Q6 — not be silently absorbed into either
"replay matched" or "replay differed." A tool that reports FAIL when code
legitimately changed on purpose is exactly as misleading as one that
reports PASS when it shouldn't have compared at all; both are the same
shape of bug this whole investigation keeps finding elsewhere.

### Q5 — What does a fixture bundle look like, how is it generated, and what stops it being regenerated to match broken code?

**The never-regenerate rule does not generalize uniformly — it has two
different justifications for two different kinds of fixture, and only one
of them is about historical provenance.**

`tests/fixtures/bundles/partial-infra-failure-2026-08-03/`'s rule exists
because its entire evidentiary value *is* being an untouched historical
capture — a real 429 against a real, at-the-time-underfunded OpenAI
account, on a specific date. Regenerating it either requires another real
failure (not reproducible on demand) or a synthesized stand-in that would
no longer be evidence of anything that actually happened — its whole point
is "this is what genuinely occurred," not "this is a representative
example."

The W12/W29 replay fixtures (`ROADMAP.md` row 29: "one passing, one
failing, one infra-error" in `tests/replay/fixtures/`) are a different
category: **deliberately-constructed golden fixtures for exercising the
replay mechanism itself**, not captures of specific incidents. For these,
demanding they never change at all would be actively wrong — when the
bundle *format* changes (ADR 0002's new fields, a `BUNDLE_FORMAT_VERSION`
bump), these fixtures legitimately need to be regenerated to stay in the
current format, which the partial-infra-failure fixture never would (its
value is being frozen, corruption and all).

**What generalizes from the precedent, stated precisely:** a fixture must
never be regenerated *merely because current code stopped matching it* —
regeneration is legitimate only as an explicit, reviewed reaction to an
intentional format change, and even then the fixture's *intended scenario*
(what state it represents — "a passing run," "an infra-failed run") must
stay the same; only its serialization updates. Silently regenerating a
fixture to make a newly-failing replay test pass again is indistinguishable
from deleting the test.

**How they should be generated:** exclusively via the **mock runner**, not
a real model-backed one. This sidesteps live-model nondeterminism entirely
for "does replay correctly detect a match" — the empirical diff above
already shows the mock runner's business-logic output (everything except
timestamps) is byte-identical run to run, which is exactly what a
hand-verifiable, deterministically-reproducible golden fixture needs.
Reserves the live-replay-drift concern (`ROADMAP.md` Risk #6) for a
separate, later, explicitly-opt-in live test — consistent with
`ARCHITECTURE.md` §7's own framing that recorded replay is CI's job and
live replay is a user's.

**Recommendation, needs sign-off:** the preserved partial-infra-failure
fixture should **not** be repurposed as W12/W29's "infra-error" fixture.
Its update discipline (frozen forever) and the golden fixtures' discipline
(updatable on format change) are different enough that forcing one artifact
to serve both jobs would blur which rule applies to it. A separate,
deliberately-constructed "infra-error" fixture — built once the
orchestrator actually produces a well-formed bundle with `INFRA_FAILURE`
tasks recorded properly, rather than today's crash-with-only-`traces.jsonl`
— is real, unstarted future work, not something to build in this session.

### Q6 — How do replay outcomes map onto Part A's states?

Two layers, checked in order, each producing its own outcome — not folded
into a single PASS/FAIL:

1. **Structural validity** (existing `LocalReplayVerifier`/
   `validate_bundle`): `valid` / `invalid` (with a specific error list).
   Maps directly onto ADR 0002's "bundle corrupt or incomplete" state. Must
   pass before a behavioral comparison is attempted at all — comparing a
   corrupt bundle's contents against a fresh run is meaningless.
2. **Behavioral comparison** (the unbuilt `ReplayRunner`), attempted only
   if (1) passed: a **three-state** outcome, not the existing two-state
   `Verdict` and not `TerminationReason` — a genuinely new, small
   vocabulary, since neither existing enum answers "did the comparison
   between two runs succeed":
   - `MATCH` — per-task `termination_reason` and evaluator `passed`
     verdicts agree between the original bundle and the replay attempt,
     tool-call sequences agree in name and order, cost is within tolerance.
   - `DIFFER` — code and model are both confirmed unchanged (`git_sha`
     matches, model substituted from the recording), but the comparison
     above doesn't hold. This is the case that's actually informative: the
     platform's own orchestration/evaluation/policy code produced a
     different result from recorded inputs it was given verbatim.
   - `CODE_CHANGED` — the bundle's recorded `git_sha` doesn't match the
     current checkout (or the tree is dirty). A `MATCH` or `DIFFER`
     conclusion under these conditions doesn't mean what it would otherwise
     mean and must be reported as its own thing, per Q4.

   `MATCH`/`DIFFER`/`CODE_CHANGED` are themselves derived from comparing
   ADR 0002's Axis 1 (`termination_reason`) and Axis 2 (`evaluated`/
   `passed`) states between two bundles — replay's outcome is not an
   independent new per-task state, it's a run-level verdict *about* a
   comparison of existing per-task states.

## Alternatives considered and rejected

- **Reuse `Verdict` (`PASS`/`FAIL`/`WARN`) for replay's outcome.** Rejected
  — `Verdict` is a policy-compliance concept (ADR 0002, Axis 5); "did
  replay match" is not a compliance question, and forcing `CODE_CHANGED`
  into `WARN` would be exactly the kind of overloading ADR 0002 already
  rejected for evaluation outcome, for the same reason.
- **Collapse `DIFFER` and `CODE_CHANGED` into one FAIL.** Rejected — this
  is precisely the failure mode the task named explicitly: *"A replay that
  silently passes against a different codebase is worse than one that
  fails,"* and the inverse is equally true — a replay that reports FAIL
  when the *only* thing that changed was the codebase, on purpose, trains
  users to ignore the tool.
- **Compare `manifest.json` checksums directly instead of semantic
  fields.** Rejected — Q3's empirical diff shows manifest checksums differ
  on every run even when nothing behaviorally meaningful changed (they
  embed timestamped files' hashes). A checksum-level comparison would fail
  on every replay attempt, informative or not.

## Scope explicitly excluded (Part C — three-filter test)

Each of these was tempting as an adjacent feature and was cut for a stated
reason, not silently dropped:

- **Provenance/lineage tracking** (a graph connecting bundles to the
  bundles they were replayed from). Library-sized; not asked for by
  DESIGN.md, which asks for each bundle to be self-contained and portable,
  not for cross-bundle relationship tracking.
- **A diff UI** rendering exactly what changed between original and replay.
  Presentation layered on top of a comparison that doesn't exist yet;
  building the renderer before the underlying `MATCH`/`DIFFER`/
  `CODE_CHANGED` comparison exists is building the view before the model.
  Also adjacent to the dashboard, an explicit DESIGN.md §8 non-goal.
- **Caching replay attempts or reconstructed model responses.** No
  demonstrated problem — recorded replay makes no network calls, so it
  should already be fast; caching would add invalidation complexity to
  solve a performance issue nobody has measured.
- **Live-replay parity testing across model versions** ("does this still
  work against the new model version"). Explicitly out of scope per
  `ARCHITECTURE.md` §7 itself: supported, not required for the CI
  regression test this ADR is designing toward.
- **`CloudBackend`-aware replay** (replaying a bundle that ran on AWS).
  Premature — `CloudBackend`/`AwsBackend` are mock-only, nothing real
  exists to replay against yet (`ARCHITECTURE.md` §2.8).
- **A generic "diff any two bundles" comparator**, not limited to
  original-vs-replay (e.g. comparing two different agent versions'
  results). Adjacent, and DESIGN.md §7 does list "regression comparisons
  between agent versions" as a real CLI feature — but `Scorer.summarize
  (scores, baseline=...)` (`core/protocols.py`) already names this as that
  protocol's job, with a `baseline` parameter for exactly this comparison.
  Replay answers "did the *same* config reproduce"; a general comparator
  answers "how do two *different* configs compare" — different questions
  that happen to need similar plumbing. Building one general tool now,
  before either concrete need is implemented, would be solving both badly
  at once.

## Consequences

- Behavioral replay cannot be built before ADR 0002's schema/orchestrator
  work lands — `git_sha` and the resolved `agent_id`/model id are
  prerequisites for `replay_command.txt` and for detecting `CODE_CHANGED`
  at all; `INFRA_FAILURE`/`evaluated` wiring is a prerequisite for a bundle
  to ever record an infra-failed task cleanly enough to build the
  "infra-error" golden fixture against.
- A new, small `ReplayOutcome`-style vocabulary (`MATCH`/`DIFFER`/
  `CODE_CHANGED`) is needed, separate from `Verdict` and
  `TerminationReason` — not designed in full here (field names, exact
  serialization) since that's implementation, but its existence and
  meaning are decided.
- The `agentevalops replay` CLI command's name/scope needs an explicit
  decision (Q1) before behavioral replay ships, or its meaning shifts under
  existing users without warning.
- Fixture strategy is two-track: the preserved incident fixture stays
  frozen forever under its existing rule; new W12/W29 golden fixtures get
  a different, format-change-triggered update discipline — both tracks
  share the "never regenerate just because code broke" principle, but nothing else.
