"""CLI tests for the validate-bundle command (WBS 8)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agentevalops.bundles.constants import MANIFEST_FILENAME
from agentevalops.bundles.writer import BundleWriter
from agentevalops.cli import app
from agentevalops.core.schemas import (
    AgentOutput,
    EvaluationResult,
    PolicyVerdict,
    ResourceLimits,
    RunConfig,
    TerminationReason,
    TraceEvent,
    TraceEventKind,
    Verdict,
)
from agentevalops.core.types import BACKEND_LOCAL, AgentId, RunId, TaskId
from agentevalops.orchestration.local import RunSummary, TaskRunResult

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_bundle(tmp_path: Path) -> Path:
    run_id = RunId("cli-val-001")
    run_config = RunConfig(
        run_id=run_id,
        agent_id=AgentId("mock"),
        backend_id=BACKEND_LOCAL,
        resource_limits=ResourceLimits(),
    )
    events = [
        TraceEvent(
            run_id=run_id,
            step_index=0,
            kind=TraceEventKind.AGENT_PLAN,
            payload={},
        ),
        TraceEvent(
            run_id=run_id,
            step_index=1,
            kind=TraceEventKind.AGENT_TERMINAL,
            payload={"success": True},
        ),
    ]
    output = AgentOutput(
        success=True,
        termination_reason=TerminationReason.COMPLETED,
        total_tokens=5,
    )
    evaluation = EvaluationResult(
        evaluator_id="ev",
        evaluator_kind="deterministic",
        score=1.0,
        passed=True,
        notes="ok",
    )
    policy_verdict = PolicyVerdict(
        checker_id="basic-v1",
        policy_id="p1",
        verdict=Verdict.PASS,
        citations=[],
        notes="ok",
    )
    task_result = TaskRunResult(
        task_id=TaskId("t-1"),
        output=output,
        evaluation=evaluation,
        event_count=2,
    )
    summary = RunSummary(
        run_id=run_id,
        task_results=[task_result],
        policy_verdict=policy_verdict,
        total_tasks=1,
        passed_tasks=1,
        total_cost_usd=0.0,
        total_tokens=5,
        trace_event_count=2,
    )
    bundle_dir = tmp_path / "bundle"
    BundleWriter(bundle_dir).write(
        run_config=run_config,
        summary=summary,
        all_events=events,
    )
    return bundle_dir


# ---------------------------------------------------------------------------
# Valid bundle
# ---------------------------------------------------------------------------


def test_validate_bundle_exits_zero_for_valid_bundle(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    result = runner.invoke(
        app, ["validate-bundle", "--bundle", str(bundle_dir)]
    )
    assert result.exit_code == 0


def test_validate_bundle_output_contains_pass(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    result = runner.invoke(
        app, ["validate-bundle", "--bundle", str(bundle_dir)]
    )
    assert "PASS" in result.output


def test_validate_bundle_output_contains_files_checked(
    tmp_path: Path,
) -> None:
    bundle_dir = _make_bundle(tmp_path)
    result = runner.invoke(
        app, ["validate-bundle", "--bundle", str(bundle_dir)]
    )
    assert "Files checked:" in result.output


def test_validate_bundle_output_contains_bundle_path(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    result = runner.invoke(
        app, ["validate-bundle", "--bundle", str(bundle_dir)]
    )
    assert "Bundle:" in result.output


# ---------------------------------------------------------------------------
# Missing bundle path
# ---------------------------------------------------------------------------


def test_validate_bundle_missing_path_exits_nonzero(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["validate-bundle", "--bundle", str(tmp_path / "no_such_dir")],
    )
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Tampered bundle (checksum mismatch)
# ---------------------------------------------------------------------------


def test_validate_bundle_tampered_fails(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    # Modify a content file after manifest was written
    summary_path = bundle_dir / "summary.json"
    obj = json.loads(summary_path.read_text(encoding="utf-8"))
    obj["total_cost_usd"] = 12345.0
    summary_path.write_text(json.dumps(obj), encoding="utf-8")
    result = runner.invoke(
        app, ["validate-bundle", "--bundle", str(bundle_dir)]
    )
    assert result.exit_code != 0


def test_validate_bundle_tampered_output_contains_fail(
    tmp_path: Path,
) -> None:
    bundle_dir = _make_bundle(tmp_path)
    summary_path = bundle_dir / "summary.json"
    obj = json.loads(summary_path.read_text(encoding="utf-8"))
    obj["total_cost_usd"] = 12345.0
    summary_path.write_text(json.dumps(obj), encoding="utf-8")
    result = runner.invoke(
        app, ["validate-bundle", "--bundle", str(bundle_dir)]
    )
    assert "FAIL" in result.output or "FAIL" in (result.stderr or "")


# ---------------------------------------------------------------------------
# Missing manifest
# ---------------------------------------------------------------------------


def test_validate_bundle_missing_manifest_exits_nonzero(
    tmp_path: Path,
) -> None:
    bundle_dir = _make_bundle(tmp_path)
    (bundle_dir / MANIFEST_FILENAME).unlink()
    result = runner.invoke(
        app, ["validate-bundle", "--bundle", str(bundle_dir)]
    )
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Replay still passes on WBS 8 bundle
# ---------------------------------------------------------------------------


def test_replay_passes_on_wbs8_bundle(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    result = runner.invoke(app, ["replay", "--bundle", str(bundle_dir)])
    assert result.exit_code == 0


def test_replay_output_contains_format_version(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    result = runner.invoke(app, ["replay", "--bundle", str(bundle_dir)])
    assert "Format:" in result.output


def test_replay_output_contains_manifest_status(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    result = runner.invoke(app, ["replay", "--bundle", str(bundle_dir)])
    assert "Manifest:" in result.output


def test_replay_on_tampered_bundle_fails(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    # Tamper after manifest was written
    summary_path = bundle_dir / "summary.json"
    obj = json.loads(summary_path.read_text(encoding="utf-8"))
    obj["total_cost_usd"] = 99999.0
    summary_path.write_text(json.dumps(obj), encoding="utf-8")
    result = runner.invoke(app, ["replay", "--bundle", str(bundle_dir)])
    assert result.exit_code != 0


def test_replay_remains_read_only(tmp_path: Path) -> None:
    """Replay must not create new files in the bundle directory."""
    bundle_dir = _make_bundle(tmp_path)
    files_before = set(bundle_dir.iterdir())
    runner.invoke(app, ["replay", "--bundle", str(bundle_dir)])
    files_after = set(bundle_dir.iterdir())
    assert files_before == files_after
