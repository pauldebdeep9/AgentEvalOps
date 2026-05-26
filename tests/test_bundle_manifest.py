"""Tests for bundle manifest generation (WBS 8)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agentevalops import __version__
from agentevalops.bundles.constants import (
    BUNDLE_FORMAT_VERSION,
    MANIFEST_FILENAME,
    REQUIRED_BUNDLE_FILES,
)
from agentevalops.bundles.manifest import generate_manifest, write_manifest
from agentevalops.bundles.writer import BundleWriter
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
# Helpers
# ---------------------------------------------------------------------------


def _make_bundle(tmp_path: Path) -> Path:
    run_id = RunId("manifest-test-001")
    run_config = RunConfig(
        run_id=run_id,
        agent_id=AgentId("mock"),
        backend_id=BACKEND_LOCAL,
        resource_limits=ResourceLimits(),
    )
    event = TraceEvent(
        run_id=run_id,
        step_index=0,
        kind=TraceEventKind.AGENT_PLAN,
        payload={"plan": "test"},
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
        total_tokens=10,
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
        notes="passed",
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
        total_tokens=10,
        trace_event_count=2,
    )
    policy_spec = PolicySpec(policy_id="p1", max_cost_usd=1.0)
    bundle_dir = tmp_path / "bundle"
    BundleWriter(bundle_dir).write(
        run_config=run_config,
        summary=summary,
        all_events=[event, event2],
        policy_spec=policy_spec,
        config_name="configs/toy_smoke.yaml",
    )
    return bundle_dir


# ---------------------------------------------------------------------------
# manifest.json is written by BundleWriter
# ---------------------------------------------------------------------------


def test_manifest_file_exists_after_writer(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    assert (bundle_dir / MANIFEST_FILENAME).exists()


def test_manifest_is_valid_json(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    text = (bundle_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
    obj = json.loads(text)
    assert isinstance(obj, dict)


# ---------------------------------------------------------------------------
# bundle_format_version
# ---------------------------------------------------------------------------


def test_manifest_has_bundle_format_version(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    manifest = json.loads(
        (bundle_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert "bundle_format_version" in manifest


def test_manifest_bundle_format_version_value(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    manifest = json.loads(
        (bundle_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert manifest["bundle_format_version"] == BUNDLE_FORMAT_VERSION


# ---------------------------------------------------------------------------
# required_files
# ---------------------------------------------------------------------------


def test_manifest_has_required_files_list(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    manifest = json.loads(
        (bundle_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert "required_files" in manifest
    assert isinstance(manifest["required_files"], list)


def test_manifest_required_files_contains_all_content_files(
    tmp_path: Path,
) -> None:
    bundle_dir = _make_bundle(tmp_path)
    manifest = json.loads(
        (bundle_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    for fname in REQUIRED_BUNDLE_FILES:
        assert fname in manifest["required_files"]


# ---------------------------------------------------------------------------
# files section: size_bytes and sha256
# ---------------------------------------------------------------------------


def test_manifest_files_section_present(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    manifest = json.loads(
        (bundle_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert "files" in manifest
    assert isinstance(manifest["files"], dict)


def test_manifest_files_covers_all_required_files(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    manifest = json.loads(
        (bundle_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    for fname in REQUIRED_BUNDLE_FILES:
        assert fname in manifest["files"], f"Missing in manifest.files: {fname}"


def test_manifest_files_each_has_size_bytes(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    manifest = json.loads(
        (bundle_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    for fname, info in manifest["files"].items():
        assert "size_bytes" in info, f"No size_bytes for {fname}"
        assert isinstance(info["size_bytes"], int)


def test_manifest_files_each_has_sha256(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    manifest = json.loads(
        (bundle_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    for fname, info in manifest["files"].items():
        assert "sha256" in info, f"No sha256 for {fname}"
        assert isinstance(info["sha256"], str)
        assert len(info["sha256"]) == 64  # SHA-256 hex = 64 chars


def test_manifest_sha256_values_match_actual_files(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    manifest = json.loads(
        (bundle_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    for fname, info in manifest["files"].items():
        path = bundle_dir / fname
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert info["sha256"] == actual, f"SHA-256 mismatch for {fname}"


def test_manifest_size_bytes_match_actual_files(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    manifest = json.loads(
        (bundle_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    for fname, info in manifest["files"].items():
        path = bundle_dir / fname
        assert info["size_bytes"] == path.stat().st_size


# ---------------------------------------------------------------------------
# manifest does not checksum itself
# ---------------------------------------------------------------------------


def test_manifest_does_not_include_itself_in_files(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    manifest = json.loads(
        (bundle_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert MANIFEST_FILENAME not in manifest["files"]


# ---------------------------------------------------------------------------
# generated_at
# ---------------------------------------------------------------------------


def test_manifest_has_generated_at(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    manifest = json.loads(
        (bundle_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert "generated_at" in manifest
    assert isinstance(manifest["generated_at"], str)
    assert "T" in manifest["generated_at"]  # ISO-8601


# ---------------------------------------------------------------------------
# writer section
# ---------------------------------------------------------------------------


def test_manifest_has_writer_section(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    manifest = json.loads(
        (bundle_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert "writer" in manifest


def test_manifest_writer_name_is_agentevalops(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    manifest = json.loads(
        (bundle_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert manifest["writer"]["name"] == "agentevalops"


def test_manifest_writer_version_present(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    manifest = json.loads(
        (bundle_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert manifest["writer"]["version"] == __version__


# ---------------------------------------------------------------------------
# run section
# ---------------------------------------------------------------------------


def test_manifest_has_run_section(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    manifest = json.loads(
        (bundle_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert "run" in manifest
    assert isinstance(manifest["run"], dict)


def test_manifest_run_contains_run_id(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    manifest = json.loads(
        (bundle_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert manifest["run"].get("run_id") == "manifest-test-001"


def test_manifest_run_contains_config_name(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    manifest = json.loads(
        (bundle_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert manifest["run"].get("config_name") == "configs/toy_smoke.yaml"


# ---------------------------------------------------------------------------
# generate_manifest and write_manifest low-level
# ---------------------------------------------------------------------------


def test_generate_manifest_returns_dict(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    result = generate_manifest(bundle_dir)
    assert isinstance(result, dict)


def test_write_manifest_returns_path(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    # Remove existing manifest so we can call write_manifest directly
    (bundle_dir / MANIFEST_FILENAME).unlink()
    out = write_manifest(bundle_dir)
    assert out == bundle_dir / MANIFEST_FILENAME
    assert out.exists()
