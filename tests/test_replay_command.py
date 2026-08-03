"""Tests for replay_command.txt generation and its version-aware required-
file status (docs/adr/0003-replay-semantics.md, D3/D4/D5).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentevalops.bundles.constants import (
    BUNDLE_FORMAT_VERSION,
    MANIFEST_FILENAME,
    REPLAY_COMMAND_FILENAME,
    REQUIRED_BUNDLE_FILES,
    required_files_for_version,
)
from agentevalops.bundles.reader import BundleReader
from agentevalops.bundles.validator import validate_bundle
from agentevalops.bundles.writer import BundleWriter
from agentevalops.core.schemas import (
    AgentOutput,
    EvaluationResult,
    PolicySpec,
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
# Helpers
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_bundle(
    tmp_path: Path,
    *,
    run_id_str: str = "replay-cmd-001",
    model: str | None = None,
    deployment: str | None = None,
    api_version: str | None = None,
    dirname: str = "bundle",
) -> Path:
    run_id = RunId(run_id_str)
    run_config = RunConfig(
        run_id=run_id,
        agent_id=AgentId("mock"),
        backend_id=BACKEND_LOCAL,
        resource_limits=ResourceLimits(),
        model=model,
        deployment=deployment,
        api_version=api_version,
    )
    events = [
        TraceEvent(
            run_id=run_id, step_index=0, kind=TraceEventKind.AGENT_PLAN,
            payload={},
        ),
        TraceEvent(
            run_id=run_id, step_index=1, kind=TraceEventKind.AGENT_TERMINAL,
            payload={"success": True},
        ),
    ]
    output = AgentOutput(
        success=True, termination_reason=TerminationReason.COMPLETED,
        total_tokens=5,
    )
    evaluation = EvaluationResult(
        evaluator_id="ev", evaluator_kind="deterministic", score=1.0,
        passed=True, notes="ok",
    )
    policy_verdict = PolicyVerdict(
        checker_id="basic-v1", policy_id="p1", verdict=Verdict.PASS,
        citations=[], notes="ok",
    )
    task_result = TaskRunResult(
        task_id=TaskId("t-1"), output=output, evaluation=evaluation,
        event_count=len(events),
    )
    summary = RunSummary(
        run_id=run_id, task_results=[task_result], policy_verdict=policy_verdict,
        total_tasks=1, passed_tasks=1, total_cost_usd=0.0, total_tokens=5,
        trace_event_count=len(events),
    )
    bundle_dir = tmp_path / dirname
    BundleWriter(bundle_dir).write(
        run_config=run_config,
        summary=summary,
        all_events=events,
        policy_spec=PolicySpec(policy_id="default"),
        config_name="configs/toy_smoke.yaml",
    )
    return bundle_dir


# ---------------------------------------------------------------------------
# D3: replay_command.txt is required starting at "0.4" only
# ---------------------------------------------------------------------------


def test_replay_command_txt_written_by_bundle_writer(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    assert (bundle_dir / REPLAY_COMMAND_FILENAME).exists()


def test_fresh_bundle_is_0_4_and_requires_replay_command(
    tmp_path: Path,
) -> None:
    bundle_dir = _make_bundle(tmp_path)
    manifest = json.loads(
        (bundle_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert manifest["bundle_format_version"] == "0.4"
    assert REPLAY_COMMAND_FILENAME in manifest["required_files"]
    assert REPLAY_COMMAND_FILENAME in manifest["files"]


def test_0_3_format_bundle_still_validates_without_replay_command_txt(
    tmp_path: Path,
) -> None:
    """The fail-first case D3 exists for: a bundle that genuinely predates
    replay_command.txt (declares "0.3") must not be penalized for lacking
    a file it was never obligated to have.
    """
    bundle_dir = _make_bundle(tmp_path, dirname="v03-bundle")

    # Downgrade in place: remove replay_command.txt, declare "0.3", and
    # regenerate manifest entries/checksums to match -- same discipline as
    # tests/test_bundle_legacy_compat.py's _write_legacy_bundle.
    (bundle_dir / REPLAY_COMMAND_FILENAME).unlink()

    manifest_path = bundle_dir / MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["bundle_format_version"] = "0.3"
    manifest["required_files"] = sorted(REQUIRED_BUNDLE_FILES)
    manifest["files"].pop(REPLAY_COMMAND_FILENAME, None)
    for filename in manifest["files"]:
        file_path = bundle_dir / filename
        manifest["files"][filename] = {
            "size_bytes": file_path.stat().st_size,
            "sha256": _sha256_file(file_path),
        }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    result = validate_bundle(bundle_dir)
    assert result.valid is True, result.errors
    assert not any(REPLAY_COMMAND_FILENAME in e for e in result.errors)


def test_0_4_format_bundle_fails_without_replay_command_txt(
    tmp_path: Path,
) -> None:
    """The mirror-image fail-first case: a bundle that genuinely declares
    "0.4" but is missing replay_command.txt (e.g. deleted after the fact)
    must fail -- the same way a "0.4" bundle missing report.md would.
    """
    bundle_dir = _make_bundle(tmp_path, dirname="v04-missing-bundle")
    manifest = json.loads(
        (bundle_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert manifest["bundle_format_version"] == BUNDLE_FORMAT_VERSION

    (bundle_dir / REPLAY_COMMAND_FILENAME).unlink()

    result = validate_bundle(bundle_dir)
    assert result.valid is False
    assert any(
        f"Missing required file: {REPLAY_COMMAND_FILENAME}" == e
        for e in result.errors
    )


def test_required_files_for_version_pre_0_4_is_flat_set() -> None:
    assert required_files_for_version("0.3") == REQUIRED_BUNDLE_FILES
    assert required_files_for_version("0.2") == REQUIRED_BUNDLE_FILES
    assert required_files_for_version("0.1") == REQUIRED_BUNDLE_FILES
    assert required_files_for_version(None) == REQUIRED_BUNDLE_FILES
    assert required_files_for_version("unknown-future-version") == (
        REQUIRED_BUNDLE_FILES
    )


def test_required_files_for_version_current_includes_replay_command() -> None:
    current = required_files_for_version(BUNDLE_FORMAT_VERSION)
    assert REPLAY_COMMAND_FILENAME in current
    assert set(REQUIRED_BUNDLE_FILES).issubset(set(current))


# ---------------------------------------------------------------------------
# D4: replay_command.txt content -- provenance fields, never omitted
# ---------------------------------------------------------------------------


def test_replay_command_contains_run_id(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path, run_id_str="rc-run-id-001")
    text = (bundle_dir / REPLAY_COMMAND_FILENAME).read_text(encoding="utf-8")
    assert "run_id: rc-run-id-001" in text


def test_replay_command_contains_reconstructable_invocation(
    tmp_path: Path,
) -> None:
    bundle_dir = _make_bundle(tmp_path)
    text = (bundle_dir / REPLAY_COMMAND_FILENAME).read_text(encoding="utf-8")
    assert "agentevalops run --config configs/toy_smoke.yaml" in text
    assert "config.json" in text  # points at it rather than duplicating


def test_replay_command_contains_platform_version_coarseness_note(
    tmp_path: Path,
) -> None:
    bundle_dir = _make_bundle(tmp_path)
    text = (bundle_dir / REPLAY_COMMAND_FILENAME).read_text(encoding="utf-8")
    assert "platform_version:" in text
    assert "too coarse" in text


def test_replay_command_contains_model_for_model_backed_run(
    tmp_path: Path,
) -> None:
    bundle_dir = _make_bundle(tmp_path, model="gpt-4.1-mini-2025-04-14")
    text = (bundle_dir / REPLAY_COMMAND_FILENAME).read_text(encoding="utf-8")
    assert "model: gpt-4.1-mini-2025-04-14" in text


def test_replay_command_omits_model_for_mock_run(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    text = (bundle_dir / REPLAY_COMMAND_FILENAME).read_text(encoding="utf-8")
    assert "model:" not in text


def test_replay_command_git_sha_never_omitted_outside_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No git checkout -> git_sha renders the literal "unavailable"
    sentinel, never a blank or omitted field.
    """
    from agentevalops.bundles import writer as writer_module

    monkeypatch.setattr(
        writer_module,
        "_capture_git_provenance",
        lambda cwd=None: ("unavailable", None),
    )
    bundle_dir = _make_bundle(tmp_path, dirname="no-checkout-bundle")
    text = (bundle_dir / REPLAY_COMMAND_FILENAME).read_text(encoding="utf-8")
    assert "git_sha: unavailable" in text
    assert "git_dirty: unknown" in text


def test_replay_command_git_dirty_true_never_hidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dirty working tree at write time must render git_dirty: True --
    never silently shown as clean.
    """
    from agentevalops.bundles import writer as writer_module

    monkeypatch.setattr(
        writer_module,
        "_capture_git_provenance",
        lambda cwd=None: ("abc123def456", True),
    )
    bundle_dir = _make_bundle(tmp_path, dirname="dirty-tree-bundle")
    text = (bundle_dir / REPLAY_COMMAND_FILENAME).read_text(encoding="utf-8")
    assert "git_sha: abc123def456" in text
    assert "git_dirty: True" in text


def test_replay_command_azure_resource_present_for_azure_openai_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://my-resource.openai.azure.com")
    bundle_dir = _make_bundle(
        tmp_path,
        model="gpt-4.1-mini-2025-04-14",
        deployment="my-deployment",
        api_version="2024-10-21",
        dirname="azure-bundle",
    )
    text = (bundle_dir / REPLAY_COMMAND_FILENAME).read_text(encoding="utf-8")
    assert "azure_resource: my-resource" in text
    # Never the full URL -- a bundle is portable/shareable (row 13b D5).
    assert "https://" not in text
    assert "openai.azure.com" not in text


def test_replay_command_azure_resource_unavailable_when_endpoint_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset AZURE_OPENAI_ENDPOINT on an azure-openai run -> azure_resource
    renders the literal "unavailable" sentinel, never omitted.
    """
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    bundle_dir = _make_bundle(
        tmp_path,
        model="gpt-4.1-mini-2025-04-14",
        deployment="my-deployment",
        api_version="2024-10-21",
        dirname="azure-no-endpoint-bundle",
    )
    text = (bundle_dir / REPLAY_COMMAND_FILENAME).read_text(encoding="utf-8")
    assert "azure_resource: unavailable" in text


def test_replay_command_omits_azure_resource_for_non_azure_run(
    tmp_path: Path,
) -> None:
    bundle_dir = _make_bundle(tmp_path)
    text = (bundle_dir / REPLAY_COMMAND_FILENAME).read_text(encoding="utf-8")
    assert "azure_resource" not in text


def test_replay_command_git_sha_matches_metadata_json(tmp_path: Path) -> None:
    """metadata.json and replay_command.txt must agree -- both come from
    the same single git-provenance capture (D4).
    """
    bundle_dir = _make_bundle(tmp_path)
    metadata = json.loads(
        (bundle_dir / "metadata.json").read_text(encoding="utf-8")
    )
    text = (bundle_dir / REPLAY_COMMAND_FILENAME).read_text(encoding="utf-8")
    assert f"git_sha: {metadata['git_sha']}" in text


# ---------------------------------------------------------------------------
# Manifest checksums the new file
# ---------------------------------------------------------------------------


def test_manifest_checksums_replay_command_txt(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    manifest = json.loads(
        (bundle_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    entry = manifest["files"][REPLAY_COMMAND_FILENAME]
    actual_sha = _sha256_file(bundle_dir / REPLAY_COMMAND_FILENAME)
    assert entry["sha256"] == actual_sha
    assert entry["size_bytes"] == (
        bundle_dir / REPLAY_COMMAND_FILENAME
    ).stat().st_size


def test_tampered_replay_command_txt_fails_checksum(tmp_path: Path) -> None:
    bundle_dir = _make_bundle(tmp_path)
    path = bundle_dir / REPLAY_COMMAND_FILENAME
    path.write_text(path.read_text(encoding="utf-8") + "tampered\n")
    result = validate_bundle(bundle_dir)
    assert result.valid is False
    assert any("SHA-256 mismatch" in e for e in result.errors)


# ---------------------------------------------------------------------------
# D5: code-drift is a warning, never an error, never changes the exit code
# ---------------------------------------------------------------------------


def test_drift_warning_present_when_git_sha_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle_dir = _make_bundle(tmp_path)
    # Rewrite metadata.json to claim a different, plausible commit SHA and
    # a clean tree, so the "determinate" branch is reached and a genuine
    # mismatch (not just an inconclusive one) is what gets exercised.
    meta_path = bundle_dir / "metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["git_sha"] = "0" * 40
    meta["git_dirty"] = False
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    # Recompute checksum so this isn't reported as tampering instead.
    manifest_path = bundle_dir / MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["metadata.json"] = {
        "size_bytes": meta_path.stat().st_size,
        "sha256": _sha256_file(meta_path),
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    from agentevalops.bundles import validator as validator_module

    monkeypatch.setattr(
        validator_module,
        "_capture_git_provenance",
        lambda cwd=None: ("1" * 40, False),
    )

    result = validate_bundle(bundle_dir)
    assert result.valid is True  # drift never fails validation
    assert any("Code drift" in w for w in result.warnings)
    assert not any("Code drift" in e for e in result.errors)


def test_no_drift_warning_when_sha_matches_and_tree_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The genuinely-clean case: same git_sha, both trees clean -- no
    drift warning at all, not even an "inconclusive" one.
    """
    bundle_dir = _make_bundle(tmp_path, dirname="clean-match-bundle")
    meta_path = bundle_dir / "metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["git_sha"] = "a" * 40
    meta["git_dirty"] = False
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    manifest_path = bundle_dir / MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["metadata.json"] = {
        "size_bytes": meta_path.stat().st_size,
        "sha256": _sha256_file(meta_path),
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    from agentevalops.bundles import validator as validator_module

    monkeypatch.setattr(
        validator_module,
        "_capture_git_provenance",
        lambda cwd=None: ("a" * 40, False),
    )

    result = validate_bundle(bundle_dir)
    assert result.valid is True
    assert not any("drift" in w.lower() for w in result.warnings)


def test_drift_warning_never_appears_in_errors_or_changes_validity(
    tmp_path: Path,
) -> None:
    """End-to-end (no monkeypatching): whatever the real drift outcome is
    for a bundle written from this actual checkout, it must never surface
    as an error or flip `valid`.
    """
    bundle_dir = _make_bundle(tmp_path)
    result = validate_bundle(bundle_dir)
    assert result.valid is True
    assert not any("Code drift" in e or "Code-drift" in e for e in result.errors)


def test_drift_warning_does_not_change_cli_exit_code(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from agentevalops.cli import app

    bundle_dir = _make_bundle(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app, ["validate-bundle", "--bundle", str(bundle_dir)]
    )
    # Whatever drift warning fires (this repo's tree is very likely dirty
    # during development), the exit code reflects structural validity only.
    assert result.exit_code == 0


def test_no_drift_check_for_bundles_predating_git_sha(tmp_path: Path) -> None:
    """A "0.1"-era bundle has no git_sha field at all in metadata.json --
    the drift check must be silently skipped, not produce a spurious
    finding about a field that was never captured.
    """
    bundle_dir = _make_bundle(tmp_path, dirname="pre-git-sha-bundle")
    meta_path = bundle_dir / "metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    del meta["git_sha"]
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    manifest_path = bundle_dir / MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["metadata.json"] = {
        "size_bytes": meta_path.stat().st_size,
        "sha256": _sha256_file(meta_path),
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = validate_bundle(bundle_dir)
    assert not any(
        "drift" in w.lower() for w in result.warnings
    )


# ---------------------------------------------------------------------------
# BundleReader also honors the version-aware required set (D3, reader.py)
# ---------------------------------------------------------------------------


def test_reader_does_not_require_replay_command_for_0_3_bundle(
    tmp_path: Path,
) -> None:
    bundle_dir = _make_bundle(tmp_path, dirname="reader-v03-bundle")
    (bundle_dir / REPLAY_COMMAND_FILENAME).unlink()
    manifest_path = bundle_dir / MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["bundle_format_version"] = "0.3"
    manifest["files"].pop(REPLAY_COMMAND_FILENAME, None)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    loaded = BundleReader(bundle_dir).read()
    assert loaded.metadata["run_id"] == "replay-cmd-001"
