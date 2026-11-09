"""``triage --summarize``: a bundle's sidecar as counts (ROADMAP R38).

The headline is the reviewer's verdict against the harness's, pass and
fail only, with its two off-diagonal cells named: a run the reviewer
failed and the harness passed is an evaluator's blind spot; one the
reviewer passed and the harness failed, an over-strict evaluator. A
deferral is "could not judge", not a disagreement, so it is counted beside
the table, as is a run the harness did not evaluate. Then the verdicts by
category, and the notes grouped by verdict. Only counts: grouping the
notes into failure modes stays a human step, and nothing here calls a
model.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from agentevalops.triage.store import Annotation
from agentevalops.triage.walker import TaskInfo, TriageView

__all__ = ["TriageSummary", "render_summary", "summarize"]

#: The order verdicts are listed in.
_ORDER = ("fail", "pass", "defer")


@dataclass(frozen=True)
class TriageSummary:
    run_id: str
    task_runs: int
    annotated: int
    #: (reviewer verdict, harness verdict) -> runs, pass/fail only.
    table: Mapping[tuple[str, str], int]
    deferred: int
    not_evaluated: int
    #: category -> verdict -> runs.
    by_category: Mapping[str, Mapping[str, int]]
    #: verdict -> (task_id, repetition, note), sorted.
    notes: Mapping[str, tuple[tuple[str, int, str], ...]] = field(default_factory=dict)


def summarize(
    view: TriageView,
    annotations: Mapping[tuple[str, int], Annotation],
    task_info: Mapping[str, TaskInfo] | None = None,
) -> TriageSummary:
    """The counts of *annotations* (the last per task run) against the
    harness's verdicts in *view*; an annotation of a task run the bundle
    does not hold is not counted."""
    table = {(r, h): 0 for r in ("pass", "fail") for h in ("pass", "fail")}
    deferred = not_evaluated = 0
    by_category: dict[str, dict[str, int]] = {}
    notes: dict[str, list[tuple[str, int, str]]] = {v: [] for v in _ORDER}
    counted = 0
    for key, annotation in annotations.items():
        records = view.records.get(key)
        if records is None:
            continue
        counted += 1
        evaluation = records.result.evaluation
        category = (task_info or {}).get(key[0], TaskInfo()).category or "-"
        tally = by_category.setdefault(category, dict.fromkeys(_ORDER, 0))
        tally[annotation.verdict] += 1
        notes[annotation.verdict].append((key[0], key[1], annotation.note))
        if annotation.verdict == "defer":
            deferred += 1
        elif not evaluation.evaluated:
            not_evaluated += 1
        else:
            harness = "pass" if evaluation.passed else "fail"
            table[(annotation.verdict, harness)] += 1
    return TriageSummary(
        run_id=view.run_id,
        task_runs=len(view.records),
        annotated=counted,
        table=table,
        deferred=deferred,
        not_evaluated=not_evaluated,
        by_category={c: by_category[c] for c in sorted(by_category)},
        notes={v: tuple(sorted(notes[v])) for v in _ORDER},
    )


def render_summary(summary: TriageSummary) -> str:
    table = summary.table
    lines = [
        f"Triage of {summary.run_id}: {summary.annotated} of {summary.task_runs} "
        "task runs annotated.",
        "",
        "Reviewer verdict x harness verdict (pass/fail only):",
        " " * 18 + "harness pass  harness fail",
    ]
    for reviewer in ("pass", "fail"):
        lines.append(
            f"  reviewer {reviewer:<4}{table[(reviewer, 'pass')]:>15}"
            f"{table[(reviewer, 'fail')]:>14}"
        )
    lines += [
        "Evaluator blind spots (reviewer fail, harness pass): "
        f"{table[('fail', 'pass')]}",
        "Over-strict evaluators (reviewer pass, harness fail): "
        f"{table[('pass', 'fail')]}",
        f"Outside the table: {summary.deferred} deferred; "
        f"{summary.not_evaluated} the harness did not evaluate.",
        "",
        "By category:",
    ]
    lines += [
        f"  {category}: {c['pass']} pass, {c['fail']} fail, {c['defer']} defer"
        for category, c in summary.by_category.items()
    ]
    lines += ["", "Notes by reviewer verdict:"]
    for verdict in _ORDER:
        entries = summary.notes.get(verdict, ())
        lines.append(f"  {verdict} ({len(entries)}):")
        lines += [
            f"    {task} rep {rep}: {note or '(no note)'}"
            for task, rep, note in entries
        ]
    return "\n".join(lines)
