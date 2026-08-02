"""Backward compatibility: a bundle written before the 2026-08-05 taxonomy/
provenance/version changes (docs/adr/0002) must still validate and read.

The preserved incident fixture
(``tests/fixtures/bundles/partial-infra-failure-2026-08-03/``) does *not*
exercise this -- it never had a ``manifest.json`` at all (it's a genuinely
incomplete bundle, not a well-formed old-format one), so its missing-file
assertions are unaffected by anything in this file. This module instead
constructs a synthetic bundle that mimics what ``BundleWriter`` actually
produced before this session: ``manifest.json.bundle_format_version ==
"0.1"``, ``metadata.json.schema_version == "0.1.0"`` (the two independently
hardcoded, never-cross-checked literals docs/adr/0002 found), and none of
``RunSummary.not_evaluated_tasks`` / ``EvaluationResult.evaluated`` /
``ResultBundleMetadata.status``/``git_sha``/``git_dirty``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agentevalops.bundles.reader import BundleReader
from agentevalops.bundles.validator import validate_bundle
from agentevalops.bundles.writer import BundleWriter
from agentevalops.core.schemas import (
    AgentOutput,
    EvaluationResult,
    PolicySpec,
    ResourceLimits,
    RunConfig,
    RunSummary,
    TaskRunResult,
    TerminationReason,
    TraceEvent,
    TraceEventKind,
)
from agentevalops.core.types import BACKEND_LOCAL, AgentId, RunId, TaskId


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_legacy_bundle(tmp_path: Path) -> Path:
    """Write a real, current-format bundle, then downgrade it in place to
    look exactly like a pre-2026-08-05 one -- including regenerating
    checksums against the *edited* content by hand (not by calling
    write_manifest() again, which would silently restore the current
    version string this test is specifically trying to simulate the
    absence of).
    """
    run_id = RunId("legacy-001")
    run_config = RunConfig(
        run_id=run_id,
        agent_id=AgentId("mock-agent-v1"),
        backend_id=BACKEND_LOCAL,
        resource_limits=ResourceLimits(),
    )
    output = AgentOutput(
        success=True, termination_reason=TerminationReason.COMPLETED
    )
    evaluation = EvaluationResult(
        evaluator_id="ev", evaluator_kind="deterministic", score=1.0, passed=True
    )
    summary = RunSummary(
        run_id=run_id,
        task_results=[
            TaskRunResult(
                task_id=TaskId("t-1"), output=output, evaluation=evaluation,
                event_count=1,
            )
        ],
        total_tasks=1,
        passed_tasks=1,
        trace_event_count=1,
    )
    events = [
        TraceEvent(
            run_id=run_id, step_index=0, kind=TraceEventKind.AGENT_TERMINAL,
            payload={"success": True},
        )
    ]

    bundle_dir = tmp_path / "legacy-bundle"
    BundleWriter(bundle_dir).write(
        run_config=run_config,
        summary=summary,
        all_events=events,
        policy_spec=PolicySpec(policy_id="default"),
    )

    # --- downgrade metadata.json: old hardcoded "0.1.0", no new fields ---
    meta_path = bundle_dir / "metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["schema_version"] = "0.1.0"
    for field_name in ("status", "git_sha", "git_dirty"):
        meta.pop(field_name, None)
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    # --- downgrade summary.json: no not_evaluated_tasks ---
    summary_path = bundle_dir / "summary.json"
    summary_data = json.loads(summary_path.read_text(encoding="utf-8"))
    summary_data.pop("not_evaluated_tasks", None)
    summary_path.write_text(
        json.dumps(summary_data, indent=2) + "\n", encoding="utf-8"
    )

    # --- downgrade evaluations.json: no evaluated field ---
    evaluations_path = bundle_dir / "evaluations.json"
    evals = json.loads(evaluations_path.read_text(encoding="utf-8"))
    for e in evals:
        e.pop("evaluated", None)
    evaluations_path.write_text(
        json.dumps(evals, indent=2) + "\n", encoding="utf-8"
    )

    # --- regenerate manifest by hand: old "0.1" version, checksums that
    # actually match the edited content above (not a fresh write_manifest()
    # call, which would put the current version string right back) ---
    manifest_path = bundle_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["bundle_format_version"] = "0.1"
    for filename in manifest["files"]:
        file_path = bundle_dir / filename
        manifest["files"][filename] = {
            "size_bytes": file_path.stat().st_size,
            "sha256": _sha256_file(file_path),
        }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    return bundle_dir


def test_legacy_v01_bundle_still_validates() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        bundle_dir = _write_legacy_bundle(Path(tmp))
        result = validate_bundle(bundle_dir)
        assert result.valid is True, result.errors


def test_legacy_v01_bundle_still_reads() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        bundle_dir = _write_legacy_bundle(Path(tmp))
        loaded = BundleReader(bundle_dir).read()
        assert loaded.metadata["schema_version"] == "0.1.0"
        assert loaded.metadata.get("status") is None
        assert loaded.summary.get("not_evaluated_tasks") is None
        assert loaded.evaluations[0].get("evaluated") is None
