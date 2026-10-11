# 0005. Re-anchor v0.5 on agentic assurance: Singapore's agent-testing guidance is the target, the result bundle becomes the evidence, SWE-bench and AWS leave the critical path

## Status

Proposed — 2026-10-10. Documentation-only: no code changes accompany this
ADR, and nothing was executed to write it (the code baseline cited below is
`CLAUDE.md`'s 2026-09-15 figure, not re-verified).

**Accepted — 2026-10-10.** `DESIGN.md` amended per "Required `DESIGN.md`
edits", 0003's Status pointer and the `docs/benchmark_adapters.md` freeze
banner added, in one docs commit (`ROADMAP.md` R22, part 1). ADR-0006 and
catalogue v0 — the rest of R22 — are not started.

**Q5 resolved — 2026-10-10 (owner decision).** The repository stays
private through v0.5, and a JOSS submission is not a v0.5 goal. What v0.5
has to serve is an organisation preparing an AI Verify Testing Framework
report for an agent it deploys. Where D2 and the `DESIGN.md` text below
say "published" live run, read: a live run committed to the private
repository and shared with partners. Private means unpublished, not
confidential: the code stays Apache-2.0. `ROADMAP.md` E7, R30, R34, §3 and
§8 carry the change; `DESIGN.md` §5 and §10 were updated to match.

**D6's pre-fill list replaced — 2026-10-11.**
[`docs/imda-alignment.md`](../imda-alignment.md) classifies all 112 process
checks against v0.5: 2 evidenced directly by a test run, 12 in part, 32
supported by a ready-made artefact, 9 reached only after v0.5, 49
organisational, 8 scoped to traditional AI. D6's list over-reached: 3.2.1,
3.5.1, 3.9.1 and 3.11.1 concern the organisation's own training data,
model artefacts and deployments; 3.4.1, 5.10.1 and 5.11.1 need production
logs (v0.7); 9.9.1 and 10.3.3, inside its ranges, are organisational; 3.3.1
and 3.10.1 are traditional-AI only. "Pre-filled" means an evidence
pointer, never an answer: each check's Yes / No / N/A and elaboration stay
with the organisation. No decision in this ADR is reversed: D2's "published"
is narrowed as above, D6's list is replaced, and D9's seed domain gains a
refer-to-officer tool. The same review amended rows R22, R24–R28, R30, R31
and R34 (`ROADMAP.md` §0).

- **Supersedes** `ROADMAP.md` §1's v0.5 scope and the forward plan in rows
  9, 10, 12c, 15 (remainder), 16, 16b and 17–34. The re-baselined plan is
  `ROADMAP.md` §2b, rows **R22–R34**, committed alongside this ADR. Old rows
  are annotated, not deleted — other documents cite them by number.
- **Amends** `DESIGN.md` §1, §3, §5, §7, §8, §9, §10 and §11 once
  accepted. Exact text is under "Required `DESIGN.md` edits" below, applied
  in one docs commit at acceptance (`ROADMAP.md` R22) — positioning drift
  is exactly what DESIGN.md §12 says must update that document.
- **Extends** [0003](0003-replay-semantics.md) Q6 with a middle comparison
  layer (D7). 0003's own decisions are unchanged; its Status line gains a
  pointer here.
- **Leaves [0004](0004-cloud-backend-two-axes.md) intact** — see
  Consequences for how its start condition reads after this re-baseline.
- **Requires two follow-up ADRs before the rows that depend on them start:**
  0006 (tool environment and observation seam — before R23) and 0007
  (evidence-pack format, bundle format 0.5 — before R28).

## Context

The question this ADR answers is not "should AgentEvalOps add compliance
features". It is narrower: Singapore spent 2026 publishing guidance that
asks organisations to test AI agents before deployment, and none of the open
tools built around that guidance — the AI Verify toolkit, Project Moonshot,
ASQI — tests agents. This repository's hardest-to-fake asset — a result bundle
with integrity checks, provenance and an honest outcome taxonomy — is most
of what that tool needs. Its current v0.5 headline (SWE-bench Verified on
AWS Fargate) answers a question none of that guidance asks.

### What changed outside this repository (checked 2026-10-10)

| Publication | Date | What matters here |
|---|---|---|
| AI Verify Testing Framework for Traditional and Generative AI (IMDA / AI Verify Foundation) | 2025 edition (PDF dated May 2025) | 11 principles → 88 outcomes → 112 process checks, counted from the PDF text, not estimated. Only 5 are tagged technical testing (2.1.1, 4.1.1, 6.1.1, 7.1.1, 7.2.1); 10 are GenAI-only; the string "agentic" occurs 0 times. Almost all evidence asked for is documentary. Stated audience: AI system owners/developers, internal compliance teams, external auditors. |
| Model AI Governance Framework for Agentic AI (IMDA) | v1.0 22 Jan 2026; **v1.5 20 May 2026** (updated 5 Jun) | §2.3.2 "Before deploying, test agents": overall task execution, policy compliance, tool calling, robustness — test whole workflows, agents individually and together, in realistic sandboxed environments, repeatedly with perturbations; deterministic tests for structured tool calls. §1.2.2: five negative outcomes (erroneous, unauthorised, biased/unfair actions; data breaches; disruption to connected systems). §1.1.3: four human-involvement levels. §2.1.1: impact and likelihood factors. §2.2.2 (v1.5): automation-bias practices — override rates, response times. §2.3.3: logging and monitoring. Ships no tooling. |
| Starter Kit for Testing LLM-Based Applications for Safety and Reliability (IMDA) | v1.0, Jan 2026 | Five risks (hallucination and inaccuracy, bias in decision making, undesirable content, data leakage, vulnerability to adversarial prompts); Identify → Test → Assess; thresholds set **before** testing. Agentic AI is deferred to "future updates". |
| Securing Agentic AI — Addendum to the Guidelines and Companion Guide on Securing AI Systems (CSA) | 17 Jun 2026 | Element taxonomy (components, system designs, capabilities) shared with ARC; rogue actions; data disclosure through agent manipulation; threat modelling plus taint tracing. Informational; no testing guidance. |
| Agentic Risk & Capability (ARC) framework (GovTech) | first release Aug 2025; site updated 12 Aug 2026 | 46 risks, 86 controls, crosswalks to the OWASP Top 10 for Agentic Applications, the CSA addendum and MGF-Agentic. Maps risk → control; does not execute tests. Repository is **Apache-2.0**. |
| AI Tester Accreditation Programme (AI Verify Foundation) | announced 18 May 2026; available Q3 2026 | Accredits testing *firms*; validates their claimed services against guidelines such as the Starter Kit; 12-month accreditation; emergent risks, deployment differences and third-party-integration risks are out of its scope. |
| Global AI Assurance Sandbox (AI Verify Foundation) | pilot Feb 2025 → ongoing | 30 applications, 14 sectors, 12 geographies; latest cohort 13 deployer–tester pairs, agentic applications included. |
| ISO/IEC 42119-8 proposal (IMDA, Enterprise Singapore) | 20 Apr 2026 | Proposed international standard for GenAI testing (benchmarking, red teaming), built on the AI Verify toolkit and the Starter Kit. Proposed, not adopted. |
| MAS Guidelines on AI Risk Management | finalised 7 Oct 2026 (press reporting) | Effective Oct 2027, phased to Oct 2028: inventory, materiality, lifecycle controls including testing, assurance over third-party AI. Agentic-specific guidance: industry consultation planned for 2027. |

**The tooling gap, as of 2026-10-10.** The AI Verify toolkit tests
supervised classification and regression models. Project Moonshot (v0.7.6)
benchmarks and red-teams LLM endpoints and ships the Starter Kit's
workflows; its README describes no agent testing. Resaro's open-source ASQI
Engineer (v0.5.9, Apache-2.0) runs containerised test packages against
`llm_api`, `rag_api`, image and VLM system types and "focuses first on
chatbot testing"; it requires Python 3.12+ and Docker. Resaro posted a role
on 11 Jul 2026 to build an agentic evaluation engine for its platform —
others see the same gap, and it will not stay open.

**Methodology evidence.** The third international joint testing exercise on
agentic evaluation (Singapore-led safety strand, arXiv 2601.15679) found
LLM-judge vs human discrepancy of 15–36% for one judge and 23–41% for the
other, with judges more lenient; in its cybersecurity strand, infrastructure
bugs affected 40% and 13% of the two models' failed cases. Its
recommendations — realistic tools, a solvability check per task,
scenario-level pass rates, repeated trials with sample sizes and intervals,
pass@k — are this repository's existing discipline, applied to agents.

### What this repository already has that fits

The outcome taxonomy that keeps "not evaluated" distinct from "failed"
([0002](0002-outcome-taxonomy.md)); provenance (`git_sha`, `git_dirty`,
model id, `replay_command.txt`); a checksummed manifest and a version-aware
`validate-bundle`; the hermeticity discipline (`docs/benchmark_adapters.md`);
registry entry points; a closed `TraceEventKind` that already has
`AGENT_TOOL_CALL` and `AGENT_TOOL_RESULT`; and — from row 15 part 1, D5,
implemented 2026-09-15 — `prepare_environment`/`teardown` on every
`BenchmarkAdapter`, which is precisely where a per-task tool environment is
created and torn down.

### Verified findings (evidence, not assumed)

1. **Runners cannot use anyone else's tools.** `agents/openai_runner.py:130`
   and `agents/langgraph_runner.py:102` each define a module-level
   `_TOOLS` containing exactly `calculator` and `finish`; dispatch is a
   hard-coded branch (`openai_runner.py:602-610` — any other name returns
   `"error: unknown tool"`). `AgentInput` reaches the runner carrying only
   the task and `context={"run_id": run_id}` (`orchestration/local.py:294-296`).
   No path exists for a benchmark or environment to supply tools, and every
   one of MGF §2.3.2's four categories needs one.
2. **Evaluators cannot see environment state.**
   `Evaluator.evaluate(task, output, trace)` (`core/protocols.py:166-171`),
   called with exactly those three arguments (`orchestration/local.py:320`).
   `ARCHITECTURE.md` §2.3 lists a state-based evaluator ("final
   filesystem/db/cloud state vs target") as first-class, but no argument can
   carry state to one.
3. **`PolicySpec.allowed_tool_ids` has no consumer — and row 11 is marked
   met as if it did.** Defined at `core/schemas.py:282`;
   `grep -rn allowed_tool_ids src/` hits only that definition (a schema test
   is its only other user). `config/loader.py` parses `deny_tool_ids` (line
   319) but not `allowed_tool_ids`, so it cannot even be set from YAML.
   `ROADMAP.md` row 11 describes `BasicPolicyChecker` as validating
   "(cost ceiling, allowlist)" and carries ✅ **Met**; what exists is the cost
   ceiling, the deny-list and the trace-count check
   (`policy/basic_checker.py`). This is W8's premature-closure shape, found
   a second time — and tool allowlisting is the least-privilege control
   that MGF-Agentic §2.1's first dimension, assessing and bounding risks
   upfront, turns on.
4. **`_SUPPORTED_BENCHMARKS` is the 2026-08-03 runner-parity bug, waiting.**
   `config/loader.py:37`: `frozenset({"toy"})`, hand-maintained. Runners have
   `test_supported_runners_matches_registry` (`tests/test_config_loader.py:405`);
   benchmarks have no equivalent, and `ROADMAP.md` never mentions it. The
   first new benchmark registered — this ADR's — is the trigger.
5. **Integrity is checksums, by design not signatures.** README: *"this is
   **not** cryptographic signing and provides no remote attestation or
   governance guarantee"* (`README.md:129-130`). The AI Verify Testing
   Framework's evidence list for responsible release (process 5.9.1)
   includes verifying model and data signatures and hashes.
6. **The plan as written has already reached its own AWS-cut threshold.**
   11 of the 20 remaining non-buffer rows (15, 16, 17–25) are phase A (AWS)
   or S (SWE-bench). The calendar is at row 21 (Oct 5–11); the latest
   closed work is row 15 part 1, 2026-09-15 — the uploaded snapshot's last
   commit — about six weeks behind. §6's ladder ties "six weeks behind" to
   cutting AWS entirely, checked at W32; the condition holds at W21.
7. **`DESIGN.md` §7's bundle tree does not describe the built bundle.** It
   lists `config.yaml`, `trace.jsonl`, `scores.json`, `artifacts/`, `logs/`,
   `cost_report.json`, `failure_analysis.md`. The real bundle (README
   "Result bundle anatomy"; `bundles/constants.py`) is `metadata.json`,
   `config.json`, `traces.jsonl`, `evaluations.json`, `policy.json`,
   `summary.json`, `report.md`, `manifest.json`, plus `replay_command.txt`
   from format 0.4. Corrected in the DESIGN.md edits below, since D6 adds
   files to it.

## Decision

**D1. Positioning and audience.** AgentEvalOps becomes an open,
local-first toolkit for testing agents against Singapore's frameworks —
aligned to them, never presented as endorsed by their publishers. The
audience is the one the AI Verify Testing Framework names for itself — AI
system owners and developers, internal compliance teams, external
auditors — plus third-party testing firms, including accredited ones. The
Applied Research Engineer persona leaves `DESIGN.md` §3. Singapore's
frameworks are the first-class target; international mappings are inherited
through the crosswalks those frameworks and ARC already publish (the testing
framework states its consistency with ASEAN, EU, OECD and US frameworks; ARC
crosswalks the OWASP Agentic Top 10). This project does not author its own
EU or NIST mappings.

**D2. v0.5 is "agentic baseline + evidence pack".** A built-in
`agentic-baseline` benchmark implements MGF-Agentic §2.3.2's four
categories — task execution, policy compliance, tool calling, robustness —
on a seeded synthetic domain (D9), scored by deterministic evaluators (D8),
reported per scenario and per §1.2.2 negative outcome, never as a
composite; packaged as a signed, re-scorable evidence pack (D6, D7); with
one published live run on one real model whose cost is reconciled against
the provider. This replaces "SWE-bench Verified subset on AWS Fargate". The
target week (Jan 4–10, 2027) is unchanged.

**D3. Deferred, not deleted:**
- **SWE-bench is frozen.** Row 14's validated result and
  `docs/benchmark_adapters.md` D1–D6 stand as reference. Row 15 part 1 (the
  D5 lifecycle methods) stays and is reused by D4. `SWEBenchAdapter` — the
  rest of row 15, row 16, and the Fargate runs in rows 23–25 — is not built
  for v0.5. The seventh import-linter contract stays: cheap, and still
  correct.
- **AWS leaves v0.5**: rows 16b and 17–22. `CloudBackend` stays
  protocol-only; the `boto3` contract stays vacuous. `LocalBackend` (16b)
  goes with it — it has no caller without a second backend.
- **`ReplayRunner` (W12c) is deferred.** D7's re-scoring delivers the part
  of re-verification an assessor needs. W12c's own start trigger ("real
  SWE-bench bundles") no longer arrives in v0.5 anyway.
- **LLM-as-judge (row 10) moves to v0.6**, and then only under D8's
  calibration rule. OpenTelemetry instrumentation (row 9) moves to the
  continuous-assurance milestone (v0.7).

**D4. Tools come from a harness-owned environment, and that environment is
the observation point.**
- Per task, `prepare_environment` creates a stateful, deterministic tool
  environment: tool specifications, a permission class per tool (read /
  write / irreversible), a simulated approver, and slots for injected faults
  and injected content.
- Runners obtain tool specifications from the environment and dispatch
  calls to it. Each runner keeps its own provider-specific loop —
  `openai_runner.py`'s docstring and [0001](0001-openai-runner-chat-completions.md)
  stand: *"Do not 'tidy' the two loops into one without new evidence from a
  third provider."*
- The environment records every call itself — name, arguments, permission
  class, approval state, injected fault, result — independently of what the
  agent reports. Policy and tool-use verdicts are computed from the
  **harness record**. The agent's self-reported `AGENT_TOOL_CALL` events
  become a cross-check; a mismatch between the two is itself a finding.
- The environment's end state is snapshotted before `teardown` and given to
  evaluators (finding 2).
- **Transport-neutral.** In-process function calling in v0.5. An MCP
  transport after v0.5 lets an agent the harness does not control — a
  vendor's — be tested against the same instrumented tools ("buyer mode"),
  which is what the testing framework's processes 3.12.1 and 9.9.x, MGF
  v1.5's third-party agents and MAS's third-party assurance expectation all
  ask for. This answers `DESIGN.md` §11's MCP question: yes — as the
  environment's transport, not as a separate feature.
- Exact types — whether the environment travels as a typed `AgentInput`
  field or through `context`, how evaluators receive end state (a change to
  `Evaluator.evaluate`), any new `TraceEventKind` and its `schema_version`
  consequence — are ADR-0006's to decide, before R23 starts.

**D5. A control catalogue as versioned data, with an explicit IP posture.**
- One machine-readable catalogue maps control identifiers across
  MGF-Agentic (v1.5 section numbers), the Starter Kit's five risks, the
  testing framework's process IDs, CSA addendum section references, and ARC
  risk/control IDs.
- Every source is pinned: title, version, date, URL, and the sha256 of the
  retrieved document.
- Every test declares the control IDs it evidences. Coverage is computed
  from **executed** tests, never from declarations: a control declared by a
  scenario that did not run reports as uncovered.
- **IMDA and AI Verify Foundation text: identifiers and our own paraphrased
  labels only.** The testing framework's notice: *"may not be reproduced,
  republished or transmitted in any form or by any means, in whole or in
  part, without written permission."* The same posture applies to the
  Starter Kit and MGF-Agentic until their terms are checked. A permission
  request is tracked as external dependency E1 in `ROADMAP.md` §2c.
- **ARC is Apache-2.0** (repository `LICENSE`, checked 2026-10-10): its IDs
  and labels may be included with attribution and a `NOTICE` entry.
- No "AI Verify" in any package, command or artefact name, and no
  "compliant" or "certified" wording anywhere (D10).

**D6. The evidence pack is the result bundle, at format 0.5.** Additive;
0.1–0.4 bundles keep validating.
- `test_plan.json` — the system declaration, selected scenarios, repetition
  count and seeds, thresholds (hard gates distinguished from comparative
  thresholds). Its sha256 is recorded before the first task executes, and
  `validate-bundle` rejects a bundle whose plan no longer matches it. This
  is pre-registration — the Starter Kit's "set thresholds before testing",
  made checkable.
- `controls.json` — computed coverage (D5).
- Per-task environment end states (D4).
- `evidence_report.md` — shaped like the testing framework: principle →
  process ID → *pre-filled from this run, with an evidence pointer* or
  *requires organisational evidence*. Roughly 30 of the 112 process checks
  are candidates for pre-fill (the 3.x traceability and reproducibility
  checks, 4.1.1, 5.9–5.12, 6.1.1, 6.5.1, 6.7.x, 8.3.2, 9.8.1, 9.9.x,
  10.3–10.5); ADR-0007 fixes the exact list against the catalogue.
- `manifest.json.sig` — a detached signature over `manifest.json` produced
  with `ssh-keygen -Y sign` and verified against an allowed-signers file.
  Chosen because it adds no Python dependency and needs no network — target
  users include air-gapped and agency environments. Sigstore keyless signing
  is a post-v0.5 option.
- `TraceEventKind.EVALUATOR_SCORE` gets wired (closing that `ROADMAP.md` §0
  item): the evidence report needs per-task verdict events.
  `POLICY_VERDICT`'s open run-level `step_index` question is ADR-0007's to
  decide.
- **Gating fails closed.** A new `agentevalops gate` command fails any gated
  category with `not_evaluated > 0`, even when every evaluated task passed.
  0002's always-both reporting is unchanged; the gate is a separate consumer
  of the same counts.
- Exact file formats are ADR-0007's, before R28 starts.

**D7. Re-scoring is the evaluation layer of 0003's comparison stack.**
`agentevalops rescore --bundle <dir>` re-runs the current deterministic
evaluators and policy checks over the bundle's recorded harness-side tool
records and end states, and compares the results with the recorded
verdicts. Outcomes reuse 0003 Q6's vocabulary with an explicit layer —
**structural** (`validate-bundle`, built) → **evaluation** (`rescore`, new)
→ **behavioural** (`ReplayRunner`, deferred) — each layer running only if
the previous one passed. Re-scoring is the first real producer of
`MATCH`/`DIFFER`/`CODE_CHANGED`, so it does not repeat the "vocabulary with
nothing to construct it" pattern 0003 explicitly warns against. Model-judged
results, once they exist, are recorded rather than re-derived and are
excluded from a `MATCH` claim.

**D8. Scoring basis: deterministic first, repeatability measured, judges
only when calibrated.**
- v0.5 adds `tool_use` and `state_based` evaluators — two of `DESIGN.md`
  §9's five kinds — alongside the existing `DeterministicEvaluator`.
  MGF-Agentic §2.3.2 itself recommends deterministic tests for structured
  tool calls.
- Each scenario runs k times (k in `test_plan.json`); results report pass^k
  and per-scenario counts, with confidence intervals where k allows.
- LLM-as-judge (v0.6+) is used only for unstructured reasoning, and only
  with a human-agreement measurement stored in the bundle; a judge without
  one cannot feed a gate.
- **Environment defects are not agent failures.** Every scenario ships a
  scripted known-good trajectory; if that trajectory fails, the scenario is
  defective and its runs are recorded `not_evaluated` with that cause —
  the joint-testing lesson mapped onto 0002's taxonomy.
- `allowed_tool_ids` gets its first consumer as part of the
  policy-compliance category (finding 3), or ADR-0006 removes it in favour
  of per-tool permission classes. Either way it does not stay an unconsumed
  field.

**D9. Domain packs are plugins; v0.5 ships one seed domain.**
- A domain pack is a materiality profile (MGF §2.1.1's impact and
  likelihood factors), scenarios, synthetic tools, SOP and approval
  policies, thresholds, and catalogue extensions — discovered through
  registry entry points like any other adapter.
- v0.5's seed domain is synthetic public-sector case handling: case lookup,
  status update, an outbound official notice (irreversible, approval-gated),
  a records export (restricted data), and a policy-document search (the
  carrier for injected content). It exercises all four categories, and
  ARC's "Official Communication" and "File & Data Management" capabilities.
- Financial services (timed to MAS's 2027 agentic consultation) and
  industrial/OT (agents with write access to schedulers or MES: disruption
  to connected systems, irreversibility) are post-v0.5 packs.

**D10. Interoperate instead of reimplementing; new non-goals.**
- LLM-level Starter Kit tests run through Moonshot (an adapter after v0.5),
  not reimplemented; attack libraries likewise.
- Classical-ML fairness and explainability tests (processes 2.1.1, 7.1.1)
  are the AI Verify toolkit's job — never in scope.
- ASQI test-package export comes after v0.5. ASQI is an export target, not
  a base: it needs Python 3.12+ and Docker, while this repository supports
  3.10+ (`pyproject.toml:11`; CI matrix 3.10–3.12) and the baseline runs
  without Docker.
- New `DESIGN.md` §8 non-goals: not a certification or compliance verdict
  (the accreditation programme accredits testers, not tools, and the
  Sandbox explicitly offers no regulatory certainty); not a
  governance-workflow (GRC) product — the ~80 documentary checks are
  attachments in the evidence report; does not reproduce third-party
  framework text; not a capability leaderboard; does not fine-tune or
  remediate models (Quench's side of that boundary).

## Alternatives considered and rejected

- **Keep SWE-bench-on-Fargate as v0.5's headline; do assurance afterwards.**
  Rejected. It answers a capability-ranking question; every framework in
  the Context table asks a use-case-risk question. The plan has already
  reached its own AWS-cut condition (finding 6), and the agent-testing gap
  is time-limited.
- **Build as a Moonshot plugin.** Rejected for v0.5. Moonshot's documented
  scope is benchmarking and red-teaming LLM endpoints; nothing in it covers
  stateful tool environments or trajectory scoring. The LLM layer
  integrates later (D10).
- **Build as ASQI test packages from the start.** Rejected. Beyond the
  Python 3.12+/Docker requirement, D4's observation-at-the-tool-boundary
  design needs the harness to own the tool environment — a container
  package can host that later, but should not define it.
- **Cover the whole testing framework (all 112 checks).** Rejected. About 80
  of them ask for organisational documents — governance-workflow territory
  (D10).
- **LLM-as-judge for policy compliance in v0.5.** Rejected. D8; the
  joint-testing discrepancy figures.
- **Sigstore keyless signing in v0.5.** Rejected. It needs network access
  and an OIDC identity; air-gapped targets would lose verification. Revisit
  after v0.5.
- **Treat the agent's own trace as the evidence.** Rejected. An agent's trace
  is a claim about what it did; the harness record is evidence of what it
  did (D4).
- **Keep W12c ahead of re-scoring.** Rejected. Behavioural replay answers
  "did the platform itself break" (`ARCHITECTURE.md` §7); an assessor needs
  "can this verdict be re-derived from this evidence". Re-scoring answers
  that at a fraction of W12c's cost — W12c is rated H and was re-estimated
  in weeks, not days.

## Open questions

- **Q1 — The Starter Kit's agentic update.** If IMDA publishes agent-testing
  categories that differ from MGF §2.3.2, the catalogue maps both; scenario
  IDs do not change. Revisit on publication.
- **Q2 — AI Verify Foundation permission (E1).** With permission, the
  evidence report can carry the framework's own process titles; without it,
  identifiers and paraphrase (the default).
- **Q3 — ARC's machine-readable form.** The repository has an
  `arc-risk-register/` folder; its format is unverified. R30 checks it
  before importing anything.
- **Q4 — Simulated approver semantics.** Approve / deny / timeout
  behaviour, and how "approval was requested" is detected for each of MGF
  §1.1.3's four involvement levels — ADR-0006.
- **Q5 — Repository visibility.** JOSS's six-month public-history
  requirement — the same rule that puts Quench's earliest submission at
  late November 2026 — applies here too. An owner decision, not an
  engineering one.
- **Q6 — A funded model account (E3).** Three runners have never had cost
  reconciled against a provider (`ROADMAP.md` §0's standing gap). R30
  closes it for one.
- **Q7 — `POLICY_VERDICT`'s run-level `step_index`** (`ROADMAP.md` §0, W12a
  assessment) — ADR-0007.

## Consequences

- **In the same commit as this ADR:** `ROADMAP.md` is re-baselined (§0
  entry, §1, §2 annotations, §2b rows R22–R34, §2c external dependencies,
  §3–§9), and `docs/adr/README.md` gains a row for 0005.
- **On acceptance (R22), in one docs commit:**
  - `DESIGN.md` is amended (exact text below).
  - 0003's Status line gains: *"Q6 extended 2026-10-10 by
    [0005](0005-agentic-assurance-rebaseline.md) D7 — an evaluation layer
    (`rescore`) between structural and behavioural comparison."*
  - `docs/benchmark_adapters.md` gains a one-line banner: *"Frozen
    2026-10-10 by ADR-0005 D3 — reference only; the rest of row 15 and row
    16 are not scheduled."*
  - This ADR's Status and its `docs/adr/README.md` row change to Accepted.
- **0004 is not edited.** Its mechanical check stays correct — the first
  clause (`grep -rn "class AwsBackend" src/` returns a hit) still gates
  Azure-as-backend work. Its second clause's "v0.2" named the superseded AWS
  milestone; read it as "the first milestone that ships `AwsBackend`".
- `ARCHITECTURE.md` is unchanged by this ADR; ADR-0006 and ADR-0007 bring
  their own edits (new protocol and package, new bundle files).
- The bundle-format bump to 0.5 lands with R28 under ADR-0007, per
  `ARCHITECTURE.md` §3's `schema_version` rule.
- New packages (working names `environments/`, `controls/`, `evidence/`)
  join the import-linter layers and independence contracts in the rows that
  create them, each contract proven to fail against an injected violation
  before it is trusted (row 13's precedent).
- Findings 3 and 4 become explicit sub-tasks of R25 and R23.

### Required `DESIGN.md` edits (on acceptance, R22)

1. **§1** — replace the bold definition with:
   > **AgentEvalOps is a local-first, open-source toolkit that tests
   > tool-using AI agents against Singapore's agentic-AI testing guidance
   > and produces signed evidence packs — mapped to the AI Verify Testing
   > Framework — whose verdicts a third party can re-derive from the
   > recorded runs.**
2. **§3** — replace the three personas with, in priority order:
   - **The AI System Owner** (primary): owns an agent headed for
     production and needs pre-deployment baseline tests, a fail-closed
     release gate, and evidence for the testing framework's and
     MGF-Agentic's checks — from their own CI, on their own infrastructure.
   - **The Third-Party Tester** (secondary): a testing firm, accredited or
     applying, that needs a consistent, documented, reproducible method and
     portable evidence its clients can verify.
   - **The Reviewer** (tertiary): internal compliance, external auditors,
     procurement. Consumes evidence packs; needs to verify integrity and
     re-derive verdicts without re-running anyone's agent.

   Remove the Applied Research Engineer. In the closing paragraph, replace
   "(the procurement officer, the data scientist using a coding assistant)"
   with "(the citizen using a service agent, the data scientist using a
   coding assistant)" — procurement is now a reviewer of evidence.
3. **§5** — append after the v0.1 paragraphs:
   > **v0.5 (Agentic baseline + evidence pack).** MGF-Agentic §2.3.2's four
   > test categories, run against a harness-owned tool environment on a
   > seeded synthetic domain, scored deterministically, repeated k times,
   > gated fail-closed, and packaged as a signed bundle a reviewer can
   > validate and re-score on a fresh clone. One published run on one real
   > model. See `docs/adr/0005-agentic-assurance-rebaseline.md`.
4. **§7** — replace the tree with the bundle as built, plus format 0.5's
   additions: `metadata.json`, `config.json`, `traces.jsonl`,
   `evaluations.json`, `policy.json`, `summary.json`, `report.md`,
   `replay_command.txt`, `manifest.json`; then `test_plan.json`,
   `controls.json`, per-task environment end states, `evidence_report.md`,
   `manifest.json.sig` (final names per ADR-0007).
5. **§8** — in "not bound to one cloud provider", replace "AWS is the
   planned cloud backend" with "No cloud backend is planned before v0.6
   (ADR-0005 D3); ADR-0004's two-axis framing stands." Then add:
   - **It is not a certification scheme or a compliance verdict.** It
     produces evidence. Accreditation applies to testers, not tools, and
     acceptance of residual risk stays with the deploying organisation.
   - **It is not a governance-workflow (GRC) product.** Documentary
     evidence is attached to the evidence report, not managed by it.
   - **It does not reproduce third-party framework text.** Identifiers and
     paraphrase only, unless the publisher grants permission.
   - **It is not a capability leaderboard.** Research benchmarks can run
     through `BenchmarkAdapter`, but none is on the roadmap.
   - **It does not fine-tune or remediate models.** That is Quench's side
     of the boundary.
6. **§9** — retitle "AWS-first for cloud, cloud-neutral core" to "Local-first;
   cloud deferred", keeping the `boto3` confinement rule. In "Reproducibility
   is a tested property", replace the replay CI test with: "the CI suite
   re-scores golden fixture bundles with `rescore` and requires `MATCH`;
   behavioural replay joins it when `ReplayRunner` exists."
7. **§10** — replace the v0.5 and v1.0 paragraphs:
   > At v0.5, success is a published live baseline run on one real model
   > whose signed evidence pack validates, verifies and re-scores `MATCH`
   > on a fresh clone, with cost reconciled against the provider. At v1.0,
   > success is three properties together: (a) at least two domain packs;
   > (b) buyer mode over MCP; (c) at least one evidence pack produced by
   > someone other than the author, against their own agent — ideally
   > inside the Global AI Assurance Sandbox.
8. **§11** — resolve three questions in place: the MCP question (yes, as
   the tool environment's transport — ADR-0005 D4); the LLM-judge
   disagreement question (surface, never average; calibrated judges only —
   D8); the SWE-bench vendoring question (moot while SWE-bench is frozen —
   D3). The hosted-cost-model question stays open.
