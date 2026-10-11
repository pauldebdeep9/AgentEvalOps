# IMDA alignment — what the v0.5 evidence pack contributes to an AI Verify Testing Framework report

> **Status:** review, 2026-10-11. **Covers:** v0.5 as planned in [`ROADMAP.md`](../ROADMAP.md) §2b, including the changes this review made. **Source:** *AI Verify Testing Framework for Traditional and Generative AI*, 2025 edition (PDF dated May 2025; sha256 of the copy reviewed `363e59d140896b2ab75dffe508b03a8339e164fcfdab192b61862fb86c282379`), read with MGF-Agentic v1.5 and the Starter Kit v1.0 as cited in [ADR-0005](adr/0005-agentic-assurance-rebaseline.md). **IP posture:** identifiers and this project's own labels only (ADR-0005 D5); no sentence of the source appears here.
>
> This is the project's own reading of public guidance. IMDA and the AI Verify Foundation have not reviewed or endorsed it, and nothing in it is a compliance claim.

---

## 1. Why this exists

On 2026-10-10 the owner decided that the repository stays private through v0.5 and that a JOSS submission is not a v0.5 goal (`ROADMAP.md` E7; ADR-0005 Q5). The package's job for v0.5 is narrower and more practical: serve an organisation that has to prepare an AI Verify Testing Framework report for an agent it deploys.

That report is the framework's process checklist. For each of its 112 process checks, the organisation records Yes, No or N/A, an elaboration, and the evidence behind it. This document goes through the checklist one check at a time and asks what a v0.5 evidence pack contributes — and with that, whether ADR-0005's direction is the right one. The answer changed parts of the WBS (§8).

Two rules hold throughout:

- **Evidence pointers, never answers.** The evidence report says where in a bundle the evidence for a check is. It never fills in Yes, No or N/A, or the elaboration; those are the organisation's (ADR-0005 D10).
- **Coverage counts only what ran.** A check has evidence in a bundle only if the scenarios behind it executed in that run (ADR-0005 D5).

## 2. Verdict

**ADR-0005's direction holds. No decision in it is reversed.**

- An agent built on a language model is a generative-AI system under the framework, so 104 of the 112 checks apply to the agent itself. The other 8 are scoped to traditional AI and apply only if the system also contains a traditional model, which the package does not test (ADR-0005 D10 leaves those models to the AI Verify toolkit).
- As of October 2026 (ADR-0005's tooling check), the tools the framework points to test models. The AI Verify toolkit tests classical models; Project Moonshot benchmarks and red-teams model endpoints. Neither tests what an agent does with its tools. The checks where an agent's actions decide the answer are the ones v0.5 is built for: repeatability within the owner's limits (3.6.1), robustness to perturbation (6.1.1), performance on a curated test set (3.12.1), release gating (5.9.1), personal data in output (8.3.2), equal treatment of comparable cases (7.2.1) and referral of unclear cases to a person (10.3.2).
- Three of the framework's technical-testing checks apply to generative AI: 4.1.1, 6.1.1 and 7.2.1. v0.5 now evidences all three, at least in part. Before this review nothing in the plan evidenced 7.2.1, and the first cut on `ROADMAP.md` §6's scope-cut ladder removed the perturbations 6.1.1 asks for.
- Few checks are evidenced in full. Only 3.6.1 and 6.1.1 lie wholly within what a test run can show. For the other 12 checks a run evidences, part of the answer is always the organisation's, which is how the framework is built rather than a gap in the package.
- Most of the checklist is documentary, as ADR-0005 expected. 49 checks are the organisation's own policy, people and production practice. For 32 more, the package supplies an artefact the organisation can cite.

| Class | Checks | Meaning |
|---|---:|---|
| Direct | 2 | A v0.5 test run produces the evidence the check asks for |
| Partial | 12 | A test run evidences part of the check; the rest is the organisation's, or a later version's |
| Supporting | 32 | An organisational check for which a v0.5 artefact is a ready-made input |
| Later | 9 | Organisational in v0.5; v0.6 (model-level content tests) or v0.7 (production monitoring) adds evidence |
| Organisational | 49 | The organisation's own policy, people or operations; nothing specific from the package |
| Traditional AI only | 8 | Scoped by the framework to traditional AI; they apply only if the system also contains a traditional model |
| **Total** | **112** | |

The review made four kinds of change:

1. **Gaps closed.** Five things had no test behind them: unequal treatment of otherwise identical cases, disruption to connected systems, personal data appearing in output, what an affected person is told about a decision, and referral of unclear cases to a person. R24–R27 gained sub-tasks (§8).
2. **An over-reach corrected.** ADR-0005 D6's pre-fill list included checks that no pre-deployment test can evidence (§9). ADR-0005's Status records the correction.
3. **An ordering bug fixed.** The evidence report (R28) is generated from the testing framework's process entries, but the plan loaded them into the catalogue in R30, two weeks later. They now load in R28.
4. **A new risk.** Synthetic identifiers that belong to real people (`ROADMAP.md` §5, risk 9).

**A limit on what the evidence says.** It describes the agent's behaviour in the domain that was tested. v0.5's seed domain is synthetic public-sector case handling, modelled on agency work; any other organisation needs a domain pack for its own tools and cases (ADR-0005 D9). R24 builds the seed domain as the first pack. A guide to writing one is scheduled in R31 as slack and may slip to v0.6.

## 3. The 14 checks a test run evidences

*Direct*: a v0.5 test run produces the evidence the check asks for. *Partial*: it produces part of it. *Rows* are `ROADMAP.md` §2b rows.

| Check | Our label | Class | What a v0.5 run produces | Rows | What stays with the organisation |
|---|---|---|---|---|---|
| 3.6.1 | Repeat inferences; check outputs stay within owner-set limits | Direct | Each scenario repeated k times with seeds; pass^k per scenario against thresholds fixed in `test_plan.json` before the first task ran, which the owner can set or tighten | R27 | Choosing the limits |
| 6.1.1 | Performance under noise and prompt perturbation | Direct | Task text perturbed by at least one deterministic family (typos and punctuation) and scored on the same metric as the originals | R26 | Which perturbations matter to its users |
| 1.1.3 | Users can tell they face an AI, and where content came from | Partial | Every outbound notice in the tested domain is checked for an AI-use disclosure | R25 | Labelling in its own channels; showing content lineage |
| 1.1.4 | Explain an AI-influenced decision and its automation to those affected | Partial | Each notice is checked for the disclosure and for the reason codes the case's seeded facts call for | R25 | Explanations outside the tested notice channel |
| 3.7.1 | Name the technical and business metrics used for assessment | Partial | `test_plan.json` records every technical metric and its threshold, and the business metrics the owner declares (name, definition), without computing them | R27 | Identifying the business metrics, and measuring them |
| 3.12.1 | Test performance on a curated set, including vendor claims | Partial | The curated scenario set run against the agent and the model behind it; R30's live run does this on a third-party model | R24, R30 | Setting results against the vendor's accuracy claims; a scenario set for its own domain (a domain pack) |
| 4.1.1 | Benchmark and red-team for harmful outputs | Partial | Red-teaming at the agent level: instructions injected through tool results and retrieved documents, scored on what the agent then does | R26 | Content-harm benchmarks on the model (Project Moonshot today; an adapter in v0.6) |
| 4.3.1 | Use-case risk assessment, red-teaming and safeguard checks | Partial | Safeguards stated as controls and tested (approval gates, least privilege, the restricted-data boundary), with red-teaming and fault-injection results | R25, R26 | The risk assessment itself, the model card and the halt plan |
| 4.10.1 | Test-and-validation record: ground truth and adversarial tests | Partial | A pre-registered plan, scoring against seeded ground truth, adversarial scenarios, and a solvability check (a known-good trajectory) per scenario | R24, R26, R27 | Measurement-error and construct-validity arguments for its own metrics |
| 5.9.1 | Release gated on security testing | Partial | `gate` fails a run with any unevaluated task in a gated category; security scenarios (injection, least privilege) in the evidence; the pack is hashed and signed | R25, R26, R27, R29 | The release decision and procedure; signature checks on model artefacts |
| 7.2.1 | Benchmark and red-team for harm to people or groups | Partial | Counterfactual pairs: the same case with one protected attribute changed must reach the same decision and end state | R26 | Bias and toxicity benchmarks on the model; deciding whether the scores are acceptable |
| 8.3.2 | Detect personal or sensitive data in outputs | Partial | A detector for NRIC/FIN numbers (S, T, F, G and M prefixes, checksum-validated), phone numbers and e-mail addresses in every outbound message, notice and export. In a run it catches the seeded identifiers, which use a test-only scheme (risk 9); its own tests cover the real prefixes and checksum-invalid look-alikes | R25 | Other kinds of personal or sensitive data; anonymisation and privacy-enhancing measures |
| 9.9.2 | Understand a third-party model's data, evaluation and weak spots | Partial | Where the agent fails, scenario by scenario, when it runs on a third-party model | R30 | The model's training data and the vendor's own evaluation |
| 10.3.2 | Escalate uncertain decisions; track overrides | Partial | Seeded ambiguous cases whose only correct outcome is referral to a human officer, and approval gates before irreversible actions, tested for approve, deny and timeout | R24, R25 | Uncertainty signals from the model itself; monitoring overrides in production (v0.7) |

## 4. All 112 checks, by principle

One table per principle, in the framework's order. *Applies to* is the framework's own scoping — both, generative AI only, or traditional AI only — and *technical test* marks the five checks it tags as technical testing. *Our label* is this project's paraphrase; look a check up by its ID for the framework's text. *Rows* are `ROADMAP.md` §2b rows, and *built* means the bundle already has it at format 0.4. From R28 these entries live in the control catalogue as data, and this document stays the rationale.

### 1. Transparency

| Check | Applies to | Our label | Class | What v0.5 contributes | Rows |
|---|---|---|---|---|---|
| 1.1.1 | Both | Policy for explaining the AI system to consumers | Organisational | — | — |
| 1.1.2 | Both | Tell stakeholders the product uses AI | Organisational | — | — |
| 1.1.3 | GenAI | Users can tell they face an AI, and where content came from | Partial | Every outbound notice must carry an AI-use disclosure | R25 |
| 1.1.4 | Both | Explain an AI-influenced decision and its automation to those affected | Partial | Notices must carry the disclosure and the reason codes the case's facts call for | R25 |
| 1.1.5 | Both | Engage end users early about intended use | Organisational | — | — |
| 1.1.6 | Both | Report performance, scope, limits and risk handling to outside stakeholders | Supporting | `evidence_report.md`: results, declared limits, and what was not tested | R28 |
| 1.2.1 | Both | Explain the system's purpose and expected behaviour to end users | Supporting | Intended and out-of-scope uses declared in the plan and repeated in the report | R27 |
| 1.3.1 | Both | Meet applicable data-protection law | Organisational | — | — |
| 1.4.1 | Both | Public privacy notice on the system's personal-data handling | Organisational | — | — |
| 1.5.1 | Both | Notify end users when incidents occur | Organisational | — | — |
| 1.6.1 | Both | Harm-reporting channel that protects whistleblowers | Organisational | — | — |
| 1.7.1 | Both | Public statement of the organisation's AI governance | Organisational | — | — |
| 1.7.2 | Both | Publish an accessible system card where trust calls for it | Supporting | Testing approach and known limitations from the evidence report | R28 |

### 2. Explainability

| Check | Applies to | Our label | Class | What v0.5 contributes | Rows |
|---|---|---|---|---|---|
| 2.1.1 | Traditional, technical test | Feature-attribution analysis | Traditional AI only | — | — |
| 2.2.1 | Both | Justify opaque model choices; apply explanation techniques | Supporting | Step-level trace of every tool call; counterfactual pairs as counterfactual prompts | R24, R26 |

### 3. Repeatability / Reproducibility

| Check | Applies to | Our label | Class | What v0.5 contributes | Rows |
|---|---|---|---|---|---|
| 3.1.1 | Both | Version code and each model version's inputs | Supporting | Bundle provenance: git SHA and dirty flag, resolved config, model ID | built |
| 3.2.1 | Both | Record the data behind training and inference | Organisational | — | — |
| 3.3.1 | Traditional | Trace predictions back to model and code | Traditional AI only | — | — |
| 3.4.1 | Both | Keep reviewable logs of production inputs and outputs | Later (v0.7) | Production-trace ingestion; a test run logs only its own tasks | v0.7 |
| 3.5.1 | Both | Tie each model artefact to its build inputs | Organisational | — | — |
| 3.6.1 | Both | Repeat inferences; check outputs stay within owner-set limits | Direct | k repetitions and pass^k against thresholds fixed before the run, which the owner can set (`test_plan.json`) | R27 |
| 3.7.1 | Both | Name the technical and business metrics used for assessment | Partial | `test_plan.json` records each technical metric and threshold, and the business metrics the owner declares | R27 |
| 3.8.1 | Both | Record challenger experiments and why the final model won | Supporting | One bundle per candidate under the same pre-registered plan (side-by-side comparison is v0.6) | R27 |
| 3.9.1 | Both | Make training inputs reproducible (versioning or lineage) | Organisational | — | — |
| 3.10.1 | Traditional | Track feature and output distributions for drift | Traditional AI only | — | — |
| 3.11.1 | Both | Audit trail of each model deployment | Organisational | — | — |
| 3.12.1 | Both | Test performance on a curated set, including vendor claims | Partial | The scenario set run against the agent and its model; R30 runs it on a third-party model | R24, R30 |

### 4. Safety

| Check | Applies to | Our label | Class | What v0.5 contributes | Rows |
|---|---|---|---|---|---|
| 4.1.1 | GenAI, technical test | Benchmark and red-team for harmful outputs | Partial | Agent-level red-teaming: instructions injected through tool results and documents | R26 |
| 4.2.1 | Both | Materiality assessment for accountable owners | Supporting | The plan's materiality profile (MGF-Agentic §2.1.1 factors) | R27 |
| 4.3.1 | Both | Use-case risk assessment, red-teaming and safeguard checks | Partial | Safeguards stated as controls and tested; red-teaming and fault-injection results | R25, R26 |
| 4.3.2 | GenAI | Re-assess risks when a model is tuned or repurposed | Supporting | The same pre-registered plan re-run on the changed model | R27 |
| 4.4.1 | Both | Post-deployment review by a use-case expert | Later (v0.7) | Periodic reports from production traces | v0.7 |
| 4.5.1 | Both | Confidence and out-of-range input signals | Organisational | — | — |
| 4.5.2 | Both | Third-party monitoring, contracts and contingencies | Organisational | — | — |
| 4.6.1 | Both | Staged deployment strategies | Organisational | — | — |
| 4.6.2 | Both | Standby model server | Organisational | — | — |
| 4.6.3 | Both | Fail safe to a backup or sensible default | Supporting | Tool-fault results: the agent's fallback when a tool times out, errs or returns malformed data, under a call budget | R26 |
| 4.6.4 | Both | Retrain on ground truth from production | Organisational | — | — |
| 4.7.1 | Both | Residual-risk assessment and tolerance rationale | Supporting | Thresholds fixed before testing, results against them, untested controls listed | R27, R28 |
| 4.8.1 | GenAI | Find and block unlawful or harmful generated content | Later (v0.6) | Model-level content tests through the Moonshot adapter | v0.6 |
| 4.9.1 | GenAI | Content-provenance measures in use | Organisational | — | — |
| 4.9.2 | GenAI | Evaluate content-provenance methods | Organisational | — | — |
| 4.10.1 | Both | Test-and-validation record: ground truth and adversarial tests | Partial | Pre-registered plan; scoring against seeded ground truth; adversarial scenarios; a solvability check per scenario | R24, R26, R27 |
| 4.10.2 | GenAI | Fact-checking methods and retention of test records | Later (v0.6) | In part: factuality tests through Moonshot; AI-content detection is not planned | v0.6 |

### 5. Security

| Check | Applies to | Our label | Class | What v0.5 contributes | Rows |
|---|---|---|---|---|---|
| 5.1.1 | Both | AI-security training for staff and leaders | Organisational | — | — |
| 5.2.1 | Both | Threat model with security-effectiveness metrics | Supporting | Injection-resistance and unauthorised-action rates | R25, R26 |
| 5.3.1 | Both | Supply-chain security assessment | Organisational | — | — |
| 5.4.1 | Both | Security trade-offs in model selection | Organisational | — | — |
| 5.5.1 | Both | Inventory and protect AI assets, including assessments | Supporting | Checksummed, signed evidence packs | R29 |
| 5.6.1 | Both | Harden the development environment | Organisational | — | — |
| 5.7.1 | Both | Harden the deployment environment; least privilege | Organisational | — | — |
| 5.8.1 | Both | Incident response and remediation plan | Organisational | — | — |
| 5.9.1 | Both | Release gated on security testing | Partial | Fail-closed `gate`; security scenarios; a hashed, signed evidence pack | R25, R26, R27, R29 |
| 5.10.1 | Both | Monitor and log live inputs | Later (v0.7) | Production monitoring | v0.7 |
| 5.11.1 | Both | Monitor live behaviour and alert owners | Later (v0.7) | Production monitoring | v0.7 |
| 5.12.1 | Both | Manage the risk of model updates | Supporting | Each update re-run under the same pre-registered plan; bundles record the model ID and configuration | built, R27 |
| 5.13.1 | Both | Vulnerability-disclosure channel | Organisational | — | — |
| 5.14.1 | Both | Destroy retired data and models securely | Organisational | — | — |

### 6. Robustness

| Check | Applies to | Our label | Class | What v0.5 contributes | Rows |
|---|---|---|---|---|---|
| 6.1.1 | Both, technical test | Performance under noise and prompt perturbation | Direct | Use-case benchmark with perturbed task text, scored on the same metric | R26 |
| 6.2.1 | Both | Keep data current and representative; watch drift | Organisational | — | — |
| 6.3.1 | Both | Record intended use and limits (low-accuracy factors) | Supporting | Per-scenario failures and declared limits | R28 |
| 6.4.1 | Both | Record intended use and limits (feedback and adaptation effects) | Supporting | Indirect-injection results, for the adversarial part; feedback loops need production data (v0.7) | R26 |
| 6.5.1 | Both | Re-review when code, data or outputs change | Supporting | Bundles record code version, configuration, plan hash and outputs, so changes between runs are visible (automated comparison is v0.6) | built, R27 |
| 6.6.1 | Both | Training-time adversarial defences | Organisational | — | — |
| 6.6.2 | Both | Watch live traffic for suspicious use | Later (v0.7) | Production monitoring | v0.7 |
| 6.6.3 | Both | Act against suspicious users | Organisational | — | — |
| 6.7.1 | Both | Validate output quality and safeguards before and after launch | Supporting | The pre-registered plan as pre-launch validation, repeatable after launch | R27 |
| 6.7.2 | GenAI | Track knowledge sources, verify citations, watch anthropomorphism | Supporting | The trace records each tool call and result, including retrieved policy documents; prompts are not logged in v0.5 | built, R23, R24 |

### 7. Fairness

| Check | Applies to | Our label | Class | What v0.5 contributes | Rows |
|---|---|---|---|---|---|
| 7.1.1 | Traditional, technical test | Group error-rate disparity metrics | Traditional AI only | — | — |
| 7.2.1 | GenAI, technical test | Benchmark and red-team for harm to people or groups | Partial | Counterfactual pairs: one protected attribute changed, the same decision required | R26 |
| 7.3.1 | Traditional | Counterfactual fairness testing | Traditional AI only | — | — |
| 7.4.1 | Both | Track fairness results over the lifecycle | Supporting | Counterfactual-pair results from each run under the same plan | R26 |
| 7.5.1 | Traditional | Structured choice of fairness metric | Traditional AI only | — | — |
| 7.6.1 | Traditional | Name sensitive features and their groups | Traditional AI only | — | — |
| 7.7.1 | Traditional | Consult affected communities on what fairness means | Traditional AI only | — | — |
| 7.8.1 | Both | Check group representation in training data | Organisational | — | — |
| 7.9.1 | Both | Post-launch fairness threshold monitoring | Later (v0.7) | Production monitoring | v0.7 |
| 7.10.1 | Both | Ongoing metrics on current production data | Later (v0.7) | Production monitoring | v0.7 |
| 7.11.1 | Both | Rebalance skewed training data | Organisational | — | — |

### 8. Data Governance

| Check | Applies to | Our label | Class | What v0.5 contributes | Rows |
|---|---|---|---|---|---|
| 8.1.1 | Both | Data quality, rights and safety checks | Organisational | — | — |
| 8.2.1 | Both | Data lineage and provenance record | Organisational | — | — |
| 8.3.1 | Both | Lawful data collection, use and retention | Organisational | — | — |
| 8.3.2 | GenAI | Detect personal or sensitive data in outputs | Partial | A detector for Singapore identifiers, phone numbers and e-mail addresses in outbound messages, notices and exports | R25 |
| 8.4.1 | Both | Data-governance roles and training | Organisational | — | — |
| 8.5.1 | Both | Third-party rights and IP in data | Supporting | PII-detector results on test outputs (watching live output is v0.7) | R25 |

### 9. Accountability

| Check | Applies to | Our label | Class | What v0.5 contributes | Rows |
|---|---|---|---|---|---|
| 9.1.1 | Both | Define permitted, forbidden and refused uses | Supporting | Policy rules in the domain pack; violation counts per scenario | R25 |
| 9.2.1 | Both | Document deployment context and expectations | Supporting | Declared involvement level, materiality profile and intended use | R27 |
| 9.3.1 | Both | Choose and manage AI suppliers | Supporting | The same plan run on each candidate model | R27, R30 |
| 9.4.1 | Both | Plan for switching the system off | Organisational | — | — |
| 9.5.1 | Both | Adapt governance structures for AI | Organisational | — | — |
| 9.5.2 | Both | Cross-functional AI oversight body | Organisational | — | — |
| 9.5.3 | Both | Staff policies and training on AI risk duties | Organisational | — | — |
| 9.5.4 | Both | AI incident management and reporting | Organisational | — | — |
| 9.6.1 | Both | Role-based access to code, data and environments | Organisational | — | — |
| 9.7.1 | Both | Supply-chain duties, contracts and vendor assessment | Supporting | An evidence pack on the vendor's model | R30 |
| 9.8.1 | Both | Auditability by design | Supporting | Tamper-evident, signed bundles with provenance and a replay command | built, R29 |
| 9.9.1 | Both | Justify relying on third-party models | Organisational | — | — |
| 9.9.2 | Both | Understand a third-party model's data, evaluation and weak spots | Partial | Per-scenario failures of the agent on a third-party model | R30 |
| 9.10.1 | Both | External feedback, before and after launch | Organisational | — | — |
| 9.11.1 | Both | Compare against non-AI alternatives | Organisational | — | — |
| 9.12.1 | Both | AI system inventory and risk priorities | Organisational | — | — |

### 10. Human Agency and Oversight

| Check | Applies to | Our label | Class | What v0.5 contributes | Rows |
|---|---|---|---|---|---|
| 10.1.1 | Both | Independent review of development and deployment | Supporting | Verdicts a third party re-derives without re-running the agent | R29 |
| 10.2.1 | Both | Structured documentation with objective criteria | Supporting | Thresholds pre-registered and hashed before the run | R27 |
| 10.2.2 | Both | Role-based information for reviewers and operators | Supporting | Bundle layout: a human report plus machine-readable files | R28 |
| 10.3.1 | Both | Define the human's oversight role | Supporting | Declared MGF-Agentic §1.1.3 level; approval gates tested | R25, R27 |
| 10.3.2 | Both | Escalate uncertain decisions; track overrides | Partial | Ambiguous cases whose correct outcome is referral to an officer; approval gates before irreversible actions | R24, R25 |
| 10.3.3 | Both | Flag inputs unlike the training data | Organisational | — | — |
| 10.4.1 | Both | Review metrics with technical and non-technical teams | Supporting | Evidence report grouped by negative outcome, no composite score | R26, R28 |
| 10.5.1 | Both | Periodic and event-driven performance review | Supporting | Re-run the pre-registered plan at each trigger (run-to-run comparison is v0.6) | R27 |
| 10.6.1 | Both | Declare where AI assists or replaces people | Supporting | Declared involvement level and which actions need approval | R27 |

### 11. Inclusive Growth, Societal and Environmental Well-being

| Check | Applies to | Our label | Class | What v0.5 contributes | Rows |
|---|---|---|---|---|---|
| 11.1.1 | Both | Environmental impact assessment | Organisational | — | — |
| 11.2.1 | Both | Impact on people and society | Organisational | — | — |

### Counts by principle

| Principle | Checks | Direct | Partial | Supporting | Later | Organisational | Traditional AI only |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1. Transparency | 13 | 0 | 2 | 3 | 0 | 8 | 0 |
| 2. Explainability | 2 | 0 | 0 | 1 | 0 | 0 | 1 |
| 3. Repeatability / Reproducibility | 12 | 1 | 2 | 2 | 1 | 4 | 2 |
| 4. Safety | 17 | 0 | 3 | 4 | 3 | 7 | 0 |
| 5. Security | 14 | 0 | 1 | 3 | 2 | 8 | 0 |
| 6. Robustness | 10 | 1 | 0 | 5 | 1 | 3 | 0 |
| 7. Fairness | 11 | 0 | 1 | 1 | 2 | 2 | 5 |
| 8. Data Governance | 6 | 0 | 1 | 1 | 0 | 4 | 0 |
| 9. Accountability | 16 | 0 | 1 | 5 | 0 | 10 | 0 |
| 10. Human Agency and Oversight | 9 | 0 | 1 | 7 | 0 | 1 | 0 |
| 11. Inclusive Growth, Societal and Environmental Well-being | 2 | 0 | 0 | 0 | 0 | 2 | 0 |
| **Total** | **112** | **2** | **12** | **32** | **9** | **49** | **8** |

## 5. The WBS, row by row

Which checks each row's work evidences or supports. §8 lists what this review added to each row; R32–R34 are buffer and release weeks.

| Row | Weeks | Evidences (direct or partial) | Supports | MGF-Agentic v1.5 and Starter Kit v1.0 |
|---|---|---|---|---|
| R22 | Oct 12–18 | — | — | Catalogue v0: MGF-Agentic sections and the Starter Kit's five risks, as data |
| R23 | Oct 19–25 | — | 6.7.2 | MGF-Agentic §2.3.2's deterministic tool-call checks become possible (harness-side records) |
| R24 | Oct 26–Nov 1 | 3.12.1, 4.10.1, 10.3.2 | 2.2.1, 6.7.2 | §2.3.2 task execution and tool calling; §1.2.2 erroneous actions; §1.1.3 referral to a person; hallucination and inaccuracy |
| R25 | Nov 2–8 | 1.1.3, 1.1.4, 4.3.1, 5.9.1, 8.3.2, 10.3.2 | 5.2.1, 8.5.1, 9.1.1, 10.3.1 | §2.3.2 policy compliance; §1.1.3 approval gates; §1.2.2 unauthorised actions and data breaches; data leakage |
| R26 | Nov 9–15 | 4.1.1, 4.3.1, 4.10.1, 5.9.1, 6.1.1, 7.2.1 | 2.2.1, 4.6.3, 5.2.1, 6.4.1, 7.4.1, 10.4.1 | §2.3.2 robustness, with perturbations; §1.2.2 biased actions and disruption to connected systems; bias in decision making; adversarial prompts |
| R27 | Nov 16–22 | 3.6.1, 3.7.1, 4.10.1, 5.9.1 | 1.2.1, 3.8.1, 4.2.1, 4.3.2, 4.7.1, 5.12.1, 6.5.1, 6.7.1, 9.2.1, 9.3.1, 10.2.1, 10.3.1, 10.5.1, 10.6.1 | §2.1.1 scoping; §2.3.2 repetitions; the Starter Kit's Identify step and thresholds set before testing |
| R28 | Nov 23–29 | — | 1.1.6, 1.7.2, 4.7.1, 6.3.1, 10.2.2, 10.4.1 | The Starter Kit's Assess step; the testing framework's checklist shape |
| R29 | Nov 30–Dec 6 | 5.9.1 | 5.5.1, 9.8.1, 10.1.1 | Re-derivable verdicts for reviewers |
| R30 | Dec 7–13 | 3.12.1, 9.9.2 | 9.3.1, 9.7.1 | Catalogue v1 adds CSA addendum and ARC references; one live run on a real model |
| R31 | Dec 14–20 | — | — | — |

Already built at bundle format 0.4: provenance and the trace, which support 3.1.1, 5.12.1, 6.5.1, 6.7.2, 9.8.1. Every row from R28 on also feeds the evidence report, which lists all 112 checks.

## 6. MGF-Agentic v1.5

| Section | What it asks for (our paraphrase) | v0.5 | After v0.5 |
|---|---|---|---|
| §1.1.3 | Four levels of human involvement in what an agent does | R27 records the level the owner declares; R25 tests approval gates through the simulated approver; R24's ambiguous cases test referral to a person | — |
| §1.2.2 | Five kinds of harm: erroneous, unauthorised or biased actions, data breaches, disruption to connected systems | Every kind has scenarios — erroneous (R24), unauthorised and data breach (R25), biased and disruption (R26, added by this review). The report is grouped by them, with no composite score | — |
| §2.1.1 | Impact and likelihood factors that decide how much testing a use case needs | R27's materiality profile selects scenarios and thresholds | — |
| §2.2.2 | Guarding against automation bias: override rates, response times | — | v0.7, from production traces |
| §2.3.2 | Before deployment, test task execution, policy compliance, tool calling and robustness — whole workflows, repeatedly, with perturbations, deterministic checks for tool calls | R24 task execution and tool calling; R25 policy compliance; R26 robustness and perturbation; R27 repetitions and pass^k | Agents tested together (multi-agent), v0.6 |
| §2.3.3 | Logging and monitoring once deployed | — | v0.7 |

## 7. Starter Kit v1.0

| Risk or step | v0.5 | After v0.5 |
|---|---|---|
| Hallucination and inaccuracy | R24: actions checked against seeded ground truth through end states. R25: a notice's reason codes checked against the case's facts | Model-level factuality tests through the Moonshot adapter (v0.6) |
| Bias in decision making | R26: counterfactual pairs (added by this review) | Moonshot bias benchmarks, including the Singapore and ASEAN sets (v0.6) |
| Undesirable content | — | Moonshot (v0.6) |
| Data leakage | R25: the restricted-data boundary, and the PII detector (added by this review) | — |
| Vulnerability to adversarial prompts | R26: indirect injection through tool results and documents | Direct jailbreak and injection suites through Moonshot (v0.6) |
| Identify → Test → Assess, with thresholds set before testing | Identify: R27's scoping. Test: R24–R26. Assess: R28's evidence report. Thresholds pre-registered and hashed, and the owner can set them (R27) | — |

## 8. WBS changes made by this review

| Row | Change | Checks it serves | Added effort |
|---|---|---|---|
| R22 | ADR-0006 also decides the domain-pack interface (ADR-0005 D9) and how synthetic identifiers are made (risk 9) | — | Design only |
| R24 | The seed domain is built as the first domain pack. The notice tool carries disclosure and reason-code fields; seeded cases carry the expected reason codes and the attributes R26 varies. Ambiguous cases whose only correct outcome is referral to an officer. Exit criteria for all three | 10.3.2; inputs for 1.1.3, 1.1.4, 7.2.1 | ~2 h |
| R25 | Notice transparency rule. A Singapore PII detector — NRIC/FIN with checksum validation (S, T, F, G and M prefixes), phone numbers, e-mail — over every outbound message, notice and export, with a rule for when a detection is a violation; seeded identifiers use a test-only scheme, and the detector's own tests cover the real prefixes. If R25 runs over, both move to R26 with their exit clauses rather than holding the v0.2 tag | 1.1.3, 1.1.4, 8.3.2 | ~4 h |
| R26 | Counterfactual pairs, built on the perturbation-variant mechanism. A per-scenario tool-call budget under injected faults. One perturbation family is required, not optional. No negative-outcome group may be empty | 7.2.1, 6.1.1; supports 4.6.3 | ~3 h |
| R27 | The owner can set or tighten thresholds in `system.yaml`, and the plan records each threshold's source. Intended use, out-of-scope uses and business metrics are declared, recorded and repeated in the report, never evaluated. Exit criteria for both | 3.6.1, 3.7.1; supports 1.2.1, 9.2.1 | ~1 h |
| R28 | The 112 process entries load into the catalogue here, moved from R30. The evidence report lists every check, with the organisation's answers left blank | All 112 | ~2 h |
| R30 | Catalogue v1 keeps the CSA addendum and ARC; the live run is committed to the private repository | — | about −1 h (entries moved to R28) |
| R31 | `docs/domain-packs.md`, with the seed domain as the worked example (slack); the dry-run tester gets read access to the private repository | 3.12.1 outside the seed domain | ~2 h, slack |
| R34 | A private release to named partners instead of a public one; no PyPI upload; the release notes say how outside reviewers get access | — | — |

**Net cost: about eleven hours across R24–R30, plus two for the domain-pack guide if it survives, against the ~130 the re-baseline budgeted.** About half falls in R24–R25, before the W25 checkpoint; if R25 runs over, its two additions move to R26 rather than holding the v0.2 tag. The rest comes out of `ROADMAP.md` §4's slack — confidence intervals, perturbation families beyond the first, evidence-report polish — and, if needed, R32's catch-up week. The release week does not move. `ROADMAP.md` §6's ladder now cuts confidence intervals and extra perturbation families first, and keeps what the technical-testing checks depend on.

Outside the rows: E7 is decided, so R30's run stays in the private repository, R34 is a private release, and `ROADMAP.md` §3 and §8 say so. E1's fallback now points to this document's labels. `ROADMAP.md` §5 gains risk 9 and clarifies risks 3 and 8. `DESIGN.md` §5 and §10 no longer say "published", and §7 describes the report's new shape. `CLAUDE.md` gains a rule against pasting the framework's text.

## 9. Correction to ADR-0005 D6

D6 named roughly 30 pre-fill candidates by range. Read check by check:

| D6's range | This review |
|---|---|
| 3.x, traceability and reproducibility | Direct: 3.6.1. Partial: 3.7.1, 3.12.1. Supporting: 3.1.1, 3.8.1. **Later: 3.4.1**. **Organisational: 3.2.1, 3.5.1, 3.9.1, 3.11.1**. **Traditional AI only: 3.3.1, 3.10.1** |
| 4.1.1 | Partial: 4.1.1 |
| 5.9–5.12 | Partial: 5.9.1. Supporting: 5.12.1. **Later: 5.10.1, 5.11.1** |
| 6.1.1 | Direct: 6.1.1 |
| 6.5.1 | Supporting: 6.5.1 |
| 6.7.x | Supporting: 6.7.1, 6.7.2 |
| 8.3.2 | Partial: 8.3.2 |
| 9.8.1 | Supporting: 9.8.1 |
| 9.9.x | Partial: 9.9.2. **Organisational: 9.9.1** |
| 10.3–10.5 | Partial: 10.3.2. Supporting: 10.3.1, 10.4.1, 10.5.1. **Organisational: 10.3.3** |
| Not in D6's list | Partial: 1.1.3, 1.1.4, 4.3.1, 4.10.1, 7.2.1 |

In bold, the over-reach: 3.2.1, 3.5.1, 3.9.1 and 3.11.1 concern the organisation's own training data, model artefacts and deployments; 3.4.1, 5.10.1 and 5.11.1 need production logs (v0.7); 9.9.1 and 10.3.3 are organisational; 3.3.1 and 3.10.1 are scoped to traditional AI. "Pre-filled" in D6 means an evidence pointer, never an answer. ADR-0007 still fixes the report's format; this table replaces D6's list as its input.

## 10. What the package will not do for the report

- **It never answers a check.** Yes, No or N/A, and the elaboration, are the organisation's; the report leaves them blank.
- **It never claims compliance or certification**, and never uses "AI Verify" in a package, command or artefact name (ADR-0005 D5, D10).
- **It does not reproduce the framework's text** — identifiers and this document's labels; the framework's own titles only with written permission (E1).
- **It does not manage the organisation's documentary evidence.** For the 49 organisational checks the report leaves room for the organisation's own evidence and nothing more: no collection, tracking or scoring. That is the GRC product ADR-0005 D10 rules out, and the creep `ROADMAP.md` §5's risk 8 warns about.
- **It does not test the model's content safety** (Project Moonshot's job; an adapter in v0.6) **or the system in production** (v0.7).

## 11. Open questions

1. **Synthetic identifiers** (`ROADMAP.md` §5, risk 9). Seeded cases need NRIC/FIN and phone numbers realistic enough to exercise the detector, yet not anyone's. Singapore publishes no reserved test ranges that we know of, so ADR-0006 has to argue the choice rather than cite it, and date the argument — for example, a prefix no issuing scheme uses, which the detector accepts only in test configuration. E-mail addresses use reserved domains (RFC 2606).
2. **Which protected attributes the pairs vary.** The seed domain names them, and the published source it relies on, in R24.
3. **A machine-readable twin of the report.** Organisations transcribe the checklist into their own templates or into the AI Verify toolkit. Before ADR-0007 fixes the report's format, check what the toolkit's process-checklist import accepts.
4. **Official titles (E1).** With the AI Verify Foundation's written permission, the report can show the framework's own titles beside these labels.
5. **Agent-specific test guidance** (ADR-0005 Q1). The framework never mentions agents, and the Starter Kit defers them to a later update. When that update appears, or a new edition of the framework, redo this review.
6. **Private is not confidential.** The code is Apache-2.0, so anyone given access may redistribute it. A reviewer outside the partner group also needs access to run `validate-bundle` and `rescore`, which DESIGN.md's re-derivable-verdict promise depends on. If partners must not share the code before v0.5, that takes an agreement; the licence won't do it. R34's release notes say how outside reviewers get access.
7. **Prompts in the bundle.** v0.5 traces record tool calls, results and answers, but not the agent's system prompt. Whether a bundle records the prompt, or its hash, is a question for ADR-0006 or ADR-0007; it bears on 6.7.2 and on what a reviewer can see.

## 12. How this was produced, and keeping it current

- The framework's PDF text was extracted and split into its 112 process checks by ID. The structure matches ADR-0005's count: 11 principles, 112 checks, 5 tagged technical testing, 10 generative-AI only, 8 traditional-AI only.
- Each check's description and evidence list was read in full and classified against §2b as amended. Where a check sat between two classes, it got the lower one.
- An independent pass then re-read every direct and partial check, and a sample of the rest, against the framework's text. It moved four checks from direct to partial, one from partial to supporting, 5.7.1 from supporting to organisational and 8.5.1 the other way, and corrected how several rows described `rescore`, before anything was committed.
- Labels and table entries were written fresh, then compared mechanically with the source text: none shares a run of five consecutive words with it.
- Every count in this document is computed from the same table, and the counts sum to 112.
- **Keeping it current.** From R28 the classifications live in the control catalogue: change them there first, and update this document in the same commit. Redo the review when the framework, MGF-Agentic or the Starter Kit changes, or when a row's scope does.
