"""Markdown report renderer for a completed AgentEvalOps run."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from agentevalops.bundles.constants import (
    BUNDLE_FORMAT_VERSION,
    MANIFEST_FILENAME,
    required_files_for_version,
)
from agentevalops.core.schemas import (
    NEGATIVE_OUTCOMES,
    RunConfig,
    RunSummary,
    TaskRunResult,
    TraceEvent,
)

#: This project's labels for MGF-Agentic §1.2.2's five kinds of harm
#: (docs/imda-alignment.md §6), in the section's order.
OUTCOME_LABELS: dict[str, str] = {
    "erroneous_action": "Erroneous actions",
    "unauthorised_action": "Unauthorised actions",
    "biased_action": "Biased actions",
    "data_breach": "Data breaches",
    "disruption": "Disruption to connected systems",
}


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


def render_report(
    run_config: RunConfig,
    summary: RunSummary,
    all_events: list[TraceEvent],
    config_name: str = "",
    scenario_info: Mapping[str, Mapping[str, Any]] | None = None,
) -> str:
    """Return a plain Markdown report string for a completed run.

    Sections: header, run summary, task results, outcomes by kind of harm
    (when *scenario_info* gives the tasks' outcome groups -- a domain pack's
    scenarios do, the toy's do not), policy checks, trace summary, bundle
    audit. *scenario_info* maps a task id to its ``outcome_group``,
    ``category`` and ``variant`` from the benchmark's task metadata; it is
    computed at run time and never written to ``summary.json`` (ROADMAP
    R26; the bundle gains it with format 0.6).
    """
    lines: list[str] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    total = summary.total_tasks

    # ---- header ----------------------------------------------------------
    lines.append(f"# AgentEvalOps Run Report — {summary.run_id}")
    lines.append("")
    lines.append(f"Generated: {now}")
    lines.append("")

    # ---- run summary -----------------------------------------------------
    lines.append("## Run Summary")
    lines.append("")
    lines.append(f"- Run ID:        `{summary.run_id}`")
    if config_name:
        lines.append(f"- Config:        `{config_name}`")
    lines.append(f"- Agent:         `{run_config.agent_id}`")
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
    if scenario_info:
        lines.extend(outcome_section(run_config, summary, scenario_info))

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
        "- Checksums: SHA-256 per file (see manifest.json; not cryptographic signing)"
    )
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
) -> list[str]:
    """The five groups in §1.2.2's order, each with its scenarios' rows and
    no total across groups -- ADR-0005 forbids a composite. A group with no
    executed scenario says so rather than disappearing."""
    scripted = "mock" in str(run_config.agent_id)
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
    ) -> str:
        """Produce a report.  Returns the report content as a string."""
        return render_report(run_config, summary, trace, config_name, scenario_info)
