"""Tests for BundleReader."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentevalops.bundles.reader import BundleReader, LoadedBundle
from agentevalops.bundles.writer import BundleWriter
from agentevalops.core.errors import BundleError
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

# ---------------------------------------------------------------------------
# Shared bundle fixture helpers
# ---------------------------------------------------------------------------


def _make_valid_bundle(tmp_path: Path) -> Path:
    """Create a valid result bundle in *tmp_path* and return the bundle dir."""
    run_id = RunId("reader-test-001")
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
            payload={"plan": "step1"},
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
        evaluator_id="test-eval",
        evaluator_kind="deterministic",
        score=1.0,
        passed=True,
        notes="ok",
    )
    policy_verdict = PolicyVerdict(
        checker_id="test-checker",
        policy_id="p1",
        verdict=Verdict.PASS,
        citations=[],
        notes="ok",
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


# ---------------------------------------------------------------------------
# Valid-bundle reads
# ---------------------------------------------------------------------------


def test_reads_valid_bundle_returns_loaded_bundle(tmp_path: Path) -> None:
    bundle_dir = _make_valid_bundle(tmp_path)
    loaded = BundleReader(bundle_dir).read()
    assert isinstance(loaded, LoadedBundle)


def test_loaded_bundle_has_correct_run_id(tmp_path: Path) -> None:
    bundle_dir = _make_valid_bundle(tmp_path)
    loaded = BundleReader(bundle_dir).read()
    assert loaded.metadata["run_id"] == "reader-test-001"


def test_loaded_bundle_path_matches(tmp_path: Path) -> None:
    bundle_dir = _make_valid_bundle(tmp_path)
    loaded = BundleReader(bundle_dir).read()
    assert loaded.bundle_path == bundle_dir


def test_parses_traces_into_list(tmp_path: Path) -> None:
    bundle_dir = _make_valid_bundle(tmp_path)
    loaded = BundleReader(bundle_dir).read()
    assert isinstance(loaded.traces, list)
    assert len(loaded.traces) == 2


def test_each_trace_is_a_dict(tmp_path: Path) -> None:
    bundle_dir = _make_valid_bundle(tmp_path)
    loaded = BundleReader(bundle_dir).read()
    for trace in loaded.traces:
        assert isinstance(trace, dict)


def test_evaluations_is_a_list(tmp_path: Path) -> None:
    bundle_dir = _make_valid_bundle(tmp_path)
    loaded = BundleReader(bundle_dir).read()
    assert isinstance(loaded.evaluations, list)


def test_policy_is_dict_when_present(tmp_path: Path) -> None:
    bundle_dir = _make_valid_bundle(tmp_path)
    loaded = BundleReader(bundle_dir).read()
    assert isinstance(loaded.policy, dict)
    assert "verdict" in loaded.policy


def test_policy_is_none_when_null(tmp_path: Path) -> None:
    bundle_dir = _make_valid_bundle(tmp_path)
    (bundle_dir / "policy.json").write_text("null\n", encoding="utf-8")
    loaded = BundleReader(bundle_dir).read()
    assert loaded.policy is None


def test_summary_is_dict(tmp_path: Path) -> None:
    bundle_dir = _make_valid_bundle(tmp_path)
    loaded = BundleReader(bundle_dir).read()
    assert isinstance(loaded.summary, dict)
    assert "run_id" in loaded.summary


def test_skips_empty_lines_in_traces_jsonl(tmp_path: Path) -> None:
    bundle_dir = _make_valid_bundle(tmp_path)
    original = (bundle_dir / "traces.jsonl").read_text()
    # Insert blank lines between entries
    with_blanks = original.replace("\n", "\n\n")
    (bundle_dir / "traces.jsonl").write_text(with_blanks, encoding="utf-8")
    loaded = BundleReader(bundle_dir).read()
    # Original trace count is preserved despite blank lines
    assert len(loaded.traces) == 2


# ---------------------------------------------------------------------------
# Error: path problems
# ---------------------------------------------------------------------------


def test_fails_on_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(BundleError, match="does not exist"):
        BundleReader(tmp_path / "no_such_dir").read()


def test_fails_on_path_that_is_a_file(tmp_path: Path) -> None:
    a_file = tmp_path / "not_a_dir.txt"
    a_file.write_text("hello", encoding="utf-8")
    with pytest.raises(BundleError, match="not a directory"):
        BundleReader(a_file).read()


# ---------------------------------------------------------------------------
# Error: missing required files
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename",
    [
        "metadata.json",
        "config.json",
        "traces.jsonl",
        "evaluations.json",
        "policy.json",
        "summary.json",
        "report.md",
    ],
)
def test_fails_when_required_file_missing(
    tmp_path: Path, filename: str
) -> None:
    bundle_dir = _make_valid_bundle(tmp_path)
    (bundle_dir / filename).unlink()
    with pytest.raises(BundleError, match="missing required files"):
        BundleReader(bundle_dir).read()


def test_report_md_required_to_exist(tmp_path: Path) -> None:
    bundle_dir = _make_valid_bundle(tmp_path)
    (bundle_dir / "report.md").unlink()
    with pytest.raises(BundleError):
        BundleReader(bundle_dir).read()


# ---------------------------------------------------------------------------
# Error: malformed content
# ---------------------------------------------------------------------------


def test_fails_when_metadata_json_invalid(tmp_path: Path) -> None:
    bundle_dir = _make_valid_bundle(tmp_path)
    (bundle_dir / "metadata.json").write_text(
        "{ not valid json }", encoding="utf-8"
    )
    with pytest.raises(BundleError, match="Invalid JSON"):
        BundleReader(bundle_dir).read()


def test_fails_when_metadata_json_is_array(tmp_path: Path) -> None:
    bundle_dir = _make_valid_bundle(tmp_path)
    (bundle_dir / "metadata.json").write_text("[]", encoding="utf-8")
    with pytest.raises(BundleError, match="must contain a JSON object"):
        BundleReader(bundle_dir).read()


def test_fails_when_traces_jsonl_has_invalid_json_line(
    tmp_path: Path,
) -> None:
    bundle_dir = _make_valid_bundle(tmp_path)
    (bundle_dir / "traces.jsonl").write_text(
        '{"step_index": 0}\nnot-valid-json\n', encoding="utf-8"
    )
    with pytest.raises(BundleError, match="Invalid JSON on line"):
        BundleReader(bundle_dir).read()


def test_fails_when_traces_jsonl_line_is_not_object(tmp_path: Path) -> None:
    bundle_dir = _make_valid_bundle(tmp_path)
    (bundle_dir / "traces.jsonl").write_text(
        "[1, 2, 3]\n", encoding="utf-8"
    )
    with pytest.raises(BundleError, match="Expected a JSON object"):
        BundleReader(bundle_dir).read()


def test_fails_when_evaluations_json_is_not_list(tmp_path: Path) -> None:
    bundle_dir = _make_valid_bundle(tmp_path)
    (bundle_dir / "evaluations.json").write_text(
        '{"oops": true}', encoding="utf-8"
    )
    with pytest.raises(BundleError, match="must contain a JSON array"):
        BundleReader(bundle_dir).read()


def test_fails_when_summary_json_invalid(tmp_path: Path) -> None:
    bundle_dir = _make_valid_bundle(tmp_path)
    (bundle_dir / "summary.json").write_text("INVALID", encoding="utf-8")
    with pytest.raises(BundleError, match="Invalid JSON"):
        BundleReader(bundle_dir).read()
