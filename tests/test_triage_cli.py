"""``agentevalops triage`` (ROADMAP R38, slice 3).

The command walks a bundle's sampling frame in the terminal -- the walker
behind typer's prompts -- with the task text and category rebuilt from the
bundle's own config.json and plan, and ``--summarize`` turns the sidecar
into counts: the reviewer's verdict against the harness's as the
headline, its off-diagonal cells named (evaluator blind spots, over-strict
evaluators), deferrals beside the table, then counts by category and the
notes by verdict. Nothing calls a model; nothing writes into the bundle.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from reference_bundles import ReferenceBundles
from typer.testing import CliRunner

from agentevalops.cli import app
from agentevalops.replay.rebuild import rebuild_run
from agentevalops.triage.frame import load_frame
from agentevalops.triage.store import append_annotation, load_annotations, sidecar_path

FIXTURES = Path(__file__).parent / "fixtures" / "bundles"
T0 = datetime(2026, 11, 9, 8, 30, tzinfo=timezone.utc)


def _now() -> datetime:
    return T0


def _copy(source: Path, tmp_path: Path) -> Path:
    """A copy: the sidecar is written beside the bundle, never beside a
    committed fixture."""
    return Path(shutil.copytree(source, tmp_path / source.name))


def _triage(bundle: Path, *args: str, input: str = "") -> Any:
    return CliRunner().invoke(
        app, ["triage", "--bundle", str(bundle), *args], input=input
    )


def _run_id(bundle: Path) -> str:
    return str(json.loads((bundle / "summary.json").read_text("utf-8"))["run_id"])


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------


def test_a_walkthrough_through_the_cli_writes_countable_notes(tmp_path: Path) -> None:
    """Fail-first: no triage command. The known-bad 0.6 fixture's four
    failed runs, each failed with a note."""
    bundle = _copy(FIXTURES / "seed-narrow-known-bad-0.6", tmp_path)
    answers = "".join(f"f\nnote {n}\n\n" for n in range(4))
    result = _triage(bundle, input=answers)
    assert result.exit_code == 0, result.output
    assert "End of the frame: 4 annotated this session." in result.output
    annotations = load_annotations(bundle, _run_id(bundle))
    assert sorted(a.note for a in annotations.values()) == [
        f"note {n}" for n in range(4)
    ]
    assert {a.verdict for a in annotations.values()} == {"fail"}
    assert all(a.annotator is None for a in annotations.values())


def test_the_task_text_and_category_come_from_the_rebuilt_run(tmp_path: Path) -> None:
    bundle = _copy(FIXTURES / "seed-narrow-known-bad-0.6", tmp_path)
    rebuilt = rebuild_run(
        json.loads((bundle / "config.json").read_text("utf-8")),
        json.loads((bundle / "test_plan.json").read_text("utf-8")),
    )
    first = load_frame(bundle).entries[0]
    (task,) = [t for t in rebuilt.tasks if str(t.task_id) == first.task_id]
    category = rebuilt.scenario_info[first.task_id]["category"]
    result = _triage(bundle, input="q\n")
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    screen = next(n for n, line in enumerate(lines) if line.startswith("[1/4] "))
    assert lines[screen] == f"[1/4] {first.task_id} rep 0 · {category} · scripted run"
    assert lines[screen + 1] == f"Task: {task.description}"
    assert "not recorded in this view" not in result.output


def test_a_bundle_whose_benchmark_does_not_rebuild_is_still_walked(
    reference_bundles: ReferenceBundles, tmp_path: Path
) -> None:
    """The carried-preflight bundle's edited pack is not registered here:
    the walk goes on, without task text, and says why."""
    bundle = reference_bundles["carried-preflight"].copy(tmp_path / "preflight")
    result = _triage(bundle, input="q\n")
    assert result.exit_code == 0, result.output
    assert "Task text and categories are not available: " in result.output
    assert "Task: (not recorded in this view)" in result.output


def test_include_not_evaluated_walks_them(
    reference_bundles: ReferenceBundles, tmp_path: Path
) -> None:
    """The carried-infra bundle: PC5's infrastructure failed. Without the
    flag it is counted, not walked; with it, walked."""
    bundle = reference_bundles["carried-infra"].copy(tmp_path / "infra")
    plain = _triage(bundle, input="q\n")
    assert "1 not evaluated, not walked." in plain.output
    flagged = _triage(bundle, "--include-not-evaluated", input="d\n\n\n" * 3)
    assert flagged.exit_code == 0, flagged.output
    assert "1 not evaluated, walked." in flagged.output
    assert "The harness did not evaluate this run." not in flagged.output  # deferred
    annotations = load_annotations(bundle, _run_id(bundle))
    assert ("PC5", 0) in annotations


def test_the_flags_reach_the_walk(tmp_path: Path) -> None:
    """--annotator is recorded; --seed overrides the run's own; findings
    come first with --show-findings-first; --passing-share sizes the
    passing slice."""
    bundle = _copy(FIXTURES / "seed-narrow-0.6", tmp_path)
    result = _triage(
        bundle,
        "--annotator",
        "dp",
        "--seed",
        "11",
        "--show-findings-first",
        "--passing-share",
        "1.0",
        input="p\n\nq\n",
    )
    assert result.exit_code == 0, result.output
    assert "4 of 4 passing sampled" in result.output and "Seed 11." in result.output
    assert result.output.index("Harness: PASSED") < result.output.index("Your verdict")
    (annotation,) = load_annotations(bundle, _run_id(bundle)).values()
    assert annotation.annotator == "dp"


def test_a_bundle_that_does_not_read_cannot_be_triaged(tmp_path: Path) -> None:
    result = _triage(tmp_path / "missing")
    assert result.exit_code == 2
    assert "Cannot triage" in result.output


# ---------------------------------------------------------------------------
# --summarize
# ---------------------------------------------------------------------------


def _annotate(bundle: Path, *rows: tuple[str, str, str]) -> None:
    for task, verdict, note in rows:
        append_annotation(bundle, _run_id(bundle), task, 0, verdict, note, now=_now)  # type: ignore[arg-type]


def test_summarize_heads_with_the_disagreement_table_naming_blind_spots(
    reference_bundles: ReferenceBundles, tmp_path: Path
) -> None:
    """The seed run passes everything: three runs a reviewer failed are the
    evaluators' blind spots."""
    bundle = reference_bundles["seed"].copy(tmp_path / "seed")
    _annotate(
        bundle,
        ("TE1", "fail", "notice lacks the reason"),
        ("TE2", "fail", "wrong reason code"),
        ("TC1", "fail", "lookup skipped"),
        ("TE3", "pass", ""),
        ("PC1", "pass", "fine"),
        ("RB1", "defer", "cannot tell"),
    )
    result = _triage(bundle, "--summarize")
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[:9] == [
        f"Triage of {_run_id(bundle)}: 6 of 26 task runs annotated.",
        "",
        "Reviewer verdict x harness verdict (pass/fail only):",
        "                  harness pass  harness fail",
        "  reviewer pass              2             0",
        "  reviewer fail              3             0",
        "Evaluator blind spots (reviewer fail, harness pass): 3",
        "Over-strict evaluators (reviewer pass, harness fail): 0",
        "Outside the table: 1 deferred; 0 the harness did not evaluate.",
    ]
    assert "  task_execution: 1 pass, 2 fail, 0 defer" in lines
    assert "  robustness: 0 pass, 0 fail, 1 defer" in lines
    notes = lines[lines.index("Notes by reviewer verdict:") :]
    assert notes[:5] == [
        "Notes by reviewer verdict:",
        "  fail (3):",
        "    TC1 rep 0: lookup skipped",
        "    TE1 rep 0: notice lacks the reason",
        "    TE2 rep 0: wrong reason code",
    ]
    assert "    TE3 rep 0: (no note)" in notes


def test_a_superseding_line_wins_in_summarize(
    reference_bundles: ReferenceBundles, tmp_path: Path
) -> None:
    bundle = reference_bundles["known-bad"].copy(tmp_path / "known-bad")
    _annotate(bundle, ("TE1", "fail", "first look"), ("TE1", "pass", "on reflection"))
    result = _triage(bundle, "--summarize")
    assert "Over-strict evaluators (reviewer pass, harness fail): 1" in result.output
    assert "  reviewer pass              0             1" in result.output
    assert "first look" not in result.output and "on reflection" in result.output


def test_defers_sit_outside_the_two_by_two(
    reference_bundles: ReferenceBundles, tmp_path: Path
) -> None:
    bundle = reference_bundles["known-bad"].copy(tmp_path / "known-bad")
    _annotate(bundle, ("TE1", "defer", ""), ("TE2", "defer", ""), ("TE3", "defer", ""))
    result = _triage(bundle, "--summarize")
    assert (
        "Outside the table: 3 deferred; 0 the harness did not evaluate."
        in result.output
    )
    assert "  reviewer pass              0             0" in result.output
    assert "  reviewer fail              0             0" in result.output


def test_summarize_without_a_sidecar_says_so(tmp_path: Path) -> None:
    bundle = _copy(FIXTURES / "seed-narrow-0.6", tmp_path)
    result = _triage(bundle, "--summarize")
    assert result.exit_code == 0
    assert result.output.strip() == (
        f"No triage annotations beside this bundle ({sidecar_path(bundle)})."
    )


@pytest.mark.parametrize("flag", ["--passing-share", "--seed"])
def test_a_bad_option_value_is_refused(tmp_path: Path, flag: str) -> None:
    bundle = _copy(FIXTURES / "seed-narrow-0.6", tmp_path)
    result = _triage(bundle, flag, "x")
    assert result.exit_code != 0
