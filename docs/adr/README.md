# Architecture Decision Records

No ADR convention existed in this repo before `0001` — searched for `*adr*`
and `*decision*` under the repo root, found nothing. This directory and the
lightweight format below are established by `0001`, not carried over from a
prior pattern.

**Format:** one Markdown file per decision, numbered sequentially
(`NNNN-short-title.md`), with `Status` / `Context` / `Decision` /
`Consequences` sections. Superseding a decision means adding a new numbered
file that says so and updating the old one's `Status` line — never editing
history in place.

**When to add one:** a decision with consequences that outlive the PR that
made it — a provider choice, a boundary/abstraction call, anything a future
session might be tempted to "clean up" without knowing why it's shaped that
way. Routine implementation detail belongs in code comments or the commit
message, not here.

| # | Title |
|---|---|
| [0001](0001-openai-runner-chat-completions.md) | OpenAI-backed agent runner: scope, API choice, model-id config, no shared provider abstraction. Model-provenance gap closed 2026-08-05. |
| [0002](0002-outcome-taxonomy.md) | Unified outcome taxonomy: five axes (task outcome, evaluation outcome, run aggregation, bundle integrity, replay comparison), not one enum. **Implemented 2026-08-05 (W12a).** |
| [0003](0003-replay-semantics.md) | Replay semantics: what it asserts, the determinism boundary, nondeterminism enumerated from real bundles, `replay_command.txt`, fixture strategy. Depends on 0002 (implemented). **Q1/Q4 implemented 2026-08-04 (row 12b): `validate-bundle` is the structural entry point, `replay` a deprecated alias, `replay_command.txt` real.** `ReplayRunner`, golden fixtures, and behavioral comparison (Q2/Q3/Q5/Q6's second layer) remain unimplemented — W12c/W29. |
| [0004](0004-cloud-backend-two-axes.md) | Azure support is two axes — Azure OpenAI as an `AgentRunner` (Axis B, no positioning change needed, off critical path) vs. Azure as a `CloudBackend` (Axis A, stays out of v0.5, doesn't start before `AwsBackend` exists). Primitive mapping table and asymmetries recorded now, before `AwsBackend` is designed. **Axis B implemented 2026-08-03 (row 13b, `AzureOpenAIRunner`); Axis A not started.** |
| [0005](0005-agentic-assurance-rebaseline.md) | Re-anchor v0.5 on agentic assurance: MGF-Agentic §2.3.2's four test categories on a harness-owned tool environment; the result bundle becomes a signed, re-scorable evidence pack mapped to AI Verify Testing Framework process IDs; SWE-bench frozen, AWS and `ReplayRunner` deferred past v0.5. Re-baselines `ROADMAP.md` (rows R22–R34). Two findings: `PolicySpec.allowed_tool_ids` unconsumed; `_SUPPORTED_BENCHMARKS` has no parity test. **Proposed 2026-10-10; accepted 2026-10-10** (`DESIGN.md` synced; ADR-0006 and catalogue v0 not started). **Q5 resolved 2026-10-10: repository private through v0.5, JOSS not a v0.5 goal; D6's pre-fill list replaced by [`docs/imda-alignment.md`](../imda-alignment.md).** |
| [0006](0006-tool-environment-and-observation-seam.md) | Tool environment and observation seam: a harness-owned environment supplies and records every tool call; evaluators and policy read the harness record through an `EvaluatorContext`; several evaluators per task, a real failure wins; the orchestrator owns repetitions; bundle format 0.5 in R24 (amended 2026-11-01), evidence pack 0.6; synthetic identifiers that cannot be a real person's. Proposed 2026-10-31; **Accepted 2026-11-01.** |
| [0007](0007-evidence-pack.md) | The evidence pack, bundle format 0.6: every bundle pre-registers a plan (an undeclared run writes a minimal one; plan version 2); `controls.json` with coverage in five states computed from evaluated scenarios and each evidencing scenario's pass^k, no per-control aggregate; `evidence_report.md` over all 112 testing-framework checks with pointers only where a citing scenario ran, answers blank, deterministic; `git_sha` means the package's tree from 0.6, with `package_sha256` and the caller's directory kept as information; the 112 process entries as catalogue data under source `testing-framework`, scenarios citing the 14 direct and partial checks directly; `EVALUATOR_SCORE` wired, `POLICY_VERDICT` retired; `manifest.json.sig` named now so R29 adds no bump. R28 in two build sessions: (a) data and library code with no bundle change, (b) the 0.6 bump as one slice (amended at acceptance). Proposed 2026-11-01; **Accepted 2026-11-01; amended 2026-11-02; built 2026-11-02 (R28 met, format 0.6 at `3a2d0bc`).** |
| [0008](0008-signing-and-rescore.md) | Signing and rescore (R29): `agentevalops sign` puts a detached `ssh-keygen -Y` signature over `manifest.json` with an explicit key and namespace `agentevalops-bundle`, refusing an invalid bundle or an existing `.sig`; `validate-bundle --allowed-signers` verifies and names the signer, and `gate --require-signature` can demand it; `agentevalops rescore` re-derives every deterministic verdict, the summary, the policy verdict and coverage from a 0.6 bundle's own records, read-only, as `MATCH` / `DIFFER` / `CODE_CHANGED` (exit 0/1/3; 2 cannot rescore) over five provenance items; a new check that `evaluations.json` agrees with the summary; no format change; `report.md` dated by the bundle; signing tests never skip in CI. Proposed 2026-11-02; **Accepted 2026-11-02**, with decisions 9-11 added; R29 in two sessions. |
