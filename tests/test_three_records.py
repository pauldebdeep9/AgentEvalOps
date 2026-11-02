"""The three records of each evaluation result agree (ADR-0008 C1; R29 (a)
slice 3).

A 0.6 bundle records every evaluation result three times: its entry in
evaluations.json, its place in summary.json's task results, and its
evaluator.score event in traces.jsonl. validate-bundle holds them to each
other. This catches an inconsistent edit -- one file changed and the
manifest re-sealed -- not a careful forger who edits all three: only a
signature, or rescore, catches that.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agentevalops.bundles.manifest import write_manifest
from agentevalops.bundles.validator import validate_bundle
from agentevalops.cli import app

ROOT = Path(__file__).resolve().parents[1]
TASK, REP, EVALUATOR = "TE2", 0, "deterministic-v1"


@pytest.fixture(scope="module")
def seed(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("c1") / "seed"
    result = CliRunner().invoke(
        app,
        [
            "run",
            "--config",
            str(ROOT / "configs/agentic_baseline_mock.yaml"),
            "--output",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    return out


def _flip_evaluations(bundle: Path) -> None:
    path = bundle / "evaluations.json"
    entries = json.loads(path.read_text("utf-8"))
    (entry,) = [
        e
        for e in entries
        if (e["task_id"], e["repetition"], e["evaluator_id"]) == (TASK, REP, EVALUATOR)
    ]
    entry["passed"] = not entry["passed"]
    path.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def _flip_summary(bundle: Path) -> None:
    path = bundle / "summary.json"
    summary = json.loads(path.read_text("utf-8"))
    (run,) = [
        r
        for r in summary["task_results"]
        if (r["task_id"], r["repetition"]) == (TASK, REP)
    ]
    (entry,) = [e for e in run["evaluations"] if e["evaluator_id"] == EVALUATOR]
    entry["passed"] = not entry["passed"]
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def _flip_trace(bundle: Path) -> None:
    path = bundle / "traces.jsonl"
    lines = []
    for line in path.read_text("utf-8").splitlines():
        event = json.loads(line)
        if (
            event["kind"] == "evaluator.score"
            and (event["task_id"], event["repetition"]) == (TASK, REP)
            and event["payload"]["evaluator_id"] == EVALUATOR
        ):
            event["payload"]["passed"] = not event["payload"]["passed"]
        lines.append(json.dumps(event))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


FLIPS: dict[str, Callable[[Path], None]] = {
    "evaluations.json": _flip_evaluations,
    "summary.json": _flip_summary,
    "traces.jsonl": _flip_trace,
}


def _copy(seed: Path, tmp_path: Path) -> Path:
    copy = tmp_path / "bundle"
    shutil.copytree(seed, copy)
    return copy


def test_a_real_bundles_three_records_agree(seed: Path) -> None:
    result = validate_bundle(seed)
    assert result.valid, result.errors
    assert not any("disagree" in e for e in result.errors)


@pytest.mark.parametrize("name", list(FLIPS))
def test_a_verdict_flipped_in_one_record_fails_validation(
    seed: Path, tmp_path: Path, name: str
) -> None:
    """Fail-first, once per file: before C1 each of these validated."""
    bundle = _copy(seed, tmp_path)
    FLIPS[name](bundle)
    write_manifest(bundle)  # re-sealed: the checksums say nothing
    result = validate_bundle(bundle)
    assert not result.valid
    disagreement = [e for e in result.errors if "evaluation results disagree" in e]
    assert disagreement == [
        next(e for e in disagreement if f"at {TASK}/{REP} ({EVALUATOR}): passed" in e)
    ]
    assert name in disagreement[0]


def test_a_result_missing_from_one_record_fails(seed: Path, tmp_path: Path) -> None:
    bundle = _copy(seed, tmp_path)
    path = bundle / "summary.json"
    summary: dict[str, Any] = json.loads(path.read_text("utf-8"))
    run = next(r for r in summary["task_results"] if r["task_id"] == TASK)
    run["evaluations"] = run["evaluations"][:-1]
    path.write_text(json.dumps(summary), encoding="utf-8")
    write_manifest(bundle)
    result = validate_bundle(bundle)
    assert any(
        f"evaluation results disagree at {TASK}/{REP}: " in e for e in result.errors
    )


def test_a_consistent_edit_of_all_three_is_not_this_checks_to_catch(
    seed: Path, tmp_path: Path
) -> None:
    """The documented limit: an edit made consistently in all three records,
    with the manifest re-sealed, passes this check. A signature over the
    manifest (R29) or rescore (R29 (b)) is what catches it."""
    bundle = _copy(seed, tmp_path)
    for flip in FLIPS.values():
        flip(bundle)
    write_manifest(bundle)
    result = validate_bundle(bundle)
    assert not any("evaluation results disagree" in e for e in result.errors)
