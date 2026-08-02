"""Markdown report renderer for a completed AgentEvalOps run."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from agentevalops.bundles.constants import BUNDLE_FORMAT_VERSION, REQUIRED_BUNDLE_FILES
from agentevalops.core.schemas import RunConfig, RunSummary, TraceEvent


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
) -> str:
    """Return a plain Markdown report string for a completed run.

    Sections: header, run summary, task results, policy checks, trace summary.
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
            f"| {tr.task_id} | {passed_str} | {score_str}"
            f" | {reason} | {notes} |"
        )
    lines.append("")

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
        kind_counts: Counter[str] = Counter(
            e.kind.value for e in all_events
        )
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
    lines.append(f"- Files written: {len(REQUIRED_BUNDLE_FILES) + 1}")
    lines.append(
        "  (" + ", ".join(REQUIRED_BUNDLE_FILES) + ", manifest.json)"
    )
    lines.append(f"- Trace event count: {len(all_events)}")
    lines.append(
        "- Checksums: SHA-256 per file "
        "(see manifest.json; not cryptographic signing)"
    )
    lines.append("")

    return "\n".join(lines)


class MarkdownReportGenerator:
    """``ReportGenerator`` implementation backed by ``render_report``."""

    report_id: str = "markdown-v1"

    async def render(
        self,
        run_config: RunConfig,
        summary: RunSummary,
        trace: list[TraceEvent],
        config_name: str = "",
    ) -> str:
        """Produce a report.  Returns the report content as a string."""
        return render_report(run_config, summary, trace, config_name)
