"""Markdown report renderer for a completed AgentEvalOps run."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from agentevalops.core.schemas import RunConfig, TraceEvent
from agentevalops.orchestration.local import RunSummary


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
    passed = summary.passed_tasks
    failed = summary.failed_tasks
    pass_rate = (passed / total * 100) if total > 0 else 0.0

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
    lines.append(
        f"- Tasks:         {passed} / {total} passed"
        f"  ({failed} failed, {pass_rate:.0f}%)"
    )
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

    return "\n".join(lines)
