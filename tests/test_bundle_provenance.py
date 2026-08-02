"""Model provenance and git provenance (docs/adr/0001 §3 revised,
docs/adr/0002 Axis 4 -- 2026-08-05 session, Tasks 2 and 3).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agentevalops.bundles.constants import BUNDLE_FORMAT_VERSION
from agentevalops.bundles.writer import BundleWriter, _capture_git_provenance
from agentevalops.core.schemas import PolicySpec, ResourceLimits, RunConfig, RunSummary
from agentevalops.core.types import BACKEND_LOCAL, AgentId, RunId

# ---------------------------------------------------------------------------
# Task 2: model-backed bundle names its model
# ---------------------------------------------------------------------------


def test_model_backed_bundle_names_its_model(tmp_path: Path) -> None:
    """config.json must record which model produced the run -- by
    construction, since model lives on RunConfig itself now (no separate
    write-back path to keep in sync).
    """
    from agentevalops.config.loader import load_run_config

    loaded = load_run_config(Path("configs/toy_openai.yaml"))
    assert loaded.run_config.model == "gpt-4.1-mini-2025-04-14"

    bundle_dir = tmp_path / "model-bundle"
    BundleWriter(bundle_dir).write(
        run_config=loaded.run_config,
        summary=RunSummary(run_id=loaded.run_config.run_id),
        all_events=[],
        policy_spec=loaded.policy_spec,
    )

    config_data = json.loads((bundle_dir / "config.json").read_text(encoding="utf-8"))
    assert config_data["run_config"]["model"] == "gpt-4.1-mini-2025-04-14"


def test_mock_backed_bundle_records_no_model(tmp_path: Path) -> None:
    """The mock runner genuinely has no model -- config.json should say so
    explicitly (null), not omit the key, so a reader never has to guess
    whether "no model" means "mock" or "the field wasn't populated."
    """
    run_config = RunConfig(
        run_id=RunId("mock-001"),
        agent_id=AgentId("mock-agent-v1"),
        backend_id=BACKEND_LOCAL,
        resource_limits=ResourceLimits(),
    )
    assert run_config.model is None

    bundle_dir = tmp_path / "mock-bundle"
    BundleWriter(bundle_dir).write(
        run_config=run_config,
        summary=RunSummary(run_id=run_config.run_id),
        all_events=[],
        policy_spec=PolicySpec(policy_id="default"),
    )
    config_data = json.loads((bundle_dir / "config.json").read_text(encoding="utf-8"))
    assert "model" in config_data["run_config"]
    assert config_data["run_config"]["model"] is None


def test_loaded_model_property_matches_run_config_model() -> None:
    """LoadedRunConfig.model is a derived property over run_config.model,
    not a second stored field -- they cannot drift because there is only
    one value.
    """
    from agentevalops.config.loader import load_run_config

    loaded = load_run_config(Path("configs/toy_openai.yaml"))
    assert loaded.model == loaded.run_config.model
    assert loaded.model == "gpt-4.1-mini-2025-04-14"


# ---------------------------------------------------------------------------
# Task 3: git provenance
# ---------------------------------------------------------------------------


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo, check=True, capture_output=True,
    )
    (repo / "a.txt").write_text("1", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo, check=True, capture_output=True,
    )


def test_git_dirty_true_against_a_dirty_tree(tmp_path: Path) -> None:
    """A clean SHA recorded against a dirty working tree would be a false
    provenance claim -- this is the failure mode this field exists to
    catch, so it needs a real test against a real dirty tree, not a mock.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "a.txt").write_text("2 -- uncommitted change", encoding="utf-8")

    sha, dirty = _capture_git_provenance(cwd=repo)

    assert sha != "unavailable"
    assert len(sha) == 40  # a real git SHA1
    assert dirty is True


def test_git_dirty_false_against_a_clean_tree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    sha, dirty = _capture_git_provenance(cwd=repo)

    assert sha != "unavailable"
    assert dirty is False


def test_git_sentinel_when_git_binary_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """git itself unavailable (OSError, e.g. not installed / not on PATH)
    must produce the same sentinel as "not a checkout" -- never raise, and
    never fabricate a value.
    """
    from agentevalops.bundles import writer as writer_module

    def _raise_file_not_found(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("git: command not found")

    monkeypatch.setattr(writer_module.subprocess, "run", _raise_file_not_found)

    sha, dirty = writer_module._capture_git_provenance(cwd=tmp_path)

    assert sha == "unavailable"
    assert dirty is None


def test_git_sha_found_but_status_check_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If `git rev-parse` succeeds but `git status` itself fails (OSError)
    after that, the SHA is still reported -- but dirty must be None, not a
    fabricated True/False, since it was never determined.
    """
    from agentevalops.bundles import writer as writer_module

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    real_run = writer_module.subprocess.run

    def _fail_status_only(cmd: list, **kwargs: object):  # type: ignore[type-arg]
        if cmd[:2] == ["git", "status"]:
            raise OSError("simulated: git status failed")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(writer_module.subprocess, "run", _fail_status_only)

    sha, dirty = writer_module._capture_git_provenance(cwd=repo)

    assert sha != "unavailable"
    assert len(sha) == 40
    assert dirty is None


def test_git_sentinel_outside_a_checkout(tmp_path: Path) -> None:
    """Never omitted, never fabricated: outside a checkout, git_sha is the
    literal sentinel string, and git_dirty is None (no working tree for
    "dirty" to mean anything about) -- not False, which would be a
    fabricated "clean."
    """
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()

    sha, dirty = _capture_git_provenance(cwd=not_a_repo)

    assert sha == "unavailable"
    assert dirty is None


def test_bundle_metadata_carries_real_git_provenance_in_this_repo(
    tmp_path: Path,
) -> None:
    """End-to-end: a bundle written from within this actual repo checkout
    records a real SHA (not the sentinel), proving _capture_git_provenance
    is actually wired into BundleWriter, not just unit-testable in
    isolation.
    """
    run_config = RunConfig(
        run_id=RunId("git-prov-001"),
        agent_id=AgentId("mock-agent-v1"),
        backend_id=BACKEND_LOCAL,
        resource_limits=ResourceLimits(),
    )
    bundle_dir = tmp_path / "git-prov-bundle"
    BundleWriter(bundle_dir).write(
        run_config=run_config,
        summary=RunSummary(run_id=run_config.run_id),
        all_events=[],
        policy_spec=PolicySpec(policy_id="default"),
    )
    metadata = json.loads((bundle_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["git_sha"] != "unavailable"
    assert len(metadata["git_sha"]) == 40
    assert metadata["git_dirty"] in (True, False)


# ---------------------------------------------------------------------------
# Task 3: the "0.1" (manifest) vs "0.1.0" (metadata) split, resolved
# ---------------------------------------------------------------------------


def test_metadata_and_manifest_version_strings_agree(tmp_path: Path) -> None:
    """docs/adr/0002 found metadata.json's schema_version ("0.1.0") and
    manifest.json's bundle_format_version ("0.1") were two independently
    hardcoded literals, never cross-checked, that happened to agree with
    nothing in particular. Both must now come from the single
    BUNDLE_FORMAT_VERSION constant.
    """
    run_config = RunConfig(
        run_id=RunId("version-001"),
        agent_id=AgentId("mock-agent-v1"),
        backend_id=BACKEND_LOCAL,
        resource_limits=ResourceLimits(),
    )
    bundle_dir = tmp_path / "version-bundle"
    BundleWriter(bundle_dir).write(
        run_config=run_config,
        summary=RunSummary(run_id=run_config.run_id),
        all_events=[],
        policy_spec=PolicySpec(policy_id="default"),
    )
    metadata = json.loads((bundle_dir / "metadata.json").read_text(encoding="utf-8"))
    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))

    assert metadata["schema_version"] == manifest["bundle_format_version"]
    assert metadata["schema_version"] == BUNDLE_FORMAT_VERSION
