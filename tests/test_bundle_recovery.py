"""Partial-write / interrupted-bundle recovery tests (ROADMAP W4 exit criterion).

The ``BundleWriter`` writes the seven content files first and the
``manifest.json`` seal *last* (see ``BundleWriter.write``).  If the process is
interrupted after the content files but before the seal, the bundle is
*unsealed* — every content file is present and individually valid, only the
manifest is missing.  Such a bundle must still be readable so that a
partially-completed run is not lost.

These tests reconstruct that exact interrupted state (a full bundle with
``manifest.json`` removed) and assert:

* a reader in recovery mode (``strict=False``) loads it and the pre-seal
  content is intact; and
* the default strict reader refuses it with a clear, actionable message
  (so a missing seal is never silently mistaken for a complete bundle).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentevalops.bundles.constants import MANIFEST_FILENAME, REQUIRED_BUNDLE_FILES
from agentevalops.bundles.reader import BundleReader
from agentevalops.bundles.writer import BundleWriter
from agentevalops.core.errors import BundleError
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

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


def _write_full_bundle(tmp_path: Path) -> Path:
    """Write a complete, sealed bundle and return its directory."""
    run_id = RunId("recovery-test-001")
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


def _make_interrupted_bundle(tmp_path: Path) -> Path:
    """Return a bundle interrupted before the manifest seal was written."""
    bundle_dir = _write_full_bundle(tmp_path)
    # Simulate a crash between the last content file and write_manifest().
    (bundle_dir / MANIFEST_FILENAME).unlink()
    return bundle_dir


# ---------------------------------------------------------------------------
# The interrupted bundle is still readable in recovery mode
# ---------------------------------------------------------------------------


def test_interrupted_bundle_has_all_content_files(tmp_path: Path) -> None:
    bundle_dir = _make_interrupted_bundle(tmp_path)
    for filename in REQUIRED_BUNDLE_FILES:
        assert (bundle_dir / filename).exists()
    assert not (bundle_dir / MANIFEST_FILENAME).exists()


def test_interrupted_bundle_is_readable_in_recovery_mode(
    tmp_path: Path,
) -> None:
    bundle_dir = _make_interrupted_bundle(tmp_path)
    loaded = BundleReader(bundle_dir, strict=False).read()
    # No seal was recorded, so the manifest is absent...
    assert loaded.manifest is None
    # ...but all the streamed content survived intact.
    assert loaded.metadata["run_id"] == "recovery-test-001"
    assert len(loaded.traces) == 2
    assert isinstance(loaded.summary, dict)
    assert loaded.policy is not None
    assert loaded.policy["verdict"] == Verdict.PASS.value


def test_interrupted_bundle_content_files_are_individually_valid(
    tmp_path: Path,
) -> None:
    # Each content file written before the interruption is complete JSON,
    # i.e. the interruption did not leave a torn/partial content file.
    bundle_dir = _make_interrupted_bundle(tmp_path)
    loaded = BundleReader(bundle_dir, strict=False).read()
    assert loaded.evaluations  # non-empty list parsed cleanly
    assert loaded.config["run_config"]["run_id"] == "recovery-test-001"


# ---------------------------------------------------------------------------
# The default (strict) reader refuses an unsealed bundle loudly
# ---------------------------------------------------------------------------


def test_strict_reader_rejects_interrupted_bundle(tmp_path: Path) -> None:
    bundle_dir = _make_interrupted_bundle(tmp_path)
    with pytest.raises(BundleError, match=MANIFEST_FILENAME):
        BundleReader(bundle_dir).read()
