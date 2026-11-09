# Triage: a reviewer's verdicts on a bundle's task runs

`agentevalops triage` walks a deterministic sample of a bundle's task runs
in the terminal and asks a reviewer for a verdict on each -- pass, fail or
defer -- and a free-text note. `--summarize` turns the notes into counts.
No model is called anywhere: deciding what the notes mean stays a human
step. (ROADMAP R38.)

```bash
agentevalops triage --bundle runs/seed
agentevalops triage --bundle runs/seed --summarize
```

## The loop it serves

1. **A note.** The reviewer reads a run -- the task, the calls with their
   faults and injections, the end state -- and records a verdict and why.
2. **A failure mode.** Notes that say the same thing are one failure mode.
   Grouping them is the reviewer's judgement, not a tool's.
3. **A new seed scenario.** Each failure mode the harness missed becomes a
   scenario in the domain pack, with a known-good and a known-bad
   trajectory that pin it.
4. **A regression, forever.** The scenario runs in every later run, and
   the plan's thresholds and the gate hold it there.

## Which runs, in what order

- **Every failed task run** -- evaluated, not passed.
- **A share of the passing ones: 0.2 unless `--passing-share` sets it**,
  rounded up, so a single passing run is still sampled. The random passing
  slice is never skipped, because it is where an evaluator's blind spots
  are: a run the harness passed that a reviewer would fail is invisible
  if only the failures are read.
- **Not-evaluated runs** are counted in the session's header and walked
  only with `--include-not-evaluated`; their cause is not a stage a
  reviewer can judge.
- **One shuffle of the whole frame**, so failures and passes interleave
  and the harness's verdict cannot be read off a run's position. The seed
  is the run's own, from the bundle's `config.json`, so two reviewers of
  one bundle walk it in one order; `--seed` overrides it.

## One screen per run

The verdict comes first: the evaluators' findings stay hidden until the
reviewer has answered (`--show-findings-first` shows them before).

```
[3/9] TC2 rep 0 · tool_calling · scripted run
Task: ...
Calls:
  1. case_lookup(case_id=C4)  ok
  2. send_notice(case_id=C4)  approved, FAULT timeout
Injections: none · End state: decisions=1, notices=1, referrals=0, exports=0

Your verdict [p]ass [f]ail [d]efer, [v] full records, [b] back, [q] quit: f
Note: notice sent before the decision was recorded

Harness: FAILED (policy: fail; tool_use: pass)
  policy: ...
You and the harness agree. [enter] next
```

`v` shows every argument and result in full; `b` steps back to the run
before; `q` quits, and a later session resumes at the first run not yet
annotated. The task text and category are rebuilt from the bundle's own
config and plan; a run whose benchmark is not installed here is still
walked without them, and the command says why.

## The sidecar, beside the bundle

The verdicts go to `<bundle>.triage.jsonl`, next to the bundle directory.
The bundle itself is never written: every file, the manifest and the
signature are the same after a session as before -- a test holds this --
and validate-bundle, the gate and rescore read only the bundle's own
files, so the sidecar never feeds a verdict. The sidecar is a log: one
JSON line per annotation (`task_id`, `repetition`, `verdict`, `note`,
`annotated_at`, the bundle's `run_id`, and `annotator` only when
`--annotator` gives one), appended, never edited. A new verdict on a run
appends a line that supersedes the old one; readers take the last per run.

## --summarize

The headline is the reviewer's verdict against the harness's, pass and
fail only:

- **Evaluator blind spots** -- the reviewer failed what the harness passed.
- **Over-strict evaluators** -- the reviewer passed what the harness
  failed.

A deferral means "could not judge", not a disagreement, so deferrals are
counted beside the table, as are runs the harness did not evaluate. Then
the verdicts by category, and the notes grouped by verdict -- the raw
material for step 2 of the loop.
