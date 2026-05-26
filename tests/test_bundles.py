"""Tests for BundleWriter and to_jsonable serializer."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

import pytest

from agentevalops.bundles.constants import MANIFEST_FILENAME
from agentevalops.bundles.serializers import to_jsonable
from agentevalops.bundles.writer import BUNDLE_FILES, BundleWriter
from agentevalops.core.errors import BundleError
from agentevalops.core.schemas import (
    AgentOutput,
    EvaluationResult,
    PolicySpec,
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
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_bundle_inputs() -> (
    tuple[RunConfig, RunSummary, list[TraceEvent], PolicySpec]
):
    run_id = RunId("bundle-test-001")
    run_config = RunConfig(
        run_id=run_id,
        agent_id=AgentId("mock"),
        backend_id=BACKEND_LOCAL,
        resource_limits=ResourceLimits(),
    )
    policy_spec = PolicySpec(policy_id="p-test", max_cost_usd=1.0)

    event = TraceEvent(
        run_id=run_id,
        step_index=0,
        kind=TraceEventKind.AGENT_PLAN,
        payload={"plan": "do the thing"},
    )
    event2 = TraceEvent(
        run_id=run_id,
        step_index=1,
        kind=TraceEventKind.AGENT_TERMINAL,
        payload={"success": True},
    )

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
        policy_id="p-test",
        verdict=Verdict.PASS,
        citations=[],
        notes="all checks passed",
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
    return run_config, summary, [event, event2], policy_spec


def _write_bundle(tmp_path: Path) -> Path:
    run_config, summary, events, policy_spec = _make_bundle_inputs()
    writer = BundleWriter(tmp_path / "bundle")
    return writer.write(
        run_config=run_config,
        summary=summary,
        all_events=events,
        policy_spec=policy_spec,
        config_name="configs/test.yaml",
    )


# ---------------------------------------------------------------------------
# BundleWriter structural tests
# ---------------------------------------------------------------------------


def test_all_seven_bundle_files_created(tmp_path: Path) -> None:
    bundle_dir = _write_bundle(tmp_path)
    for filename in BUNDLE_FILES:
        assert (bundle_dir / filename).exists(), f"Missing: {filename}"


def test_manifest_json_created(tmp_path: Path) -> None:
    bundle_dir = _write_bundle(tmp_path)
    assert (bundle_dir / MANIFEST_FILENAME).exists()


def test_write_returns_output_directory(tmp_path: Path) -> None:
    run_config, summary, events, policy_spec = _make_bundle_inputs()
    expected = tmp_path / "my-bundle"
    result = BundleWriter(expected).write(
        run_config=run_config,
        summary=summary,
        all_events=events,
    )
    assert result == expected


def test_creates_nested_output_directory(tmp_path: Path) -> None:
    deep = tmp_path / "a" / "b" / "c"
    run_config, summary, events, _ = _make_bundle_inputs()
    BundleWriter(deep).write(
        run_config=run_config, summary=summary, all_events=events
    )
    assert deep.exists()


# ---------------------------------------------------------------------------
# traces.jsonl
# ---------------------------------------------------------------------------


def test_traces_jsonl_line_count(tmp_path: Path) -> None:
    bundle_dir = _write_bundle(tmp_path)
    lines = (bundle_dir / "traces.jsonl").read_text().splitlines()
    assert len(lines) == 2  # two events in fixture


def test_traces_jsonl_each_line_is_valid_json(tmp_path: Path) -> None:
    bundle_dir = _write_bundle(tmp_path)
    for line in (bundle_dir / "traces.jsonl").read_text().splitlines():
        obj = json.loads(line)
        assert isinstance(obj, dict)


def test_traces_jsonl_contains_step_index(tmp_path: Path) -> None:
    bundle_dir = _write_bundle(tmp_path)
    first_line = (bundle_dir / "traces.jsonl").read_text().splitlines()[0]
    event = json.loads(first_line)
    assert "step_index" in event
    assert event["step_index"] == 0


# ---------------------------------------------------------------------------
# JSON files: well-formed + key fields
# ---------------------------------------------------------------------------


def test_metadata_json_is_valid_json(tmp_path: Path) -> None:
    bundle_dir = _write_bundle(tmp_path)
    data = json.loads((bundle_dir / "metadata.json").read_text())
    assert data["run_id"] == "bundle-test-001"
    assert data["sealed"] is True
    assert data["schema_version"] == "0.1.0"


def test_config_json_contains_run_config(tmp_path: Path) -> None:
    bundle_dir = _write_bundle(tmp_path)
    data = json.loads((bundle_dir / "config.json").read_text())
    assert data["run_config"]["run_id"] == "bundle-test-001"
    assert data["config_name"] == "configs/test.yaml"


def test_evaluations_json_is_list(tmp_path: Path) -> None:
    bundle_dir = _write_bundle(tmp_path)
    data = json.loads((bundle_dir / "evaluations.json").read_text())
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["passed"] is True


def test_policy_json_contains_verdict(tmp_path: Path) -> None:
    bundle_dir = _write_bundle(tmp_path)
    data = json.loads((bundle_dir / "policy.json").read_text())
    assert data["verdict"] == "pass"
    assert data["checker_id"] == "test-checker"


def test_summary_json_contains_counts(tmp_path: Path) -> None:
    bundle_dir = _write_bundle(tmp_path)
    data = json.loads((bundle_dir / "summary.json").read_text())
    assert data["total_tasks"] == 1
    assert data["passed_tasks"] == 1
    assert data["run_id"] == "bundle-test-001"


def test_policy_json_is_null_when_no_policy_verdict(tmp_path: Path) -> None:
    run_config, summary, events, _ = _make_bundle_inputs()
    summary.policy_verdict = None
    bundle_dir = BundleWriter(tmp_path / "b").write(
        run_config=run_config, summary=summary, all_events=events
    )
    data = json.loads((bundle_dir / "policy.json").read_text())
    assert data is None


# ---------------------------------------------------------------------------
# report.md
# ---------------------------------------------------------------------------


def test_report_md_is_created(tmp_path: Path) -> None:
    bundle_dir = _write_bundle(tmp_path)
    assert (bundle_dir / "report.md").exists()


def test_report_md_contains_run_id(tmp_path: Path) -> None:
    bundle_dir = _write_bundle(tmp_path)
    content = (bundle_dir / "report.md").read_text()
    assert "bundle-test-001" in content


def test_report_md_contains_config_name(tmp_path: Path) -> None:
    bundle_dir = _write_bundle(tmp_path)
    content = (bundle_dir / "report.md").read_text()
    assert "configs/test.yaml" in content


def test_report_md_contains_task_table(tmp_path: Path) -> None:
    bundle_dir = _write_bundle(tmp_path)
    content = (bundle_dir / "report.md").read_text()
    assert "| t-1 |" in content


# ---------------------------------------------------------------------------
# Overwrite protection
# ---------------------------------------------------------------------------


def test_overwrite_false_raises_on_existing_bundle(tmp_path: Path) -> None:
    _write_bundle(tmp_path)  # first write
    with pytest.raises(BundleError, match="already contains"):
        _write_bundle(tmp_path)  # second write without overwrite=True


def test_overwrite_true_replaces_existing_bundle(tmp_path: Path) -> None:
    run_config, summary, events, policy_spec = _make_bundle_inputs()
    first = BundleWriter(tmp_path / "bundle").write(
        run_config=run_config, summary=summary, all_events=events
    )
    # should NOT raise
    BundleWriter(tmp_path / "bundle", overwrite=True).write(
        run_config=run_config, summary=summary, all_events=events
    )
    assert (first / "metadata.json").exists()


def test_write_to_empty_existing_dir_is_allowed(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    run_config, summary, events, _ = _make_bundle_inputs()
    # no exception — dir exists but no bundle files present
    BundleWriter(empty).write(
        run_config=run_config, summary=summary, all_events=events
    )


# ---------------------------------------------------------------------------
# to_jsonable unit tests
# ---------------------------------------------------------------------------


def test_to_jsonable_primitives() -> None:
    assert to_jsonable(None) is None
    assert to_jsonable(True) is True
    assert to_jsonable(42) == 42
    assert to_jsonable(3.14) == pytest.approx(3.14)
    assert to_jsonable("hello") == "hello"


def test_to_jsonable_enum() -> None:
    class Color(Enum):
        RED = "red"

    assert to_jsonable(Color.RED) == "red"


def test_to_jsonable_datetime() -> None:
    dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
    result = to_jsonable(dt)
    assert isinstance(result, str)
    assert "2026-01-01" in result


def test_to_jsonable_path() -> None:
    assert to_jsonable(Path("/tmp/foo")) == "/tmp/foo"


def test_to_jsonable_list() -> None:
    assert to_jsonable([1, "a", None]) == [1, "a", None]


def test_to_jsonable_tuple_becomes_list() -> None:
    result = to_jsonable((1, 2, 3))
    assert result == [1, 2, 3]
    assert isinstance(result, list)


def test_to_jsonable_nested_dict() -> None:
    data = {"a": {"b": 1}}
    assert to_jsonable(data) == {"a": {"b": 1}}


def test_to_jsonable_dataclass() -> None:
    rl = ResourceLimits(max_tokens=500)
    result = to_jsonable(rl)
    assert isinstance(result, dict)
    assert result["max_tokens"] == 500


def test_to_jsonable_nested_dataclass() -> None:
    run_config = RunConfig(
        run_id=RunId("r1"),
        agent_id=AgentId("a"),
        backend_id=BACKEND_LOCAL,
    )
    result = to_jsonable(run_config)
    assert result["run_id"] == "r1"
    assert isinstance(result["resource_limits"], dict)
