# 0004. "Azure support" is two independent axes, only one of which is a
`CloudBackend`, and the `CloudBackend` one is not starting yet

## Status

Proposed — 2026-08-03.

## Context

"Should AgentEvalOps support Azure?" is not one question. It collapses two
independent concerns that happen to share a vendor name, and answering it as
one question risks either of two failure modes: refusing a cheap, already-
authorized addition because it sounds like the expensive one, or starting the
expensive one because a user said "we're an Azure shop" and meant something
much narrower. In practice, an enterprise saying "we're an Azure shop" usually
means *the model endpoint* — they want to call Azure OpenAI instead of the
Anthropic or OpenAI APIs directly — not the compute plane their evaluation
jobs run on. The two differ by roughly an order of magnitude in implementation
cost, and DESIGN.md already has a considered position on each half
independently; this ADR's job is to keep them from being merged into one
undifferentiated "Azure work" item.

### Axis B — Azure OpenAI as an `AgentRunner`

A model-provider concern. `DESIGN.md` §8 already states the platform's
non-goal here: *"It is not bound to one model provider. Anthropic, OpenAI,
Bedrock, Ollama, and OpenAI-compatible endpoints are all first-class."*
Admitting Azure OpenAI requires **no positioning change** to `DESIGN.md` —
it's exactly the kind of endpoint that non-goal already names as in scope.
Off the critical path, roughly one session of work, sized the same as
`OpenAIRunner` was (`docs/adr/0001-openai-runner-chat-completions.md`).

### Axis A — Azure as a `CloudBackend`

A compute/secrets/telemetry concern. Landing it means a real `cloud/azure/`
package, IaC, and live cloud debugging — the same category of work as
`AwsBackend`, not the same category as a new runner. This stays **out of v0.5
scope**, unchanged from `ROADMAP.md` §1's existing exclusion list.

### Verified findings (evidence, not assumed)

1. **The `CloudBackend` schemas ARCHITECTURE.md §2.8 shows in its code
   block — `JobSpec`, `JobHandle`, `JobStatus`, `ContainerSpec`, `SecretRef`
   — do not exist anywhere in the codebase.**
   `grep -rn "JobSpec\|JobHandle\|JobStatus\|ContainerSpec\|SecretRef" src/
   tests/` returns zero hits. The real protocol
   (`src/agentevalops/core/protocols.py:219-253`) uses plain
   `dict[str, Any]` and `str`:
   ```python
   async def submit_job(self, spec: dict[str, Any]) -> str: ...
   async def job_status(self, handle: str) -> dict[str, Any]: ...
   async def cancel_job(self, handle: str) -> None: ...
   async def fetch_secret(self, ref: str) -> str: ...
   async def export_telemetry(self, run_id: RunId) -> None: ...
   ```
   A protocol with zero implementations and a docstring describing schemas
   that were never built is not a stable surface to port anything against
   yet — see D2 below.

2. **The backend allowlist has no registry kind at all — a strictly worse
   version of the `_SUPPORTED_RUNNERS` shape already recorded in
   `ROADMAP.md` §0 (2026-08-03).** `config/loader.py:36`:
   `_SUPPORTED_BACKENDS: frozenset[str] = frozenset({"local", "aws"})` — a
   hand-maintained literal, not derived from anything. `registry.py` defines
   `KIND_BENCHMARK`, `KIND_RUNNER`, `KIND_EVALUATOR`, `KIND_POLICY`
   (`registry.py:43-52`) but **no `KIND_BACKEND`** — `grep -n "KIND_"
   registry.py` confirms it. The runner allowlist at least had *something*
   to drift out of parity with (`test_supported_runners_matches_registry`
   exists to catch exactly that drift); the backend allowlist doesn't even
   have a registry-side allowlist to be out of parity *with* — there's
   nothing for `"local"`/`"aws"` to diverge from because no backend is ever
   resolved through the registry at all.

3. **`backend_id` is carried into bundle metadata and the markdown report,
   but nothing dispatches on it.** `bundles/writer.py:193`:
   `backend_id=run_config.backend_id` (writes it into
   `ResultBundleMetadata`). `reports/markdown.py:71`: `f"- Backend:
   \`{run_config.backend_id}\`"` (prints it). `grep -rn "backend_id" src/`
   shows no other call site — no `if backend_id == "aws": ...` branch, no
   registry lookup, nothing. `LocalOrchestrator` runs every task directly
   against `AgentRunner`/`BenchmarkAdapter`/etc.; `backend_id` is metadata
   describing what *would* be used, not a value anything branches on today.

4. **`CloudBackend`'s docstring asserts a closed, two-member set, and
   `core/types.py` defines exactly those two members, no more.**
   `core/protocols.py:226-227`: `` `backend_id` MUST be one of
   `BACKEND_LOCAL` or `BACKEND_AWS` from `agentevalops.core.types`. Azure and
   GCP are out of scope for v0.1.`` `core/types.py:31-32`:
   `BACKEND_LOCAL: BackendId = BackendId("local")` /
   `BACKEND_AWS: BackendId = BackendId("aws")` — no `BACKEND_AZURE`, no
   `BACKEND_GCP`. This ADR does not change either file (see "Do not, in this
   session" in the originating ticket) — it only records that both become
   wrong the day `AzureBackend` actually lands (see Consequences).

5. **No new import-linter contract is implied by Axis B.** The existing 6th
   contract, `"openai confined to agents/"` (`pyproject.toml:341-361`), sets
   `forbidden_modules = ["openai"]` — a top-level module name. Azure OpenAI
   is reached through `openai.AzureOpenAI`, a class inside that same
   top-level package, not a separate import target. The contract already
   covers it; an `AzureOpenAIRunner` built on the `openai` SDK needs no new
   or modified contract, only to live in `agents/` like every other runner.

## Decision

**D1. Two independent axes, tracked and scoped separately, never merged
into one "Azure" line item.** Axis B (runner) and Axis A (backend) have
different owners-in-spirit, different DESIGN.md justifications, and — per
D2 below — different start conditions. Conflating them risks either
under-scoping Axis A (treating a compute-plane port as "just add Azure
OpenAI") or over-scoping Axis B (blocking a cheap runner addition on AWS
backend work that has nothing to do with it).

**D2. Axis A does not start before `AwsBackend` exists and v0.2 ships.**
`CloudBackend` has zero implementations today — no `AwsBackend`, no
`LocalBackend` (finding 1). Porting an abstraction that has never carried
one real implementation means guessing at the protocol's actual shape from
both ends simultaneously: without a concrete AWS implementation to see what
the protocol needs to look like in practice, and without a concrete Azure
implementation to see where AWS-specific assumptions leaked in, there is no
independent way to tell whether the protocol is genuinely cloud-neutral or
just AWS-shaped with a neutral-sounding docstring. `AwsBackend` first gives
the protocol one real data point; that's the minimum needed before a second
one is worth attempting.

**D3. The Azure primitive mapping (below) is written down now, before
`AwsBackend` is designed** — not after. This is the deliberate counter-
measure to the opposite failure mode: designing `AwsBackend` against
exactly one cloud's shape and ending up with an ECS-flavored protocol that
merely has an AWS-neutral-sounding docstring, the same gap finding 1 already
shows exists today. The cost of this counter-measure is one ADR read before
`AwsBackend`'s design session, not a week of Azure code — cheap insurance
against a protocol design that only *looks* portable until a second cloud
is actually attempted.

**D4. `AzureBackend` targets post-v0.5** (v0.6/v0.7 horizon), earliest after
v0.2 ships on Fargate (`ROADMAP.md` §3's milestone ladder). When it lands,
it is also the first real test of `DESIGN.md` §9's claim that *"the first
port to another cloud should be a two-week project, not a rewrite"* — a
claim that has never been exercised, since no second cloud backend has ever
been attempted against this protocol.

### Primitive mapping (AWS, planned, vs. Azure, post-v0.5)

| `CloudBackend` primitive | AWS (planned, W17+) | Azure (post-v0.5) |
|---|---|---|
| `submit_job` | ECS Fargate `RunTask` | Container Apps **Jobs** (ACI as fallback; AKS excluded by DESIGN.md §8 "not Kubernetes-first") |
| `job_status` | `DescribeTasks` | Job execution status API |
| `cancel_job` | `StopTask` | Stop execution |
| `fetch_secret` | Secrets Manager | **Key Vault** |
| `export_telemetry` | CloudWatch via ADOT | **Azure Monitor** via `azure-monitor-opentelemetry-exporter` |
| Bundle/artifact store | S3 | **Blob Storage** |
| Run index | DynamoDB | Table Storage / Cosmos DB (Table API) |
| IaC | CDK (Python) | **Bicep** or Terraform — no Python-native CDK parity |

### Asymmetries that constrain `AwsBackend`'s design (not Azure implementation notes)

These are recorded now, against the AWS side, precisely because D3 says the
mapping must inform `AwsBackend`'s design rather than be reverse-engineered
from it later:

- **Container Apps Jobs has no clean synchronous handle-returning
  equivalent of `RunTask`.** `submit_job(...) -> str` (today's real
  signature, finding 1) may be over-fitted to ECS's request/response shape
  in a way that doesn't generalize; `AwsBackend`'s design should not assume
  every cloud's job-submission API returns a simple, immediately-usable
  handle string the way `RunTask` does.
- **Azure authenticates via `DefaultAzureCredential`, a credential *chain*,
  not a single credential object.** This pushes back on `fetch_secret(ref:
  str) -> str` being a sufficient secrets surface for every cloud —
  `AwsBackend`'s design should not bake in the assumption that "authenticate,
  then fetch a named secret" is a universal two-step shape.
- **Axis B only — Azure OpenAI keys requests by deployment name, not model
  id**, which breaks `openai_runner.py`'s `_PRICE_PER_1M_USD` model-id
  lookup and needs an explicit deployment→model mapping in config.
  `api_version` is also a required parameter with no OpenAI equivalent. This
  asymmetry is Axis B's problem to solve when `AzureOpenAIRunner` is
  actually built (`ROADMAP.md` row 13b) — recorded here because it was
  surfaced during the same investigation, not because it affects `AwsBackend`.

## Prerequisite work identified, not implemented by this ADR

- `KIND_BACKEND` in `registry.py`, alongside the existing `KIND_BENCHMARK`/
  `KIND_RUNNER`/`KIND_EVALUATOR`/`KIND_POLICY`.
- `config/loader.py`'s `_SUPPORTED_BACKENDS` derived from the registry
  instead of hand-maintained, with a fail-first parity test mirroring
  `test_supported_runners_matches_registry` (finding 2).
- `LocalBackend` as the honest first implementation — not Azure, not even
  AWS; a real backend for the mode the platform already runs in by default,
  so `CloudBackend` has at least one concrete implementation before a second
  is attempted (see D2).
- **Open decision, not resolved here:** whether `JobSpec`/`JobHandle`/
  `JobStatus` (named in ARCHITECTURE.md §2.8's target code block but absent
  from the real protocol, finding 1) become real typed schemas, or whether
  the `dict[str, Any]`/`str` shape already in production is kept
  permanently. If job state ever reaches `metadata.json`, that decision
  becomes a bundle-contract change under ARCHITECTURE.md §3's
  `schema_version` rule, the same category of change `docs/adr/0002`
  already went through for the outcome taxonomy — not a decision to make
  informally inside a future backend PR.

## Consequences

- **`ROADMAP.md`'s §1 exclusion list must stop reading "Azure and GCP
  backends" unqualified** — it should name `CloudBackend` implementations
  specifically, since the Azure OpenAI *runner* (Axis B) is a separate,
  optional, off-critical-path item that the old wording would have
  incorrectly swept into the same exclusion.
- **Two `core/` edits become necessary later, but not now, and not by this
  ADR:** `core/protocols.py`'s `CloudBackend` docstring's `backend_id` MUST-
  clause (currently `BACKEND_LOCAL`/`BACKEND_AWS` only) and
  `core/types.py`'s constant set (currently only those two `BackendId`
  values) both become wrong the day `AzureBackend` actually lands — not the
  day Axis B lands, since a runner never touches `backend_id` at all. Until
  then both are accurate statements about the real code and should stay
  exactly as they are.
- **A future `azure.* confined to cloud/` import-linter contract** will be
  needed once `AzureBackend` exists, mirroring the `boto3`/`botocore`
  contract that is already wired but vacuous until `cloud/` exists
  (`pyproject.toml`'s `"boto3/botocore confined to cloud/ ... vacuous until
  cloud/ exists, W17+"` contract). Not added now, for the same reason the
  boto3 contract's own comment gives: import-linter errors, not merely
  fails, against a contract naming a module that doesn't exist yet.
- **`agentevalops.cloud` joins the independence contract** the day it's
  created — `pyproject.toml`'s existing independence contract comment
  already anticipates this explicitly (*"`agentevalops.cloud` is
  deliberately excluded: it does not exist yet ... add it here once `cloud/`
  is created"*). This ADR doesn't add it now; it confirms the existing
  comment's plan is still the right one.
- **DESIGN.md's cloud non-goal (§8) needs a precision sharpening, not a
  positioning change**, and ARCHITECTURE.md §6's closing "Azure and GCP are
  future roadmap items" paragraph is replaced by a pointer to this ADR's
  two-axis framing and mapping table, rather than dropped or duplicated —
  both edits are made alongside this ADR, in the same documentation session.
