"""report.md's "Where runs first fail" section (ROADMAP R37, slice 2).

``transition_section`` renders ``reports/heatmap.py``'s matrix from the
run's own records -- its task results, its trace and its tool-call records
-- at render time. The writer passes the run's records; rescore passes the
ones it reads back, so a re-render gives the same bytes. Nothing new is
written: format 0.6 is unchanged, and a bundle from before R37 rescores
CODE_CHANGED with its verdicts reproducing.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import fast_yaml
from reference_bundles import CONFIGS, ReferenceBundles
from typer.testing import CliRunner

from agentevalops.bundles.serializers import (
    from_jsonable,
    load_run_config,
    load_tool_call,
    load_trace_event,
)
from agentevalops.cli import app
from agentevalops.core.schemas import (
    RunSummary,
    ToolCallRecord,
    TraceEvent,
)
from agentevalops.reports.heatmap import transition_matrix
from agentevalops.reports.markdown import render_report, transition_section

FIXTURES = Path(__file__).parent / "fixtures" / "bundles"
HEADING = "## Where runs first fail"
READING = (
    "Rows: the last completed stage. Columns: the stage of the first failure, "
    "judged from the failing evaluations' record citations. A cited tool call "
    "that went wrong -- denied, faulted, erroring or not executed -- is the "
    "first failure before any cited call that ran cleanly."
)
HEADER = "| From \\ In | issue | gate | exec | handle | finish | unattributed |"


def _records(
    bundle: Path,
) -> tuple[RunSummary, list[TraceEvent], list[ToolCallRecord]]:
    summary = from_jsonable(
        RunSummary, json.loads((bundle / "summary.json").read_text("utf-8"))
    )
    lines = (bundle / "traces.jsonl").read_text("utf-8").splitlines()
    events = [load_trace_event(json.loads(line)) for line in lines if line]
    lines = (bundle / "tool_calls.jsonl").read_text("utf-8").splitlines()
    calls = [load_tool_call(json.loads(line)) for line in lines if line]
    return summary, events, calls


def _section(report: str) -> list[str]:
    lines = report.splitlines()
    start = lines.index(HEADING)
    end = next(
        (n for n in range(start + 1, len(lines)) if lines[n].startswith("## ")),
        len(lines),
    )
    return lines[start:end]


def test_an_all_pass_run_states_the_matrix_is_empty(
    reference_bundles: ReferenceBundles,
) -> None:
    """Fail-first: report.md had no such section."""
    report = (reference_bundles["seed"].path / "report.md").read_text("utf-8")
    assert _section(report) == [
        HEADING,
        "",
        "No failed task runs; the matrix is empty.",
        "",
    ]


def test_failed_runs_fill_the_matrix_and_the_not_evaluated_are_counted_apart(
    tmp_path: Path,
) -> None:
    """Five known-bad scenarios at $0.25 with a $0.50 cap: two run and
    fail, three are not evaluated."""
    declared = fast_yaml.safe_load((CONFIGS / "seed_system.yaml").read_text("utf-8"))
    declared["run_limits"] = {"max_run_cost_usd": 0.5}
    (tmp_path / "system.yaml").write_text(fast_yaml.safe_dump(declared), "utf-8")
    doc = fast_yaml.safe_load(
        (CONFIGS / "agentic_baseline_mock.yaml").read_text("utf-8")
    )
    doc.update(
        system="system.yaml",
        mock_trajectory="known_bad",
        mock_cost_per_task_usd=0.25,
        benchmark_options={
            "pack": "seed",
            "scenarios": ["TE1", "TE3", "TC1", "PC5", "RB5"],
        },
    )
    config = tmp_path / "seed.yaml"
    config.write_text(fast_yaml.safe_dump(doc), "utf-8")
    out = tmp_path / "bundle"
    result = CliRunner().invoke(
        app, ["run", "--config", str(config), "--output", str(out)]
    )
    assert result.exit_code == 0, result.output
    section = _section((out / "report.md").read_text("utf-8"))
    assert section[:6] == [
        HEADING,
        "",
        READING,
        "2 failed task runs of 5; 3 not evaluated are not in the matrix.",
        "",
        HEADER,
    ]
    rows = [line for line in section[7:] if line.startswith("| ")]
    total = 0
    for row in rows:
        cells = [c.strip() for c in row.strip("|").split("|")][1:]
        assert len(cells) == 6, row
        for cell in cells:
            assert cell == "–" or cell.split(" ", 1)[0].isdigit(), cell
            assert cell != "0"
            total += 0 if cell == "–" else int(cell.split(" ", 1)[0])
    assert total == 2


def test_a_bundle_renders_its_section_twice_byte_for_byte(
    reference_bundles: ReferenceBundles, tmp_path: Path
) -> None:
    """Rescore re-renders report.md from the records it reads back."""
    bundle = reference_bundles["known-bad"].path
    reports = []
    for n in (1, 2):
        out = tmp_path / f"rescore-{n}"
        result = CliRunner().invoke(
            app, ["rescore", "--bundle", str(bundle), "--output", str(out)]
        )
        assert result.exit_code == 0, result.output
        assert result.output.rstrip().endswith("RESCORE: MATCH")
        reports.append((out / "report.md").read_bytes())
    assert reports[0] == reports[1] == (bundle / "report.md").read_bytes()
    section = _section(reports[0].decode("utf-8"))
    assert "26 failed task runs of 26." in section
    assert HEADER in section


def test_the_section_renders_from_a_0_5_bundles_records() -> None:
    """The refs exist since format 0.5: the legacy fixture passes every
    run, and with one of its runs judged failed in memory, its own
    tool_use refs place that run."""
    summary, events, calls = _records(FIXTURES / "seed-narrow-0.5")
    assert transition_section(summary, events, calls) == [
        HEADING,
        "",
        "No failed task runs; the matrix is empty.",
        "",
    ]
    first = summary.task_results[0]
    failing = [
        replace(e, passed=False, score=0.0) if e.evaluator_kind == "tool_use" else e
        for e in first.evaluations
    ]
    assert any(e.refs for e in failing if e.evaluator_kind == "tool_use")
    failed = replace(
        first,
        evaluation=replace(first.evaluation, passed=False, score=0.0),
        evaluations=failing,
    )
    judged = replace(summary, task_results=[failed, *summary.task_results[1:]])
    section = transition_section(judged, events, calls)
    assert section[3] == "1 failed task run of 3."
    matrix = transition_matrix(judged.task_results, calls, events)
    assert matrix.failed == 1 and sum(c.count for c in matrix.cells) == 1
    (cell,) = matrix.cells
    assert any(
        line.startswith(f"| {cell.row} |") and f"1 ({cell.labels[0]})" in line
        for line in section
    )


def test_a_bundle_from_before_r37_rescores_code_changed_with_no_verdict_moved(
    tmp_path: Path,
) -> None:
    out = tmp_path / "rescored"
    result = CliRunner().invoke(
        app,
        [
            "rescore",
            "--bundle",
            str(FIXTURES / "seed-narrow-known-bad-0.6"),
            "--output",
            str(out),
        ],
    )
    assert result.exit_code == 3, result.output
    lines = result.output.splitlines()
    assert any(line.startswith("Verdicts: reproduce -- ") for line in lines), lines
    assert result.output.rstrip().endswith(
        "RESCORE: CODE_CHANGED -- the verdicts reproduce under the running code"
    )
    section = _section((out / "report.md").read_text("utf-8"))
    assert "4 failed task runs of 4." in section
    assert HEADING not in (
        FIXTURES / "seed-narrow-known-bad-0.6" / "report.md"
    ).read_text("utf-8")


def test_a_render_without_the_tool_call_records_says_it_needs_them(
    reference_bundles: ReferenceBundles,
) -> None:
    """render_report's records default to none: with failed runs whose
    trace shows tool calls, the section says what it needs instead of
    failing or guessing."""
    bundle = reference_bundles["known-bad"].path
    summary, events, _ = _records(bundle)
    config: dict[str, Any] = json.loads((bundle / "config.json").read_text("utf-8"))
    report = render_report(load_run_config(config["run_config"]), summary, events)
    assert _section(report) == [
        HEADING,
        "",
        "The matrix needs the run's tool-call records; this render was given none.",
        "",
    ]


def test_an_empty_cell_is_a_dash_never_a_zero(
    reference_bundles: ReferenceBundles,
) -> None:
    summary, events, calls = _records(reference_bundles["known-bad"].path)
    rows = [
        line
        for line in transition_section(summary, events, calls)
        if line.startswith("| ") and line != HEADER
    ]
    assert rows
    assert all(" 0 " not in row for row in rows)
    assert any("–" in row for row in rows)


def test_the_known_bad_reading_with_the_preference(
    reference_bundles: ReferenceBundles,
) -> None:
    """The seed known-bad bundle's matrix, re-asserted under R42's
    preference (D-R42-4). One run moved, on purpose: PC2's tool_use
    evaluation cites its clean case_lookup and status_update (calls 0 and
    1), its policy evaluation the denied send_notice and the approved one
    after it (2 and 3), so the denied call is now the first failure --
    issue -> gate, where the smallest index had put it at parse -> issue.
    The other 22 runs at parse -> issue cite no call that went wrong."""
    summary, events, calls = _records(reference_bundles["known-bad"].path)
    matrix = transition_matrix(summary.task_results, calls, events)
    assert (matrix.task_runs, matrix.failed, matrix.not_evaluated) == (26, 26, 0)
    assert sum(cell.count for cell in matrix.cells) == matrix.failed
    assert {(c.row, c.column): c.count for c in matrix.cells} == {
        ("parse", "issue"): 22,
        ("issue", "gate"): 1,
        ("issue", "exec"): 1,
        ("exec", "handle"): 2,
    }
    assert matrix.labels("issue", "gate") == ("PC2:send_notice",)
    assert "PC2:case_lookup" not in matrix.labels("parse", "issue")
    assert matrix.labels("issue", "exec") == ("RB2:case_lookup",)
    assert matrix.labels("exec", "handle") == ("PC7:send_notice", "RB1:case_lookup")
