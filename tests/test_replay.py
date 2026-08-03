"""Tests for LocalReplayVerifier and the CLI replay/validate-bundle commands.

docs/adr/0003-replay-semantics.md, D1/D2: ``LocalReplayVerifier`` is now an
internal cross-file-consistency helper folded into
``bundles.validator.validate_bundle()`` -- it returns raw ``(errors,
warnings)``, not a standalone ``ReplaySummary`` (deleted). ``replay`` is a
deprecated CLI alias for ``validate-bundle``, delegating to the identical
code path.
"""

from __future__ import annotations

import pathlib
from pathlib import Path

from typer.testing import CliRunner

from agentevalops.bundles.reader import BundleReader, LoadedBundle
from agentevalops.bundles.writer import BundleWriter
from agentevalops.cli import app
from agentevalops.core.schemas import (
    AgentOutput,
    EvaluationResult,
    PolicyVerdict,
    ResourceLimits,
    RunConfig,
    RunSummary,
    TaskRunResult,
    TerminationReason,
    TraceEvent,
    TraceEventKind,
    Verdict,
)
from agentevalops.core.types import BACKEND_LOCAL, AgentId, RunId, TaskId
from agentevalops.replay.local import LocalReplayVerifier

runner = CliRunner()
_CONFIG = (
    pathlib.Path(__file__).parent.parent / "configs" / "toy_smoke.yaml"
)


# ---------------------------------------------------------------------------
# Bundle + LoadedBundle helpers
# ---------------------------------------------------------------------------


def _make_valid_bundle(tmp_path: Path) -> Path:
    run_id = RunId("replay-test-001")
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
        notes="all checks passed",
    )
    task_result = TaskRunResult(
        task_id=TaskId("t-1"),
        output=output,
        evaluation=evaluation,
        event_count=len(events),
    )
    summary = RunSummary(
        run_id=run_id,
        task_results=[task_result],
        policy_verdict=policy_verdict,
        total_tasks=1,
        passed_tasks=1,
        total_cost_usd=0.0,
        total_tokens=5,
        trace_event_count=len(events),
    )
    bundle_dir = tmp_path / "bundle"
    BundleWriter(bundle_dir).write(
        run_config=run_config,
        summary=summary,
        all_events=events,
    )
    return bundle_dir


def _load(bundle_dir: Path) -> LoadedBundle:
    return BundleReader(bundle_dir).read()


# ---------------------------------------------------------------------------
# LocalReplayVerifier — valid bundle
# ---------------------------------------------------------------------------


def test_check_passes_on_valid_bundle(tmp_path: Path) -> None:
    loaded = _load(_make_valid_bundle(tmp_path))
    errors, warnings = LocalReplayVerifier(loaded).check()
    assert errors == []
    assert warnings == []


# ---------------------------------------------------------------------------
# LocalReplayVerifier — consistency failures
# ---------------------------------------------------------------------------


def test_check_fails_when_traces_empty(tmp_path: Path) -> None:
    bundle_dir = _make_valid_bundle(tmp_path)
    # Overwrite traces.jsonl with empty content
    (bundle_dir / "traces.jsonl").write_text("", encoding="utf-8")
    loaded = _load(bundle_dir)
    errors, _ = LocalReplayVerifier(loaded).check()
    assert any("no events" in e for e in errors)


def test_check_fails_when_summary_trace_count_mismatches(
    tmp_path: Path,
) -> None:
    bundle_dir = _make_valid_bundle(tmp_path)
    # Corrupt the event_count in task_results
    import json

    summary_data = json.loads(
        (bundle_dir / "summary.json").read_text()
    )
    summary_data["task_results"][0]["event_count"] = 999
    (bundle_dir / "summary.json").write_text(
        json.dumps(summary_data), encoding="utf-8"
    )
    reloaded = _load(bundle_dir)
    errors, _ = LocalReplayVerifier(reloaded).check()
    assert any("mismatch" in e for e in errors)


def test_check_fails_when_run_id_missing_from_metadata(
    tmp_path: Path,
) -> None:
    import json

    bundle_dir = _make_valid_bundle(tmp_path)
    meta = json.loads((bundle_dir / "metadata.json").read_text())
    del meta["run_id"]
    (bundle_dir / "metadata.json").write_text(
        json.dumps(meta), encoding="utf-8"
    )
    loaded = _load(bundle_dir)
    errors, _ = LocalReplayVerifier(loaded).check()
    assert errors  # non-empty: missing run_id is a real finding


def test_check_fails_when_evaluations_empty(tmp_path: Path) -> None:
    bundle_dir = _make_valid_bundle(tmp_path)
    (bundle_dir / "evaluations.json").write_text("[]", encoding="utf-8")
    loaded = _load(bundle_dir)
    errors, _ = LocalReplayVerifier(loaded).check()
    assert errors


# ---------------------------------------------------------------------------
# Replay is read-only
# ---------------------------------------------------------------------------


def test_check_does_not_create_new_files(tmp_path: Path) -> None:
    bundle_dir = _make_valid_bundle(tmp_path)
    files_before = set(bundle_dir.iterdir())
    loaded = _load(bundle_dir)
    LocalReplayVerifier(loaded).check()
    files_after = set(bundle_dir.iterdir())
    assert files_after == files_before


# ---------------------------------------------------------------------------
# CLI replay command (deprecated alias for validate-bundle, D1)
# ---------------------------------------------------------------------------


def test_cli_replay_exits_zero_on_valid_bundle(tmp_path: Path) -> None:
    bundle_dir = _make_valid_bundle(tmp_path)
    result = runner.invoke(app, ["replay", "--bundle", str(bundle_dir)])
    assert result.exit_code == 0, result.output


def test_cli_replay_output_includes_status(tmp_path: Path) -> None:
    bundle_dir = _make_valid_bundle(tmp_path)
    result = runner.invoke(app, ["replay", "--bundle", str(bundle_dir)])
    assert "Status" in result.output


def test_cli_replay_output_includes_pass(tmp_path: Path) -> None:
    bundle_dir = _make_valid_bundle(tmp_path)
    result = runner.invoke(app, ["replay", "--bundle", str(bundle_dir)])
    assert "PASS" in result.output


def test_cli_replay_output_includes_run_id(tmp_path: Path) -> None:
    bundle_dir = _make_valid_bundle(tmp_path)
    result = runner.invoke(app, ["replay", "--bundle", str(bundle_dir)])
    assert "replay-test-001" in result.output


def test_cli_replay_output_includes_trace_events(tmp_path: Path) -> None:
    bundle_dir = _make_valid_bundle(tmp_path)
    result = runner.invoke(app, ["replay", "--bundle", str(bundle_dir)])
    assert "Trace events" in result.output


def test_cli_replay_output_includes_evaluations(tmp_path: Path) -> None:
    bundle_dir = _make_valid_bundle(tmp_path)
    result = runner.invoke(app, ["replay", "--bundle", str(bundle_dir)])
    assert "Evaluations" in result.output


def test_cli_replay_missing_bundle_path_exits_nonzero(
    tmp_path: Path,
) -> None:
    result = runner.invoke(
        app, ["replay", "--bundle", str(tmp_path / "no_such")]
    )
    assert result.exit_code != 0


def test_cli_replay_malformed_bundle_exits_nonzero(tmp_path: Path) -> None:
    bundle_dir = _make_valid_bundle(tmp_path)
    (bundle_dir / "metadata.json").write_text("NOT JSON", encoding="utf-8")
    result = runner.invoke(
        app, ["replay", "--bundle", str(bundle_dir)]
    )
    assert result.exit_code != 0


def test_cli_replay_end_to_end_via_run_then_replay(
    tmp_path: Path,
) -> None:
    """Create a bundle with the run command, then replay it."""
    output_dir = tmp_path / "e2e-bundle"
    run_result = runner.invoke(
        app,
        ["run", "--config", str(_CONFIG), "--output", str(output_dir)],
    )
    assert run_result.exit_code == 0, run_result.output

    replay_result = runner.invoke(
        app, ["replay", "--bundle", str(output_dir)]
    )
    assert replay_result.exit_code == 0, replay_result.output
    assert "PASS" in replay_result.output
    assert "Status" in replay_result.output


# ---------------------------------------------------------------------------
# D1: replay is a true alias -- deprecation notice on stderr only, identical
# stdout and exit code to validate-bundle for the same bundle.
# ---------------------------------------------------------------------------


def test_cli_replay_deprecation_notice_on_stderr_not_stdout(
    tmp_path: Path,
) -> None:
    bundle_dir = _make_valid_bundle(tmp_path)
    result = runner.invoke(app, ["replay", "--bundle", str(bundle_dir)])
    assert "deprecated" in result.stderr.lower()
    assert "deprecated" not in result.stdout.lower()


def test_cli_replay_and_validate_bundle_agree_on_exit_code(
    tmp_path: Path,
) -> None:
    bundle_dir = _make_valid_bundle(tmp_path)
    replay_result = runner.invoke(app, ["replay", "--bundle", str(bundle_dir)])
    validate_result = runner.invoke(
        app, ["validate-bundle", "--bundle", str(bundle_dir)]
    )
    assert replay_result.exit_code == validate_result.exit_code


def test_cli_replay_and_validate_bundle_identical_stdout(
    tmp_path: Path,
) -> None:
    bundle_dir = _make_valid_bundle(tmp_path)
    replay_result = runner.invoke(app, ["replay", "--bundle", str(bundle_dir)])
    validate_result = runner.invoke(
        app, ["validate-bundle", "--bundle", str(bundle_dir)]
    )
    assert replay_result.stdout == validate_result.stdout
