"""Render the human record of a run; JSON remains the machine record.

Counts and classifications come from ``RunSummary``'s derived properties,
never a second result model. Results are named ``k/n`` observations rather
than percentages, and cost is labelled as not invoice-verified: small samples
and unreconciled provider billing do not support stronger claims.
"""

from __future__ import annotations

from collections import Counter

from minieval.schemas import RunSummary, Stability, TaskResult, Verdict

_STABILITIES = (
    Stability.STABLE_PASS,
    Stability.FLAKY,
    Stability.STABLE_FAIL,
    Stability.NOT_EVALUATED,
)


def _label(value: Stability | Verdict) -> str:
    return value.value.replace("_", " ").capitalize()


def _inline(value: object) -> str:
    """Keep user-supplied text on one Markdown line and inside table cells."""
    return " ".join(str(value).splitlines()).replace("|", "\\|")


def _code(value: object) -> str:
    escaped = _inline(value).replace("`", "\\`")
    return f"`{escaped}`"


def _citations(steps: list[int]) -> str:
    if not steps:
        return ""
    return " (trace steps: " + ", ".join(str(step) for step in steps) + ")"


def _policy_summary(task_result: TaskResult) -> str:
    counts = Counter(a.policy.verdict.value for a in task_result.attempts)
    return ", ".join(f"{name}: {counts[name]}" for name in sorted(counts))


def _findings(task_result: TaskResult) -> list[str]:
    lines: list[str] = []
    for attempt in task_result.attempts:
        number = attempt.index + 1
        evaluation = attempt.evaluation
        if not evaluation.evaluated or not evaluation.passed:
            outcome = (
                "not evaluated" if not evaluation.evaluated else "evaluation failed"
            )
            note = _inline(evaluation.notes) or "no details supplied"
            lines.append(
                f"- Attempt {number} {outcome}: {note}"
                f"{_citations(evaluation.citations)}"
            )
        if attempt.policy.verdict is not Verdict.PASS:
            note = _inline(attempt.policy.notes) or "no details supplied"
            lines.append(
                f"- Attempt {number} policy {attempt.policy.verdict.value}: {note}"
                f"{_citations(attempt.policy.citations)}"
            )
    return lines


def render_report(summary: RunSummary) -> str:
    """Return decision-ready Markdown with a final newline."""
    config = summary.config
    lines = [
        "# MiniEval Report",
        "",
        f"- Run ID: {_code(summary.run_id)}",
        f"- Runner: {_code(config.runner)}",
        f"- Model: {_code(config.model or 'none')}",
        f"- Repeats: {config.repeats}",
        f"- Total attempts: {summary.total_attempts}",
        f"- Passed attempts: {summary.passed_attempts}",
        f"- Failed attempts: {summary.failed_attempts}",
        f"- Not evaluated attempts: {summary.not_evaluated_attempts}",
        f"- Reported cost (not invoice-verified): ${summary.total_cost_usd:.6f}",
        "",
        "## Task results",
        "",
        "| Task | Description | Result | Evaluated | Stability | Policy |",
        "|---|---|---:|---:|---|---|",
    ]
    for result in summary.task_results:
        lines.append(
            f"| {_code(result.task.task_id)} | {_inline(result.task.description)} | "
            f"{result.k}/{result.n} | {result.evaluated}/{result.n} | "
            f"{_label(result.stability)} | {_policy_summary(result)} |"
        )
    if not summary.task_results:
        lines.append("| _No tasks_ | | | | | |")

    lines.extend(["", "## Tasks by stability", ""])
    for stability in _STABILITIES:
        task_ids = summary.tasks_by_stability(stability)
        names = ", ".join(_code(task_id) for task_id in task_ids) or "_none_"
        lines.append(f"- {_label(stability)}: {names}")

    lines.extend(["", "## Attempt-level findings", ""])
    found = False
    for result in summary.task_results:
        findings = _findings(result)
        if findings:
            found = True
            lines.extend(
                [
                    f"### {_code(result.task.task_id)} — "
                    f"{_inline(result.task.description)}",
                    "",
                    *findings,
                    "",
                ]
            )
    if not found:
        lines.append("No attempt-level findings.")
    return "\n".join(lines).rstrip() + "\n"
