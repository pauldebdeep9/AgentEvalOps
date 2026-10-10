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
| [0005](0005-agentic-assurance-rebaseline.md) | Re-anchor v0.5 on agentic assurance: MGF-Agentic §2.3.2's four test categories on a harness-owned tool environment; the result bundle becomes a signed, re-scorable evidence pack mapped to AI Verify Testing Framework process IDs; SWE-bench frozen, AWS and `ReplayRunner` deferred past v0.5. Re-baselines `ROADMAP.md` (rows R22–R34). Two findings: `PolicySpec.allowed_tool_ids` unconsumed; `_SUPPORTED_BENCHMARKS` has no parity test. **Proposed 2026-10-10; accepted 2026-10-10** (`DESIGN.md` synced; ADR-0006 and catalogue v0 not started). |
