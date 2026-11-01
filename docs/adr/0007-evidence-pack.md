# 0007. The evidence pack, bundle format 0.6: every bundle pre-registers a plan, records what code and data produced it, and shows which testing-framework checks its executed scenarios evidence

## Status

**R28 (b1) built — 2026-11-02** (`fb7f657`, `77df2ef`, `8e57cde`,
`334f1c0`; ROADMAP R28 (b1) met). Library code only; no bundle changed.
Where the build adds to the text and the decisions below:
- **D8:** `bundles/provenance.py` holds `package_sha256`, `git_state` (the
  old writer probe, moved; the writer delegates to it unchanged), the
  editable-install probe and the invocation probe, each with an
  injectable runner. A test holds every tracked file under
  `src/agentevalops` to `.py` or `.yaml`. The Package workflow installs
  the built wheel and checks that it hashes the same as the source tree.
- **D11, decision 3:** the catalogue marks the five run-level checks with
  `run_level: true` (direct or partial only, checked by the loader); a test
  holds the seed pack's `every_scenario_cites` to the same set.
- **D15:** `BundleReader` loads the optional files by default;
  `validate_bundle` reads with `optional_files=False` until (b2), so a
  malformed optional file cannot stop its cross-file checks.
- **D5, decision 1:** `control_coverage` takes the run's end-state records
  (`end_states=`) in write order, so a ref's index is its line.
- **Decision 6, open:** a test that no cell of an undeclared run's report
  is empty conflicts with D7's blank Implemented and Elaboration cells. The
  renderer's test exempts those two columns; the owner should confirm.

**Amended — 2026-11-02 (owner decisions before R28 (b)).** Eight
decisions. They change D5, D6, D7, D13 and the build order; nothing else.
1. **`end_states` on each verdict (D5): accepted.** Every `controls.json`
   verdict carries `end_states`, always present (an empty list when no
   state-based evaluation judged an end state), as refs in the bundle's
   existing ref format (`stream`, `task_id`, `repetition`, `index`, with
   stream `end_states` and the line's index in `end_states.jsonl`). In 0.6
   the validator checks that every such ref points at a real line of
   `end_states.jsonl`.
2. **"undeclared" becomes "not in pack" (D6).** A control no scenario in
   the pack cites is `not_in_pack` in `controls.json` and "not in pack" in
   the report. Reason: an undeclared run and an undeclared control would
   read the same in one report. The report carries a legend saying what
   each of the five states means. An undeclared run is still an
   undeclared run (D2, decision 6).
3. **Compact cells in `evidence_report.md` (D7).** `controls.json` keeps
   the full lists; only the Markdown is shorter.
   - The five run-level checks (3.6.1, 3.7.1, 3.12.1, 4.10.1, 9.9.2) say
     "every scenario in the plan", then their artefact pointers. Which
     checks read this way comes from the catalogue entry, not from
     counting scenarios.
   - A Scenarios table lists each pack scenario once: a selected one with
     its pass^k, or its cause when pass^k is null; an excluded one with
     the plan's reason.
   - No counts or percentages in these cells (ADR-0005 D2: no
     aggregate).
4. **The scripted-run sentence once (D7, review item E5).** It appears
   once, in the header. Each evidence cell carries a short "(scripted)"
   tag instead, so a row copied out of the report keeps the warning.
5. **Scenario descriptors (D5, review item A9).** `controls.json`'s
   `scenarios` block lists every scenario in the pack, each marked
   selected or excluded, with the plan's reason where it has one. The CLI
   builds the descriptors from the whole pack, before it narrows the
   benchmark. In 0.6 the validator checks that the block's selected ids
   are the plan's `selected`.
6. **The undeclared run (D2, D7, review item A1).** `report.md` and
   `evidence_report.md` each say one plain sentence, for example "No test
   plan was declared before this run, so nothing here was registered in
   advance.", and show nothing else from the plan. A test checks that
   neither report contains "None", "null" or an empty table cell for such
   a run.
7. **The writer's evidence inputs (D13).** `BundleWriter.write()` takes
   one keyword argument for the evidence inputs: the catalogue and the
   pack's scenario descriptors. The CLI always passes it, and an R28 (b2)
   test proves it does for a narrowed seed run. When a caller leaves it
   out, the writer uses the shipped catalogue and the run's own tasks, so
   the bump does not touch every test that calls the writer.
8. **R28 (b) runs as two sessions.** (b1) builds the library pieces with
   no bundle change; (b2) is the bump in one commit, then the close-out.

**R28 (a) built — 2026-11-01** (`9efca72`, `54a68fb`, `1eadf87`,
`4b591f8`; ROADMAP R28 (a) met). Where the build departs from or adds to
the text below:
- **D10:** the class counts are declared in the data file
  (`process_counts`) and the loader holds the entries to them, so a new
  edition is a data change. The shipped numbers are pinned by a test. The
  0.6 file list an `artefact:` pointer must name lives in the catalogue
  (`BUNDLE_0_6_FILES`, since the catalogue imports only `core`), and a test
  holds it to `bundles.constants`. `Catalogue.files` records each data
  file's sha256 for D5's `catalogue.files`.
- **D10, IP:** against all three PDFs, 1.7.1's label shared a five-word run
  with MGF-Agentic (spike 2 had checked the framework alone). It is now
  "Public statement of how the organisation governs AI", in the catalogue
  and `docs/imda-alignment.md` §4.
- **D11:** the benchmark's guard also rejects a control id the catalogue
  does not know.
- **D5:** each verdict also lists `end_states`, the repetitions whose end
  state a state-based evaluation judged, so the evidence cell's
  `end_states PC7/0` renders from `controls.json` alone. A selected
  scenario with no pass^k is "not run"; a citing scenario neither selected
  nor excluded is "not requested by the run config".

**Accepted — 2026-11-01 (owner), with the amendment below.** ADR-0005's
and ADR-0006's Status lines carry their pointers; `DESIGN.md` §7 and
`ARCHITECTURE.md` §1, §3 and §7 carry the edits listed under "Required
`DESIGN.md` and `ARCHITECTURE.md` edits"; `ROADMAP.md` R28 is rewritten to
the amended split. One docs commit.

**Amended before acceptance — 2026-11-01 (owner): R28's two sessions are
reordered so the format bump lands in one slice, as 0.5 did (ADR-0006
D21).** The text as proposed put the version string, the required plan,
provenance, `EVALUATOR_SCORE` and `config.json`'s new fields in session
(a), and `controls.json` and `evidence_report.md` in (b). That splits one
bump across two sessions: a bundle written between them would say "0.6"
and still lack two files every 0.6 bundle requires. Landing those pieces
under 0.5 instead does not work either. Re-pointing `git_sha` at the
package's tree changes what an existing field means, and retiring
`POLICY_VERDICT` removes a value the enum's own rule ties to a version
bump (`core/schemas.py:7-8`). Both need the bump. So:
- **(a) Data and library only, with no bundle change:** the catalogue's
  112 process entries and their loader checks; the seed scenarios citing
  testing-framework ids, with the 14-check equality test and the
  benchmark's guard; the alignment-doc sync test (D12, moved from (b));
  the IP script over every field, against all three PDFs; and the coverage
  computation and the evidence-report renderer as library code, tested on
  synthetic inputs and not yet called by the writer.
- **(b) The 0.6 bump as one slice:** the version and the required set;
  plan version 2; `package_sha256`, the re-pointed `git_sha` and the
  invocation fields; `runner_id` and `mock_trajectory` in `config.json`;
  `EVALUATOR_SCORE` events, with `POLICY_VERDICT` retired;
  `manifest.json.sig` recognised; the reader's 0.6 fields;
  `replay_command.txt`'s new lines; the drift tests made deterministic; and
  the writer producing `controls.json` and `evidence_report.md`. Then the
  close-out.

No decision changes. The Summary's "Built in" column, D1's build-sequence
bullet, D8's drift-test bullet and Consequences "By row" are rewritten to
match. A bundle written during (a) is a format 0.5 bundle with the same
file list as before. The only differences a reader can see are new values
in existing fields: the plan's `scenarios_sha256`, the `control_ids` in
task metadata, and report text.

Proposed — 2026-11-01. Documentation only: no code changes accompany this
ADR. The owner decided seven questions put to them on 2026-11-01 (D2, D3,
D8, D11, D6, D7, D12) and accepted the engineering calls with two notes
(coverage lives beside the plan; the scripted-run sentence reads
`runner_id`). Two spikes ran in the session scratchpad against copies of
the code, never the repository (see Context). Line numbers are at
`5aad47c`.

Settles every format question ROADMAP R28 needs, and carries the four items
parked for it: what `git_sha` records (2026-10-31 review finding 11, D8);
the source versions a bundle must pin because catalogue IDs carry no version
(R22, D9); `POLICY_VERDICT`'s run-level `step_index` (W12a assessment, D3);
and the plan file's format, lifted from ADR-0006's R27 notes (D2).

ADR-0005 D6 named the pack's files; ADR-0006 D21 moved its format number
from 0.5 to 0.6 and built format 0.5 in R24. This ADR fixes 0.6.

## Summary

| D | Decision | Code it changes (today) | Built in |
|---|---|---|---|
| D1 | Format 0.6 lands once; "0.5" legacy; three more required files; `manifest.json.sig` optional and recognised | `bundles/constants.py:32`, `:44-46`, `:102-105`; `bundles/manifest.py:103-112`; `bundles/validator.py:170-175`, `:231-283`; `bundles/reader.py:146-153` | R28 (b) |
| D2 | Every bundle has a plan; an undeclared run writes a minimal one; plan version "2" | `core/schemas.py:483-517`; `plans/builder.py:216-264`; `plans/io.py:17-29`; `bundles/writer.py:159-175` | R28 (b) |
| D3 | `EVALUATOR_SCORE` wired per evaluation result; `POLICY_VERDICT` retired; the allowlist holds `AGENT_OBSERVATION` only | `core/schemas.py:38-54`; `orchestration/local.py:542-565`; `policy/basic_checker.py:88`; `replay/local.py:75-106`; `tests/test_enum_construction_coverage.py:77-95` | R28 (b) |
| D4 | `config.json` records `runner_id`, `mock_trajectory`, `evaluators` | `bundles/writer.py:273-294`; `cli.py:240-254`; `reports/markdown.py:269` | R28 (b) |
| D5 | `controls.json`: sources, catalogue hashes, scenario descriptors, every control with its coverage | new `plans/coverage.py`; `cli.py:611-630` | R28 (a) the computation, as library code; R28 (b) the file |
| D6 | Coverage: five states over the plan's selected scenarios; each evidencing scenario's pass^k listed; no per-control aggregate | new `plans/coverage.py`; `core/schemas.py:783-816` | R28 (a), as library code; R28 (b) wired |
| D7 | `evidence_report.md`: all 112 checks by principle, pointers only when a citing scenario ran, answers blank, deterministic, the scripted-run sentence | new `reports/evidence.py`; `reports/markdown.py:68-85`, `:385-449` | R28 (a) the renderer, as library code; R28 (b) the file |
| D8 | `git_sha` means the package's tree from 0.6; `package_sha256`; invocation fields; drift compares the hash first, by format version | `bundles/writer.py:91-139`, `:200`, `:352-413`; `bundles/validator.py:483-507`; `core/schemas.py:859-911` | R28 (b) |
| D9 | One home per pin: package hash in `metadata.json`, pack hash in `test_plan.json`, catalogue sources and file hashes in `controls.json`; `DataHashed` | `plans/builder.py:205-213`, `:244-264`; `core/protocols.py:194-210`; `domain_packs/seed/pack.py:1000-1006` | R28 (a) the testing-framework pin; R28 (b) the rest |
| D10 | The 112 process entries as catalogue data: schema, validation, source id `testing-framework`, principle labels, evidence pointers; the IP script over every field and three PDFs | `catalogue/loader.py:31`, `:47-75`, `:114-151`, `:188-245`; new `catalogue/data/testing-framework-2025.yaml`; `scripts/check_catalogue_ip.py:7-9`, `:64` | R28 (a) |
| D11 | Scenarios cite testing-framework ids directly; five whole-run checks cited by every scenario through a pack-level list; the cited set equals the direct and partial set | `domain_packs/seed/data/scenarios.yaml`; `domain_packs/seed/pack.py:823-830`; `benchmarks/agentic_baseline/adapter.py:93-100` | R28 (a) |
| D12 | `docs/imda-alignment.md` §4 is checked against the catalogue by a test | new `tests/test_imda_alignment_doc.py` | R28 (a) |
| D13 | No new package: `plans/coverage.py` and `reports/evidence.py`; the writer calls both; contracts unchanged | `pyproject.toml:213-222`; `plans/__init__.py:11`; `ARCHITECTURE.md` §1 | R28 (a) the two modules; R28 (b) the writer's calls |
| D14 | The gate does not read `controls.json` | `plans/gate.py:83-110`; `cli.py:476-563` | — |
| D15 | Re-scoring groundwork: what 0.6 records, what `rescore` recomputes, no new vocabulary; the reader loads the 0.6 files | `bundles/reader.py:35-124`; `bundles/validator.py:466-476` | R28 (b) records; R29 builds |

## Context

ADR-0005 D6 made the result bundle the evidence pack and named its files:
`test_plan.json`, `controls.json`, per-task end states, `evidence_report.md`
and `manifest.json.sig`, with the exact formats left to this ADR. Since
then R24 built format 0.5 (ADR-0006 D21: `tool_calls.jsonl`,
`end_states.jsonl`, task and repetition on every event, typed refs), R27
built the plan and the gate (`test_plan.json`, optional under 0.5, hashed
before the first task; `agentevalops gate --bundle` fails closed), and
R22 built catalogue v0 (two pinned sources, eleven controls). The
alignment review (`docs/imda-alignment.md`, 2026-10-11) classified all 112
process checks of the testing framework against v0.5 and said that from
R28 the classifications live in the catalogue.

### Verified findings (at `5aad47c`)

1. **`git_sha` describes the caller's working directory, not the
   package** (review finding 11). `_capture_git_provenance()` runs `git`
   in the process's working directory (`bundles/writer.py:91-139`, called
   at `:200`; `bundles/validator.py:484`). A partner running from their
   own repository records their own commit. An installed wheel records
   `"unavailable"`.
2. **A package content hash cannot come from install metadata.** Spike 1:
   for this editable install `importlib.metadata` reports version 0.1.0
   (stale dist-info) and its `RECORD` lists no package files;
   `direct_url.json` says `{"dir_info": {"editable": true}, "url": "file:///…/AgentEvalOps"}`.
   Hashing the files on disk under the package directory covers 74 `.py`
   and 7 `.yaml` files in milliseconds.
3. **`runner_id` is recorded nowhere in a bundle.** `config.json` holds
   `agent_id`, a string the YAML author chooses (`bundles/writer.py:282-294`);
   the report decides a run was scripted by the substring `"mock"` in it
   (`reports/markdown.py:269`).
4. **An evaluator event touches three cross-file checks.** The sum of
   task event counts must equal the trace length (`replay/local.py:75-86`),
   the per-task-run counts must agree (`:96-106`), and the policy trace
   ceiling counts every event (`policy/basic_checker.py:88`;
   `configs/toy_trace_limit.yaml` uses it).
5. **The plan's `scenarios_sha256` does not cover every pack fact that
   decides a verdict.** It hashes `Scenario` objects
   (`plans/builder.py:205-213`); the `policy` evaluator reads roles,
   export domains and notice fields from `task.metadata["policy"]`, which
   the pack builds from `policy.yaml` and `cases.yaml`
   (`evaluators/policy_compliance.py:36-54`).
6. **Scenario control ids reach task metadata but not the bundle.**
   `benchmarks/agentic_baseline/adapter.py:98` puts `control_ids` in task
   metadata; `cli._task_info` lifts category, outcome group and variant
   only (`cli.py:611-630`), and ADR-0006's R26 note promised the bundle
   would record them at 0.6.
7. **The loader's wording guard is stricter than ADR-0005 D5.**
   `_check_wording` rejects "AI Verify" in a source's title and publisher
   (`catalogue/loader.py:188-197`, called at `:208-210`). D5 forbids it in
   package, command and artefact names. The testing framework's own title
   and publisher carry the words, so the source cannot be pinned under the
   guard as written.
8. **Finding 2 of the review is closed.** ADR-0006 D20, built in `36fec5b`:
   `ScoreResult.failed_tasks` means evaluated and failed, with
   `not_evaluated_tasks` beside it (`core/schemas.py:663-668`);
   `SimpleScorer.summarize()` meets `Scorer` (`scorers/simple.py:37-44`,
   `core/protocols.py:286-301`); the orchestrator calls it through the
   protocol (`orchestration/local.py:377-378`). Nothing is left for R28.

### The source, pinned

The Foundation's page (`https://aiverifyfoundation.sg/what-is-ai-verify/`)
links `https://go.gov.sg/aivtf-pdf`, which resolves to
`https://file.go.gov.sg/aivtf-pdf.pdf`: 118 pages, 3,518,624 bytes, PDF
created 2025-05-21, released 2025-05-29. Its sha256,
`363e59d140896b2ab75dffe508b03a8339e164fcfdab192b61862fb86c282379`, equals
the hash in `docs/imda-alignment.md`'s header, so the alignment review was
done against this document. Retrieved 2026-11-01 to
`~/Documents/aeo-sources/aivtf-2025.pdf` (never committed).

Spike 2, the five-word check (`scripts/check_catalogue_ip.py --extra`)
against that PDF: all 112 labels in `docs/imda-alignment.md` §4 are clean,
and so are the 55 "what v0.5 contributes" cells. The full name of
principle 11 shares three five-word runs with the source; principles 1 to
10 are clean. D10 draws the consequence.

The Foundation's process-checks tool imports and exports Excel only, with
Outcome, Process, Evidence, Implemented (Yes / No / N/A) and Elaboration
per process; its column layout is not documented (Open questions).

## Decision

### A. Format 0.6

**D1. Format 0.6 lands once, in R28; "0.5" joins the legacy set; the
required set grows by three files; `manifest.json.sig` is optional and
recognised.**
- `BUNDLE_FORMAT_VERSION` (`bundles/constants.py:32`) becomes `"0.6"`;
  `"0.5"` joins `LEGACY_BUNDLE_FORMAT_VERSIONS` (`:44-46`). Bundles at
  0.1–0.5 still validate and read.
- `required_files_for_version("0.6")` (`:102-126`) is the 0.5 set plus
  `test_plan.json`, `controls.json` and `evidence_report.md`. The manifest
  checksums them like any content file (`bundles/manifest.py:103-112`)
  and its `required_files` lists them (`:118`); `TEST_PLAN_FILENAME` stops
  being a special case there.
- `manifest.json.sig` is named now (`MANIFEST_SIGNATURE_FILENAME`) as an
  optional 0.6 file: a detached signature over `manifest.json`, so the
  manifest never lists it and nothing checksums it. When it is present,
  `validate-bundle` warns "signature present, not verified; pass
  `--allowed-signers` (R29)". R29's signing therefore adds no bump.
- A validation sequence for 0.6 adds: `controls.json` parses and its
  `plan_sha256` equals `metadata.json`'s `test_plan_sha256`;
  `evidence_report.md` is present; every scenario `controls.json` lists is
  in the plan's `selected` set; the number of `evaluator.score` events
  equals the number of entries in `evaluations.json` (D3).
- **Build sequence** (amended at acceptance, see Status). The bump lands
  in one slice of session (b): the version string, `"0.5"` in the legacy
  set and all three new required files together, as 0.5 did (ADR-0006
  D21). Session (a) changes no bundle's shape and no field's meaning, so a
  bundle written between the sessions is a complete 0.5 bundle.

**D2. Every 0.6 bundle has a plan. A run without a declaration writes a
minimal plan that says so. The plan's format is version "2".**
- The format ADR-0006's R27 notes recorded is lifted here as plan version
  "1": `plan_version`, `run_id`, `config_name`, `created_at`,
  `platform_version`, `system` (the declaration as loaded), `system_path`,
  `system_sha256`, `pack` (`pack_id`, `pack_version`, `scenarios_sha256`),
  `repetitions`, `seed`, `seeds`, `evaluators`, `requested`, `selected`,
  `excluded`, `thresholds`, `uncovered_tools`.
- Version "2" adds `declared: bool` and `pack.data_sha256` (D9), and lets
  `system`, `system_path`, `system_sha256` and `pack` be `null`. Making a
  field nullable is a shape change, so the number moves; `gate` accepts
  both versions and the only reader of the number
  (`tests/test_test_plan.py:480`) updates.
- **The undeclared plan** (`plans/builder.py`, a second constructor beside
  `build_plan`): `declared: false`, `system: null`, `pack: null`,
  `requested: null`, `selected` = the task ids the run will execute, in
  benchmark order; `excluded: []`; `thresholds: []`; `seeds` derived as
  ADR-0006 D6 says; `evaluators` as configured; `uncovered_tools: []`.
  The CLI builds and registers it before the first task runs, exactly as
  for a declared run (`cli.py:172-185`), so `metadata.test_plan_sha256`
  is set on every bundle and `validate-bundle` holds the file to it. A
  library caller of `BundleWriter.write()` that passes no plan gets one
  built from the run config and the summary's task ids at write time.
- `gate` on an undeclared plan says it cannot judge, as today
  (`plans/gate.py:87-92`). The evidence report for such a run states that
  nothing was declared and that no control is evidenced (D7).
- `RunPlan` (`core/schemas.py:483-517`): `system: SystemDeclaration | None`,
  `system_path: str`, `pack_id`, `pack_version`, `scenarios_sha256`,
  `data_sha256: str | None`, `declared: bool`. `to_document`
  (`plans/io.py:17-29`) lifts the pack fields as before, `null` when
  undeclared.

**D3. `EVALUATOR_SCORE` is wired per evaluation result; `POLICY_VERDICT` is
retired; the allowlist keeps exactly one member, with its producer named.**
- After the evaluators run in `_run_task` (`orchestration/local.py:542-553`)
  and before `TaskRunResult` is built (`:555-565`), the orchestrator appends
  one `TraceEvent` of kind `evaluator.score` to the task run's `events` for
  **every** `EvaluationResult` the run carries, in `evaluations` order: the
  real results, the synthesised `configuration` ones for a declared kind the
  run does not configure, and the not-evaluated ones `_not_evaluated_result`
  builds. The event is stamped with the task and repetition, its
  `step_index` continues the task's own stream, and its payload is
  `evaluator_id`, `evaluator_kind`, `applicable`, `evaluated`, `passed`,
  `score`, `not_evaluated_cause` and `refs` (as `Ref` objects). `notes`
  stay in `evaluations.json`. The preflight (`:569-630`) emits nothing: it
  checks the scenario, not the agent.
- `TaskRunResult.event_count` counts these events, so the cross-file sums
  (`replay/local.py:75-106`) hold unchanged, and a 0.6 bundle gains the
  check that the number of `evaluator.score` events equals the number of
  evaluations.
- `core/schemas.py` gains `HARNESS_EVENT_KINDS = frozenset({TraceEventKind.EVALUATOR_SCORE})`.
  `BasicPolicyChecker`'s trace ceiling (`policy/basic_checker.py:88`)
  counts events whose kind is not in it: `max_trace_events` is a ceiling
  on the agent's activity, not on what the harness writes about it.
- `TraceEventKind.POLICY_VERDICT` (`core/schemas.py:53`) is removed under
  this bump, as the enum's own rule allows (`:7-8`, "new values require a
  schema_version bump"; removal likewise). No bundle ever carried one. A
  policy verdict is one judgement over the whole run; it has no task to be
  addressed by, no `step_index` with a meaning, and no consumer that
  `policy.json` does not already serve (`rescore` reads that file, D15).
  `bundles/constants.py`'s version comment and `ARCHITECTURE.md` §3's
  closed set record the removal.
- `tests/test_enum_construction_coverage.py`'s allowlist (`:77-95`) keeps
  `AGENT_OBSERVATION` alone, with its anchor unchanged: ROADMAP row 14 via
  `agents/langgraph_runner.py`'s docstring, which defers file and sandbox
  observation tooling to a code-editing benchmark. That member has a
  documented producer; it stays. `EVALUATOR_SCORE` leaves by being
  constructed; `POLICY_VERDICT` leaves by deletion. `test_allowlist_has_no_stale_entries`
  keeps holding.

**D4. `config.json` records the runner, the scripted mode and the
evaluators.**
- `BundleWriter.write()` (`bundles/writer.py:159-175`) takes `runner_id`,
  `mock_trajectory` (`null` unless the mock runner played a script) and
  `evaluators` (the kinds the run configured), and `_write_config`
  (`:273-294`) writes them beside `benchmark_options`. The CLI passes them
  from `LoadedRunConfig` (`cli.py:240-254`).
- Everything that says "scripted" reads `runner_id` and `mock_trajectory`
  from the bundle, never the `"mock"` substring (`reports/markdown.py:269`
  changes with the report).

**D5. `controls.json` is one JSON object: the sources the run resolved ids
against, the catalogue's own hashes, every selected scenario's descriptors,
and every catalogue control with its coverage.**
- `controls_version: "1"`, `run_id`, `plan_sha256` (the registered hash,
  so the file is tied to the plan it was computed from), `declared`.
- `sources`: every catalogue source, each with `id`, `title`, `publisher`,
  `version`, `published`, `url`, `sha256`, `retrieved_on` — the pin, copied
  from the catalogue so a reader of the bundle needs no catalogue (D9).
- `catalogue`: `{"files": [{"name", "sha256"}]}` over the catalogue's data
  files as shipped in the package that wrote the bundle (D9).
- `scenarios`: keyed by scenario id, for every scenario the plan selected:
  `category`, `outcome_group`, `variant`, `control_ids`, `k`, `passed`,
  `failed`, `not_evaluated`, `pass_k` (`true`, `false` or `null`),
  `not_evaluated_cause`. This is the join table coverage is computed from,
  and where ADR-0006's R26 note put the descriptors; `summary.json` and
  the plan do not change for it. `cli._task_info` (`cli.py:611-630`) lifts
  `control_ids` from task metadata beside the three fields it already
  lifts.
- `controls`: one entry per catalogue control, in source order then
  locator order: `id`, `source`, `kind`, `label`, `state` (D6),
  `declared_by` (every scenario in the pack that cites it), `excluded`
  (scenario id → the plan's exclusion reason), `not_evaluated` (scenario
  id → cause), and `verdicts`: for each selected citing scenario, its
  `pass_k`, `k`, `passed`, `failed`, `not_evaluated`, `cause`, and `refs`
  — the union of the `Ref`s its evaluations cite across repetitions. For a
  process entry, `classification`, `principle` and `evidence` are repeated
  from the catalogue so the report renders from this file.
- No timestamp of its own: `controls.json` is a pure function of the plan,
  the summary, the scenario descriptors and the catalogue, so two renders
  from one run are identical and `rescore` can regenerate it.
- An undeclared run writes `scenarios: {}` and every control with state
  `undeclared`.

**D6. Coverage is computed from executed, evaluated scenarios only, over
the plan's selection, in five states; each evidencing scenario's pass^k is
listed; nothing is aggregated per control.**
- For a control, let *citing* be the pack's scenarios that cite it and
  *selected* those among them the plan selected. Then: **covered** — every
  selected citing scenario has a pass^k verdict (`true` or `false`);
  **partly_covered** — some do and some are not evaluated; **uncovered** —
  selected citing scenarios exist and none has a verdict, each with its
  cause; **not_in_plan** — citing scenarios exist but the plan excluded
  every one, each with its reason; **undeclared** — no scenario in this
  pack cites it. A scenario that ran but was not evaluated never counts as
  covering (ADR-0005 D5).
- A failing scenario still covers: coverage says the control was tested,
  the verdict beside it says how it went. The two are never folded into
  one word.
- There is no per-control pass or fail. A control with three citing
  scenarios shows three pass^k verdicts; summing them would be the
  composite ADR-0005 D2 forbids and would read as the compliance verdict
  D10 rules out.
- The computation is `plans/coverage.py` (D13): a join over the plan's
  selection, the summary's `pass_k`, the scenario descriptors and the
  catalogue — core and catalogue imports only.

**D7. `evidence_report.md` lists all 112 checks by principle, points at
evidence only where a citing scenario executed, leaves the organisation's
answers blank, and is deterministic.**
- **Shape.** A header: run id, the run's `created_at` copied from
  `metadata.json`, system id or "no declaration", pack id and version,
  `runner_id`, and the scripted-run sentence when `runner_id` is `mock`.
  Then "What this report is and is not": evidence pointers, never answers;
  no compliance or certification claim; identifiers and this project's
  labels, not the framework's text; the Implemented and Elaboration
  columns are the organisation's. Then the plan repeated — intended use,
  out-of-scope uses, business metrics (recorded, not evaluated),
  thresholds with their sources and the run's standing against each, the
  selection counts — in the same words `report.md`'s plan section uses
  (`reports/markdown.py:385-449`, shared, not duplicated). Then one table
  per principle, numbered 1 to 11 under this project's short labels (D10):
  Check, Scope, Label, Class, Evidence from this run, Implemented,
  Elaboration — the last two empty in every row. Then "MGF-Agentic v1.5"
  and "Starter Kit v1.0": each control's label, state and verdicts. Then
  "Sources", the pins from `controls.json`. Then "What this report does
  not say": no composite, no verdict, the domain limit (the tested pack,
  not the organisation's system), and the scripted caveat.
- **The evidence cell.** For a direct or partial check: the state, then
  each selected citing scenario as `PC7 passed 1/1 (tool_calls PC7/0/2,
  PC7/0/4; end_states PC7/0)`, refs written `stream task/repetition/index`.
  For a supporting check: the artefact pointer from the catalogue, such as
  `test_plan.json → system.intended_use` or `metadata.json → git_sha,
  package_sha256`. For a later check: the version that adds evidence. For
  an organisational check: "requires the organisation's evidence". For a
  traditional-only check: "applies only if the system also contains a
  traditional model". A check whose citing scenarios did not run shows its
  state and no pointer.
- **The scripted-run sentence.** When `runner_id` is `mock`, the header and
  every evidence pointer carry: "This run played scripted trajectories;
  it is not evidence of an agent's behaviour until a model-backed run
  (ROADMAP R30)." Read from `config.json` (D4).
- **Deterministic.** The renderer is a pure function of the catalogue, the
  plan, `controls.json`, the summary, the run config and `created_at`. No
  render time appears anywhere. The writer builds `ResultBundleMetadata`
  once and hands its `created_at` to the renderer. Two renders from one
  bundle are byte-identical, and a test proves it. (`report.md` keeps its
  `Generated:` line; ADR-0003 Q3 already excludes it from comparison.)
- **Wording.** The rendered text contains no "compliant" or "certified"
  anywhere, and "AI Verify" only inside the Sources section's title and
  publisher fields, which cite the publisher's own names, with the
  standing sentence that the framework's publishers have not reviewed or
  endorsed this reading. No artefact file name contains any of the three.
- **An undeclared run.** The header says no system was declared; every
  table row shows `undeclared` and no pointer; the closing section says
  the run evidences no control.
- The W28 scope cut (ROADMAP §6) still applies to this file: the tables
  without the prose.

### B. Provenance

**D8. From format 0.6, `git_sha` means the package's own source tree;
`package_sha256` identifies the code on any install; the caller's
directory is kept as information; drift compares the hash first, and by
format version.**
- **`git_sha` and `git_dirty`** (`core/schemas.py:884-907`) are captured in
  the directory `importlib.metadata`'s `direct_url.json` names, and only
  when it says the install is editable. For any other install — a wheel,
  a copy — they are `"unavailable"` and `None`, even if `git` would find a
  repository around `site-packages` (a virtual environment inside some
  other project's checkout must not inherit that project's commit). An
  editable install from a checkout therefore records the checkout the
  code came from, wherever the caller runs.
- **`package_sha256`**: over every regular file under the installed
  package directory whose suffix is `.py` or `.yaml` (the catalogue and
  the seed pack's data decide verdicts), skipping `__pycache__`, sorted by
  POSIX relative path; for each file the path bytes, a NUL, the content
  with every CRLF turned into LF, a NUL; the sha256 hex digest of the
  whole. **Line endings are normalised**, so a CRLF checkout of the same
  source hashes the same as an LF one, by construction; a test proves it
  on a copied file. The hash is computed once per write, next to the git
  capture (`bundles/writer.py:200`).
- **`invocation_git_sha` and `invocation_git_dirty`**: what today's fields
  record — the process's working directory — kept as information for a
  partner running from their own repository (R31's dry run). Never
  compared.
- **The drift warning** (`bundles/validator.py:483-507`) follows the
  bundle's declared format version, which the validator already peeks
  (`:170`). For a 0.1–0.5 bundle it compares the recorded `git_sha` with
  the current invocation sha, exactly as today, because that is what those
  bundles recorded. For a 0.6 bundle it compares `package_sha256` with the
  running package's hash: equal means no drift; different means the code
  or its data changed; absent means inconclusive. The package-tree git
  comparison follows as a second line when both sides are determinate.
  The comparison is determinate on every install, so the drift tests stop
  depending on this checkout's git state — the 2026-10-10 finding in
  ROADMAP §0 closes in session (b), with the 0.5 branch tested against an
  injected provenance function or a temporary repository.
- `replay_command.txt` (`bundles/writer.py:352-413`) gains lines for
  `package_sha256`, `invocation_git_sha`, `invocation_git_dirty` and
  `test_plan_sha256`, and its comment says `git_sha` is the package's tree
  from 0.6.
- `metadata.json` carries the three new fields (additive). The meaning of
  `git_sha` on an older bundle is unchanged; the reader of a 0.6 bundle
  learns the new meaning from the format version.

**D9. Every version a verdict rests on is pinned once, in one file; the
pack's data gets a hash through an optional protocol; the testing framework
is pinned as a catalogue source.**
- `metadata.json`: `package_sha256` (D8) and `platform_version` (already).
- `test_plan.json`, `pack` block: `pack_id`, `pack_version`,
  `scenarios_sha256` (already) and `data_sha256` — the sha256 of the
  pack's data files, so an edit to `policy.yaml` or `cases.yaml` shows
  (finding 5). A pack provides it through an optional `DataHashed`
  protocol in `core/protocols.py` (`data_sha256: str`), checked with
  `isinstance` like `Thresholded` and `Perturbing`; the seed pack hashes
  the canonical JSON of the five files it loads
  (`domain_packs/seed/pack.py:1000-1006`); a pack without it records
  `null`.
- `controls.json`: every catalogue source's pin and the catalogue data
  files' sha256 (D5). Catalogue ids carry no version (R22), so the bundle
  names the versions it resolved them against, here.
- `evaluations.json`: `evaluator_id` already carries the version as its
  suffix (`deterministic-v1`, `tool-use-v1`, `state-based-v1`,
  `policy-v1`). The convention is now stated: `<kind>-v<N>`, and `N` moves
  when an evaluator's verdict rule changes. `test_plan.json` records the
  kinds configured (already).
- The testing framework's pin, from Context: id `testing-framework`
  (D10), title "AI Verify Testing Framework for Traditional and Generative
  AI", publisher "AI Verify Foundation, Singapore", version "2025",
  published 2025-05-29, URL `https://file.go.gov.sg/aivtf-pdf.pdf`, sha256
  `363e59d1…282379`, retrieved 2026-11-01, posture
  `identifiers-and-own-labels`.

### C. The catalogue

**D10. The 112 process entries are catalogue data under a third source,
with a classification, a scope and evidence pointers; the loader checks the
counts; the IP script checks every field against three PDFs.**
- New data file `catalogue/data/testing-framework-2025.yaml`, source id
  `testing-framework` — the name the code and documents already use (an
  acronym of the framework's title would put "AI Verify" into every
  control id and artefact, against ADR-0005 D5's spirit).
- A new control kind, `process` (`catalogue/loader.py:31`), with fields:
  `id` (`testing-framework:8.3.2`), `locator`, `principle` (1–11),
  `scope` (`both`, `generative`, `traditional`), `technical_test` (the five
  the framework tags), `label` (this project's words, from
  `docs/imda-alignment.md` §4), `classification` (`direct`, `partial`,
  `supporting`, `later`, `organisational`, `traditional-only`),
  `available_from` (`v0.6` or `v0.7`, with `later`), `evidence` (a list:
  `scenarios`, or `artefact:<file>#<field>` such as
  `artefact:test_plan.json#system.intended_use`), `rows` (ROADMAP rows, as
  documentation), `links`.
- The file carries a `principles` map, number → this project's short
  label, and the loader exposes it as `Catalogue.principles`. The labels
  for 1–10 are the alignment doc's; 11 is "Inclusive growth and
  well-being", because the framework's full name of that principle fails
  the five-word rule (Context). The evidence report's headings come from
  this map.
- Validation (`catalogue/loader.py:200-245`): every `process` entry has
  every field above; the source has exactly 112 of them; the class counts
  are 2, 12, 32, 9, 49 and 8; `available_from` is set exactly when the
  class is `later`; every `artefact:` pointer names a 0.6 bundle file;
  `evidence` is non-empty exactly when the class is `direct`, `partial`
  or `supporting`.
- **The wording guard** (`:188-197`) keeps rejecting "AI Verify" in ids,
  labels, principle labels and anything that names an artefact, and keeps
  rejecting "compliant" and "certified" everywhere. A source's `title` and
  `publisher` are exempt: they cite the publisher's own names, and the
  framework cannot be pinned otherwise (finding 7). The exemption is
  narrow and tested.
- `scripts/check_catalogue_ip.py` checks every text field of every control
  and every principle label, not labels alone (`:64`), against three PDFs;
  its docstring names the third (`:7-9`). It stays a dev script: the PDFs
  are never committed, so CI cannot run it.

**D11. Scenarios cite testing-framework ids directly. Five whole-run checks
are cited by every scenario through a pack-level list. The cited set equals
the catalogue's direct and partial set, and a scenario can never cite
anything else.**
- The seed pack's scenarios add process ids to `control_ids`
  (`domain_packs/seed/data/scenarios.yaml`): PC7 and PC8 cite 8.3.2; PC6,
  TE3 and TE4 cite 1.1.3 and 1.1.4; RB4 and RB5 cite 4.1.1; RB6 and RB7
  cite 7.2.1; TE5, TE6, PC2 and PC3 cite 10.3.2; RB8 cites 6.1.1; PC1–PC5
  and RB1–RB3 cite 4.3.1; PC4, PC5, RB4 and RB5 cite 5.9.1.
- Five checks are properties of every executed scenario rather than of
  one: 3.6.1 (each scenario is repeated k times against a threshold),
  3.7.1 (each category's pass^k is a named technical metric), 3.12.1
  (each scenario is part of the curated set), 4.10.1 (each scenario has
  seeded ground truth and a known-good preflight), 9.9.2 (each scenario's
  failure on a third-party model is the evidence). The scenarios file
  gains a top-level `every_scenario_cites` list holding those five ids;
  the pack loader merges it into every scenario's `control_ids` at load,
  after the scenario's own, without duplicates. Their evidence cells
  carry the k, the thresholds and the scripted-run sentence, so k = 1 or a
  scripted run cannot read as more than it is.
- **The guard, for any pack.** The `agentic-baseline` benchmark resolves
  every scenario's control ids against the catalogue when it is built
  (today the seed loader does, `domain_packs/seed/pack.py:823-830`) and
  rejects a `process` entry whose class is not `direct` or `partial`: a
  scenario cannot cite an organisational, supporting, later or
  traditional-only check. Supporting checks are evidenced by artefacts, not
  scenarios, and the catalogue's `evidence` field says so.
- **The equality test, for the seed pack.** The set of testing-framework
  ids cited across `SeedDomainPack().scenarios()` equals the set of
  `direct` and `partial` entries in the catalogue — the 14 checks in
  `docs/imda-alignment.md` §3 — so every check the alignment review said a
  run evidences is cited by at least one scenario, and nothing else is.

**D12. `docs/imda-alignment.md` stays hand-written and is checked against
the catalogue by a test.**
- `tests/test_imda_alignment_doc.py` parses §4's eleven tables and compares
  each row's id, applies-to, label, class and rows with the catalogue's
  entry; parses §3 and checks its 14 ids are exactly the direct and
  partial set; recomputes the counts table from the catalogue. Any
  difference fails, in either direction, so §12's rule — change the
  catalogue first and the document in the same commit — is enforced rather
  than remembered.
- Generating §4 from the catalogue was rejected: the document's value is
  its reasoning around the tables, and a generated section inside a
  hand-written document gives it two provenances.

### D. Where the code lives

**D13. No new package. Coverage is `plans/coverage.py`; the report is
`reports/evidence.py`; the writer calls both; no import-linter contract
changes.**
- Coverage is a join over the plan's selection, not a score, so it sits
  beside the plan. The layers contract (`pyproject.toml:213-222`) ranks
  the middle tier with `:`, so `plans` may import `catalogue` (the low
  tier) and `bundles` may import `plans`; `reports` may import `catalogue`
  as any adapter may. `plans/__init__.py:11` and `ARCHITECTURE.md` §1
  change from "core and bundles only" to "core, bundles and catalogue".
- `BundleWriter.write()` takes a `catalogue` (the CLI passes the one it
  loads; a library caller gets the shipped one), computes coverage, writes
  `controls.json`, renders `evidence_report.md`, and only then the
  manifest — so a bundle written by the writer is always a complete 0.6
  bundle, whoever calls it.
- ADR-0005's working package names `controls/` and `evidence/` are not
  used: two modules do the work, the README's structure diagram and its
  drift guard (`tests/test_readme_drift.py`) stay as they are, and no
  tenth contract is needed.

### E. The gate and re-scoring

**D14. The gate does not read `controls.json`.** `agentevalops gate` stays
a judgement of category pass^k against pre-registered thresholds
(`plans/gate.py:83-110`). Controls are evidence of what was tested; an
uncovered control is a fact for the report, not a release failure —
ADR-0005 D10's scope rule.

**D15. Re-scoring groundwork: what 0.6 records so that R29 can rebuild and
compare verdicts; no vocabulary is built here.**
- R29's `rescore` recomputes `evaluations.json` by rebuilding each task
  from the pack at the recorded `pack_version` and `data_sha256`, under the
  plan's seeds, and running the configured evaluators over
  `tool_calls.jsonl`, `end_states.jsonl` and `traces.jsonl`; it recomputes
  `policy.json` from the same records and `config.json`'s policy spec; it
  compares with the recorded files. The run's identity it needs is all in
  0.6: `package_sha256` (D8), the pack hashes (D9), the catalogue sources
  and file hashes (D9), the evaluator ids and the configured kinds (D9),
  `runner_id` (D4), the seeds (plan).
- `MATCH`, `DIFFER` and `CODE_CHANGED` stay unbuilt until `rescore` exists
  to produce them (ADR-0003's rule). This ADR only says what each would
  compare: `DIFFER` is a recorded verdict the current evaluators do not
  reproduce from the same records; `CODE_CHANGED` is a `package_sha256`
  or pack hash that differs from the running one; `MATCH` is neither.
- `LoadedBundle` (`bundles/reader.py:35-71`) gains `test_plan`, `controls`
  (`None` when absent) and `tool_calls`, `end_states` (empty lists when
  absent), so `rescore`, the validator's cross-file checks
  (`bundles/validator.py:466-476`) and any future reader go through one
  reader.

### Every 0.6 change, in one place

| Change | File in the bundle | Decision |
|---|---|---|
| `BUNDLE_FORMAT_VERSION` `"0.6"`; `"0.5"` legacy | all | D1 |
| `test_plan.json` required; plan version `"2"`: `declared`, `pack.data_sha256`, `system` and `pack` nullable | `test_plan.json` | D2, D9 |
| `controls.json` (new, required) | new | D5, D6 |
| `evidence_report.md` (new, required) | new | D7 |
| `manifest.json.sig` (optional, recognised, never checksummed) | new | D1 |
| manifest `required_files` grows by three | `manifest.json` | D1 |
| `evaluator.score` events, one per evaluation result, in the task's stream | `traces.jsonl` | D3 |
| `TraceEventKind.POLICY_VERDICT` removed | none (never written) | D3 |
| `HARNESS_EVENT_KINDS` excluded from the policy trace ceiling | `policy.json` (behaviour) | D3 |
| `config.json` gains `runner_id`, `mock_trajectory`, `evaluators` | `config.json` | D4 |
| `ResultBundleMetadata.package_sha256`, `.invocation_git_sha`, `.invocation_git_dirty`; `git_sha` means the package's tree | `metadata.json` | D8 |
| `replay_command.txt` gains the package hash, the invocation fields and the plan hash | `replay_command.txt` | D8 |
| `TaskRunResult.event_count` includes evaluator events | `summary.json` (counts) | D3 |

All additive except the removal of `POLICY_VERDICT`, which no bundle
carries, and the plan's nullable fields, which is why the plan version
moves.

## Alternatives considered and rejected

- **A required set that depends on content** (`test_plan.json` required
  only when `metadata.json` records its hash). Rejected for D1: a second
  mechanism in validator, reader and manifest, and a toy 0.6 bundle shaped
  like a 0.5 one.
- **One `evaluator.score` event per task run, with the derived verdict.**
  Rejected for D3: `evaluations.json` already has every evaluator's
  result; the trace should match it, not summarise it.
- **Emitting `POLICY_VERDICT` as a run-level event** with a null task id
  and its own `step_index` sequence. Rejected for D3: the verifier's sums
  would need a run-level case, `Ref` a null task, and nothing consumes it.
- **Keeping the `"mock"`-in-`agent_id` heuristic.** Rejected for D4: a
  renamed agent turns scripted evidence into agent evidence.
- **Scenario descriptors in `summary.json` or as objects in the plan's
  `selected`.** Rejected for D5: an orchestrator change for data the CLI
  already has, or a plan reshaped for a report's convenience.
- **Four coverage states, folding `undeclared` into `not_in_plan`.**
  Rejected for D6: "the owner excluded it" and "nothing in this pack tests
  it" must read differently.
- **A per-control pass or fail.** Rejected for D6: a composite, and it
  reads as a compliance verdict.
- **A machine-readable twin of the report** (`evidence_report.json`).
  Rejected for D7: `controls.json` is the machine-readable side and the
  report is rendered from it. An Excel in the Foundation tool's shape is an
  open question, not a rejection.
- **Keeping `git_sha`'s working-directory meaning and adding only the
  hash.** Rejected for D8: the field would keep misleading a partner.
- **Git from the package tree without a content hash.** Rejected for D8: a
  wheel would record nothing.
- **Documenting that a CRLF checkout hashes differently** instead of
  normalising. Rejected for D8: the hash is meant to say whether the
  source changed, and a checkout setting is not a source change.
- **Everything pinned in `metadata.json`.** Rejected for D9: a catalogue
  dump that duplicates the plan.
- **A required `data_sha256` on `DomainPack`.** Rejected for D9: every test
  pack would change; the optional-protocol pattern already exists.
- **Hashing scenarios only.** Rejected for D9 (finding 5).
- **Source id `aivtf`.** Rejected for D10: "AI Verify" in every control id.
- **The framework's full principle names in the catalogue.** Rejected for
  D10: principle 11 fails the five-word rule, and the IP script now checks
  every field.
- **Deriving which scenarios evidence a check through links to MGF
  sections.** Rejected for D11: every scenario citing 2.3.2 would
  "evidence" 4.3.1 and 5.9.1.
- **Catalogue entries listing scenario ids.** Rejected for D11: the
  catalogue would learn one pack's internals and break on the second.
- **Generating `docs/imda-alignment.md` §4 from the catalogue.** Rejected
  for D12.
- **A new `evidence/` package with its own contract.** Rejected for D13: a
  README diagram change, a tenth contract and an ARCHITECTURE edit for two
  modules.
- **The gate reading `controls.json`.** Rejected for D14.
- **Serialising every `TaskSpec` for `rescore`.** Rejected for D15: it
  duplicates the pack, which the hashes already pin.

## Open questions

- **An export in the Foundation tool's shape.** The process-checks tool
  imports and exports Excel with Outcome, Process, Evidence, Implemented
  and Elaboration per process; the column layout is not documented.
  Trigger: inspect a file exported from the Foundation's tool, then decide
  whether `controls.json` gains an exporter. Post-v0.5.
- **Official titles (E1).** With the Foundation's written permission the
  report can show the framework's own titles beside this project's labels;
  the catalogue's `label` field is the only place that would change.
- **The agent's prompt in the bundle** (`docs/imda-alignment.md` §11,
  item 7). 0.6 records no prompt and no prompt hash: the mock runner has
  none, and the real runners' system prompts are their own. R30's live run
  shows what a runner can expose; a `prompt_sha256` in `config.json` is
  the candidate for v0.7, when production traces arrive.
- **A second domain pack's citations.** D11's guard applies to any pack;
  the equality test is the seed pack's. Whether every pack must cite all 14
  direct and partial checks, or only those its domain can evidence, is
  decided with the second pack (`docs/domain-packs.md`, v0.6).
- **Principle 11's heading in `docs/imda-alignment.md`.** The document
  keeps the framework's principle name as a heading, read as an
  identifier, while the catalogue carries the short label. If the IP
  posture is tightened to headings, the heading takes the short label too.
- **The W28 cut.** If taken, `evidence_report.md` ships as its tables
  (D7) and the rescore fixtures as one (R29); this ADR's decisions do not
  change.

## Consequences

### Protocol and schema changes

| Where (today) | New shape | Bundle effect |
|---|---|---|
| `TraceEventKind`, `core/schemas.py:38-54` | `− POLICY_VERDICT`; new `HARNESS_EVENT_KINDS` | `traces.jsonl` (D3) |
| `ResultBundleMetadata`, `core/schemas.py:859-911` | `+ package_sha256`, `+ invocation_git_sha`, `+ invocation_git_dirty`; `git_sha` re-pointed | `metadata.json` (D8) |
| `RunPlan`, `core/schemas.py:483-517` | `system`, `system_path`, pack fields optional; `+ declared`, `+ data_sha256` | `test_plan.json` v2 (D2, D9) |
| new, `core/protocols.py` | `DataHashed` (optional, `data_sha256: str`) | `test_plan.json` (D9) |
| `Control`, `catalogue/loader.py:66-75`; `CONTROL_KINDS`, `:31` | `+ process` kind with `principle`, `scope`, `technical_test`, `classification`, `available_from`, `evidence`, `rows`; `Catalogue.principles`; wording-guard exemption for a source's title and publisher | `controls.json`, `evidence_report.md` (D10) |
| `BundleWriter.write`, `bundles/writer.py:159-175` | `+ runner_id`, `+ mock_trajectory`, `+ evaluators`, `+ catalogue`; writes the undeclared plan when none is given; writes `controls.json` and `evidence_report.md` | `config.json`, the two new files (D2, D4, D5, D7) |
| `LoadedBundle`, `bundles/reader.py:35-71` | `+ test_plan`, `+ controls`, `+ tool_calls`, `+ end_states` | none (D15) |
| `bundles/constants.py:32`, `:44-46`, `:102-105` | `"0.6"`; `"0.5"` legacy; `CONTROLS_FILENAME`, `EVIDENCE_REPORT_FILENAME`, `MANIFEST_SIGNATURE_FILENAME`; `OPTIONAL_BUNDLE_FILES_0_6` | all (D1) |
| `validate_bundle`, `bundles/validator.py:483-507` | drift by format version; 0.6 cross-checks | none (D1, D8) |
| `LocalReplayVerifier`, `replay/local.py:42-110` | `+` evaluator-event count equals evaluation count (0.6) | none (D3) |
| `BasicPolicyChecker`, `policy/basic_checker.py:88` | ceiling over kinds not in `HARNESS_EVENT_KINDS` | `policy.json` (D3) |
| `plans/builder.py:216-264`; `plans/io.py:17-29` | `+ undeclared_plan()`; `data_sha256` through `DataHashed`; `declared` | `test_plan.json` (D2, D9) |
| new, `plans/coverage.py` | `control_coverage(catalogue, plan, scenarios, summary) -> dict` | `controls.json` (D5, D6) |
| new, `reports/evidence.py` | `render_evidence_report(...) -> str`, pure | `evidence_report.md` (D7) |
| `LocalOrchestrator._run_task`, `orchestration/local.py:542-565` | emits `evaluator.score` per evaluation result | `traces.jsonl` (D3) |
| `cli.py:172-185`, `:240-254`, `:611-630` | the undeclared plan registered; runner and evaluator fields passed; `control_ids` lifted | — |
| `AgenticBaselineBenchmark`, `benchmarks/agentic_baseline/adapter.py` | rejects a cited process entry that is not direct or partial | — (D11) |
| `SeedDomainPack`, `domain_packs/seed/pack.py:823-830`, `:1000-1006` | merges `every_scenario_cites`; `data_sha256` | `test_plan.json`, `controls.json` (D9, D11) |
| `scripts/check_catalogue_ip.py:7-9`, `:64` | every field, three PDFs | — (D10) |

### By row

**R28, session (a) — data and library only; no bundle change** (amended
at acceptance, see Status). Builds: D10, D11, D12; D9's testing-framework
pin; D6 and D7 as library code (`plans/coverage.py`,
`reports/evidence.py`) that the writer does not call yet; D13's two
modules. `BUNDLE_FORMAT_VERSION` stays `"0.5"`.

Tests (fail-first where there is a guard):
- The catalogue loads 112 process entries with the counts 2, 12, 32, 9,
  49, 8; a file with 111, a wrong class count, a `later` without
  `available_from`, or an `artefact:` pointer to a file that is not a 0.6
  bundle file is rejected (fail-first each); "AI Verify" in a source title
  loads, in a label is rejected.
- The set of testing-framework ids cited across the seed pack equals the
  catalogue's direct and partial set (14); every scenario carries the five
  whole-run ids; an organisational check added to a scenario fails the
  equality test and is rejected by the benchmark (fail-first).
- `tests/test_imda_alignment_doc.py`: §4 agrees with the catalogue row by
  row, §3's 14 ids are the direct and partial set, the counts table
  recomputes; a one-letter change to a label in either place fails
  (fail-first).
- `scripts/check_catalogue_ip.py` over the three PDFs: every field of every
  control and every principle label clean (run locally, recorded in the
  commit message; not CI).
- Coverage, on synthetic plans, summaries and catalogues: a control cited
  only by a scenario that was not evaluated reports `uncovered` with the
  cause; one cited only by an excluded scenario reports `not_in_plan` with
  the reason; one no scenario cites reports `undeclared`; one whose citing
  scenarios all failed reports `covered` with `pass_k: false` beside each;
  mixed verdicts report `partly_covered`; no control carries an aggregate.
- The renderer, on the same inputs: all 112 checks with the Implemented
  and Elaboration cells empty in every row (asserted on the rendered
  table); the plan's intended use, out-of-scope uses and business metrics
  repeated verbatim; no pointer for a check whose scenarios did not run;
  the scripted-run sentence in the header and on every pointer when the
  runner is `mock`, and nowhere otherwise; the header's `created_at` is the
  one passed in, never the clock; two renders byte-identical; no
  "compliant" or "certified" anywhere and "AI Verify" only within the
  Sources section.
- After every slice, a toy run and a seed run validate as format 0.5 with
  the same file list as before the session.

Import-linter: no contract changes; `plans` importing `catalogue` is the
layers contract's middle tier reaching the low tier. `lint-imports` stays
9 kept, 0 broken.

**R28, session (b) — the 0.6 bump as one slice, then the close-out.**
Builds: D1, D2, D3, D4, D8; D9's package and pack hashes and
`controls.json`'s pins; D5 and D7's files, written by the writer (D13);
D15's reader fields.

Tests (fail-first where there is a guard):
- A fresh toy bundle is 0.6, carries `test_plan.json` with
  `declared: false`, `controls.json` and `evidence_report.md`, and
  validates; `gate` on it exits 2; a 0.6 bundle without `test_plan.json`,
  `controls.json` or `evidence_report.md` fails `validate-bundle`
  (fail-first each; R28's standing exit criterion).
- A 0.5 bundle without a plan still validates; 0.1–0.4 fixtures still
  validate (`tests/test_bundle_legacy_compat.py`).
- Every task run's stream ends with one `evaluator.score` event per
  evaluation result, in order; the cross-file sums hold; a bundle with
  one such event removed fails (fail-first).
- A trace of 3 agent events and 2 evaluator events passes a ceiling of 3
  (fail-first against the old count).
- `TraceEventKind` has no `POLICY_VERDICT`; the allowlist holds exactly
  `AGENT_OBSERVATION`; `test_allowlist_has_no_stale_entries` passes.
- `config.json` of the shipped seed run records `runner_id: mock`,
  `mock_trajectory: known_good` and the four evaluators; the evidence
  report's scripted-run sentence follows `runner_id`, not `agent_id`.
- `package_sha256` is identical for an LF and a CRLF copy of the same file
  tree, and differs when one byte of a `.yaml` changes (fail-first).
- An editable install records the checkout's `git_sha`; a non-editable
  install records `"unavailable"` even inside a repository (a temporary
  package directory under a temporary git repository, with
  `direct_url.json` absent); the invocation fields record the working
  directory.
- The drift warning on a 0.6 bundle is determinate with a dirty tree and
  an injected `git` failure; on a 0.5 fixture it compares the invocation
  sha; the suite's coverage no longer depends on the checkout's state
  (ROADMAP §0, 2026-10-10).
- The plan's `pack.data_sha256` changes when `policy.yaml` changes and the
  scenarios do not (fail-first).
- `controls.json`'s `plan_sha256` equals the registered hash; editing it
  fails validation (fail-first); a scenario in `controls.json` that the
  plan did not select fails validation.
- The written `evidence_report.md` carries `metadata.json`'s `created_at`;
  rendering it again from the bundle gives the same bytes; no artefact
  file name contains "AI Verify", "compliant" or "certified".
- An undeclared run's report says nothing was declared and every row is
  `undeclared`.
- `LoadedBundle` of a 0.6 bundle carries the plan, the controls and both
  streams; of a 0.4 bundle, `None` and empty lists.
- `agentevalops run --config configs/agentic_baseline_mock.yaml` then
  `validate-bundle`, `replay` and `gate` on a 0.6 bundle: `GATE: PASS`;
  the known-bad bundle `GATE: FAIL` with the three hard gates named.

Close-out: `README.md` (the bundle table and the diagram's file count),
`CLAUDE.md`'s smoke lines, `DESIGN.md` §7 (the tree's 0.6 entries marked
built), `ARCHITECTURE.md` §3 and §7, ROADMAP R28 and §0, `CHANGELOG.md`,
this ADR's Status.

Import-linter: no contract changes. `lint-imports` stays 9 kept, 0 broken.

**R29 — signing and `rescore`.** Relies on D1 (`manifest.json.sig` already
a recognised optional file, so no bump), D8 and D9 (the hashes
`CODE_CHANGED` compares), D15 (the reader; what `rescore` recomputes), D3
(evaluator events are not compared; `evaluations.json` is). Builds the
`MATCH` / `DIFFER` / `CODE_CHANGED` vocabulary then, not before.

**R31 — docs and the dry run.** `docs/evidence-pack.md` walks a 0.6 bundle
file by file in this ADR's terms; `docs/controls.md` describes the
catalogue's three sources, the process-entry schema and the IP posture;
`SECURITY.md` names the signing key; `ARCHITECTURE.md` is synced. The dry
run's bundle shows a partner's `invocation_git_sha` beside this package's
`git_sha` and `package_sha256` (D8), which is what finding 11 asked for.

### Elsewhere

- ADR-0005's Status line gains a pointer: *"Formats fixed 2026-11-01 by
  [0007](0007-evidence-pack.md); D6's `manifest.json.sig` is named at 0.6
  and signed in R29."* ADR-0006's Status gains: *"The R27 notes' plan
  format is lifted by 0007 D2 (plan version 2)."* Both at acceptance, in
  one docs commit, as ADR-0005 did for 0003.
- ROADMAP §0's 2026-10-31 review: finding 2 is marked fixed (`36fec5b`,
  ADR-0006 D20); finding 11 points here (D8); the 2026-10-10 drift-test
  finding is assigned to R28 (b) (D8; (a) when proposed, moved with the
  bump at acceptance).
- `docs/imda-alignment.md` §12's "from R28 the classifications live in the
  control catalogue" becomes true, and enforced by the sync test, in
  session (a) (D12).
- `tests/test_enum_construction_coverage.py`'s docstring and allowlist
  comments change with D3.

### Required `DESIGN.md` and `ARCHITECTURE.md` edits (on acceptance)

1. **`DESIGN.md` §7** — the tree's "final names fixed by ADR-0007" line
   becomes final: `test_plan.json` (every bundle; plan version 2),
   `controls.json`, `evidence_report.md`, `manifest.json.sig` (optional
   until R29 signs it). The closing paragraph notes that from 0.6
   `metadata.json`'s `git_sha` is the package's tree and `package_sha256`
   identifies the code on any install.
2. **`ARCHITECTURE.md` §1** — `plans/` imports `core`, `bundles` and
   `catalogue` (D13).
3. **`ARCHITECTURE.md` §3** — the closed `TraceEvent.kind` set loses
   `policy.verdict` and says `evaluator.score` is emitted per evaluation
   result from format 0.6 (D3).
4. **`ARCHITECTURE.md` §7** — a sentence that structural validation of a
   0.6 bundle also holds `controls.json` to the registered plan hash and
   counts evaluator events against `evaluations.json` (D1, D3).
