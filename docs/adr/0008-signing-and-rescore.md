# 0008. Signing and rescore: a detached SSH signature over the manifest, and re-deriving a bundle's verdicts from its own records

## Status

**R29 (b2) built, and R29 met — 2026-11-04** (`ad6a45c`, `16f9cfa`,
`754751d`, `45ec731`, `1d57db5`, `a70d2ed`, `d1c52d4`, `40e0157`, `6f55bbc`,
`e29b2de`, `52e47c0`): `agentevalops rescore` and the close-out. Where the
build adds to the text:
- **R1's one path, finished.** Rescore calls what the run calls. The rest of
  the per-run code moved into `orchestration/verdicts.py` (`terminal_of`,
  `judge`, `stopped`, `scored`, `close_summary`, `run_policy_spec`); the
  registry builds the evaluators and the policy checker for both
  (`build_evaluators`, `build_policy_checker`); `bundles/writer.py`'s
  `render_bundle` returns every content file's bytes, and `write()` writes
  them, so rescore renders the bundle in memory with the writer's own code
  and the bundle's own stamp (`BundleStamp.recorded`). Each move was proved
  by the suite and by a toy, a seed and a known-bad run written again,
  identical to before apart from clock and provenance.
- **R3, as built.** The outcome is decided by R3's list: each evaluation's
  `applicable`, `evaluated`, `passed`, `score`, `not_evaluated_cause` and
  `refs`; the summary's passed, failed and not-evaluated counts and pass^k
  per scenario and category; the policy verdict, its citations and refs;
  each control's state and verdicts; and both reports byte for byte, a
  differing report naming its first differing line. Every other recomputed
  file is compared in bytes and listed when it differs, and notes are
  shown; neither decides.
- **R2, as built.** The pack's two hashes are not compared only when the
  bundle records no pack (a toy run: "not compared: no pack recorded"), and
  the data hash only when neither side has one; never because the plan is
  undeclared -- an undeclared plan on a pack-backed benchmark now records
  the pack (`1d57db5`). A recorded pack the installed benchmark lacks is
  exit 2. Evaluator ids are compared for the configured kinds.
- **Exit 2 for every error after validation**, never a traceback read as
  `DIFFER`; `--json` then writes the reason. No clock is read: score events
  take the bundle's times, and a record without its time cannot be
  rescored. Bundle text is printed with control characters escaped.
- **Amended 2026-11-03 (owner): `--output DIR` beside R1's `--json FILE`.**
  `--json FILE` stays the machine-readable result; `--output DIR` writes the
  recomputed content files into a new or empty folder, for diffing by hand
  and for the limits test. It is not a bundle: no plan, no manifest. Both
  are refused inside the bundle by file identity (`st_dev`, `st_ino`, from
  the target's nearest existing ancestor up), never by spelling, and
  written through a temporary file moved into place, never through an
  existing inode.
- **Amended 2026-11-03 (owner): R6's carried runs, read from the records.**
  A task run with an `agent.terminal` event finished, and is judged again
  whatever `summary.json` says. One without was stopped: when its
  infrastructure failed, its result is made again by the run's own
  `stopped` from the reason it records, and compared; when its scenario's
  preflight or its configuration failed, it is carried as recorded and
  named. Carried runs are not counted in the verdict line.
- **Amended 2026-11-03 (owner): the stopped-run rule in `validate-bundle`.**
  A run the orchestrator stopped -- its output `infra_failure` and no
  evaluator evaluated it -- has no `agent.terminal` event; every other run's
  output is what its terminal event gives, whatever the output says. This
  closed a forgery the review reproduced: an evaluated run relabelled
  `infra_failure` in `summary.json`, its verdicts flipped in all three
  records and the manifest re-sealed, validated and rescored as `MATCH`.
- **Amended 2026-11-04 (owner): the producer's side of that rule.** A task
  run whose runner yielded its `agent.terminal` event is finished: if the
  runner's stream raises after it, the run is still harvested and judged
  like any finished run, and the late error is logged as a warning and
  changes no result (`52e47c0`). A run with no terminal event is stopped, as
  before.
- **Amended 2026-11-08 (R35): a budget stop is carried, and held to its
  records.** A task run a capped run's budget stopped before it started
  (`run_budget_exhausted`) has no `agent.terminal` event; rescore carries
  it as recorded, like a preflight stop. `validate-bundle` holds it as the
  R6 structural check holds a preflight stop: every evaluator records the
  cause, its output says `infra_failure` and records no spend, it has no
  agent trace event, tool call or end state, and `config.json` records caps
  that the spend of the task runs that finished reaches -- read from their
  outputs, each held to its `agent.terminal` event, not from
  `summary.json`'s totals (`RunLimits.reached`, the orchestrator's rule). A
  preflight stop records no spend either.
- **Amended 2026-11-09 (R42; owner, revised D-R42-2): an evaluation's refs
  decide by the bundle's era.** From R42 the `state_based` evaluator cites
  the run's `agent.terminal` event on a judged failure (ADR-0006 D17), and
  a writer records `refs_version` (1) in `metadata.json`. For a bundle that
  records it -- and for any bundle the running code wrote, so that deleting
  the marker does not change the rule -- an evaluation's refs and a control
  verdict's stay among R3's deciding fields: a forged or dropped ref is a
  `DIFFER` naming the run and the evaluator. For an older bundle the
  deciding fields are `applicable`, `evaluated`, `passed`, `score` and
  `not_evaluated_cause`; its refs -- its evaluations' and its control
  verdicts', which repeat them -- are compared and shown as "refs
  (evidence)", so it rescores `CODE_CHANGED` with its verdicts reproducing.
  The policy verdict keeps its citations and refs among its deciding
  fields. **The first version of this amendment was wrong.** It took refs
  out of the deciding set for every bundle, reasoning that a forged ref
  would still change `report.md`'s matrix and so fail R3's byte comparison
  of the reports. It does not: rescore renders `report.md` from the
  results it derives, the recorded `report.md` was rendered from the true
  results too, and a forger who edits refs only in the JSON records leaves
  the two equal. The R42 review built such bundles -- refs moved or
  emptied in the three records, or in `controls.json` alone, which no
  three-record check covers -- and each validated and rescored `MATCH`.
  The marker is a new key: a reader older than R42 refuses a bundle that
  records it (the one-way step R35 and R36 took), where the first version
  had claimed no reader break. "The running code wrote the bundle" is read
  from recorded bytes, by R2's comparison: every provenance item reads
  `same` -- `metadata.json`'s `package_sha256` against the hash of the
  running package's files, the plan's pack `scenarios_sha256` and
  `data_sha256` against the installed pack's, `controls.json`'s catalogue
  hashes against the running catalogue's, and each evaluation's
  `evaluator_id` against the configured evaluator's; an item not compared
  (a toy run's pack) counts as the same. The same comparison makes the
  outcome `CODE_CHANGED`, so a refs difference is either deciding or the
  outcome is `CODE_CHANGED`: no refs forgery reads `MATCH`. A forger who
  deletes the marker and also edits one of those recorded items (the era
  downgrade) demotes the bundle to pre-R42 trust: its refs are evidence,
  and it rescores `CODE_CHANGED` with its verdicts reproducing. That is
  accepted because it is visible. `rescore` prints a `Refs:` line saying
  what decided and why -- `deciding (refs_version 1)`, `evidence (pre-R42
  bundle)`, or, for a bundle the running code wrote that records no marker,
  which only an edit makes, `deciding (no refs_version, but the running
  code wrote this bundle)` -- and `validate-bundle` prints the era the
  bundle records, so a supposedly new bundle claiming to be old reads as
  one. At pre-R42 trust the defence against tampering is the signature
  (S1-S4, verified by V1), as for any bundle written before R42; C1 does
  not cover `controls.json`.
- **Reviewed before it landed.** Four independent reviewers read the
  rescore slice through four lenses; one-line mutations of its rules showed
  ten of twelve surviving the first tests. Every confirmed finding was
  fixed with a test, and undoing each fix failed its test. Speculative ones
  are in ROADMAP §0 as open questions.
- **Golden fixtures (T1).** Rescored under changed code they say
  `CODE_CHANGED`, the verdicts reproducing (10 evaluations over 4 task
  runs), and both reports differing at their date line; their tests assert
  the verdict line only.

**R29 (b1) built — 2026-11-03** (`d1b632f`, `2384320`, `5308cdd`, `40da4b7`,
`09bc5ae`, `b491ab4`): the groundwork for rescore, as library code, and the
signing docs. Where the build adds to the text:
- **R1's one path.** The rules that turn evaluation results into verdicts
  and a summary are public in `orchestration/verdicts.py`, moved unchanged
  (a toy, a seed and a known-bad run gave the same evaluations, summary,
  policy verdict, traces and controls before and after), with the
  terminal event's output (`output_from_terminal`). A bundle's records load
  back into their dataclasses through `bundles/serializers.py`
  (`from_jsonable` and named loaders), next to the serialiser that writes
  them. `replay/rebuild.py` rebuilds a run's benchmark, tasks and scenario
  descriptors through the registry, sharing `registry.build_benchmark`,
  `plans.benchmark_descriptors` and `reports.markdown.scenario_info` with
  `run`; it checks that the tasks are the plan's selection and the seeds
  the ones the plan's seed derives, and raises `RebuildError`, which (b2)
  turns into exit 2. No import-linter contract changed.
- **R6 gets a structural check (beside C1).** `validate-bundle` holds what
  is carried to the records: a run stopped by its scenario's failed
  preflight has that failure in `summary.json` and no agent trace event,
  tool call or end state; a run not evaluated for the preflight or the
  infrastructure is not evaluated by any evaluator and its output says
  `infra_failure`; an evaluated run has its end state; every other run's
  output equals `output_from_terminal` of its `agent.terminal` event. A
  run not evaluated for `configuration` is not checked beyond that last
  rule: the cause is shared by a runner's configuration error and an
  evaluator the run does not configure, and the records do not tell them
  apart.
- **`mock_fail` is not an infrastructure failure.** It ends the agent with
  `agent_error`, which is evaluated and fails, so it carries nothing. The
  carried-results tests raise `InfrastructureError` from a test runner
  registered as `mock`, and make a preflight fail with an edited pack
  registered under its own name, so the run rebuilds with the same
  `data_sha256`.
- **Golden fixtures (F).** `tests/fixtures/bundles/seed-narrow-0.6` and
  `seed-narrow-known-bad-0.6`, written at `40da4b7`, one scenario per
  category (TE3, TC1, PC5, RB5); 202 KB and 208 KB, of which
  `controls.json` -- every pack scenario, however narrow the run -- is
  108 KB. Under 0003 Q5 they are regenerated only for an intended format
  change, so they keep the date line they were written with (below).
- **Decision 9's line is relabelled.** It held the run's `created_at`
  under "Generated:"; both reports now carry one line, ``Run created:
  <created_at> (`created_at` in `metadata.json`)``, written once in
  `reports/markdown.py`. A rescore of the golden fixtures runs under
  changed code (`CODE_CHANGED`) and will find `report.md` and
  `evidence_report.md` differing by that line.
- **E's signature paragraph is in the README**, with the `sign` command,
  an allowed-signers example and the `ssh-agent` route for a key with a
  passphrase. The `MATCH` paragraph, and both paragraphs in the reports,
  wait for rescore. (b2) is open.

**R29 (a) built — 2026-11-02** (`199ddee`, `111e0f0`, `230f51b`, `a733ebc`).
Where the build adds to the text: the version check reads `ssh -V` beside
`ssh-keygen` (no version flag on `ssh-keygen` itself); every `ssh-keygen`
call runs detached from any terminal with askpass off and a 30 s timeout,
because a passphrase-protected key would otherwise read the manifest on
stdin as its passphrase -- it now fails at once with a hint to use
`ssh-agent`; the signature is written to a partial file and renamed into
place; `--require-signature` without `--allowed-signers` cannot judge
(exit 2); a direct `report.md` render without a timestamp says "Generated:
not recorded". (b) is open.

**Accepted — 2026-11-02 (owner).** Decisions 1 to 8 (Open questions, "For
the owner") accepted as recommended, with two additions, and three new
decisions:
- **To 3 (`CODE_CHANGED`, R2):** the `MATCH` claims paragraph names what is
  not compared -- dependency versions, the Python version, and a plugin
  evaluator whose code changed without a new `evaluator_id`.
- **To 8 (C1):** the check also covers the `evaluator.score` events in
  `traces.jsonl`, so all three records of each evaluation result must agree.
  It catches an inconsistent edit, not a careful forger who edits all three
  and re-seals the manifest: only a signature, or `rescore`, catches that.
- **9. `report.md` is dated by the bundle, not the clock.** Its
  `Generated:` line is the run's `created_at` from `metadata.json`, as the
  evidence report's header is (ADR-0007 D7). `rescore` then compares
  `report.md` byte for byte, with no exception; R3 below and ADR-0003 Q3's
  exclusion of the line no longer apply to bundles written from R29.
- **10. Signing tests never skip in CI.** Locally they skip, with a reason
  naming what is missing, when `ssh-keygen` is absent or older than 8.2. In
  CI the workflow sets `AGENTEVALOPS_REQUIRE_SSH_KEYGEN=1`, which turns that
  skip into a failure.
- **11. The allowlist holds identifiers only.** The first word of this
  project's label for 8.3.2 changes, so the borderline entry `3 2 generative
  ai detect` leaves `scripts/ip_allowlist.yaml`.

R29 builds in two sessions: **(a)** the small fixes (decisions 9 and 11),
the structural check (C1) and signing (S1-S4, V1, V2, T1); **(b)** rescore
(R1-R6) and the close-out, where the claims paragraphs (E) go into the
README.

Proposed — 2026-11-02. Documentation only: no code accompanies this ADR.
Two spikes ran in `/tmp` against a real format 0.6 bundle and the installed
package, never the repository (see Context). Eight decisions are put to the
owner (Open questions, "For the owner"); each carries a recommendation, and
the Decision section is written as if every recommendation were accepted.
Line numbers are at `0c91dee`.

Builds ROADMAP R29 on what format 0.6 already records (ADR-0007 D1, D8, D9,
D15). Takes as settled, and does not reopen: ADR-0003 Q6's two-layer
comparison and its three-state vocabulary (`MATCH`, `DIFFER`,
`CODE_CHANGED`, not `Verdict`); 0003's rejection of collapsing `DIFFER` into
`CODE_CHANGED` and of comparing manifest checksums; 0003's rule that the
vocabulary is built only with something that produces it; 0003 Q5's
golden-fixture discipline (generated with the mock runner, regenerated only
for an intended format change); ADR-0005 D7's layer order (structural,
evaluation, behavioural; each runs only if the one before passed) and its
rule that model-judged results are recorded, not re-derived, and are
excluded from a `MATCH`; ADR-0007 D1's choice of what is signed (a detached
signature over `manifest.json`, never listed in the manifest, never
checksummed).

The R29 brief names ADR-0007 "A5, F1, F2"; no such labels exist in ADR-0007
or elsewhere in the repository. The decisions they appear to mean -- D1 (the
signature file), D8 and D9 (provenance), D15 (rescore groundwork) and the
2026-11-02 build notes -- are the ones this ADR builds on.

## Summary

| # | Decision | Code it touches | Recommendation |
|---|---|---|---|
| S1 | `agentevalops sign --bundle DIR --key PATH [--force]` | new `bundles/signing.py`; `cli.py` | a separate command, not a `run` flag |
| S2 | The key is always an explicit path; a `.pub` path means "sign with the agent" | `bundles/signing.py` | no default, never `~/.ssh` |
| S3 | Namespace `agentevalops-bundle`, a constant | `bundles/constants.py` | fixed, unversioned |
| S4 | `sign` validates first and refuses an invalid bundle; refuses an existing `.sig` without `--force` | `bundles/signing.py`, `bundles/validator.py` | exit 1, never prompts |
| V1 | `validate-bundle --allowed-signers FILE`: verified, not verified, bad signature | `bundles/validator.py:579-583`; `cli.py:499-511` | bad or missing signature with the flag is an error |
| V2 | `gate --require-signature --allowed-signers FILE` | `plans/gate.py`; `cli.py:512` | opt-in; an unverified bundle cannot be judged (exit 2) |
| R1 | `agentevalops rescore --bundle DIR [--json FILE]`, read-only | new `replay/rescore.py`; `cli.py` | never writes into the bundle |
| R2 | What `CODE_CHANGED` compares, and how it combines with the verdicts | `replay/rescore.py` | five provenance items; outcome plus a verdict line |
| R3 | What `MATCH` compares | `replay/rescore.py` | evaluations, summary counts and pass^k, policy, coverage |
| R4 | Exit codes | `cli.py` | 0 `MATCH`, 1 `DIFFER`, 2 cannot rescore, 3 `CODE_CHANGED` |
| R5 | Bundles before 0.6 | `replay/rescore.py` | cannot rescore (exit 2) |
| R6 | What is carried, not recomputed | `replay/rescore.py` | preflight, not-evaluated runs, agent outputs |
| C1 | A new structural check: `evaluations.json` equals the summary's per-run evaluations | `replay/local.py` | add in R29 |
| T1 | Tests: throwaway keys, ssh-keygen availability, determinism | `tests/` | keys per test in `tmp_path`; CI must not skip |
| F1 | Format | — | none: format stays 0.6 |

## Context

### What 0.6 gives R29

- `manifest.json.sig` is a recognised optional file: never listed in the
  manifest, never checksummed, reported as "present but not verified"
  (`bundles/validator.py:579-583`; ADR-0007 D1).
- Provenance: `metadata.json` records `package_sha256` (every `.py` and
  `.yaml` file under the package, CRLF read as LF; `bundles/provenance.py:66`),
  `git_sha` of the package's own tree, and the invocation's git; the plan
  records the pack's `scenarios_sha256` and `data_sha256`; `controls.json`
  records the catalogue's file hashes; each evaluation records its
  `evaluator_id` (`<kind>-v<N>`); `config.json` records the configured
  evaluator kinds and the runner (ADR-0007 D4, D8, D9).
- One reader for every file (`bundles/reader.py:40`, `LoadedBundle`), the
  plan read back into a `RunPlan` (`plans/io.py:50`, `plan_from_document`),
  coverage and the evidence report as pure functions
  (`plans/coverage.py:216`, `reports/evidence.py:395`).

### Spike 1: ssh-keygen signatures over a real 0.6 bundle

A throwaway Ed25519 key with no passphrase, made in `/tmp` and deleted
after; `SSH_AUTH_SOCK` unset; every path explicit, so nothing under `~/.ssh`
was read and no agent was asked.

- Local: `OpenSSH_10.5p1, LibreSSL 3.3.6` (macOS, `/usr/bin/ssh-keygen`).
  CI: GitHub's `ubuntu-latest` (Ubuntu 24.04, image 20261026) lists
  `openssh-client 1:9.6p1-3ubuntu13.20`. Minimum per the OpenSSH release
  notes: `-Y sign`, `-Y verify` and `-Y check-novalidate` from 8.1,
  `-Y find-principals` from 8.2. The implementation probes the capability
  (T1), not a version string.
- `ssh-keygen -Y sign -f KEY -n agentevalops-bundle manifest.json` wrote
  `manifest.json.sig` (an armoured `SSH SIGNATURE`). `-Y verify -f
  allowed_signers -I <principal> -n agentevalops-bundle -s
  manifest.json.sig < manifest.json` printed `Good "agentevalops-bundle"
  signature for <principal> with ED25519 key SHA256:…`, exit 0.
  `-Y find-principals -f allowed_signers -s manifest.json.sig` printed the
  principal, exit 0.
- Failures, each exit 255: the wrong namespace ("namespace does not
  match"); a one-byte edit of the manifest ("incorrect signature"); a
  signature by a key not in the allowed-signers file (find-principals: "No
  principal matched").
- A one-byte edit of a content file leaves the signature valid -- it signs
  the manifest -- and `validate-bundle` fails it on the checksum. The two
  together are the guarantee.
- **Signing over an existing `.sig` prompts "Overwrite (y/n)?" and blocks
  on stdin.** The implementation must never invoke it that way: sign from
  stdin to stdout, into a temporary file, and decide about an existing
  signature itself (S4).
- **Two signatures of the same manifest by the same key differ byte for
  byte.** Tests verify; they never compare signature bytes (T1).

### Spike 2: a rough rescore

A script in `/tmp` read a fresh seed bundle through `BundleReader`, rebuilt
the tasks from the installed pack narrowed to the plan's selection, re-ran
the configured evaluators over each task run's own `tool_calls.jsonl` and
`end_states.jsonl` records and agent trace events, and recomputed the
summary, the policy verdict, `controls.json`, `evidence_report.md` and
`report.md` with the installed code.

- Seed run (`known_good`) and known-bad run: all 72 evaluations identical
  in every field; the summary's counts, pass^k per scenario and category
  and score result identical; `policy.json` identical; `controls.json`
  identical; `evidence_report.md` byte-identical; `report.md` identical but
  its `Generated:` line (ADR-0003 Q3). The package, pack and catalogue
  hashes all equal the running code's.
- One recorded verdict flipped in `evaluations.json` and the manifest
  re-sealed: `validate-bundle` passes (only a signature catches a re-seal);
  the rescore names the one entry that differs (`TE2`, `deterministic-v1`,
  `passed` and `score`). The summary still agrees with the recomputation,
  because it was not edited -- so `evaluations.json` and `summary.json`
  now disagree, and no structural check sees it (C1).
- The 0.5 fixture (`tests/fixtures/bundles/seed-narrow-0.5`): its six
  evaluations, summary and policy verdict recompute identically, but it
  records no `package_sha256`, its plan (version 1) no `data_sha256`, it has
  no `controls.json`, and its `config.json` does not name the configured
  evaluators (they had to be inferred from `evaluations.json`). Whether its
  code was the running code cannot be decided (R5).
- Not recomputed from the records, by nature: each scenario's known-good
  preflight (it ran a script in a fresh environment whose records the
  bundle does not keep); a task run that was never evaluated (an
  infrastructure, configuration or preflight-defect cause -- there is
  nothing to evaluate); the agent's own output and termination reason (the
  behavioural layer, ADR-0005 D7). None needs a format change (F1).

## Decision

### A. Signing

**S1. `agentevalops sign --bundle DIR --key PATH [--force]`.** A separate
command, run after `run`. It calls `ssh-keygen -Y sign -n
agentevalops-bundle -f PATH` as a subprocess with `manifest.json`'s bytes on
stdin, writes stdout to a temporary file beside the bundle and renames it to
`manifest.json.sig`. No runtime dependency is added (R29's exit criterion):
`ssh-keygen` is found on `PATH` and probed (T1). Signing inside `run` is
left for later: a run that signs needs a key at run time, which the CLI does
not have today.

**S2. The key is an explicit path, always.** `--key` names a private key, or
a `.pub` file, which `ssh-keygen` takes to mean "the matching key is in the
agent". There is no default and no search: the command never looks in
`~/.ssh`. A missing or unreadable key is exit 1 with the path named.

**S3. Namespace `agentevalops-bundle`.** A constant beside
`MANIFEST_SIGNATURE_FILENAME`. Not versioned: the manifest carries the
format version, and the signature covers the manifest.

**S4. `sign` validates first, and never overwrites without `--force`.** It
runs `validate_bundle` and refuses a bundle with any error (exit 1, the
errors printed): a signature over an invalid bundle would vouch for it. An
existing `manifest.json.sig` is refused (exit 1) unless `--force`, which
replaces it atomically. `ssh-keygen`'s own prompt is never reached (spike
1).

### B. Verifying

**V1. `validate-bundle --allowed-signers FILE`.** With the flag, the
validator calls `ssh-keygen -Y find-principals` to name the signer, then
`-Y verify -I <principal>` for each candidate, with the namespace, over
`manifest.json`'s bytes.

| Bundle | Without `--allowed-signers` | With `--allowed-signers` |
|---|---|---|
| no `.sig` | nothing said | error: no signature to verify -- exit 1 |
| `.sig`, good, signer listed | warning: present, not verified (today) | `Signature: verified -- <principal>, <key fingerprint>`; exit unchanged |
| `.sig`, bad (tampered manifest, wrong namespace, signer not listed) | warning: present, not verified | error naming the cause -- exit 1 |
| `ssh-keygen` missing or without `-Y` | as above | error: cannot verify -- exit 1 |

Verification adds to the structural checks; it never replaces them, so a
verified signature over a bundle whose checksums fail still fails.

**V2. `gate --require-signature --allowed-signers FILE`.** Opt-in. When
required and not verified, the gate cannot judge (exit 2), like a tampered
plan today. Without the flag the gate ignores signatures, as `controls.json`
is outside the gate (ADR-0007 D14).

### C. Rescore

**R1. `agentevalops rescore --bundle DIR [--json FILE]`, read-only.** The
structural layer runs first (`validate_bundle`; ADR-0003 Q6, ADR-0005 D7):
an invalid bundle cannot be rescored. Then the evaluation layer: tasks
rebuilt from the benchmark the bundle names (`config.json`'s
`benchmark_id` and options, through the registry) narrowed to the plan's
selection; the configured evaluators re-run over each task run's recorded
tool calls, end state and agent events (never its `evaluator.score`
events); the summary, the policy verdict and `controls.json` recomputed. It
writes nothing into the bundle; `--json FILE` writes the comparison
elsewhere. Lives in `replay/` (the comparison layers' package) as
`replay/rescore.py`.

**R2. `CODE_CHANGED` compares five things, and never hides the verdicts.**
The bundle's code identity against the running code's:
1. `metadata.json`'s `package_sha256` against `package_sha256()`;
2. the plan's `pack.scenarios_sha256` against the installed pack's;
3. the plan's `pack.data_sha256` against the installed pack's
   (`DataHashed`; skipped when either side has none);
4. `controls.json`'s catalogue file hashes against the shipped catalogue's;
5. every recorded `evaluator_id` against the running evaluator's id for
   that kind.

The first covers the shipped pack and catalogue too (they are `.yaml`
files in the package); 2 to 4 also catch a pack installed from another
distribution, and 5 names which evaluator moved. Any difference makes the
outcome `CODE_CHANGED`, listing every item that differs. Following
ADR-0003 Q6, a comparison under changed code is reported as its own thing,
not hidden: the output says `CODE_CHANGED`, then whether the verdicts
reproduced under the new code and which did not.

**R3. `MATCH` compares the derived results, not bytes.** Equal, for every
task run the bundle evaluated: each evaluation's `evaluated`, `passed`,
`score`, `applicable`, `not_evaluated_cause` and `refs` (notes are shown when
they differ but do not decide); the summary's passed, failed and
not-evaluated counts, pass^k per scenario and per category; the policy
verdict and its refs; every control's state and verdicts in
`controls.json`; and, byte for byte, `evidence_report.md` and `report.md`
(decision 9: both are dated by the bundle, so a re-render with the same
code gives the same bytes). `MATCH`: code unchanged and all equal. `DIFFER`: code
unchanged and anything unequal.

**R4. Exit codes.** 0 `MATCH`, 1 `DIFFER`, 2 cannot rescore (an invalid
bundle, a format before 0.6, a benchmark or pack that is not installed, a
selected scenario missing from the installed pack), 3 `CODE_CHANGED`. A
distinct code for `CODE_CHANGED`, so a script cannot read "the code moved"
as "the verdicts are wrong" or as "all is well" (ADR-0003, Alternatives).

**R5. Bundles before 0.6 cannot be rescored** (exit 2, "predates
package_sha256; rescore needs format 0.6"). A 0.5 bundle's verdicts do
recompute (spike 2), but whether its code was the running code cannot be
decided, and its evaluator list would have to be inferred.

**R6. Carried, not recomputed, and said so.** Each scenario's preflight
result, every task run that was never evaluated (its recorded cause), and
the agent's outputs and termination reasons are taken as recorded and
listed in the output under "carried". A model-judged result, once one
exists, is carried and excluded from a `MATCH` (ADR-0005 D7).

### D. A structural check the spike found missing

**C1. The three records of each evaluation result must agree** (amended at
acceptance): the entry in `evaluations.json`, the same result in
`summary.json`'s task results, and its `evaluator.score` event in
`traces.jsonl`. A cross-file check in `LocalReplayVerifier`
(`replay/local.py`) for 0.6 bundles: today an edited verdict with the
manifest re-sealed leaves the files disagreeing and passes
`validate-bundle`. It catches an inconsistent edit, not a careful forger
who edits all three and re-seals: only a signature or `rescore` does. Not
a format change: all three already hold the same results.

### E. Claims, for the README and the reports

**A verified signature** says that `manifest.json` is byte for byte the file
the holder of a key in your allowed-signers file signed, for this tool's
namespace, and -- with the checksums `validate-bundle` checks -- that every
file the manifest lists is unchanged since then. It does not say who ran the
agent or that the run happened as recorded, that the code or model was what
the bundle names, that the key was not lost or stolen, or anything about how
well the system behaves; it is only as trustworthy as the allowed-signers
file you chose to trust.

**A `MATCH`** says that the code now installed -- the same code, by
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

### F. Testing and format

**T1. Tests.** Keys are made per test in `tmp_path` (`ssh-keygen -t
ed25519 -N ''`), with `SSH_AUTH_SOCK` unset and `HOME` pointed at
`tmp_path` for every subprocess, so no test can touch a real key or agent.
Signing tests skip locally, with a reason naming what is missing, when
`ssh-keygen` is absent or older than 8.2; when the workflow sets
`AGENTEVALOPS_REQUIRE_SSH_KEYGEN=1`, as CI does, the same condition fails
them instead (decision 10), so CI never passes by skipping.
Signatures are never compared as bytes (spike 1); rescore output has no
clock, so it is compared exactly. The two golden fixtures R29 names (a
passing and a failing seed run, both narrowed, under `tests/fixtures/bundles/`
-- path corrected 2026-11-04; this said `tests/replay/fixtures/` -- generated
with the mock runner per ADR-0003 Q5) record a `package_sha256`
that the next code commit changes: their tests assert the verdict line
("verdicts reproduce") under `CODE_CHANGED`, not a `MATCH`. `MATCH` itself
is tested on a bundle written in the test.

**F1. No format change.** Everything signing and rescore need is in 0.6:
the signature file is recognised, and the provenance, the plan, the records
and the configured evaluators are all recorded. Signing and rescoring write
nothing into a bundle. `BUNDLE_FORMAT_VERSION` stays `"0.6"`.

## Alternatives considered and rejected

- **Signing inside `run`** (S1): needs a key at run time; a later option.
- **A default key** (`~/.ssh/id_ed25519`) (S2): signs with whatever is
  there, silently.
- **A versioned or configurable namespace** (S3): one more thing to agree
  on, with nothing it protects that the manifest's version does not.
- **Signing an invalid bundle with a warning** (S4): the signature would
  vouch for it.
- **`--allowed-signers` with no signature as a warning** (V1): a verifier
  that asked for a signature and got none has not verified anything.
- **`CODE_CHANGED` hiding the verdict comparison** (R2): the most useful
  thing after a code change is which verdicts moved; ADR-0003 Q6 asks for
  it to be reported as its own thing, not dropped.
- **`package_sha256` alone for `CODE_CHANGED`** (R2): misses a pack or
  catalogue installed from another distribution.
- **Comparing `evidence_report.md` and `report.md` bytes** (R3): the first
  is derived from `controls.json`; the second carries a clock.
- **`CODE_CHANGED` as exit 1 or 2** (R4): reads as a failure or as "could
  not judge".
- **Best-effort rescore of 0.5 bundles as `CODE_CHANGED`** (R5): every 0.5
  bundle would report a code change it cannot show.
- **Re-running the preflight in rescore** (R6): it executes the scenario,
  which is behaviour, not evaluation.

## Open questions

### For the owner (decide before R29 starts)

1. **Rescore exit codes** (R4): 0 `MATCH`, 1 `DIFFER`, 2 cannot rescore, 3
   `CODE_CHANGED`. Recommended.
2. **`CODE_CHANGED` with a verdict line** (R2): the outcome stays
   `CODE_CHANGED`, followed by whether the verdicts reproduced and which did
   not. Recommended over hiding the comparison.
3. **What `CODE_CHANGED` compares** (R2): the five items, any difference
   counts. Recommended over `package_sha256` alone.
4. **Bundles before 0.6** (R5): cannot be rescored (exit 2). Recommended
   over a best-effort `CODE_CHANGED`.
5. **`validate-bundle --allowed-signers`** (V1): no signature, a bad
   signature, or no usable `ssh-keygen` is an error (exit 1). Recommended.
6. **The gate and signatures** (V2): opt-in `--require-signature`; an
   unverified bundle cannot be judged (exit 2). Recommended.
7. **R29's exit criteria, amended**: "a dirty or different checkout yields
   `CODE_CHANGED`" becomes "a one-byte change to a `.py` or `.yaml` file of
   the package yields `CODE_CHANGED`" -- since 0.6 the code is identified
   by `package_sha256`, so a dirty tree that changes no package file is the
   same code; and the golden fixtures assert the verdict line, not `MATCH`
   (T1). Recommended.
8. **The new structural check C1** (`evaluations.json` against the
   summary): built in R29. Recommended.

### Later

- **Signing at run time** (S1), once the CLI has a key story for runs.
- **A rescore of the behavioural layer** (`ReplayRunner`, ADR-0003): out of
  scope, as ADR-0005 deferred it.
- **Key rotation and revocation**: the allowed-signers file's
  `valid-after`/`valid-before` options cover it; documented in R31's
  `SECURITY.md`, not built here.

## Consequences

By session and slice, for R29 (Jan 4–10; amended at acceptance):

**(a)** 1. The 8.3.2 label (decision 11), catalogue and alignment doc
together; the allowlist entry dropped. 2. `report.md` dated by the bundle
(decision 9). 3. **C1**, fail-first on a flipped verdict in each of the
three records. 4. **Signing and verifying** (S1-S4, V1, V2):
`bundles/signing.py`, the `sign` command, the namespace constant,
`validate-bundle --allowed-signers`, `gate --require-signature`; tests with
throwaway keys (T1); the CI workflow's `AGENTEVALOPS_REQUIRE_SSH_KEYGEN=1`.

**(b)** 5. **Rescore** (R1-R6): `replay/rescore.py`, the `rescore` command
and the `MATCH`/`DIFFER`/`CODE_CHANGED` vocabulary, built with its
producer (ADR-0003); the two golden fixtures. 6. **Close-out**: the claims
paragraphs (E) in the README and both reports' "What this report does not
say", ROADMAP R29, CHANGELOG.

No import-linter contract changes: `replay/` and `bundles/` are in the
middle tier; `replay/rescore.py` imports `bundles`, `plans`, `catalogue`,
`evaluators` through the registry, and `core`. No runtime dependency is
added. The format stays 0.6.
