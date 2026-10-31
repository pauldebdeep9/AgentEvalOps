# 0006. Tool environment and observation seam: a harness-owned environment supplies and records every tool call; evaluators and policy read the harness record; bundle format 0.5

## Status

**Implementation notes — 2026-11-01 (R24 part 1, slices `36fec5b`,
`3e098eb`, `3997468`, `59a690f`).** Where the build departs from the text
below:
- **Two more 0.5 fields, owner's changes:** `EvaluationResult.applicable`
  (an evaluator that finds none of its inputs reports not-applicable
  instead of failing) and `RunSummary.preflight` (each scenario's
  known-good result, in the bundle). Both are in D21's table below.
- **D25:** a benchmark validates its options in its constructor, not a
  classmethod; the constructor already did that job.
- **D6:** `prepare_environment`'s `seed` parameter waits for R26, its
  first user. `RunConfig.seed` is recorded in 0.5 as planned.
- **D23:** `AgenticBaselineBenchmark` takes the pack object; the registry
  registers it through a factory that resolves `benchmark_options.pack`
  by name. A registry import inside the benchmark broke the
  adapter-independence contract.
- **D16:** the known-good hook is a small runtime-checkable protocol,
  `KnownGoodSource`, checked with `isinstance` (owner's change).
- **D24:** an entry point that re-finds the very object already
  registered (`pyproject.toml` publishes the built-in toy benchmark) is
  skipped quietly; a different object shadowed by a name still warns.

**Accepted — 2026-11-01 (owner).** Accepted with three changes made at
acceptance: D24, and D25's `benchmark_options` and CLI parts, move to R24,
where `agentic-baseline` first uses them (the derived allowlist stays in
R23); D10's real clients get a 60 s request timeout and one retry, the
timeout a constructor parameter; and the exit wait a running model call
imposes is added to Open questions. `DESIGN.md` and `ARCHITECTURE.md`
carry the edits listed under "Required `DESIGN.md` and `ARCHITECTURE.md`
edits" below.

Proposed — 2026-10-31. Documentation only: no code changes accompany this
ADR. The owner decided the seven questions put to them on 2026-10-31
(D8, D12, D13, D5, D21, D22, D26) and amended six engineering calls (D1,
D4, D3, D18, D20 and the open questions). Two spikes were run in `/tmp`
against copies of the code, never the repository (see Context). Line
numbers are at `38d6106`.

**Amended 2026-11-01, still Proposed (owner).** The bundle 0.5 bump and
everything that writes it — task and repetition stamping, several evaluators
per task, `EvaluatorContext`, the three-way `ScoreResult` — moves from R23 to
R24, where its first users arrive (the new record files and the `tool_use`
and `state_based` evaluators). R23 keeps the environment, the runner seam,
in-memory harness records with a plain cross-check function, scripted
trajectories, the derived allowlist and the cancelled-prepare fix, and
writes bundles exactly as today.

Answers every question on `ROADMAP.md` R22's ADR-0006 list, including the
five added by the 2026-10-31 external review (`ROADMAP.md` §0).

## Summary

| D | Decision | Code it changes (today) | Built in |
|---|---|---|---|
| D1 | The environment travels as a typed `AgentInput.environment`, produced by `prepare_environment` | `core/schemas.py:166-171`, `:151-163`; `orchestration/local.py:297-299` | R23 |
| D2 | `ToolEnvironment` protocol, async | new, `core/protocols.py` | R23 |
| D3 | One neutral `ToolSpec`; permission classes; `finish` reserved | new, `core/schemas.py`; `agents/openai_runner.py:139`, `agents/langgraph_runner.py:112` | R23 |
| D4 | Harness call record (`ToolCallRecord`) | new, `core/schemas.py` | R23 |
| D5 | The approver is the environment's gate; nothing unapproved runs | new, `environments/` | R25 |
| D6 | Declarative fault and injection schedules; derived seeds | `core/protocols.py:95` | R24 (seed), R26 (schedules) |
| D7 | End-state snapshot before evaluators and teardown | `orchestration/local.py:236-335` | R24 |
| D8 | `environments/` is its own layer between the adapters and `core` | `pyproject.toml:207-215` | R23 |
| D9 | Each runner converts specs inside its own loop; `finish` stays a runner control | `agents/openai_runner.py:401-410`, `:626-634`; `agents/langgraph_runner.py:341-352`, `:537-545` | R23 |
| D10 | SDK calls off the event loop; client-level request timeout | `agents/openai_runner.py:350`, `:406`; `agents/langgraph_runner.py:290`, `:346` | R23 |
| D11 | Scripted trajectories for `MockAgentRunner`, as data | `agents/mock_runner.py:60-150` | R23 (mode), R24 (data) |
| D12 | `EvaluatorContext` on `Evaluator.evaluate` and `PolicyChecker.check` | `core/protocols.py:166-171`, `:206-211`; `orchestration/local.py:228`, `:323` | R24 |
| D13 | Several evaluators per task; a real failure wins | `core/schemas.py:311-318`; `cli.py:78-79` | R24 |
| D14 | Policy and tool-use verdicts come from harness records | `policy/basic_checker.py:38-70`; `evaluators/deterministic.py:132-150` | R23 (cross-check function), R24 (evaluators), R25 (policy) |
| D15 | `allowed_tool_ids` stays, with a consumer and a YAML path | `core/schemas.py:282`; `config/loader.py` (policy block) | R25 |
| D16 | Cause of `not_evaluated`; known-good preflight | `core/schemas.py:243`; `orchestration/local.py:80-116` | R24 |
| D17 | Events and records are addressed by task, repetition and index | `core/schemas.py:196-210`, `:247`, `:297` | R24 |
| D18 | The orchestrator owns repetitions | `core/schemas.py:100-133`, `:311-318`; `orchestration/local.py:155-234` | R24 (fields), R27 (k > 1) |
| D19 | Harness data in its own files; no new `TraceEventKind` | `core/schemas.py:35-51`; `bundles/writer.py:188-281` | R24 |
| D20 | `ScoreResult` uses the three-way split; `SimpleScorer` meets `Scorer` | `core/schemas.py:251-263`; `scorers/simple.py:9-26`; `orchestration/local.py:223` | R24 |
| D21 | Bundle format 0.5 in R24, once; the evidence pack becomes 0.6 | `bundles/constants.py:32`, `:44`, `:75-97` | R24 |
| D22 | A cancelled `prepare_environment` is waited out and torn down; time limit deferred | `orchestration/local.py:281-283` | R23 |
| D23 | `DomainPack` protocol behind one generic `agentic-baseline` benchmark | new | R24 |
| D24 | Entry-point discovery, with a `domain_packs` group | `registry.py:202`, `:308`; `pyproject.toml:120` | R24 |
| D25 | Configs pick a benchmark and its options without toy coupling | `config/loader.py:27`, `:40`, `:388-394`; `cli.py:107` | R23 (derived allowlist), R24 (options, CLI, `config.json`) |
| D26 | Synthetic identifiers that cannot belong to a real person | new (seed data, detector config) | R24 (data), R25 (detector) |

## Context

ADR-0005 D4 decided that tools come from a harness-owned environment that
is also the observation point, and left the exact types to this ADR,
before R23 starts. Its findings 1–3 are the gaps: runners hard-code
`calculator` and `finish` (`agents/openai_runner.py:139`,
`agents/langgraph_runner.py:112`, dispatch at `openai_runner.py:626-634`
and `langgraph_runner.py:537-545`); evaluators cannot see environment
state (`core/protocols.py:166-171`, called at `orchestration/local.py:323`);
and `PolicySpec.allowed_tool_ids` (`core/schemas.py:282`) has no consumer.

The 2026-10-31 external review added five questions: events cannot be
traced to a task (`TraceEvent`, `core/schemas.py:196-210`, has no
`task_id`, and every runner restarts `step_index` at 0); one evaluator per
task (`core/schemas.py:311-318`, `cli.py:78-79`); real runners block the
event loop with no request timeout (`agents/openai_runner.py:406`,
`agents/langgraph_runner.py:346`); cancelling during `prepare_environment`
leaks the environment (`orchestration/local.py:281-283`); and
`_teardown`'s wait has no time limit (`orchestration/local.py:337`).

Constraints that shaped the answers:

- **R23's behaviour-preservation test.** `tests/test_openai_runner.py`,
  `tests/test_azure_openai_runner.py` and `tests/test_langgraph_runner.py`
  must pass unmodified before and after the refactor (row 13b D2; §5
  risk 1). Their helpers build `AgentInput(task=..., context={"run_id":
  ...})` with no environment (`test_openai_runner.py:151`,
  `test_langgraph_runner.py:147`, `test_azure_openai_runner.py:131`).
- **0001 §4.** No shared provider abstraction; each runner keeps its own
  loop.
- **The import contracts** (`pyproject.toml:207-236`). An adapter family
  cannot import another. A new package that both benchmarks and runners
  need cannot be one more adapter family.

**Spikes (2026-10-31, `/tmp` only).**

1. One neutral spec shape, converted by a three-line function per
   provider, reproduces both runners' `_TOOLS` byte for byte (dict equality
   and identical JSON).
2. Copies of both runners refactored to take specs from a toy environment
   and dispatch every call except `finish` to it — about ten changed lines
   per runner — pass the three runner test files unmodified (86 passed, 1
   skipped) and the whole suite (721 passed). The `tools=` payload each
   runner sent to its fake client equals `_TOOLS` at HEAD, byte for byte.
3. In the same copies, `await asyncio.to_thread(self._call_model, ...)`
   leaves the three runner test files unmodified, and two toy tasks with
   a fake client that sleeps 1 s finish in 1.01 s at
   `max_concurrent_tasks: 2`, against 2.01 s today.

## Decision

### A. The environment

**D1. The environment travels as a typed field, and the orchestrator
guarantees it.**
- `AgentInput` (`core/schemas.py:166-171`) gains
  `environment: ToolEnvironment | None = None`. `AgentInput` is never
  serialised, so this has no bundle effect.
- `EnvHandle` (`core/schemas.py:151-163`) gains
  `tools: ToolEnvironment | None = None`. `prepare_environment` returns
  the task's environment in it; the toy benchmark returns the toy
  environment.
- The orchestrator copies `handle.tools` into `AgentInput`
  (`orchestration/local.py:297-299`). If a handle carries no environment,
  the orchestrator raises `ConfigurationError` for that task, so it is
  recorded `not_evaluated` (from R24, with cause `configuration`, D16). A wiring bug
  can never look like an agent failure.
- The runners' fallback (D9) is therefore reached only when a runner is
  called directly, as the runner tests do.

**D2. The `ToolEnvironment` protocol is async.** In `core/protocols.py`:
- `async def list_tools() -> Sequence[ToolSpec]`
- `async def call(name: str, arguments: dict[str, Any]) -> ToolResult` —
  `ToolResult` is the text the agent sees plus an `error` flag
- `async def snapshot() -> EndState` (D7)
- `def records() -> Sequence[ToolCallRecord]` (D4)

Async now, so that an MCP transport (v0.6) is another implementation of
the same protocol, not a change to it. Runners are already async
generators, so awaiting costs nothing.

**D3. One neutral `ToolSpec`, with permission classes; `finish` is
reserved.** In `core/schemas.py`:
- `ToolSpec(name, description, input_schema, permission, data_class)`.
  `input_schema` is a JSON Schema object. `permission` is `"read"`,
  `"write"` or `"irreversible"`. `data_class` is `"normal"` or
  `"restricted"` (R25's records export). Neither `permission` nor
  `data_class` is ever sent to a provider (spike 1).
- `RESERVED_TOOL_NAMES = frozenset({"finish"})`. An environment that
  declares a reserved name is rejected when it is built — a domain pack
  cannot define `finish`.
- `FINISH_TOOL_SPEC`, the runner control's spec (D9), also lives in core.

**D4. The harness call record.** `ToolCallRecord` in `core/schemas.py`,
written only by the environment:
`task_id`, `repetition`, `index` (0-based, per task run), `tool_name`,
`arguments`, `permission`, `data_class`, `approval`
(`"not_required"` / `"approved"` / `"denied"` / `"timed_out"`),
`executed` (bool), `fault` (the injected fault applied, or `None`),
`injections` (ids of injected content in the result), `result`, `error`,
`timestamp`.
- **`timestamp` is information only.** `rescore` (ADR-0005 D7) never
  compares it; `index` gives the order.
- A call to an unknown tool is recorded too, with `executed: false`.

**D5. The simulated approver is the environment's gate, and nothing
unapproved runs.**
- Every call to an `irreversible` tool goes to the approver. The scenario
  scripts the answer per call: approve, deny or timeout.
- Deny or timeout: the call **does not execute**. The agent receives an
  error result saying the action was not taken and why. The record says
  `executed: false` with `approval: "denied"` or `"timed_out"`.
- **Timeouts are simulated instantly.** Nothing waits on a real clock.
- **The gate is the only mechanism.** A `request_approval` tool is an
  ordinary tool, built only when a seed scenario uses it. "Ask before
  acting" is one SOP-ordering rule over the harness records, which R25
  builds anyway — not a second approval path.
- This answers ADR-0005 Q4. MGF-Agentic §1.1.3's involvement level, which
  the owner declares in R27, decides which scenarios script which answers.

**D6. Fault and injection schedules are declarative; seeds are derived.**
- A scenario declares schedules as data: "fault *kind* on tool *T* at its
  *n*-th call" (kinds: timeout, malformed result, transient error) and
  "payload *P* in document or result *D*". No random number generator
  decides a fault.
- Anything randomised (R26's perturbation variants) uses
  `seed = int(sha256(f"{seed_base}:{task_id}:{repetition}"))`, where
  `seed_base` is a new `RunConfig.seed: int = 0`. R27 records it in
  `test_plan.json`.
- `BenchmarkAdapter.prepare_environment(task)` (`core/protocols.py:95`)
  gains a keyword `seed: int = 0` in R24, with `RunConfig.seed`. The
  orchestrator passes the derived seed; the toy adapter ignores it.
- The same seed and schedule give the same environment, so R27's
  repeated runs and `rescore` are reproducible by construction.

**D7. The end state is snapshotted before evaluators and before
teardown.** Inside `_run_task` (`orchestration/local.py:236-335`), after
the runner finishes and on every exit path where a handle exists, the
orchestrator awaits `environment.snapshot()`. It then calls evaluators
(D12), then `_teardown`. On cancellation the snapshot is skipped, not
waited for. `EndState` is a JSON object with a `schema` id chosen by the
domain pack; the toy environment's is its call count. `snapshot()` is
part of the protocol from R23 (D2); the orchestrator calls it and writes
the result from R24.

**D8. `environments/` is its own layer between the adapters and `core`.**
- New package `agentevalops.environments`: the in-process
  `ToolEnvironment` that keeps its own records (`inprocess.py`), the
  approver and schedules, and the toy environment (`toy.py`, which owns
  `run_calculator`).
- Benchmarks (domain packs) and the runners' fallback import it; it
  imports only `core`.
- The layers contract (`pyproject.toml:207-215`) becomes
  `cli` > adapters > `environments` > `core`. A new forbidden contract
  says `agentevalops.environments` imports nothing from `agentevalops`
  except `core`. The catalogue (R22 part 3) already sits in this tier;
  `catalogue` and `environments` are independent siblings in the layers
  contract, each with its own forbidden contract.
- Not one more member of the independence contract
  (`pyproject.toml:224-236`): an independent family could be imported by
  neither the benchmarks nor the runners, and both need it.

### B. The runner seam

**D9. Each runner converts specs inside its own loop; `finish` stays a
runner control.**
- At the start of `_gen`, each runner awaits `list_tools()`, appends
  `FINISH_TOOL_SPEC`, and converts the list with its own three-line
  function: OpenAI's `{"type": "function", "function": {...}}` in
  `openai_runner.py`, Anthropic's `{"name", "description",
  "input_schema"}` in `langgraph_runner.py`. `AzureOpenAIRunner` inherits
  OpenAI's. No shared base class (0001 §4).
- `_call_model` (`openai_runner.py:401-410`, `langgraph_runner.py:341-352`)
  takes the converted list instead of the module-level `_TOOLS`, which is
  deleted.
- Dispatch (`openai_runner.py:626-634`, `langgraph_runner.py:537-545`):
  `finish` is handled by the runner as today and never reaches the
  environment or its records; every other name goes to
  `await environment.call(...)`. OpenAI's malformed-JSON branch still
  answers before dispatch, as today.
- **Fallback, for direct calls only.** When `input.environment is None`,
  the runner uses `environments.toy.toy_environment()` and logs a
  warning. Runner modules keep re-exporting `_run_calculator`, which the
  runner tests import. D1 guarantees the orchestrator never reaches this
  path.
- The toy environment's spec reproduces today's `_TOOLS` exactly (spike
  1), and R23 pins that with a golden-payload test.

**D10. SDK calls run off the event loop, with a client-level timeout
(finding 7).**
- Both runners call
  `await asyncio.to_thread(self._call_model, client, messages, tools)`.
- A request timeout and retry count are set where the real client is
  built (`openai_runner.py:350`, `langgraph_runner.py:290` — the live path
  the tests never reach), so the tests' fake clients are untouched (spike
  3): a 60 s request timeout and one retry. The timeout is a constructor
  parameter (`request_timeout_seconds`, default 60), so a slower model can
  raise it.
- `max_wall_seconds` is still checked between calls; a hung request is
  bounded by the client timeout.

**D11. Scripted trajectories are data in the domain pack.**
- Per scenario, `known_good` and `known_bad`, each a list of steps:
  `call` (tool and arguments), `say` (text), `finish` (answer).
- `MockAgentRunner` (`agents/mock_runner.py:60-150`) gains a scripted mode
  that dispatches each `call` through the real environment, so the
  harness records are genuine, and emits its own `AGENT_TOOL_CALL`
  self-reports as a real agent would.
- An optional `report_as` on a step makes the self-report differ from
  the call actually made. That is the deliberately divergent script R23's
  exit criterion needs (D14).
- Today's metadata-driven mode stays the default, so existing toy configs
  and tests are unchanged.

### C. Evaluation and policy

**D12. Evaluators and the policy checker receive an `EvaluatorContext`.**
- `Evaluator.evaluate(task, output, trace, context)` (`core/protocols.py:166-171`)
  and `PolicyChecker.check(policy, output, trace, context)`
  (`core/protocols.py:206-211`). The `EvaluatorContext` that
  `protocols.py:11-14` already anticipates.
- `EvaluatorContext` holds: the harness `records` and `end_states` in
  scope, the scenario's declared applicable evaluators (D13), and the
  task run's `task_id` and `repetition`. An evaluator gets one task run's
  context (`orchestration/local.py:323`); the policy checker gets the
  whole run's (`orchestration/local.py:228`).
- **Rejected alternative** (`ROADMAP.md` §4 item 2's fallback): carry end
  state and harness calls in the trace stream. It needs two new
  `TraceEventKind`s and a schema bump, puts large untyped payloads in
  `traces.jsonl`, and mixes harness truth into the agent's own stream,
  against ADR-0005 D4. The protocol change costs one parameter on
  `DeterministicEvaluator` and on the evaluator and policy fakes in the
  orchestration tests — none of the three runner test files.

**D13. Several evaluators per task; a real failure wins.**
- `TaskRunResult` (`core/schemas.py:311-318`) gains
  `evaluations: list[EvaluationResult]`. `evaluation` stays, as the task
  verdict derived from them, so readers of 0.1–0.4 bundles still work.
- **Applicability is declared, not inferred.** The scenario (through
  `TaskSpec`) lists the evaluator kinds that apply. One that does not
  apply is not run and not counted. It is not `not_evaluated`.
- **The task verdict:** if any applicable evaluator ran and failed, the
  task **failed**. Otherwise, if any applicable evaluator could not
  evaluate, the task is **not_evaluated**. Otherwise it **passed**. A
  real failure is never downgraded, as in `BasicPolicyChecker`.
- No weighted or averaged score (ADR-0005 forbids composites); each
  evaluator's result is reported.
- The CLI builds every evaluator the run configures (default
  `[deterministic]`) instead of always one (`cli.py:78-79`). A task that
  declares an evaluator the run did not configure is `not_evaluated` with
  cause `configuration`.

**D14. Policy and tool-use verdicts come from harness records.**
- `BasicPolicyChecker`'s deny-list (`policy/basic_checker.py:58-70`) and
  `DeterministicEvaluator`'s required-tool check
  (`evaluators/deterministic.py:132-150`) read `context.records`, not the
  agent's `AGENT_TOOL_CALL` events.
- The agent's self-reports become a cross-check. A record without a
  matching self-report, or a self-report without a record, is a
  `self_report_mismatch` finding: a `WARN` from the policy checker and a
  failure in the `tool_use` evaluator.
- **R23 builds the cross-check as a plain function**,
  `environments.cross_check(records, events) -> list[Mismatch]`, over the
  in-memory records — enough for R23's exit test. The evaluators and the
  policy checker read records through `EvaluatorContext` from R24 (policy
  rules in R25).

**D15. `allowed_tool_ids` stays.**
- R25 gives it a YAML path under `policy:` and a consumer: a call
  recorded to a tool outside the list is a least-privilege violation.
- An environment may deliberately expose tools a scenario's agent should
  not use; that is the test.
- Permission classes (D3) answer a different question — what needs
  approval — so they do not replace it.

**D16. The cause of `not_evaluated` is recorded; defects are found by a
known-good preflight.**
- `EvaluationResult` (`core/schemas.py:218-248`) gains
  `not_evaluated_cause: "infrastructure" | "configuration" | "environment_defect" | None`.
  `_not_evaluated_result` (`orchestration/local.py:80-116`) sets
  `infrastructure` or `configuration` from the exception type.
- `RunSummary` (`core/schemas.py:321-345`) gains
  `not_evaluated_by_cause: dict[str, int]`.
- Built in R24 (field and preflight).
- **Preflight.** Before the agent runs a scenario, the orchestrator
  runs the scenario's `known_good` trajectory once with the scripted mock
  against a fresh environment. If it fails, the scenario is defective:
  every run of it is `not_evaluated` with cause `environment_defect`, and
  the agent is not called. Mock only, so it costs no model spend
  (ADR-0005 D8; §5 risk 5).

### D. Trace and bundle

**D17. Events and records are addressed by task, repetition and index
(finding 1).**
- `TraceEvent` (`core/schemas.py:196-210`) gains
  `task_id: TaskId | None = None` and `repetition: int = 0`.
- **The orchestrator stamps them** (from R24) as each event passes through
  `_run_task`, before it is stored. Runners do not change, so neither do
  their tests.
- New `Ref(stream, task_id, repetition, index)` in core, `stream` being
  `"trace"` or `"tool_calls"`. `EvaluationResult` and `PolicyVerdict` gain
  `refs: list[Ref]` beside the legacy int `citations`
  (`core/schemas.py:247`, `:297`), which stay for 0.1–0.4 readers and keep
  being written.
- Two tasks calling a denied tool at their own step 1 now cite two
  different refs, instead of `[1, 1]`.

**D18. The orchestrator owns repetitions.**
- `RunConfig` (`core/schemas.py:100-133`) gains `repetitions: int = 1` and
  `seed: int = 0` (D6). `TaskRunResult` gains `repetition: int = 0`.
- `run()` (`orchestration/local.py:155-234`) runs each task `repetitions`
  times. `task_id` stays the scenario's id; the repetition number is
  separate.
- pass^k (R27) groups results by `task_id`. This works for every
  benchmark, not just domain packs; the generic benchmark (D23) emits one
  task per scenario.
- `RunSummary`'s counts are over task runs. With `repetitions: 1` they
  equal today's.
- The YAML path for `repetitions` arrives in R27; until then it is always
  1.

**D19. Harness data has its own files; no new `TraceEventKind`.**
- Two new bundle files, one JSON object per line, keyed by `task_id` and
  `repetition`:
  - `tool_calls.jsonl` — every `ToolCallRecord`
  - `end_states.jsonl` — one `EndState` per task run
- The agent cannot write to them, and `rescore` reads them directly.
- No new `TraceEventKind` (`core/schemas.py:35-51`): harness data does not
  travel in the trace. `EVALUATOR_SCORE` already exists and is wired in
  R28.

**D20. `ScoreResult` uses the three-way split, and `SimpleScorer` meets
the `Scorer` protocol (finding 2).**
- `ScoreResult` (`core/schemas.py:251-263`) gains `not_evaluated_tasks`.
  `failed_tasks` means evaluated and failed, as in `RunSummary` (0002 Axis
  3). `pass_rate` stays `passed / total`, so a not-evaluated run counts
  against it, and is always shown with the not-evaluated count beside it.
- `SimpleScorer` (`scorers/simple.py:9-26`) implements `summarize()`, so
  `isinstance(SimpleScorer(), Scorer)` holds. The orchestrator calls it
  through the protocol (`orchestration/local.py:223`).
- Done in this bump (R24) rather than R28 because D13 and D16 already
  reshape the verdict model. R28's ADR-0007 list loses this item.

**D21. Bundle format 0.5 lands once, in R24; the evidence pack becomes
0.6.**
- R24 is the first row that writes a new field or file (D17, D19), and
  the first with users for them. It bumps
  `BUNDLE_FORMAT_VERSION` (`bundles/constants.py:32`) to `"0.5"` and
  implements the **whole** 0.5 contract listed below. Later rows add
  content, never shape.
- `"0.4"` joins `LEGACY_BUNDLE_FORMAT_VERSIONS` (`bundles/constants.py:44`).
  `required_files_for_version("0.5")` (`:81-97`) adds `tool_calls.jsonl`
  and `end_states.jsonl`.
- R28's evidence pack becomes **0.6**, with `"0.5"` joining the legacy
  set. ADR-0005 D6 says "format 0.5"; its Status line records the change.
- R23 writes bundles exactly as 0.4 does: its harness records stay in
  memory, and `config.json` keeps recording `benchmark_scenario` (D25).
- If R24 runs over, the bump moves whole to R25. It still happens once,
  and before the v0.2 tag.

**Every 0.5 change, in one place:**

| Change | File in the bundle | Decision |
|---|---|---|
| `TraceEvent.task_id`, `TraceEvent.repetition` | `traces.jsonl` | D17 |
| `tool_calls.jsonl` (new, required) | new | D4, D19 |
| `end_states.jsonl` (new, required) | new | D7, D19 |
| `TaskRunResult.evaluations`, `.repetition`; `evaluation` becomes the derived verdict | `summary.json` | D13, D18 |
| `EvaluationResult.not_evaluated_cause`, `.refs` | `evaluations.json`, `summary.json` | D16, D17 |
| `PolicyVerdict.refs` | `policy.json` | D17 |
| `RunSummary.not_evaluated_by_cause` | `summary.json` | D16 |
| `ScoreResult.not_evaluated_tasks`; `failed_tasks` redefined | `summary.json` | D20 |
| `RunConfig.repetitions`, `.seed` | `config.json` | D6, D18 |
| `benchmark_options` recorded | `config.json` | D25 |
| `EvaluationResult.applicable` (owner's change, 2026-11-01) | `evaluations.json`, `summary.json` | D13 |
| `RunSummary.preflight` (owner's change, 2026-11-01) | `summary.json` | D16 |
| `evaluations.json` holds one entry per evaluator per task run | `evaluations.json` | D13 |

All additive except `ScoreResult.failed_tasks`'s meaning, which is why it
waits for the version bump.

### E. Lifecycle

**D22. A cancelled `prepare_environment` is waited out and torn down; the
time limit is deferred.**
- `prepare_environment` (`orchestration/local.py:281-283`) uses
  `_teardown`'s pattern (`:337`): submitted once, waited on through
  cancellation. If it produced a handle, that handle is torn down; then
  the cancellation is re-raised. This closes the leak D5's open question
  in `docs/benchmark_adapters.md` describes.
- **No time limit on either wait, for now.** v0.5's environments run in
  process, so prepare and teardown cannot realistically hang. Daemon
  threads would let process exit kill a cleanup halfway through, and
  nothing would finish it later. An idempotent teardown only helps if
  something calls it again. See Open questions for the trigger.

### F. Domain packs and the registry

**D23. A `DomainPack` protocol behind one generic `agentic-baseline`
benchmark.**
- `DomainPack` (in `core/protocols.py`): `pack_id`, `pack_version`,
  `scenarios()`, `build_environment(scenario, seed)`,
  `trajectories(scenario)` (D11), materiality profile, thresholds, and the
  catalogue control ids each scenario declares.
- One `agentic-baseline` `BenchmarkAdapter` turns a pack's scenarios into
  tasks — **one task per scenario**; the orchestrator repeats them (D18).
  Its `prepare_environment` calls `build_environment`.
- The seed domain (ADR-0005 D9) is the first pack, built in R24.

**D24. Packs and adapters are discovered through entry points.**
- The CLI calls `registry.discover_entry_points()` (`registry.py:202`,
  `:308`; no caller today) once, before resolving names, for the existing
  `agentevalops.adapters` group (`pyproject.toml:120`) and a new
  `agentevalops.domain_packs` group (new `KIND_DOMAIN_PACK`). Import
  failures are logged and skipped; today they are skipped silently
  (`registry.py:231-233`), and the log line comes with D24.
- Built in R24, with the first domain pack.
- The seed domain registers through the same entry point as a third-party
  pack would.

**D25. Configs choose a benchmark and its options without toy coupling
(finding 8).**
- `benchmark_id` is checked against the registry (the derived allowlist
  R23 already builds, replacing `_SUPPORTED_BENCHMARKS`,
  `config/loader.py:40`).
- The derived allowlist is built in R23. The rest of D25 — options, the
  CLI change and the loader's decoupling — is built in R24, where
  `agentic-baseline` is its first user.
- A new opaque `benchmark_options:` mapping is passed as
  `benchmark_cls(**options)` (`cli.py:107`). Each benchmark validates its
  own options in a classmethod that raises `ConfigurationError`.
- The toy benchmark's `scenario` moves into its options. The loader stops
  importing `benchmarks.toy.scenarios` (`config/loader.py:27`,
  `:388-394`) and still reads `benchmark_scenario` as an alias for toy, so
  existing configs keep working.
- `config.json` keeps recording `benchmark_id` and `benchmark_scenario`
  as today through R23; `benchmark_options` is recorded from R24, as part
  of 0.5.

**D26. Synthetic identifiers cannot belong to a real person (§5 risk 9).**
Facts, checked 2026-10-31:
- **NRIC/FIN prefixes.** S and T for citizens and permanent residents; F,
  G and M for foreigners' FINs (F before 2000, G from 2000 to 31 Dec 2021,
  M from 1 Jan 2022); FINs are never reissued. Source: ICA media release,
  12 July 2021, "New M FIN series to be introduced from 1 January 2022"
  (ica.gov.sg).
- **Check letter.** A mod-11 sum over the seven digits with weights 2, 7,
  6, 5, 4, 3, 2 plus a prefix offset, mapped to a prefix-specific letter
  table. ICA does not publish it; the sources are community
  reverse-engineering (ngiam.net; privacy.com.sg). For the M prefix,
  sources disagree on the offset and the table.
- **Telephone numbers.** IMDA National Numbering Plan, Issue 2, February
  2026, Table 2.1 and §2.2.1: leading digits 3 (IP telephony), 6 (fixed
  line), 8 and 9 (mobile) are in service; 2, 4 and 5 are reserved for
  future use, and numbers with no service type cannot be used.
- **E-mail.** RFC 2606 (June 1999; updated by RFC 6761) reserves `.test`,
  `.example`, `.invalid`, `.localhost` and `example.com/.net/.org`.

The scheme:
- **NRIC-shaped ids** use the prefix `X`, which no NRIC or FIN has ever
  carried, with a check letter from a test-only table.
- **Phone numbers** are eight digits starting with `5`.
- **E-mail addresses** use the `.example` top-level domain.
- **Names** are synthetic, from a fixed list, and are never attached to a
  real identifier.
- **No other identifiers.** Seed cases carry no addresses, postal codes,
  dates of birth, passport, bank or vehicle numbers. Adding any of them
  needs the same treatment, recorded in this ADR, before the data lands.

Why none can be a real person's, as of 2026-10-31: no issued NRIC or FIN
uses `X`; no telephone number starting with 5 can be in service under
IMDA's plan; `.example` cannot resolve.

R25's detector:
- Accepts the `X` prefix and the leading `5` **only in test
  configuration**.
- Its negative controls are look-alikes with real prefixes and invalid
  check letters.
- **M is flagged by format alone** (M, seven digits, a letter) until a
  verified check-letter table is found. M's look-alike test is marked
  pending, not skipped silently. Missing a real FIN is worse than a false
  alarm.
- Each release re-checks both facts (a new FIN prefix; level 5 opened),
  and the release notes date the check.

## Alternatives considered and rejected

- **The environment in `AgentInput.context`.** Untyped, and nothing stops
  a runner from silently not finding it. Rejected for D1.
- **A synchronous environment protocol.** Simpler today; an MCP transport
  would then force a protocol change. Rejected for D2.
- **Per-provider tool specs stored with the environment.** Duplicates
  every description; spike 1 shows one neutral shape is enough. Rejected
  for D3.
- **Requiring an environment in every `AgentInput`.** Breaks the three
  runner test files (§5 risk 1). Rejected for D1/D9.
- **`finish` as an environment tool.** The environment would decide when a
  run ends, mixing two jobs, and every pack would have to redefine it.
  Rejected for D9.
- **Async SDK clients for finding 7.** `AsyncOpenAI` and its Anthropic
  counterpart would change what the tests' fake clients must provide.
  Rejected for D10.
- **A per-call `timeout=` argument.** Fake clients with fixed signatures
  could break. Rejected for D10.
- **End state through the trace stream.** See D12.
- **A primary evaluator with advisory others, or a weighted score.** Hides
  disagreement, and ADR-0005 forbids composites. Rejected for D13.
- **"Not evaluated" winning over a real failure.** Would let a broken
  evaluator hide a failed agent. Rejected for D13.
- **An explicit approval protocol separate from the gate.** Two
  mechanisms for one control. Rejected for D5.
- **Removing `allowed_tool_ids` in favour of a maximum permission class.**
  Cannot express "this tool is out of scope". Rejected for D15.
- **A structured `citations` type.** Breaks every 0.1–0.4 reader.
  Rejected for D17 in favour of `refs` beside it.
- **The benchmark expanding repetitions into tasks.** Every benchmark
  would reimplement it, and `task_id` would stop naming the scenario.
  Rejected for D18.
- **Writing the new data under 0.4 until R28.** The v0.2 and v0.3 tags
  would ship bundles holding data no version describes. Rejected for D21.
- **A bump per row.** Churn, and each version would be valid for a week.
  Rejected for D21.
- **A 30 s limit with daemon threads.** See D22.
- **`environments/` in the independence contract.** See D8.
- **One `BenchmarkAdapter` per domain pack.** Duplicates task building,
  repetitions and the preflight per pack. Rejected for D23.

## Open questions

- **Time limits on `prepare_environment` and `_teardown`.** Decide them
  together with a sweep for leftover resources, when the first environment
  holds real resources — an MCP transport (v0.6) or SWE-bench. Until then,
  today's unbounded wait stays (D22).
- **The exit wait of a running model call (D10).** A model call running
  in a worker thread cannot be cancelled. On Ctrl-C, process exit waits
  for it, up to the request timeout times the number of tries — 60 s × 2
  by default. Decide alongside the time limits above.
- **A run-wide spend cap (finding 9).** `ResourceLimits` are per task, and
  nothing caps total spend across a run. R27's repetitions multiply
  spend, so the cap needs a row before R27.
- **A verified M-prefix check-letter table.** Until one is found, the
  detector flags M by format alone and M's look-alike test is pending
  (D26).
- **A new FIN prefix, or IMDA opening level 5.** Re-checked at each
  release (D26).
- **`grade()` and the workspace** (`docs/benchmark_adapters.md` D5) stay
  with SWE-bench, frozen; nothing here depends on them.

## Consequences

### Protocol and schema changes

| Where (today) | New shape | Bundle effect |
|---|---|---|
| `AgentInput`, `core/schemas.py:166-171` | `+ environment: ToolEnvironment \| None = None` | none (never serialised) |
| `EnvHandle`, `core/schemas.py:151-163` | `+ tools: ToolEnvironment \| None = None` | none |
| `BenchmarkAdapter.prepare_environment`, `core/protocols.py:95` | `+ seed: int = 0` (keyword) | none |
| new, `core/protocols.py` | `ToolEnvironment` (D2), `DomainPack` (D23) | none |
| new, `core/schemas.py` | `ToolSpec`, `ToolResult`, `ToolCallRecord`, `EndState`, `Ref`, `EvaluatorContext`, `FINISH_TOOL_SPEC`, `RESERVED_TOOL_NAMES` | `tool_calls.jsonl`, `end_states.jsonl` (0.5) |
| `Evaluator.evaluate`, `core/protocols.py:166-171` | `+ context: EvaluatorContext` | none |
| `PolicyChecker.check`, `core/protocols.py:206-211` | `+ context: EvaluatorContext` | none |
| `TraceEvent`, `core/schemas.py:196-210` | `+ task_id`, `+ repetition` | `traces.jsonl` (0.5) |
| `EvaluationResult`, `core/schemas.py:218-248` | `+ not_evaluated_cause`, `+ refs` | `evaluations.json`, `summary.json` (0.5) |
| `PolicyVerdict`, `core/schemas.py:286-298` | `+ refs` | `policy.json` (0.5) |
| `TaskRunResult`, `core/schemas.py:311-318` | `+ evaluations`, `+ repetition`; `evaluation` derived | `summary.json` (0.5) |
| `RunSummary`, `core/schemas.py:321-345` | `+ not_evaluated_by_cause` | `summary.json` (0.5) |
| `ScoreResult`, `core/schemas.py:251-263` | `+ not_evaluated_tasks`; `failed_tasks` redefined | `summary.json` (0.5) |
| `RunConfig`, `core/schemas.py:100-133` | `+ repetitions`, `+ seed` | `config.json` (0.5) |
| `BUNDLE_FORMAT_VERSION`, `bundles/constants.py:32` | `"0.5"`; `"0.4"` legacy; two required files added | 0.5 |

### By row

**R23 — tool environment and runner seam.** Bundles unchanged (0.4).
Builds:
- D1–D4, D8–D10, the D11 mode, D14's cross-check function, D22.
- The derived runner and benchmark allowlists (ADR-0005 finding 4; D25's
  first part).
- `tests/test_orchestration.py`'s fake benchmark returns the toy
  environment in its handles (D1's guard would otherwise turn its tasks
  `not_evaluated`). That file is not one of the three protected ones.

Tests (fail-first where there is a guard):
- `git diff --exit-code <base> -- tests/test_openai_runner.py tests/test_azure_openai_runner.py tests/test_langgraph_runner.py`
  is empty at the end of the row.
- A golden-payload test: the toy environment's converted specs equal
  pinned JSON for both providers.
- A handle without an environment gives `not_evaluated`, not an agent
  failure (fail-first).
- A domain pack that declares `finish` is rejected (fail-first).
- A scripted trajectory's records match its self-reports; a `report_as`
  script is flagged by the cross-check function (fail-first).
- A cancelled `prepare_environment` whose job completes is torn down,
  reproduced with the gated executor from `9cc2b00`'s test (fail-first).
- Two fake clients that each wait for the other's call to start finish at
  `max_concurrent_tasks: 2` and deadlock when the call blocks the loop. A
  timeout guards the test. No wall-clock assertion.
- The benchmark parity test fails against a deliberately desynced
  allowlist first.
- A toy run's bundle is still 0.4 and byte-for-byte the same shape.

Import-linter: `environments` joins `catalogue` as an independent
sibling in the layers contract; a new forbidden contract keeps
`environments` to `core` only. Each is proven
against an injected violation first. The seam is the critical path (§4
item 1).

**R24 — bundle 0.5 and the seed domain.**
Builds:
- The whole 0.5 bump (D21's table): D17's stamping and refs, D19's files,
  D7's snapshot call, D13, D12, D20, D16 (field and preflight), D18's
  fields, D6's seed, D25's `config.json` entry.
- D24 (entry-point discovery, `domain_packs` group, the log line) and the
  rest of D25 (`benchmark_options`, the CLI change, the loader no longer
  importing the toy benchmark).
- D23, the seed pack's data (D11 trajectories, D26 identifiers),
  `tool_use` and `state_based` evaluators reading `EvaluatorContext`.

Tests (fail-first where there is a guard):
- Two tasks calling a denied tool at their own step 1 get different
  `refs`.
- The verdict rule: one evaluator fails and another cannot evaluate →
  failed; a non-applicable evaluator is not counted.
- `ScoreResult` with one not-evaluated task says failed 0,
  not-evaluated 1; `isinstance(SimpleScorer(), Scorer)`.
- A 0.5 bundle missing `tool_calls.jsonl` fails `validate-bundle`;
  0.1–0.4 bundles still validate.
- A handle without an environment is `not_evaluated` with cause
  `configuration`.
- Each scenario's known-good passes and known-bad fails under every
  applicable evaluator.
- A broken known-good gives `environment_defect` for every run of that
  scenario, and the agent is not called.
- Every task run has an end-state line.
- No seeded identifier uses a real NRIC/FIN prefix, a phone digit other
  than 5, or a domain outside `.example`.

R24 is now the heaviest row. If it runs over, the bump moves whole to
R25, still once and before the v0.2 tag.

**R25 — policy compliance.**
Builds: D5's gate (approve / deny / timeout, instant), D15's consumer and
YAML path, SOP-ordering rules over records ("ask first" among them), the
detector under D26.

Tests:
- An irreversible call under deny or timeout never executes and is
  recorded `executed: false` (fail-first).
- A call outside `allowed_tool_ids` fails (fail-first).
- The detector flags every seeded identifier and none of the look-alikes;
  M by format, M's look-alike test pending.

**R26 — robustness.**
Builds: D6's fault and injection schedules and the perturbation seed.

Tests:
- The same seed and repetition give the same fault and variant.
- Every scheduled fault appears in the run's records.

**R27 — scoping and repetitions.**
Builds: the YAML path for `repetitions` and `seed`; pass^k grouped by
`task_id` (D18); `test_plan.json` records the seed.

Tests: pass^k at k = 5 on a seeded flaky script matches the analytic
value (existing exit criterion), computed across repetitions of one
`task_id`.

**R28 — evidence pack.**
Builds: format 0.6 instead of 0.5, with `"0.5"` legacy (D21). ADR-0007's
list loses "one meaning of failed" (D20); `git_sha` stays.

### Elsewhere

- ADR-0005's Status line records that the evidence pack is format 0.6
  (D21).
- `docs/benchmark_adapters.md` D5's open question on cancelling
  `prepare_environment` is answered by D22 when R23 lands; its note is
  updated then.
- A full `ARCHITECTURE.md` refresh stays with R31.

### Required `DESIGN.md` and `ARCHITECTURE.md` edits (on acceptance)

1. **`DESIGN.md` §7** — the bundle tree's planned additions: format 0.5
   adds `tool_calls.jsonl` and `end_states.jsonl` (D19, D21; R24); the
   evidence-pack files move to format 0.6 (R28, ADR-0007), and
   `env_state/` is replaced by `end_states.jsonl`.
2. **`ARCHITECTURE.md` §1** — a low tier between the adapters and the
   core: `catalogue/` and `environments/`, importing only `core` (D8).
3. **`ARCHITECTURE.md` §2** — a note that ADR-0006 adds `ToolEnvironment`
   (R23) and `DomainPack` (R24).
4. **`ARCHITECTURE.md` §2.3** — a note that `evaluate` gains a
   `context: EvaluatorContext` parameter in R24, as does
   `PolicyChecker.check` (D12).
