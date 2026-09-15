# Changelog

All notable changes to AgentEvalOps are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

### Added

- **Row 15 part 1 — D5 environment lifecycle methods implemented**
  (2026-09-15, `docs/benchmark_adapters.md`). `BenchmarkAdapter` gains
  `prepare_environment`/`teardown` as required members; `LocalOrchestrator`
  now actually calls both.
  - **`EnvHandle`** (`core/schemas.py`): new frozen dataclass —
    `task_id`, `workspace_path: Path | None`, `metadata` — deliberately
    never enters a result bundle (a workspace path is machine-specific).
  - **Protocol + all 4 implementers updated**: `ToyBenchmarkAdapter`,
    `MockBenchmarkAdapter`, `_ToolCallAdapter`, `_OneTaskBenchmark` (the
    last kept its pre-existing partial `type: ignore`, untouched).
  - **`_run_task` wiring**: `prepare_environment` runs first, inside the
    concurrency semaphore, via `asyncio.to_thread`; its own
    `InfrastructureError`/`ConfigurationError` share the W12a
    not-evaluated-result helper (moved, not copied) and skip `teardown`;
    the runner/W12a-catch/`evaluate()` now run inside `try/finally`, so
    `teardown` always fires — normal return, the W12a catch, an exception
    out of `evaluate()`, and cancellation of a sibling task still waiting
    inside its runner (fail-first tested, including 20 repeat runs of the
    sibling-cancellation case).
  - **Open questions recorded, not resolved**: whether `grade()` needs the
    workspace before `teardown` deletes it (deferred to row 16), and that
    a cancellation during `prepare_environment` itself leaves the clone's
    worker thread running with no handle to ever tear down.
  - Full gate green: mypy 44 files, `lint-imports` 7/7, pytest 705 passed
    (96.90% coverage), `make smoke` unaffected (bundle format still `0.4`).

- **Row 14 close-out — grading hermeticity finding, then a validated gold
  run** (2026-08-05, `docs/benchmark_adapters.md`). Corrects the entry
  below: row 14 was marked ✅ Met on a run whose gold patch came back
  *unresolved* — diagnosis, not validation. Gold must resolve, by
  construction.
  - **New structural finding, the sharper of this document's two**:
    `psf__requests-1921`'s `test_basicauth_with_netrc` makes a live HTTP
    call to `httpbin.org` **during grading**, inside an otherwise frozen
    container. Unlike repository drift, freezing the image doesn't fix
    this — the dependency is on a live service reachable at run time, not
    on repo state fixed at build time. Consequence stated explicitly: any
    SWE-bench instance promoted to a golden fixture (W12c/W29) must be
    screened for network dependence first, or replay determinism
    silently inherits httpbin's uptime. `docs/adr/0003-replay-semantics.md`'s
    Q3 nondeterminism enumeration has no category for this yet
    (third-party-service nondeterminism, introduced by the benchmark
    adapter, not the runner) — flagged, not fixed, per this session's
    explicit instruction not to edit ADR-0003.
  - **`psf__requests-1921` disqualified**, for both roles it might have
    played (row 14 validation and a future fixture candidate).
  - **Second instance screened, then graded**: 3 candidates
    (`pallets__flask-5014`, `pylint-dev__pylint-7080`,
    `pytest-dev__pytest-6202`) had their `test_patch` diffs read directly
    and keyword-matched for network I/O — zero hits, all three
    disqualification-clean. `pallets__flask-5014` picked (smallest patch,
    pure-Python). Graded gold: **`resolved: true`**, 1/1 `FAIL_TO_PASS`,
    59/59 `PASS_TO_PASS`, ~26s once its image was local (registry pull
    needed 5m15s first — the identical `ImageNotFound`-from-a-transient-pull
    pattern as the sympy 404, this time caught and verified immediately
    via `docker manifest inspect` rather than trusted).
  - **Screening-method limitation found and reported, not glossed over**:
    checked directly whether the same diff-only screen would have caught
    `psf__requests-1921` — it would not have. The actually-failing test
    wasn't in that instance's `test_patch` at all (a pre-existing,
    untouched test); a diff-only screen is structurally blind to network
    dependence anywhere in a `FAIL_TO_PASS`/`PASS_TO_PASS` list beyond
    what the diff itself touches. `flask-5014`'s clean run stands as real
    evidence for that one instance; the screening method itself is not
    validated as complete.
  - `ROADMAP.md` row 14: the prior ✅ annotated as premature and corrected
    in place (not deleted — the reasoning at the time is left visible); a
    new §0 entry records the hermeticity finding as standing, not scoped
    to one instance.
  - Committed and pushed twice this session (start and end), both CI +
    Package Actions runs green.

- **Row 14 follow-up — registry diagnosis, then the graded run** (2026-08-05,
  `docs/benchmark_adapters.md`). **Row 14's exit criterion is now met** —
  corrects the prior entry below, which recorded it as unmet.
  - **Diagnosed, not worked around**: the previous session's 404 pulling
    a prebuilt image (default namespace, x86_64) was traced directly
    rather than repeating the `--namespace ''` fallback. `docker manifest
    inspect` and a direct `docker pull` (retried once after a transient
    CloudFront timeout) both confirmed the image genuinely exists and
    pulls cleanly — **the registry was never broken**; the original 404
    was `docker-py` surfacing a transient network condition as a
    misleading `ImageNotFound`.
  - **Graded run**: `psf__requests-1921` (SWE-bench Verified, gold
    predictions, upstream's documented default, no local build) — patch
    applied cleanly, 106/106 `PASS_TO_PASS` held, 5/6 `FAIL_TO_PASS`
    passed. The one failure (`test_basicauth_with_netrc`) is a live
    `httpbin.org` HTTP call inside a 2013-era test, traced via
    `test_output.txt` — a network-dependent flake, not a grading defect.
    ~3m9s end to end, zero infrastructure/build errors. New risk surfaced
    for rows 23–25: some instances' tests make live outbound HTTP calls,
    so grading-sandbox network egress is a correctness concern, not just
    a security one.
  - **New first-class finding, promoted to its own section**: local
    instance-image builds re-run `git clone --branch` against **live**
    upstream repo state (confirmed mechanism, not just the sympy
    symptom), so their reproducibility depends on upstream not having
    pruned a branch since the dataset was built — registry pulls are
    frozen at push time and structurally immune to this. Registry-first
    is now the recorded preference, not just the default.
  - **D1**: `swebench==4.1.0` (+ `cbor2<6`) confirmed as a working pin —
    graded a real instance successfully. Extra still not added to
    `pyproject.toml`, now deliberately (out of this session's explicit
    scope) rather than provisionally.
  - **D2/D3/D4 confirmed unchanged**, stated explicitly: import-linter
    still 7/7; the two-sandbox split (D3) was directly reinforced by
    watching the harness own every step of the grading container's
    lifecycle itself; the CI constraint (D4) is underscored by a single
    lightweight instance still taking ~3 minutes.
  - `pyproject.toml`: boto3 contract comment corrected — it only ever
    demonstrated *source*-side vacuousness (no `cloud/` package), not
    that `forbidden` contracts tolerate an *uninstalled destination*
    module (that's the swebench contract's finding, from last session,
    now cross-referenced explicitly).
  - `.gitignore`: added the print-edition build's generated outputs
    (`AgentEvalOps-print-edition.html`, `print-file-sizes.txt`,
    `print-manifest-*.txt`); `build_print_edition.py` itself left alone
    and flagged for the user, not decided here.
  - Docker images from this session (2 × SWE-bench instance images,
    6.721GB total, 100% reclaimable) deliberately **not deleted** —
    useful for rows 15/16, left for the user to reclaim if desired.
    `Sai2511` `pip freeze` confirmed byte-identical before/after;
    `swebench` installed only in the reused throwaway venv.

- **Row 14 — SWE-bench harness decision + spike** (`docs/benchmark_adapters.md`).
  Decision, not implementation — no `SWEBenchAdapter`, nothing under
  `src/agentevalops/benchmarks/`.
  - **D1**: pip-install extra, not vendored/submodule. **Not added to
    `pyproject.toml` this session** — the spike didn't clear its own bar
    (see below), and an unpinnable-in-practice dependency isn't a
    dependency yet. `swebench==4.1.0` does install cleanly in a throwaway
    venv with a `cbor2<6` constraint (`cbor2>=6`, pulled in transitively
    via `modal`, needs a Rust toolchain this laptop doesn't have; `5.9.0`
    still ships a pure-Python wheel).
  - **D2**: subprocess invocation only (`python -m
    swebench.harness.run_evaluation`), never imported. A 7th import-linter
    contract (`"swebench never imported ... subprocess only"`) was
    empirically proven addable — unlike the pre-existing boto3
    `independence`-type contract's comment (about an internal module not
    existing), a `forbidden`-type contract does **not** error when the
    forbidden third-party module itself is uninstalled; proven both ways
    (KEPT with `swebench` absent, BROKEN against a reverted injected
    violation) before being added for real. **7/7 contracts kept.**
  - **D3**: two sandboxes, not one — corrects rows 15/16 in `ROADMAP.md`.
    The agent's workspace (plain git clone, no Docker) is what
    `prepare_environment`/`teardown` own; the grading sandbox is entirely
    harness-owned and never touched by this codebase. Row 15's risk
    downgraded H → L; row 16's `teardown` no longer means container
    cleanup.
  - **D4**: SWE-bench grading cannot run in this project's CI (§0 entry in
    `ROADMAP.md`) — upstream's ~120GB/16GB RAM/8-core floor is far beyond
    GitHub-hosted runners. First hard requirement for a real
    `CloudBackend`; cross-references `docs/adr/0004`'s two-axis framing.
  - **Spike: row 14's own exit criterion not met.** Upstream's documented
    install-smoke-test instance (`sympy__sympy-20590`) failed — not a
    harness defect (base+env Docker images built successfully from
    scratch once `--namespace ''` forced local builds instead of a 404
    Docker Hub pull, itself a finding, on x86_64 where that flag wasn't
    expected to be needed) but real upstream repository drift:
    `sympy/sympy` no longer has a `1.7` branch (confirmed via `git
    ls-remote`; only the `sympy-1.7` tag survives), so `setup_repo.sh`'s
    `git clone --branch 1.7` fails before the test suite ever runs. Per
    the ticket's own stop condition, the SWE-bench Verified run (run 2)
    was not attempted. `Sai2511`'s `pip freeze` confirmed byte-identical
    before/after (`swebench` installed only in a throwaway venv outside
    the repo); spike Docker images removed, `docker system df` back to
    pre-spike baseline.

- **Row 12b — `replay_command.txt`, structural replay folded into
  `validate-bundle`** (`docs/adr/0003-replay-semantics.md`). Bundle format
  bumped **`0.3` → `0.4`** (`replay_command.txt`, the first bump to add a
  new *required file* rather than a field; `"0.3"` joins the legacy-accept
  set).
  - **`replay_command.txt`**: a real, required-at-`"0.4"` bundle file
    written by `BundleWriter` — the reconstructable invocation (pointing at
    the bundle's own `config.json` rather than duplicating it), `git_sha`/
    `git_dirty`, `platform_version` (with its own coarseness noted inline),
    `run_id`, the resolved model id for model-backed runners, and — new,
    closing the gap the row 13b ticket's D5 deliberately left open — the
    Azure **resource name** (the endpoint's subdomain only, never the full
    URL — a bundle is portable/shareable, DESIGN.md) for `azure-openai`
    runs, or the same `"unavailable"` sentinel `git_sha` already uses if
    `AZURE_OPENAI_ENDPOINT` is unset. Human-readable text, not JSON: read by
    a person deciding whether a bundle is still comparable, not parsed by a
    machine.
  - **Q1 decided**: `validate-bundle` is the single structural entry point;
    `agentevalops replay` becomes a deprecated alias delegating to the
    identical code path (same arguments, same output, same exit codes),
    with a stderr-only deprecation notice — not renamed to `verify`, not
    removed, so the name stays available and unambiguous for behavioral
    replay once it ships.
  - **`ReplaySummary` deleted**; `LocalReplayVerifier` (`replay/local.py`)
    is now an internal cross-file-consistency helper `validate_bundle()`
    calls and merges findings from, rather than a class with its own
    parallel summary type. Every field `ReplaySummary` carried (`run_id`,
    `trace_event_count`, `evaluation_count`, `policy_verdict`,
    `bundle_format_version`, `manifest_valid`) is now on
    `BundleValidationResult` instead — closing the exact "two things
    sharing one name" confusion ADR-0003's Context section names. The
    import direction reverses cleanly: `replay/local.py` no longer imports
    `bundles.validator` back, so `bundles.validator` importing
    `replay.local` introduces no cycle.
  - **Required-file set became version-aware**
    (`bundles/constants.py`'s new `required_files_for_version()`): a bundle
    declaring format `"0.3"` still validates cleanly without
    `replay_command.txt`; one declaring `"0.4"` but missing it fails the
    same way one missing `report.md` would. Both directions fail-first
    verified — guard disabled, confirmed the specific failure, restored —
    not just asserted. The frozen `partial-infra-failure-2026-08-03`
    incident fixture still fails for its exact original pinned reason (the
    same 7-item missing-file list, no new entry for `replay_command.txt`),
    checked directly against `validate_bundle()`; its never-regenerate rule
    (docs/adr/0003 Q5) needed no exception. `bundles/reader.py`'s
    `BundleReader` picks the same version-aware required set.
  - **Code-drift is a `validate-bundle` warning, never an error** (docs/adr/
    0003 D5): a bundle's recorded `git_sha` compared against the current
    checkout surfaces as a warning when they differ or either side is
    undeterminable (dirty tree, no checkout, missing provenance on a
    pre-`git_sha` bundle), but never changes `valid` or the exit code — a
    bundle written six months ago is still structurally valid today.
  - **`run_id` auto-generation explicitly not implemented** (docs/adr/0003
    D6) — `ARCHITECTURE.md` §5's stale UUIDv7 claim corrected via an inline
    callout instead; the real, unchanged behavior is `config/loader.py`
    taking `run_id` verbatim from YAML. Implementing the documented
    behavior would introduce a nondeterministic field into exactly the
    bundle-comparison work this row builds toward.
  - **`MATCH`/`DIFFER`/`CODE_CHANGED` and `ReplayRunner` deliberately not
    built** — adding that vocabulary now, with nothing to construct it
    yet, would repeat the "vocabulary exists, nothing constructs it"
    pattern this project has already hit three times (`INFRA_FAILURE`,
    `Verdict.WARN` before their fixes, `EVALUATOR_SCORE`/`POLICY_VERDICT`,
    still open). Both remain W12c/W29, real and unstarted.
  - **Tests**: `tests/test_replay_command.py` (new, 26 tests covering D3's
    version-aware required set, D4's content/sentinel fields, D5's drift
    warning); `tests/test_replay.py` rewritten for the new
    `LocalReplayVerifier.check() -> (errors, warnings)` API;
    `tests/test_bundle_validator.py`/`tests/test_example_configs.py`
    updated for the version-aware checked-file count. Full suite green,
    97% coverage; `lint-imports` unaffected (6/6 kept).

- **Row 13b — `AzureOpenAIRunner`** (`runner_id: "azure-openai"`), Axis B of
  `docs/adr/0004-cloud-backend-two-axes.md`. Subclasses `OpenAIRunner` (no
  shared provider base class, per D1; `_gen` untouched) overriding only
  `_build_real_client` (`openai.AzureOpenAI`, endpoint/key from
  `AZURE_OPENAI_ENDPOINT`/`AZURE_OPENAI_API_KEY`), price resolution, and
  `agent_id` (`azure-openai-chat:` prefix). Bundle format bumped **`0.2` →
  `0.3`** (`RunConfig.deployment`/`.api_version`, additive; `"0.2"` joins
  the legacy-accept set).
  - **Prerequisite refactor, proved behavior-preserving before anything else
    was built (D2)**: `OpenAIRunner._model` — one string serving three jobs
    (the API's `model=` parameter, the pricing-table key, `agent_id`
    provenance) — split into `_api_model`/`_model_id`. For `OpenAIRunner`
    both are set to the same value; `tests/test_openai_runner.py` ran
    completely unmodified before and after (35/35 both times) to prove it.
    `_compute_step_cost` added to `OpenAIRunner` as the price-resolution
    seam `AzureOpenAIRunner` overrides, so no per-provider branch was needed
    inside the shared loop.
  - **Follow-up (same day): the third attribute resolved, not left as a
    hand-maintained duplicate.** `_model` was initially kept alive alongside
    the two new attributes, purely so three existing white-box tests
    (`runner._model == DEFAULT_MODEL`) would pass unmodified. Checked via
    `grep -rn "self\._model\b" src/` whether anything in non-test `src/`
    actually read it: nothing did, and `AzureOpenAIRunner` never set it to
    the deployment name either (the confusable case this would have been a
    real defect). Deleted `_model` entirely; the three tests now assert
    `_model_id` instead, since that's the attribute that actually carries
    the resolved model going forward. No parity test needed — there is no
    longer a third value to keep in sync.
  - **Azure price-table comment gained two caveats, values/enforcement
    unchanged**: (1) the table is keyed on model id alone, but Azure's real
    rate is also a function of deployment type (Global Standard/Regional
    Standard/Provisioned/Batch) and region — construction-time enforcement
    guarantees the model id is *known*, not that the price is *right* for
    whichever deployment type the caller's `deployment` name actually
    points at; (2) the sourcing is weaker than `OpenAIRunner`'s
    `_PRICE_PER_1M_USD`, which cites OpenAI's own pricing page directly —
    Azure's page renders client-side and couldn't be scraped, so this rests
    on two secondary sources instead of the vendor's own numbers.
  - **Split into two separable commits, each independently verified green**:
    a behavior-preserving refactor commit (`_api_model`/`_model_id` split,
    the `_model` resolution above, the affected `OpenAIRunner` tests —
    nothing Azure) and a second commit for everything else. The refactor
    commit's isolation was proven, not assumed: checked out alone in a
    separate `git worktree`, confirmed the editable install resolved
    *inside* that worktree (not the main tree), then ran ruff/mypy/pytest
    there and got the same clean result (639 passed, 1 skipped, 97% —
    identical to the pre-row-13b baseline) before the worktree was removed.
    Both commits pushed to `origin/main`; CI (lint, mypy, import contracts,
    tests+coverage, smoke run/validate-bundle/replay) and the packaging
    check both passed across Python 3.10/3.11/3.12.
  - **Own price table, fails loud instead of guessing**: Azure gets
    `_AZURE_PRICE_PER_1M_USD`, deliberately not sharing `OpenAIRunner`'s
    `_FALLBACK_PRICE_PER_1M_USD` — Azure keys requests by deployment name,
    so an unknown model id could never coincidentally match a model-id-keyed
    table the way an occasional OpenAI miss might. `AzureOpenAIRunner.
    __init__` raises `ConfigurationError` at construction time for an
    unpriced model id, never a per-call silent default. Table seeded with
    one model (`gpt-4.1-mini-2025-04-14`, $0.40/$1.60 per 1M input/output) —
    Azure's own pricing page renders via client-side JavaScript and
    couldn't be scraped directly, so sourced from two independent secondary
    articles (cloudzero.com, redresscompliance.com, both checked
    2026-08-03, both reporting identical Global Standard-tier figures, which
    happen to match OpenAI's own direct-API rate for this model — not
    copied from it), with model availability cross-confirmed via
    Microsoft's own model-availability doc.
  - **Config**: `RunConfig.deployment`/`.api_version` (additive, default
    `None`, same shape `model` took in W12a). `config/loader.py` requires
    both for `runner_id: azure-openai` and *rejects* — not silently ignores
    — them for every other `runner_id`, since a stale field surviving a
    runner switch is exactly the shape of bug this project's history keeps
    finding. `configs/toy_azure_openai.yaml` added, mirroring
    `toy_openai.yaml`.
  - **Registered as `azure-openai`**, added to `_SUPPORTED_RUNNERS` —
    closing `test_supported_runners_matches_registry`'s guard (the parity
    test written after the 2026-08-03 `openai` incident) exactly as
    designed, rather than repeating that incident a second time.
  - **Tests**: 49 offline tests total (14 new in
    `tests/test_azure_openai_runner.py`, 35 inherited unmodified from
    `test_openai_runner.py`), no network, no `llm` extra required. Two
    specifically fail-first verified — guard disabled, failure confirmed,
    then restored — not just written and assumed correct: the seam that
    sends the deployment name to the API while pricing off the model id,
    and construction-time price-table enforcement for an unknown model.
  - **No new import-linter contract**: confirmed the existing "openai
    confined to agents/" contract already covers `openai.AzureOpenAI` (same
    top-level module) — 6/6 contracts still kept.
  - **Live verification not attempted** — remains blocked on Azure
    subscription quota, the same class of blocker as `OpenAIRunner`'s
    2026-08-03 attempt, not a new one. Live pass-rate and cost-accounting
    reconciliation against a real Azure invoice are both unchecked, same as
    the other two model-backed runners — see `ROADMAP.md` §0's standing
    cost-validation-gap entry, which names this as one structural fact
    across all three runners rather than three separate footnotes.
    `DefaultAzureCredential`/AAD authentication is explicitly out of scope
    (D6): API-key auth only.

- **`docs/adr/0004-cloud-backend-two-axes.md` + doc sync — "Azure support" is
  two independent axes, not one.** Documentation only, no code. Axis B
  (Azure OpenAI as an `AgentRunner`) needs no `DESIGN.md` positioning
  change — already covered by the existing "not bound to one model
  provider" non-goal — and is off the critical path (new `ROADMAP.md` row
  13b, optional, `not yet started`). Axis A (Azure as a `CloudBackend`)
  stays out of v0.5 scope and does not start before `AwsBackend` exists,
  since `CloudBackend` has zero implementations today, not even
  `LocalBackend` (new `ROADMAP.md` row 16b names this prerequisite, filling
  a hole §0 already flagged: `LocalBackend` was in the v0.5 scope list with
  no assigned week). The ADR records the AWS/Azure primitive mapping table
  and three protocol-shaped asymmetries now, deliberately before
  `AwsBackend`'s design, as the counter-measure to designing against exactly
  one cloud.
  - Verified, not assumed, before writing: the five job-related schema types
    ARCHITECTURE.md §2.8's code block shows (`JobSpec`/`JobHandle`/
    `JobStatus`/`ContainerSpec`/`SecretRef`) don't exist anywhere in `src/`
    or `tests/` (real protocol uses `dict[str, Any]`/`str`); `registry.py`
    has no `KIND_BACKEND` at all, so `config/loader.py`'s
    `_SUPPORTED_BACKENDS` has no registry allowlist to be out of parity
    with, unlike `_SUPPORTED_RUNNERS`; `backend_id` reaches bundle metadata
    and the markdown report but nothing dispatches on it; `core/types.py`
    defines only `BACKEND_LOCAL`/`BACKEND_AWS`; the existing "openai
    confined to agents/" import-linter contract already covers Azure OpenAI
    (`openai.AzureOpenAI` is inside the same `openai` top-level module the
    contract already forbids outside `agents/`), so no new contract is
    implied by Axis B.
  - `DESIGN.md` §8/§9, `ARCHITECTURE.md` §2.8/§3/§6, and `ROADMAP.md`
    §0/§1/§2 updated to cross-reference the ADR and correct stale claims
    found along the way: `ARCHITECTURE.md` §3's schema list asserted seven
    names (`ArtifactRef`, `ReportArtifact`, `JobSpec`, `JobHandle`,
    `JobStatus`, `ContainerSpec`, `SecretRef`) are defined in
    `core/schemas.py` when they aren't; `ROADMAP.md` §1's runner list was
    stale, missing the already-shipped `OpenAIRunner`.
  - Deliberately not done in this session, per its own explicit scope:
    `core/protocols.py`'s `CloudBackend` docstring and `core/types.py`'s
    `BackendId` constants are unchanged — both are accurate today and only
    become wrong the day `AzureBackend` itself lands, not before.

- **W12a — outcome taxonomy, model provenance, and best-effort finalize**
  (`docs/adr/0002-outcome-taxonomy.md`, implemented in full; closes the
  remaining gap in `docs/adr/0001-openai-runner-chat-completions.md` §3).
  Bundle format bumped **`0.1` → `0.2`** (`bundles.constants.
  BUNDLE_FORMAT_VERSION`) — additive and backward-compatible, see
  "Compatibility" below.
  - **The core fix**: `orchestration/local.py`'s `_run_task` now catches a
    runner's own `InfrastructureError`/`ConfigurationError` *per task*
    instead of letting it abort the whole run via `asyncio.gather()`.
    Reproduced the old behavior first, fault-injected (no network): the
    orchestrator raised uncaught and `cli.py` never reached
    `BundleWriter`, leaving only a bare `traces.jsonl` — exactly what
    `tests/fixtures/bundles/partial-infra-failure-2026-08-03/` (a real,
    preserved incident) already showed. A caught failure now becomes a
    well-formed `AgentOutput(termination_reason=INFRA_FAILURE)` +
    `EvaluationResult(evaluated=False, ...)` for that task, and the run
    continues.
  - **New/changed fields** (all additive): `RunSummary.not_evaluated_tasks`
    (named for the observable fact, not the inferred cause — infra failure
    is one reason evaluation didn't happen, not the only one;
    `failed_tasks` now means a genuine, evaluated merit-based failure, no
    longer silently absorbing not-evaluated tasks). `EvaluationResult.
    evaluated: bool = True` (the orchestrator sets `False` directly for a
    caught per-task failure — the evaluator itself is never asked to
    special-case "there was nothing to judge"). `RunConfig.model: str |
    None` — closes the model-provenance gap: `config.json` now records
    which model produced a run *by construction* (the whole `RunConfig` is
    already serialized there), no write-back path needed or added.
    `ResultBundleMetadata.status` (`"sealed"`/`"interrupted"`/`"partial"`)
    — makes a finalized-under-failure bundle distinguishable from a
    complete one; `sealed: bool` kept for compatibility, now always
    derived from `status`. `ResultBundleMetadata.git_sha`/`.git_dirty` —
    real subprocess-based capture (`bundles/writer.py:
    _capture_git_provenance`), never `None`/omitted (`"unavailable"`
    sentinel outside a checkout), never fabricated (a clean SHA against a
    dirty tree — the likelier failure mode than "no checkout at all" — is
    the specific case this exists to catch, and is tested against a real
    temporary git repo, not mocked).
  - **CLI best-effort finalize**: `cli.py`'s `run` command now writes a
    bundle on *every* exit path — clean completion, `KeyboardInterrupt`,
    and (now rare, defensive-backstop-only, since the orchestrator fix
    above means a runner's own failure no longer reaches here)
    `InfrastructureError`/`ConfigurationError`, plus a new catch-all for
    any unexpected exception. Before this, only clean completion ever
    reached `BundleWriter`.
  - **`LoadedRunConfig.model` found to be a second, independently-set copy
    of `run_config.model`** — the same `_SUPPORTED_RUNNERS`-shaped drift
    risk flagged as a pattern to watch for in the prior session, found by
    inspection this time rather than by a failure. Collapsed into a
    read-only property over `run_config.model`; there is now exactly one
    stored value, not two that happen to agree.
  - **Version-string consolidation**: `metadata.json`'s `schema_version`
    and `manifest.json`'s `bundle_format_version` were two independently
    hardcoded literals (`"0.1.0"` vs `"0.1"`) never cross-checked against
    anything. Both now come from the single `BUNDLE_FORMAT_VERSION`
    constant; a regression test asserts they agree.
  - **Pass-rate string always shows both figures**
    (`reports/markdown.py`'s new `format_pass_rate()`, shared by the CLI
    and the markdown report so the two surfaces can't drift into two
    different conventions): `"{passed}/{evaluated} evaluated passed
    ({not_evaluated} not evaluated)"`. Not a config option, not a single
    denominator choice — the not-evaluated count is always present, even
    at zero, and the zero-evaluated case is spelled out in words
    (`"0 evaluated (N not evaluated)"`) rather than left as a confusing
    `0/0` fraction.
  - **Compatibility**: old (`"0.1"`) bundles — including the
    `runs/toy-smoke*` bundles already on disk — still validate and read;
    `validate_bundle`/`BundleReader` accept `LEGACY_BUNDLE_FORMAT_VERSIONS
    = {"0.1"}` alongside the current version, and a synthetic old-format
    bundle (missing every field this entry adds) is tested for continued
    validity and readability. The preserved incident fixture
    (`tests/fixtures/bundles/partial-infra-failure-2026-08-03/`) is
    unaffected either way — it has no `manifest.json` at all, so bundle
    *format version* compatibility doesn't apply to it; its own
    missing-file assertions (`tests/test_bundle_fixtures.py`) are
    unchanged and untouched, per its own never-regenerate rule.
  - **Deliberately not done, reported rather than silently deferred**:
    `TraceEventKind.EVALUATOR_SCORE`/`.POLICY_VERDICT` remain unwired
    (found by `tests/test_enum_construction_coverage.py` in the prior
    session) — assessed this session and deferred past W12a; see
    `ROADMAP.md` §0 for the reasoning, including a real design question
    (no existing precedent for a run-level, non-runner-emitted trace
    event) that `POLICY_VERDICT` specifically surfaced. `TerminationReason.
    INFRA_FAILURE` is wired for real now and removed from the enum
    construction-coverage allowlist.
  - Every new behavior proved fail-first: reverted, confirmed the specific
    failure, restored — covering all four CLI exit paths, model
    provenance, `git_dirty` against a real dirty tree, the
    outside-a-checkout sentinel, the version-string agreement, and the
    allowlist's own drift-detection.

- **OpenAI-backed `OpenAIRunner`** (`runner_id: "openai"`), a second, independent
  `AgentRunner` alongside `LangGraphRunner` — not a fix or replacement for it,
  and does not close W8 (see `docs/adr/0001-openai-runner-chat-completions.md`
  for the API-choice, model-config, and no-shared-abstraction decisions). Chat
  Completions API, same `calculator`/`finish` tool set re-expressed in
  OpenAI's function-calling shape, own price table (`gpt-4.1-mini-2025-04-14`),
  own `llm` extra pin, own mypy override, own import-linter contract (6th
  contract: "openai confined to agents/"). Config: `configs/toy_openai.yaml`.
  Offline unit tests only (`tests/test_openai_runner.py`), no network.
  - **Live verification attempted 2026-08-03, blocked before producing a
    result — an OpenAI account funding issue, not a code defect.** Every
    model call against `configs/toy_openai.yaml` failed with `429
    insufficient_quota`. 2 tasks attempted, 0 passed, `total_cost_usd = $0.00`
    actually incurred (OpenAI does not bill rejected 429s) — but this is a
    trivial zero-vs-zero, not a real cost-accounting check; that verification
    is still unperformed. `validate-bundle` and `replay` both correctly
    rejected the resulting incomplete bundle (missing required files).
    Recorded in full in ROADMAP.md §0; the bundle is kept at
    `runs/live-verify-openai-2026-08-03/`, not deleted.
  - **Parity bug found and fixed in the same session:** `config/loader.py`'s
    `_SUPPORTED_RUNNERS` allowlist had not been updated when `OpenAIRunner`
    was registered in `registry.py`, making `runner_id: "openai"` rejected
    from any YAML config even though the runner was otherwise fully wired
    up. Fixed; guarded by a new test
    (`test_supported_runners_matches_registry`) that asserts the allowlist
    equals the registry's registered runner names, confirmed to fail against
    the pre-fix state before being trusted.
- **W8 — real model-backed `LangGraphRunner`** (post-v0.1; enables v0.2). A
  registered `AgentRunner` (`runner_id: langgraph`) that drives a minimal
  ReAct-style agent (tools: `calculator`, `finish`) against the toy Q&A tasks
  using the Anthropic API directly, with per-response token/cost accounting
  (single-source price table sourced from the Anthropic model docs) and
  `ResourceLimits` enforcement (tokens/cost/wall-time → matching
  `TerminationReason`). The `anthropic` SDK is an optional `llm` extra imported
  lazily, so core stays installable without it and CI is unaffected; unit tests
  inject a fake client (no network), with one opt-in `@pytest.mark.live` test.
  Config gains `runner_id` (mock|langgraph) and `model`; the CLI selects the
  runner via the registry and surfaces a missing `ANTHROPIC_API_KEY` as a clean
  exit. Ships with `configs/toy_langgraph.yaml`.
  - *Scope/fallback:* file/sandbox tools deferred to a code-editing benchmark;
    runner is implemented on the `anthropic` SDK directly (pre-authorized
    fallback) under the `langgraph` name, with the `langgraph`/
    `langchain-anthropic` pins declared for a future port.
- **W9 — OpenTelemetry warmup scaffold.** `observability/otel_setup.py`
  establishes the *seam* for OTel instrumentation, no more: span-name and
  attribute-key constants mirroring the ARCHITECTURE §8 hierarchy, a
  `NoOpTracer`/`NoOpSpan` pair so future instrumented call sites are safe and
  free whether or not the extra is installed, and a `get_tracer()` that
  currently always returns the no-op. `configure_tracer()` is an explicit
  `NotImplementedError` stub. **This scaffold emits no spans.** There is no
  `TracerProvider`, no exporter, no `spans.jsonl` output, and zero
  instrumentation call sites anywhere in the orchestrator, runners, or
  evaluators — real span emission is a dedicated future W9 session's work.
  Adds the optional `otel` extra (`opentelemetry-api`/`-sdk`, imported lazily,
  not installed in CI) and its own `mypy.overrides` block.
  - *Caveat 1:* `tests/test_observability.py` gives this module 100% coverage,
    but that is coverage of a no-op — it modestly inflates the repo's headline
    coverage percentage without exercising any real span-emission behavior.
  - *Caveat 2:* this ships alongside a fix for a related but separate defect —
    W8 shipped with `mypy --strict` red in CI for two commits because the dev
    environment had the `llm` extra installed and CI did not, and nothing
    checked that they agreed. That is a process gap (no dev/CI parity check),
    not a one-off missing config line; it can recur for any future optional
    extra, including this one's `opentelemetry.*` override, if the same
    unchecked assumption holds.
- **W13 — import-linter layer contracts.** `[tool.importlinter]` in
  `pyproject.toml`: 5 contracts derived from ARCHITECTURE.md §4 (not
  reverse-engineered from today's imports) — a layers contract (`cli` >
  adapters/orchestration/bundles/replay/registry/config > `core`), an
  independence contract (the 8 existing adapter families must not import each
  other), and 3 forbidden-import contracts confining `anthropic`/`langgraph`/
  `langchain_anthropic` to `agents/`, `opentelemetry` to `observability/`, and
  `boto3`/`botocore` to `cloud/` (the last is vacuous today — `cloud/` doesn't
  exist until W17). `agentevalops.cloud` is excluded from the independence
  contract for the same reason: import-linter errors, not just fails, if a
  listed module doesn't exist. Wired into `make lint-imports` and a new
  `Check import layer contracts` step in the `ci` job (not `ci-min-deps`).
  Every contract was proven to actually fail against a deliberately injected
  violation, then reverted, before being trusted — a contract that's never
  been shown to fail isn't evidence of anything. **5/5 pass.**
  - *Mis-specified contract, corrected same day — not a code violation.*
    The anthropic-confinement contract initially failed against existing
    code: `cli`/`registry` transitively reach `anthropic` via
    `agents/langgraph_runner.py` (registry imports the runner to register
    it; the runner imports `anthropic` lazily). Diagnosis: ARCHITECTURE.md
    §4's confinement rule constrains *where the import statement lives*
    (`agents/`), not how far a name can be transitively reached from — a
    registry that resolves adapters by name must reference them, which is
    correct composition, not a leak. Genuine layering leaks are already
    caught by the separate layers contract. Fix: `allow_indirect_imports =
    true` on all three forbidden-import contracts (anthropic, opentelemetry,
    boto3) — not just the one that failed today, since the other two pass
    only because `otel_setup.py` has zero call sites and `cloud/` doesn't
    exist, and would break identically the moment W9 or W17 wires anything
    through the registry. Not an `ignore_imports` exemption — the semantics
    of what each contract detects changed, deliberately, for all call sites,
    not one path around a single case. Re-proved after the fix: each
    forbidden contract still fails against a fresh direct violation, and the
    anthropic contract is confirmed KEPT with the registry chain no longer
    tripping it.
  - *Tooling gap found along the way:* `python -m importlinter.cli
    lint-imports` — the invocation import-linter's own docs suggest — silently
    no-ops (exit 0, no output) regardless of contract status: `importlinter/
    cli.py` has no `if __name__ == "__main__":` guard. Anyone reaching for
    that module form in CI would get a permanently green gate that checks
    nothing. Use the `lint-imports` console script instead (what
    `Makefile`/CI actually call).
- **`tests/test_enum_construction_coverage.py`** — a mechanical check that,
  for every member of `TraceEventKind`, `Verdict`, and `TerminationReason`
  (`core/schemas.py`), asserts the member is constructed somewhere in
  non-test `src/`, or is named in an allowlist with a comment pointing at
  the roadmap item that will wire it. Written because the same "vocabulary
  exists, never constructed" pattern had already surfaced twice
  independently (`TerminationReason.INFRA_FAILURE`, `Verdict.WARN` before
  2026-08-03) — this test, on first run, found it a **third and fourth**
  time: `TraceEventKind.AGENT_OBSERVATION` and `.EVALUATOR_SCORE`/
  `.POLICY_VERDICT` are also never constructed anywhere. All four are now
  allowlisted with a roadmap reference each (`INFRA_FAILURE` and
  `AGENT_OBSERVATION` had existing anchors; `EVALUATOR_SCORE`/
  `POLICY_VERDICT` did not — a new `ROADMAP.md` §0 entry records the
  finding since none existed to point at). Proved to actually catch drift,
  not just pass by construction: temporarily removed `INFRA_FAILURE` from
  the allowlist, confirmed the test fails naming exactly that member, then
  restored it.
  - **Does not cover `ResultBundleMetadata.sealed`** (hardcoded `True` at
    its one write site, read back nowhere) — a different shape, a
    constant-valued field rather than an unconstructed enum member. A
    narrow, single-field "is `.sealed` ever read outside its write site"
    check would be cheap to add, but would fail immediately (nothing reads
    it today, and fixing that is blocked on the same orchestrator
    prerequisite as `INFRA_FAILURE`) and doesn't generalize to a reusable
    "any write-only field" checker without real judgment about which
    fields are *supposed* to vary — not built; see the report accompanying
    this change for the full reasoning.

### Fixed

- **No model-backed bundle recorded which model produced it — `OPENAI_MODEL`
  environment-variable fallback deleted, not written back.** Investigated
  the full path a model id takes to reach a runner (`docs/adr/0001-...`
  §3, revised): `OpenAIRunner` resolved `model` from three sources (explicit
  config field → `OPENAI_MODEL` env var → `DEFAULT_MODEL` constant) and
  `LangGraphRunner` had the identical gap in miniature (config field →
  `DEFAULT_MODEL`, no env var, but the same silent-default behavior). Per
  `docs/adr/0002-outcome-taxonomy.md`'s finding that `RunConfig.agent_id`
  in `config.json` is the static YAML string, never the runner's
  model-qualified value, either fallback path let a config-driven run
  proceed with a model the bundle would never record and that couldn't be
  reconstructed from a second, potentially-divergent source. Fixed by
  *removing* the ambiguous path rather than adding a write-back (a
  write-back would leave two sources that can disagree, not one that
  can't): `OPENAI_MODEL` is deleted entirely from `OpenAIRunner`, and
  `config/loader.py` now raises `ConfigurationError` at load time if a
  model-backed `runner_id` (`langgraph` or `openai`) omits `model` —
  failing loudly instead of silently defaulting. `configs/toy_openai.yaml`
  and `configs/toy_langgraph.yaml` updated to document `model` as required,
  not merely recommended.
  - **Does not fully close the provenance gap, and isn't meant to.**
    `config.json` still doesn't record the model, because `RunConfig` has
    no `model` field and none was added — that's a config-schema change,
    explicitly deferred to the next, schema-focused session per
    `docs/adr/0002`'s open questions (fix `agent_id` to reflect the
    resolved model, vs. add a dedicated `model_id` field — undecided).
    What changed here is that there is now exactly one legal source for
    the model on any config-driven run, not that the bundle records it
    yet.

- **`BasicPolicyChecker` was a fail-open release gate.** `check()` only ever
  inspected cost, the tool deny-list, and trace length; it never asked
  whether anything was actually evaluated. A run with zero evaluated results
  (an empty benchmark) or where every task failed at the infrastructure
  layer before finishing (an API outage — see the 2026-08-03 infra-failure
  gap below) tripped none of the three checks and silently returned
  `Verdict.PASS`, `"all checks passed"` — the same class of bug as a
  deny-list silent failure: the check ran, reported success, and inspected
  nothing. Reproduced first (both a directly-constructed zero-results input
  and the real `tests/fixtures/bundles/partial-infra-failure-2026-08-03/`
  fixture's actual trace both returned `PASS` against the pre-fix code).
  Fixed by downgrading a would-be `PASS` to `Verdict.WARN` — the pre-existing,
  previously-unused third member of the `Verdict` enum
  (`core/schemas.py`'s own docstring: "binary-plus-warn") — whenever
  `output.success` is `False` **and** no `AGENT_TERMINAL` event appears
  anywhere in the trace (every runner yields one as its last event on every
  completion path; the only way to have zero across the whole trace is if no
  task's `run()` generator ever finished). Deliberately conservative: a
  genuine finding from the three existing checks is never downgraded, so
  real cost/deny-list/trace-count violations still surface even on a run
  that didn't finish cleanly. No `Verdict` member added, no bundle-contract
  or schema change — `WARN` already existed. Scoped narrowly to this one
  checker; the orchestrator's exception handling, `RunSummary`, and the
  report are untouched (that's the taxonomy-design work, still open — see
  `ROADMAP.md` §0).
  - **Known gap, not covered by this fix, reported not fixed:** a run where
    *some* tasks were genuinely evaluated (any completed, so the trace has
    at least one `AGENT_TERMINAL`) and others infra-failed still returns
    `PASS` with no signal that part of the run was never evaluated — the
    presence of even one real terminal event masks the absence for the rest,
    since `check()` only sees the whole run's combined output and trace, not
    a per-task breakdown. Not currently reachable through the real
    orchestrator (today, any infra failure aborts the whole run before
    `check()` is ever called at all, for every task — see the gap below), so
    this is a forward-looking limitation, not a live bug.

---

## [0.1.0] — 2026-07-05

### Added

- **WBS 0** — Package scaffold: hatchling build, `src/` layout, Typer CLI stub,
  ruff + mypy + pytest quality gates, GitHub Actions CI.
- **WBS 1** — Core types, schemas (`RunConfig`, `TraceEvent`, `EvaluationResult`,
  `PolicyVerdict`, `RunSummary`), protocols, and error hierarchy.
- **WBS 2** — Local orchestration skeleton: `InMemoryTraceStore`,
  `MockAgentRunner`, `ToyBenchmarkAdapter`, `DeterministicEvaluator`,
  `BasicPolicyChecker`; `agentevalops run` CLI command.
- **WBS 3** — Result bundle writer: `BundleWriter` produces `metadata.json`,
  `config.json`, `traces.jsonl`, `evaluations.json`, `policy.json`,
  `summary.json`, `report.md`; `--output` flag on `run`.
- **WBS 4** — Bundle reader, local replay verifier, `agentevalops replay` CLI;
  read-only structural validation.
- **WBS 5** — Stronger `DeterministicEvaluator`, `SimpleScorer`,
  `BasicPolicyChecker`; improved `RunSummary` and `report.md`; pass-rate in CLI
  summary; bundle and replay compatibility.
- **WBS 6** — Centralised config loader with validation; five toy configs
  (`smoke`, `failure`, `policy_violation`, `trace_limit`, `mixed`); README and
  local-demo hardening; generated `runs/` git-ignored.
- **WBS 7** — Mature toy benchmark scenarios with per-scenario task definitions;
  improved `ToyBenchmarkAdapter`; richer deterministic evaluator behaviour.
- **WBS 8** — Bundle manifest (`manifest.json`): deterministic SHA-256
  checksums, file sizes, format version, run metadata, writer info;
  `BundleValidator` with 12 integrity checks; `agentevalops validate-bundle`
  CLI (exits 0/1); tamper detection; replay surfaces manifest status.
- **WBS 9** — CI and release hygiene: multi-Python CI matrix with mypy and CLI
  smoke; packaging build check workflow; `Makefile` developer commands;
  `.pre-commit-config.yaml`; `CONTRIBUTING.md`; `CHANGELOG.md`; release
  checklist; GitHub PR and issue templates; `SECURITY.md`; `CODE_OF_CONDUCT.md`;
  pyproject.toml classifiers/keywords/coverage config; pytest-cov support.
- **WBS 10** — v0.1 release candidate readiness: Apache-2.0 `LICENSE`; version
  bumped to `0.1.0`; pyproject.toml license metadata and classifier; README
  public-readiness pass; CHANGELOG, CONTRIBUTING, SECURITY, release-checklist
  finalized; `.gitignore` extended; `__init__.py` docstring corrected.
- **WBS 11** — Orchestrator concurrency + release hardening: bounded task
  concurrency via `asyncio.Semaphore(max_concurrent_tasks)` with
  deterministic result ordering regardless of concurrency; best-effort
  trace-store finalize on `KeyboardInterrupt`/task cancellation; disk-backed
  `LocalTraceStore` (flush+fsync per event) so `run --output` survives a
  hard process kill, not just an in-process interrupt; `agentevalops report
  --run-id` CLI command; interrupted-bundle recovery test coverage
  (`strict=False` reads an unsealed bundle).
- **WBS 12** — `v0.1.0` tag cut and pushed to `origin/main`. `ReportGenerator`
  protocol given its first real (non-mock) implementation:
  `RunSummary`/`TaskRunResult` moved from `orchestration/local.py` into
  `core/schemas.py` (pure data, no adapter deps — this is what let a core
  protocol reference them without a circular import); `ReportGenerator.render()`
  widened from `(ScoreResult, trace)` to `(RunConfig, RunSummary, trace,
  config_name)`, since a useful report needs cost/tokens/per-task
  results/policy verdict, none of which fit in a bare score aggregate;
  `MarkdownReportGenerator` added and wired into `BundleWriter._write_report`
  in place of the direct `render_report()` call. `ArtifactStore`/`CloudBackend`
  left intentionally mock-only (no caller exists for either yet). Audited
  ARCHITECTURE.md and ROADMAP.md against running code and fixed drift:
  corrected `src/` directory tree (WBS packages don't nest under `core/` the
  way early drafts described), replaced stale `pyright` references with the
  actual `mypy --strict` gate, and added explicit status callouts marking
  "replay" as bundle verification (not recorded-output re-execution) and
  OpenTelemetry/`CloudBackend`/`ArtifactStore` as not yet implemented.

### Not included in v0.1

- AWS / Bedrock backend.
- SWE-bench adapter.
- LLM-as-judge evaluator.
- Model-provider runners (LangGraph, Ollama, OpenAI Agents SDK).
- FastAPI dashboard or web UI.
- Remote artifact storage.
