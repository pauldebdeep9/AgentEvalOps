"""Markdown report renderer for a completed AgentEvalOps run."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from agentevalops.bundles.constants import (
    BUNDLE_FORMAT_VERSION,
    MANIFEST_FILENAME,
    required_files_for_version,
)
from agentevalops.core.schemas import (
    BUDGET_STOP,
    NEGATIVE_OUTCOMES,
    CategoryPassK,
    PassRateInterval,
    PlanThreshold,
    RunConfig,
    RunLimits,
    RunPlan,
    RunSummary,
    TaskRunResult,
    TaskSpec,
    ToolCallRecord,
    TraceEvent,
    TraceEventKind,
)
from agentevalops.reports.heatmap import COLUMNS as TRANSITION_COLUMNS
from agentevalops.reports.heatmap import ROWS as TRANSITION_ROWS
from agentevalops.reports.heatmap import TransitionMatrix, transition_matrix

#: This project's labels for MGF-Agentic §1.2.2's five kinds of harm
#: (docs/imda-alignment.md §6), in the section's order.
OUTCOME_LABELS: dict[str, str] = {
    "erroneous_action": "Erroneous actions",
    "unauthorised_action": "Unauthorised actions",
    "biased_action": "Biased actions",
    "data_breach": "Data breaches",
    "disruption": "Disruption to connected systems",
}


#: The runner whose trajectories are scripted (docs/adr/0007 D4): a report
#: says so by ``runner_id``, never by the agent's name.
SCRIPTED_RUNNER = "mock"

#: What either report's header says when ``config.json`` records no
#: ``runner_id`` (R28 follow-up, 2026-11-02): without it neither report can
#: tell a scripted run from a model-backed one.
RUNNER_NOT_RECORDED = (
    "The runner is not recorded, so this report cannot say whether the "
    "trajectories were scripted."
)

#: docs/adr/0008 E, for both reports' "What this report does not say": what
#: a verified signature and a rescore MATCH say, and what they do not. A
#: report is written before anyone signs or rescores its bundle, so these
#: stay general and say neither has happened (R29 (b2)).
SIGNATURE_LIMITS = (
    "A verified signature over this bundle's manifest.json says only that "
    "the manifest, and with its checksums every file it lists, is unchanged "
    "since a key the verifier trusts signed it. It does not say who ran the "
    "agent, that the run happened as recorded, that the code or model was "
    "what the bundle names, that the key was not lost or stolen, or how well "
    "the system behaves. This report does not say whether the bundle has "
    "been signed."
)
MATCH_LIMITS = (
    "A MATCH from `agentevalops rescore` says only that the installed code, "
    "the same by package_sha256 as the code that wrote this bundle, derives "
    "every recorded deterministic verdict, the summary's counts and pass^k, "
    "the policy verdict and the control coverage again from the bundle's own "
    "recorded tool calls, end states and agent events. It cannot see a "
    "consistent edit to those records: tool calls, end states or traces "
    "edited, with every derived file made to match, rescore as a MATCH, and "
    "only a signature made when the bundle was written can show the edit. It "
    "does not say that the agent would act the same way again, anything "
    "about model-judged results, or anything beyond the tested domain pack, "
    "and it does not compare the versions of the package's dependencies or "
    "of Python, or see a plugin evaluator whose code changed without a new "
    "evaluator id. This report does not say whether the bundle has been "
    "rescored."
)

#: Decision 6 of 2026-11-02: all either report says about a run with no
#: system declaration's plan.
NO_PLAN_SENTENCE = (
    "No test plan was declared before this run, so nothing here was "
    "registered in advance."
)


def scenario_info(tasks: Sequence[TaskSpec]) -> dict[str, dict[str, Any]]:
    """Each task's ``category``, ``outcome_group`` and ``variant`` from its
    metadata, for the tasks that carry any of them (a domain pack's
    scenarios do; the toy's do not): what the report's outcomes and
    repeatability sections read, for a run and for ``rescore``'s re-render
    (docs/adr/0008 R3)."""
    info: dict[str, dict[str, Any]] = {}
    for task in tasks:
        entry: dict[str, Any] = {}
        for key in ("category", "outcome_group"):
            value = task.metadata.get(key)
            if isinstance(value, str) and value:
                entry[key] = value
        variant = task.metadata.get("variant")
        if isinstance(variant, dict) and variant:
            entry["variant"] = dict(variant)
        if entry:
            info[str(task.task_id)] = entry
    return info


def format_pass_rate(summary: RunSummary) -> str:
    """Render a run's pass rate as a string that cannot be misread.

    Always exposes both figures (docs/adr/0002, Axis 3) -- not a config
    option, not a single denominator choice. Counting not-evaluated tasks
    as attempted-and-failed understates capability; silently excluding them
    from the denominator inflates it; a setting to choose between the two
    makes the number's meaning depend on invisible state. The ratio's
    denominator is tasks actually evaluated (``passed_tasks +
    failed_tasks``); the not-evaluated count is always appended, even when
    it is zero -- omitting it *because* it's zero would be exactly the kind
    of conditional collapse this function exists to avoid.

    Used by both ``cli.py``'s ``_print_summary`` and this module's report
    headline, so the two surfaces can't drift into reporting the ratio two
    different ways.
    """
    evaluated_total = summary.passed_tasks + summary.failed_tasks
    not_evaluated = summary.not_evaluated_tasks
    if evaluated_total == 0:
        # "0/0 evaluated passed" reads as an undefined ratio, not as "we
        # evaluated nothing" -- say the zero-evaluated case in words instead
        # of a fraction, per this session's explicit "must be unmistakable"
        # requirement for that case.
        return f"0 evaluated ({not_evaluated} not evaluated)"
    return (
        f"{summary.passed_tasks}/{evaluated_total} evaluated passed "
        f"({not_evaluated} not evaluated)"
    )


def budget_line(summary: RunSummary) -> str | None:
    """The run's budget in one line, for the CLI and report.md (R35): the
    caps, the spend, and whether a cap was reached -- with how many task
    runs it stopped, or none when the spend reached a cap with nothing left
    to start. None for a run without a budget."""
    budget = summary.run_budget
    if budget is None:
        return None
    limits = RunLimits(budget.cap_usd, budget.cap_tokens)
    spent = f"${budget.spent_usd:.4f} and {budget.spent_tokens} tokens"
    if budget.tripped:
        stopped = summary.not_evaluated_by_cause.get(BUDGET_STOP, 0)
        stop = f"reached: {stopped} task run(s) not started"
    elif limits.reached(budget.spent_usd, budget.spent_tokens):
        stop = "reached; no task run stopped"
    else:
        stop = "not reached"
    return f"cap {limits.label()}; spent {spent}; {stop}"


def run_created_line(created_at: str | None) -> str:
    """The line that dates both reports: when the run was created, as
    ``metadata.json`` records it -- not when the report was rendered, which
    no report records (ADR-0008 decision 9)."""
    if not created_at:
        return "Run created: not recorded"
    return f"Run created: {created_at} (`created_at` in `metadata.json`)"


def render_report(
    run_config: RunConfig,
    summary: RunSummary,
    all_events: list[TraceEvent],
    config_name: str = "",
    scenario_info: Mapping[str, Mapping[str, Any]] | None = None,
    plan: RunPlan | None = None,
    runner_id: str | None = None,
    created_at: str | None = None,
    tool_calls: Sequence[ToolCallRecord] = (),
) -> str:
    """Return a plain Markdown report string for a completed run.

    *tool_calls* are the run's harness records, for the "Where runs first
    fail" matrix (R37); a render given none says the matrix needs them.

    *created_at* is the run's own timestamp, as ``metadata.json`` records
    it, and dates the report (``run_created_line``); the report reads no
    clock, so two renders of one bundle are byte-identical (ADR-0008
    decision 9). Without it the line says the time is not recorded.

    *runner_id* is the runner that produced the run (``config.json``,
    docs/adr/0007 D4); ``"mock"`` marks it scripted.

    Sections: header, run summary, task results, outcomes by kind of harm
    (when *scenario_info* gives the tasks' outcome groups -- a domain pack's
    scenarios do, the toy's do not), policy checks, trace summary, bundle
    audit. *scenario_info* maps a task id to its ``outcome_group``,
    ``category`` and ``variant`` from the benchmark's task metadata; it is
    computed at run time and never written to ``summary.json`` (ROADMAP
    R26; the bundle gains it with format 0.6).
    """
    lines: list[str] = []

    total = summary.total_tasks

    # ---- header ----------------------------------------------------------
    lines.append(f"# AgentEvalOps Run Report — {summary.run_id}")
    lines.append("")
    lines.append(run_created_line(created_at))
    lines.append("")
    if runner_id is None:
        lines.append(f"**{RUNNER_NOT_RECORDED}**")
        lines.append("")

    # ---- run summary -----------------------------------------------------
    lines.append("## Run Summary")
    lines.append("")
    lines.append(f"- Run ID:        `{summary.run_id}`")
    if config_name:
        lines.append(f"- Config:        `{config_name}`")
    lines.append(f"- Agent:         `{run_config.agent_id}`")
    lines.append(
        f"- Runner:        `{runner_id}`"
        if runner_id
        else "- Runner:        not recorded"
    )
    lines.append(f"- Backend:       `{run_config.backend_id}`")
    rl = run_config.resource_limits
    lines.append(
        f"- Limits:        {rl.max_tokens:,} tokens / "
        f"${rl.max_cost_usd:.2f} / "
        f"{rl.max_wall_seconds:.0f} s"
    )
    lines.append(f"- Tasks:         {format_pass_rate(summary)}, {total} attempted")
    lines.append(f"- Failed:        {summary.failed_tasks}")
    lines.append(f"- Total cost:    ${summary.total_cost_usd:.4f}")
    lines.append(f"- Total tokens:  {summary.total_tokens}")
    if (budget := budget_line(summary)) is not None:
        lines.append(f"- Run budget:    {budget}")
    lines.append(f"- Trace events:  {len(all_events)}")
    if summary.policy_verdict is not None:
        pv_line = summary.policy_verdict.verdict.value.upper()
        lines.append(f"- Policy:        {pv_line}")
    lines.append("")

    # ---- task results ----------------------------------------------------
    lines.append("## Task Results")
    lines.append("")
    lines.append("| Task ID | Passed | Score | Termination | Notes |")
    lines.append("|---------|--------|-------|-------------|-------|")
    for tr in summary.task_results:
        if not tr.evaluation.evaluated:
            # Distinct from "no" -- a not-evaluated task was never judged
            # at all, so reading this row as "the agent failed" would be
            # exactly the misread docs/adr/0002 traces back to a false
            # PASS on an unevaluated run; the per-task row must not repeat
            # the same collapse from the other direction.
            passed_str = "not evaluated"
        else:
            passed_str = "yes" if tr.evaluation.passed else "no"
        score_str = f"{tr.evaluation.score:.2f}"
        reason = tr.output.termination_reason.value
        notes = tr.evaluation.notes.replace("|", "\\|")
        lines.append(
            f"| {tr.task_id} | {passed_str} | {score_str} | {reason} | {notes} |"
        )
    lines.append("")

    # ---- outcomes by kind of harm (MGF-Agentic §1.2.2; ROADMAP R26) ------
    if summary.pass_k:
        lines.extend(
            repeatability_section(
                summary, scenario_info or {}, run_config.bootstrap_samples
            )
        )

    if scenario_info:
        lines.extend(
            outcome_section(
                run_config, summary, scenario_info, runner_id == SCRIPTED_RUNNER
            )
        )

    if summary.task_results:
        lines.extend(transition_section(summary, all_events, tool_calls))

    if plan is not None:
        lines.extend(
            plan_section(plan, summary, intervals=bool(run_config.bootstrap_samples))
        )

    # ---- policy checks ---------------------------------------------------
    lines.append("## Policy Checks")
    lines.append("")
    if summary.policy_verdict is not None:
        pv = summary.policy_verdict
        lines.append(f"- Verdict: **{pv.verdict.value.upper()}**")
        lines.append(f"- Checker: `{pv.checker_id}`")
        lines.append(f"- Notes:   {pv.notes}")
    else:
        lines.append("- No policy check was run.")
    lines.append("")

    # ---- trace summary ---------------------------------------------------
    lines.append("## Trace Summary")
    lines.append("")
    if all_events:
        kind_counts: Counter[str] = Counter(e.kind.value for e in all_events)
        lines.append("| Event Kind | Count |")
        lines.append("|------------|-------|")
        for kind_val, count in sorted(kind_counts.items()):
            lines.append(f"| {kind_val} | {count} |")
    else:
        lines.append("No trace events recorded.")
    lines.append("")

    # ---- bundle audit ----------------------------------------------------
    lines.append("## Bundle Audit")
    lines.append("")
    lines.append(f"- Bundle format version: `{BUNDLE_FORMAT_VERSION}`")
    lines.append("- Manifest: present (written after content files)")
    # From the version this code writes, so a format bump (0.4 added
    # replay_command.txt) cannot leave the audit listing a stale file set.
    bundle_files = (
        *required_files_for_version(BUNDLE_FORMAT_VERSION),
        MANIFEST_FILENAME,
    )
    lines.append(f"- Files written: {len(bundle_files)}")
    lines.append("  (" + ", ".join(bundle_files) + ")")
    lines.append(f"- Trace event count: {len(all_events)}")
    lines.append(
        "- Checksums: SHA-256 per file (see manifest.json); checksums are not "
        "a signature"
    )
    lines.append("")

    # ---- limits (docs/adr/0008 E) -----------------------------------------
    lines.append("## What this report does not say")
    lines.append("")
    lines.append(f"- {SIGNATURE_LIMITS}")
    lines.append(f"- {MATCH_LIMITS}")
    lines.append("")

    return "\n".join(lines)


def _verdict_word(result: TaskRunResult) -> str:
    if not result.evaluation.evaluated:
        return "not evaluated"
    return "passed" if result.evaluation.passed else "failed"


def _failed_kinds(result: TaskRunResult) -> str:
    kinds = [
        e.evaluator_kind
        for e in result.evaluations
        if e.applicable and e.evaluated and not e.passed
    ]
    return ", ".join(kinds) if kinds else "—"


def _variant_note(
    result: TaskRunResult,
    variant: Mapping[str, Any],
    by_run: Mapping[tuple[str, int], TaskRunResult],
    scripted: bool,
) -> str:
    """What a derived scenario's row says: the counterfactual pair and
    whether it differs, or the perturbation and the honest limit of a
    scripted run (owner's condition, 2026-11-01)."""
    kind = variant.get("kind")
    base_id = str(variant.get("base", ""))
    base = by_run.get((base_id, result.repetition))
    if kind == "counterfactual":
        unchanged = variant.get("unchanged") or {}
        kept = ", ".join(f"{k} {v}" for k, v in unchanged.items())
        base_word = _verdict_word(base) if base is not None else "not run"
        same = (
            base is not None
            and base.evaluation.evaluated
            and result.evaluation.evaluated
            and base.evaluation.passed == result.evaluation.passed
        )
        difference = (
            "no difference"
            if same and result.evaluation.passed
            else "differs"
            if result.evaluation.evaluated
            else "not evaluated"
        )
        return (
            f"pair with {base_id}: {variant.get('attribute')} "
            f"{variant.get('from')} → {variant.get('to')} ({kept} unchanged); "
            f"base {base_word}, variant {_verdict_word(result)}: {difference}"
        )
    if kind == "perturbation":
        note = f"{base_id}'s text under {variant.get('family')}"
        if scripted:
            note += (
                "; run on a scripted trajectory, so this is not evidence that an "
                "agent withstood the noise (R30)"
            )
        return note
    return ""


def outcome_section(
    run_config: RunConfig,
    summary: RunSummary,
    scenario_info: Mapping[str, Mapping[str, Any]],
    scripted: bool = False,
) -> list[str]:
    """The five groups in §1.2.2's order, each with its scenarios' rows and
    no total across groups -- ADR-0005 forbids a composite. A group with no
    executed scenario says so rather than disappearing. *scripted* comes
    from the run's ``runner_id`` (docs/adr/0007 D4), not the agent's name."""
    by_run = {(str(r.task_id), r.repetition): r for r in summary.task_results}
    lines = ["## Outcomes by kind of harm (MGF-Agentic §1.2.2)", ""]
    lines.append(
        "Each scenario declares the kind of harm its failure would be; a "
        "group lists its scenarios' verdicts and nothing is added up across "
        "groups."
    )
    lines.append("")
    for group in NEGATIVE_OUTCOMES:
        lines.append(f"### {OUTCOME_LABELS[group]}")
        lines.append("")
        rows = [
            r
            for r in summary.task_results
            if scenario_info.get(str(r.task_id), {}).get("outcome_group") == group
        ]
        if not rows:
            lines.append("No scenario executed in this group.")
            lines.append("")
            continue
        lines.append("| Scenario | Repetition | Verdict | Failed evaluators | Note |")
        lines.append("|----------|------------|---------|-------------------|------|")
        for r in rows:
            info = scenario_info.get(str(r.task_id), {})
            note = _variant_note(r, info.get("variant") or {}, by_run, scripted)
            lines.append(
                f"| {r.task_id} | {r.repetition} | {_verdict_word(r)} | "
                f"{_failed_kinds(r)} | {note.replace('|', chr(92) + '|')} |"
            )
        lines.append("")
    return lines


def _rate(value: float) -> str:
    return str(round(value, 2))


def _causes(roll_up: CategoryPassK) -> str:
    return ", ".join(
        f"{cause} x{count}" if count > 1 else cause
        for cause, count in roll_up.not_evaluated_causes.items()
    )


#: Under the Repeatability heading once, verbatim, when the run drew
#: intervals (owner, R36 slice 3).
INTERVAL_HONESTY = (
    "The interval is the 2.5th–97.5th percentile of B scenario-level bootstrap "
    "resamples of this run's decided scenarios, derived from the run seed. It "
    "expresses sampling uncertainty over these scenarios only; it says nothing "
    "about scenarios outside the registered plan, and the gate judges the "
    "observed rate, not the interval. At 0/n or n/n the interval is zero-width "
    "and carries no uncertainty information; the rule-of-three bound beside it "
    "is display-only."
)


def ci_text(interval: PassRateInterval) -> str:
    """A pass^k rate's 95% interval, naming its own denominator, two
    decimals: ``95% CI [a, b] over n decided`` (owner decision 1,
    2026-11-09; the CLI and report.md)."""
    return (
        f"95% CI [{interval.low:.2f}, {interval.high:.2f}] over "
        f"{interval.n_scenarios} decided"
    )


def rule_of_three(interval: PassRateInterval, passed: int) -> str | None:
    """At 0/n or n/n, where the interval is zero-width, the one-sided 95%
    rule-of-three bound to show beside it -- 1 - 3/n below all-pass, 3/n
    above all-fail -- else None. Display only: it never enters summary.json
    (owner decision 2, 2026-11-09). Up to three decided scenarios it would
    read 0.00 or 1.00 and say nothing, so it is left out; from four it
    first says something (R37 step 0s, 0e0751d and the next)."""
    n = interval.n_scenarios
    if n <= 3:
        return None
    if passed == n:
        return f"95% one-sided lower bound ~ {1 - 3 / n:.2f} (rule of three)"
    if passed == 0:
        return f"95% one-sided upper bound ~ {3 / n:.2f} (rule of three)"
    return None


def _no_interval(decided: int) -> str:
    """Why a rate has no interval: one decided scenario, or none."""
    if decided == 1:
        return "1 scenario — interval omitted"
    return f"{decided} decided scenarios — no interval"


def overall_pass_k(summary: RunSummary) -> str:
    """``pass^k p/t scenarios (p/d decided)``: the rate over every scenario,
    the gate's denominator, beside passed-of-decided, the interval's
    (owner decision 1)."""
    k = max(r.k for r in summary.pass_k)
    passed = sum(1 for r in summary.pass_k if r.pass_k is True)
    decided = sum(1 for r in summary.pass_k if r.pass_k is not None)
    return (
        f"pass^{k} {passed}/{len(summary.pass_k)} scenarios "
        f"({passed}/{decided} decided)"
    )


def _interval_clause(
    interval: PassRateInterval | None, passed: int, decided: int, b: int
) -> str:
    """report.md's words for a rate's interval: ``95% CI [a, b] over n
    decided (B=b)``, the rule-of-three bound at an extreme, or why there
    is none."""
    if interval is None:
        return f"; {_no_interval(decided)}"
    clause = f"; {ci_text(interval)} (B={b})"
    bound = rule_of_three(interval, passed)
    return clause if bound is None else f"{clause}; {bound}"


#: The "Where runs first fail" section's reading line (R37).
TRANSITION_READING = (
    "Rows: the last completed stage. Columns: the stage of the first failure, "
    "judged from the failing evaluations' record citations."
)


def _matrix_cell(matrix: TransitionMatrix, row: str, column: str) -> str:
    """``count (scenario:tool, ...)``, or a dash when the cell is empty."""
    labels = matrix.labels(row, column)
    return f"{len(labels)} ({', '.join(labels)})" if labels else "–"


def transition_section(
    summary: RunSummary,
    all_events: Sequence[TraceEvent],
    tool_calls: Sequence[ToolCallRecord],
) -> list[str]:
    """Where the run's failed task runs first fail (ROADMAP R37): rows the
    last completed stage, columns the stage of the first failure, from
    ``reports/heatmap.py`` over the run's own records -- derived at render
    time, so a re-render reproduces it. Not-evaluated runs are counted
    apart. The ``parse`` column shows only when a run failed there."""
    lines = ["## Where runs first fail", ""]
    if not any(
        r.evaluation.evaluated and not r.evaluation.passed
        for r in summary.task_results
    ):
        return [*lines, "No failed task runs; the matrix is empty.", ""]
    if not tool_calls and any(
        e.kind is TraceEventKind.AGENT_TOOL_CALL
        and e.payload.get("tool_name") != "finish"
        for e in all_events
    ):
        return [
            *lines,
            "The matrix needs the run's tool-call records; this render was "
            "given none.",
            "",
        ]
    matrix = transition_matrix(summary.task_results, tool_calls, all_events)
    counts = (
        f"{matrix.failed} failed task run{'s' if matrix.failed != 1 else ''} "
        f"of {matrix.task_runs}"
    )
    if matrix.not_evaluated:
        verb = "is" if matrix.not_evaluated == 1 else "are"
        counts += f"; {matrix.not_evaluated} not evaluated {verb} not in the matrix"
    lines += [TRANSITION_READING, f"{counts}.", ""]
    columns = [
        c
        for c in TRANSITION_COLUMNS
        if c != "parse" or any(cell.column == "parse" for cell in matrix.cells)
    ]
    header = ["From \\ In", *columns]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join("-" * (len(h) + 2) for h in header) + "|")
    for row in TRANSITION_ROWS:
        if any(cell.row == row for cell in matrix.cells):
            cells = " | ".join(_matrix_cell(matrix, row, c) for c in columns)
            lines.append(f"| {row} | {cells} |")
    lines.append("")
    return lines


def repeatability_section(
    summary: RunSummary,
    scenario_info: Mapping[str, Mapping[str, Any]],
    bootstrap_samples: int | None = None,
) -> list[str]:
    """pass^k per scenario and per category (ROADMAP R27; docs/adr/0006
    D18, D20): raw counts and k; a not-evaluated scenario always with its
    cause. When the run drew *bootstrap_samples* (R36), each category's
    rate and the overall one with its 95% interval, naming its denominator,
    or why it has none, and the honesty line once -- context; the gate
    reads the rate."""
    k = max(r.k for r in summary.pass_k)
    lines = [f"## Repeatability (pass^k, k = {k})", ""]
    intro = (
        f"Each scenario ran {k} time{'s' if k != 1 else ''}; it passes only if "
        "every repetition passed. A scenario with a repetition that was not "
        "evaluated is not evaluated, not failed, and its cause is named "
        "(docs/adr/0006 D20)."
    )
    if bootstrap_samples:
        lines.extend([intro, "", INTERVAL_HONESTY])
    else:
        lines.append(f"{intro} Raw counts; no confidence intervals.")
    lines.append("")
    for roll_up in summary.pass_k_by_category:
        line = (
            f"- {roll_up.category}: {roll_up.passed}/{roll_up.scenarios} scenarios "
            f"passed all {roll_up.k} repetitions"
        )
        if roll_up.not_evaluated:
            line += f"; {roll_up.not_evaluated} not evaluated ({_causes(roll_up)})"
        if bootstrap_samples:
            line += _interval_clause(
                roll_up.interval,
                roll_up.passed,
                roll_up.passed + roll_up.failed,
                bootstrap_samples,
            )
        lines.append(line)
    if bootstrap_samples:
        passed = sum(1 for r in summary.pass_k if r.pass_k is True)
        decided = sum(1 for r in summary.pass_k if r.pass_k is not None)
        clause = _interval_clause(
            summary.overall_pass_k_interval, passed, decided, bootstrap_samples
        )
        if summary.overall_pass_k_interval is not None:
            clause = "," + clause[1:]
        lines.append(f"- Overall: {overall_pass_k(summary)}{clause}")
    if summary.pass_k_by_category or bootstrap_samples:
        lines.append("")
    lines.append(
        "| Scenario | Category | k | Passed | Failed | Not evaluated | pass^k |"
    )
    lines.append(
        "|----------|----------|---|--------|--------|---------------|--------|"
    )
    for result in summary.pass_k:
        category = str(scenario_info.get(result.task_id, {}).get("category", "") or "-")
        if result.pass_k is None:
            verdict = f"not evaluated ({result.not_evaluated_cause})"
        else:
            verdict = "yes" if result.pass_k else "no"
        lines.append(
            f"| {result.task_id} | {category} | {result.k} | {result.passed} | "
            f"{result.failed} | {result.not_evaluated} | {verdict} |"
        )
    lines.append("")
    return lines


def threshold_status(threshold: PlanThreshold, roll_up: CategoryPassK | None) -> str:
    """How the run stands against one plan threshold, in words a reader
    cannot take for a tolerated failure: a comparative threshold that is
    not met says it does not gate; a hard gate that cannot be judged says
    it fails closed (ROADMAP R27; docs/adr/0005 D6)."""
    hard = threshold.gate == "hard"
    if roll_up is None:
        return "no scenario ran" + ("; a hard gate fails closed" if hard else "")
    if roll_up.not_evaluated:
        plural = "s" if roll_up.not_evaluated != 1 else ""
        verdict = (
            f"not evaluated: {roll_up.not_evaluated} scenario{plural} "
            f"({_causes(roll_up)})"
        )
        return verdict + (
            "; a hard gate fails closed"
            if hard
            else "; comparative threshold; does not gate"
        )
    if roll_up.pass_k_rate >= threshold.min_pass_k:
        return "met"
    if hard:
        return "below (hard gate)"
    return "below (comparative threshold; does not gate)"


def plan_section(
    plan: RunPlan, summary: RunSummary, *, intervals: bool = False
) -> list[str]:
    """The pre-registered plan repeated (docs/adr/0005 D6; testing-framework
    3.6.1, 3.7.1): the declaration's intended use, out-of-scope uses and
    business metrics, never evaluated; the selection; and each threshold
    with its source and the run's standing against it. A plan that declared
    nothing gets one sentence and nothing else (decision 6 of 2026-11-02).
    With *intervals* -- report.md's, when the run drew them (R36); never the
    evidence report's -- the observed rate carries its 95% interval as
    context; the status is the rate's against the threshold."""
    system = plan.system
    if not plan.declared or system is None:
        return ["## Test plan", "", NO_PLAN_SENTENCE, ""]
    lines = ["## Test plan (pre-registered)", ""]
    lines.append(
        "Registered as `test_plan.json` before the first task ran; its sha256 is "
        "in `metadata.json` (docs/adr/0005 D6)."
    )
    lines.append("")
    lines.append(
        f"- System: `{system.system_id}`, pack `{plan.pack_id}` {plan.pack_version}, "
        f"involvement `{system.involvement}`"
    )
    lines.append(f"- Intended use: {system.intended_use}")
    out_of_scope = (
        "; ".join(system.out_of_scope) if system.out_of_scope else "none declared"
    )
    lines.append(f"- Out of scope: {out_of_scope}")
    if system.business_metrics:
        metrics = "; ".join(
            f"`{m.name}` -- {m.definition}" for m in system.business_metrics
        )
    else:
        metrics = "none declared"
    lines.append(f"- Business metrics (recorded, not evaluated): {metrics}")
    lines.append(
        f"- Scenarios: {len(plan.selected)} selected, {len(plan.excluded)} excluded; "
        f"repetitions {plan.repetitions}; seed {plan.seed}"
    )
    uncovered = ", ".join(plan.uncovered_tools) if plan.uncovered_tools else "none"
    lines.append(f"- Declared tools the pack does not model: {uncovered}")
    lines.append("")
    if plan.excluded:
        lines.append("| Excluded scenario | Reason |")
        lines.append("|-------------------|--------|")
        for exclusion in plan.excluded:
            lines.append(f"| {exclusion.scenario_id} | {exclusion.reason} |")
        lines.append("")
    if plan.thresholds:
        roll_ups = {c.category: c for c in summary.pass_k_by_category}
        lines.append(
            "| Category | Min pass^k | Source | Gate | Gate source | "
            "Observed pass^k | Status |"
        )
        lines.append(
            "|----------|------------|--------|------|-------------|"
            "-----------------|--------|"
        )
        for threshold in plan.thresholds:
            roll_up = roll_ups.get(threshold.category)
            observed = "-" if roll_up is None else _rate(roll_up.pass_k_rate)
            if intervals and roll_up is not None and roll_up.interval is not None:
                observed += f" ({ci_text(roll_up.interval)})"
            status = threshold_status(threshold, roll_up)
            lines.append(
                f"| {threshold.category} | {threshold.min_pass_k} | "
                f"{threshold.min_pass_k_source} | {threshold.gate} | "
                f"{threshold.gate_source} | {observed} | {status} |"
            )
        lines.append("")
    else:
        lines.append("No threshold applies to this run.")
        lines.append("")
    return lines


class MarkdownReportGenerator:
    """``ReportGenerator`` implementation backed by ``render_report``."""

    report_id: str = "markdown-v1"

    async def render(
        self,
        run_config: RunConfig,
        summary: RunSummary,
        trace: list[TraceEvent],
        config_name: str = "",
        scenario_info: Mapping[str, Mapping[str, Any]] | None = None,
        plan: RunPlan | None = None,
        runner_id: str | None = None,
        created_at: str | None = None,
    ) -> str:
        """Produce a report.  Returns the report content as a string."""
        return render_report(
            run_config,
            summary,
            trace,
            config_name,
            scenario_info,
            plan,
            runner_id,
            created_at,
        )
