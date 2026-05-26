"""Tests for BundleValidator (WBS 8)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentevalops.bundles.constants import (
    MANIFEST_FILENAME,
    REQUIRED_BUNDLE_FILES,
)
from agentevalops.bundles.validator import (
    BundleValidationResult,
    validate_bundle,
)
from agentevalops.bundles.writer import BundleWriter
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
# Helpers
# ---------------------------------------------------------------------------


def _make_bundle(tmp_path: Path, run_id_str: str = "val-test-001") -> Path:
    run_id = RunId(run_id_str)
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
            payload={"plan": "step"},
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
# Return type
# ---------------------------------------------------------------------------


def test_validate_bundle_returns_result_dataclass(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    result = validate_bundle(bundle_dir)
    assert isinstance(result, BundleValidationResult)


# ---------------------------------------------------------------------------
# Valid bundle
# ---------------------------------------------------------------------------


def test_valid_bundle_passes(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    result = validate_bundle(bundle_dir)
    assert result.valid is True


def test_valid_bundle_no_errors(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    result = validate_bundle(bundle_dir)
    assert result.errors == []


def test_valid_bundle_checked_files_positive(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    result = validate_bundle(bundle_dir)
    assert result.checked_files > 0


def test_valid_bundle_checked_files_equals_required_file_count(
    tmp_path: Path,
) -> None:
    bundle_dir = _make_bundle(tmp_path)
    result = validate_bundle(bundle_dir)
    assert result.checked_files == len(REQUIRED_BUNDLE_FILES)


# ---------------------------------------------------------------------------
# Non-existent / not-directory path
# ---------------------------------------------------------------------------


def test_missing_path_fails(tmp_path: Path) -> None:
    result = validate_bundle(tmp_path / "no_such_dir")
    assert result.valid is False
    assert any("does not exist" in e for e in result.errors)


def test_file_path_fails(tmp_path: Path) -> None:
    f = tmp_path / "a_file.txt"
    f.write_text("hi", encoding="utf-8")
    result = validate_bundle(f)
    assert result.valid is False
    assert any("not a directory" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Missing required content file
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("filename", list(REQUIRED_BUNDLE_FILES))
def test_missing_required_file_fails(tmp_path: Path, filename: str) -> None:
    bundle_dir = _make_bundle(tmp_path)
    (bundle_dir / filename).unlink()
    result = validate_bundle(bundle_dir)
    assert result.valid is False
    assert any(filename in e for e in result.errors)


# ---------------------------------------------------------------------------
# Missing manifest
# ---------------------------------------------------------------------------


def test_missing_manifest_fails(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    (bundle_dir / MANIFEST_FILENAME).unlink()
    result = validate_bundle(bundle_dir)
    assert result.valid is False
    assert any(MANIFEST_FILENAME in e for e in result.errors)


# ---------------------------------------------------------------------------
# Tamper: checksum mismatch
# ---------------------------------------------------------------------------


def test_tampered_summary_fails_checksum(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    # Modify summary.json AFTER manifest was written
    original = json.loads(
        (bundle_dir / "summary.json").read_text(encoding="utf-8")
    )
    original["total_cost_usd"] = 999.99
    (bundle_dir / "summary.json").write_text(
        json.dumps(original), encoding="utf-8"
    )
    result = validate_bundle(bundle_dir)
    assert result.valid is False
    assert any("SHA-256 mismatch" in e for e in result.errors)


def test_tampered_traces_fails_checksum(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    original = (bundle_dir / "traces.jsonl").read_text(encoding="utf-8")
    (bundle_dir / "traces.jsonl").write_text(
        original + '{"injected": true}\n', encoding="utf-8"
    )
    result = validate_bundle(bundle_dir)
    assert result.valid is False
    assert any("SHA-256 mismatch" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Tamper: size mismatch
# ---------------------------------------------------------------------------


def test_size_mismatch_detected(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    # Corrupt the manifest to report a wrong size
    manifest_path = bundle_dir / MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["metadata.json"]["size_bytes"] = 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = validate_bundle(bundle_dir)
    assert result.valid is False
    assert any("Size mismatch" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Invalid JSON in content file
# ---------------------------------------------------------------------------


def test_invalid_json_in_metadata_fails(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    # Update the manifest to reflect new content so size/checksum pass,
    # but break the JSON so the JSON-parse check fires.
    bad = b"{ not valid json }"
    (bundle_dir / "metadata.json").write_bytes(bad)
    # Regenerate manifest so size/sha256 match
    from agentevalops.bundles.manifest import write_manifest

    write_manifest(bundle_dir)
    result = validate_bundle(bundle_dir)
    assert result.valid is False
    assert any("metadata.json" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Invalid traces.jsonl
# ---------------------------------------------------------------------------


def test_invalid_traces_jsonl_fails(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    bad = b"not-json-at-all\n"
    (bundle_dir / "traces.jsonl").write_bytes(bad)
    from agentevalops.bundles.manifest import write_manifest

    write_manifest(bundle_dir)
    result = validate_bundle(bundle_dir)
    assert result.valid is False
    assert any("traces.jsonl" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Unsupported bundle_format_version
# ---------------------------------------------------------------------------


def test_unsupported_format_version_fails(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    manifest_path = bundle_dir / MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["bundle_format_version"] = "99.99"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = validate_bundle(bundle_dir)
    assert result.valid is False
    assert any("Unsupported bundle_format_version" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Malformed manifest
# ---------------------------------------------------------------------------


def test_malformed_manifest_fails(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    (bundle_dir / MANIFEST_FILENAME).write_text("not json", encoding="utf-8")
    result = validate_bundle(bundle_dir)
    assert result.valid is False
    assert any(MANIFEST_FILENAME in e for e in result.errors)


def test_manifest_as_array_fails(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    (bundle_dir / MANIFEST_FILENAME).write_text("[]", encoding="utf-8")
    result = validate_bundle(bundle_dir)
    assert result.valid is False


# ---------------------------------------------------------------------------
# Validation does not mutate bundle
# ---------------------------------------------------------------------------


def test_validation_does_not_mutate_bundle(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    before = {
        f: (bundle_dir / f).read_bytes()
        for f in list(REQUIRED_BUNDLE_FILES) + [MANIFEST_FILENAME]
        if (bundle_dir / f).exists()
    }
    validate_bundle(bundle_dir)
    after = {
        f: (bundle_dir / f).read_bytes()
        for f in list(REQUIRED_BUNDLE_FILES) + [MANIFEST_FILENAME]
        if (bundle_dir / f).exists()
    }
    assert before == after


# ---------------------------------------------------------------------------
# Error messages are useful
# ---------------------------------------------------------------------------


def test_error_messages_mention_filename(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    (bundle_dir / "evaluations.json").unlink()
    result = validate_bundle(bundle_dir)
    assert any("evaluations.json" in e for e in result.errors)
