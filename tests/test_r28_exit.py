"""ROADMAP R28's exit criteria for session (b), one test each (docs/adr/0007;
the (a) criteria live in test_catalogue.py, test_control_citations.py,
test_imda_alignment_doc.py, test_coverage.py and test_evidence_report.py).

Seen failing first against the last format 0.5 code (`ba52628`).
"""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest
import test_enum_construction_coverage as enum_coverage
from reference_bundles import ReferenceBundles

from agentevalops.bundles import validator as validator_module
from agentevalops.bundles.constants import (
    BUNDLE_FORMAT_VERSION,
    MANIFEST_FILENAME,
    required_files_for_version,
)
from agentevalops.bundles.manifest import write_manifest
from agentevalops.bundles.provenance import package_sha256
from agentevalops.bundles.validator import validate_bundle
from agentevalops.catalogue import load_catalogue
from agentevalops.core.schemas import CategoryPassK, RunSummary, TraceEventKind
from agentevalops.core.types import RunId
from agentevalops.reports.evidence import render_evidence_report

ROOT = Path(__file__).resolve().parents[1]
LEGACY_0_5 = ROOT / "tests/fixtures/bundles/seed-narrow-0.5"


@pytest.fixture(scope="module")
def bundles(reference_bundles: ReferenceBundles) -> dict[str, Path]:
    """The shared toy and seed bundles; read only (tests copy before they
    edit)."""
    return {kind: reference_bundles[kind].path for kind in ("toy", "seed")}


def _copy(bundle: Path, tmp_path: Path) -> Path:
    copy = tmp_path / bundle.name
    shutil.copytree(bundle, copy)
    return copy


def _lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line]


def test_the_format_is_0_6() -> None:
    assert BUNDLE_FORMAT_VERSION == "0.6"


# (b) 0.1-0.5 bundles still validate, and a toy 0.6 bundle carries a plan
# with declared: false.


@pytest.mark.parametrize("version", ["0.3", "0.4"])
def test_a_bundle_cut_down_to_an_older_format_still_validates(
    bundles: dict[str, Path], tmp_path: Path, version: str
) -> None:
    """0.1 and 0.2 are test_bundle_legacy_compat.py's; 0.5 is the fixture's."""
    copy = _copy(bundles["toy"], tmp_path)
    keep = set(required_files_for_version(version)) | {MANIFEST_FILENAME}
    for path in copy.iterdir():
        if path.name not in keep:
            path.unlink()
    meta = json.loads((copy / "metadata.json").read_text("utf-8"))
    meta.update(schema_version=version, test_plan_sha256=None)
    (copy / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    manifest = json.loads((copy / MANIFEST_FILENAME).read_text("utf-8"))
    import hashlib

    manifest["bundle_format_version"] = version
    manifest["required_files"] = sorted(required_files_for_version(version))
    manifest["files"] = {
        name: {
            "size_bytes": (copy / name).stat().st_size,
            "sha256": hashlib.sha256((copy / name).read_bytes()).hexdigest(),
        }
        for name in sorted(required_files_for_version(version))
    }
    (copy / MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")
    result = validate_bundle(copy)
    assert result.valid, result.errors
    assert result.bundle_format_version == version


def test_the_0_5_fixture_still_validates() -> None:
    result = validate_bundle(LEGACY_0_5)
    assert result.valid, result.errors
    assert result.bundle_format_version == "0.5"


def test_a_toy_0_6_bundle_carries_a_plan_that_declared_nothing(
    bundles: dict[str, Path],
) -> None:
    result = validate_bundle(bundles["toy"])
    assert result.valid, result.errors
    assert result.bundle_format_version == "0.6"
    plan = json.loads((bundles["toy"] / "test_plan.json").read_text("utf-8"))
    assert plan["declared"] is False and plan["plan_version"] == "2"


# (b) A 0.6 bundle missing controls.json fails (fail-first); likewise the
# other two files 0.6 requires.


@pytest.mark.parametrize(
    "name", ["controls.json", "evidence_report.md", "test_plan.json"]
)
def test_a_0_6_bundle_missing_a_new_required_file_fails(
    bundles: dict[str, Path], tmp_path: Path, name: str
) -> None:
    copy = _copy(bundles["seed"], tmp_path)
    (copy / name).unlink()
    write_manifest(copy)  # a consistent manifest: only the missing file is wrong
    result = validate_bundle(copy)
    assert not result.valid
    assert f"Missing required file: {name}" in result.errors


# (b) Every task run's stream ends with one evaluator.score event per
# evaluation result and the cross-file sums hold (fail-first).


def test_every_task_runs_stream_ends_with_one_score_event_per_evaluation(
    bundles: dict[str, Path],
) -> None:
    bundle = bundles["seed"]
    streams: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for event in _lines(bundle / "traces.jsonl"):
        streams[(event["task_id"], event["repetition"])].append(event)
    evaluations: dict[tuple[str, int], list[str]] = defaultdict(list)
    for entry in json.loads((bundle / "evaluations.json").read_text("utf-8")):
        evaluations[(entry["task_id"], entry["repetition"])].append(
            entry["evaluator_id"]
        )
    assert set(streams) == set(evaluations)
    for run, ids in evaluations.items():
        tail = streams[run][-len(ids) :]
        assert [e["kind"] for e in tail] == ["evaluator.score"] * len(ids), run
        assert [e["payload"]["evaluator_id"] for e in tail] == ids, run
        assert [e["step_index"] for e in streams[run]] == list(range(len(streams[run])))
    assert validate_bundle(bundle).valid


def test_a_bundle_with_one_score_event_removed_fails(
    bundles: dict[str, Path], tmp_path: Path
) -> None:
    copy = _copy(bundles["seed"], tmp_path)
    lines = (copy / "traces.jsonl").read_text("utf-8").splitlines(keepends=True)
    last = max(i for i, line in enumerate(lines) if '"evaluator.score"' in line)
    (copy / "traces.jsonl").write_text("".join(lines[:last] + lines[last + 1 :]))
    write_manifest(copy)
    result = validate_bundle(copy)
    assert not result.valid
    assert any("evaluator.score event count mismatch" in e for e in result.errors)
    assert any("trace event count mismatch" in e for e in result.errors)


# (b) POLICY_VERDICT is gone, and the allowlist holds exactly
# AGENT_OBSERVATION: EVALUATOR_SCORE has left it.


def test_policy_verdict_is_gone_and_evaluator_score_left_the_allowlist() -> None:
    assert "POLICY_VERDICT" not in TraceEventKind.__members__
    assert enum_coverage._ALLOWLIST[TraceEventKind] == frozenset({"AGENT_OBSERVATION"})


# (b) package_sha256 is identical for LF and CRLF copies and changes on a
# one-byte data edit (fail-first; in full in test_package_provenance.py).


def test_package_sha256_ignores_line_endings_and_sees_one_byte(tmp_path: Path) -> None:
    lf, crlf = tmp_path / "lf", tmp_path / "crlf"
    for root, newline in ((lf, b"\n"), (crlf, b"\r\n")):
        (root / "data").mkdir(parents=True)
        (root / "data/pack.yaml").write_bytes(b"a: 1" + newline + b"b: 2" + newline)
    assert package_sha256(lf) == package_sha256(crlf)
    (lf / "data/pack.yaml").write_bytes(b"a: 1\nb: 3\n")
    assert package_sha256(lf) != package_sha256(crlf)


# (b) The drift check is determinate without reading the checkout's state.


def test_the_drift_check_of_a_0_6_bundle_never_asks_git(
    bundles: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_git(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a 0.6 bundle's drift check must not read git")

    monkeypatch.setattr(validator_module, "_capture_git_provenance", no_git)
    result = validate_bundle(bundles["seed"])
    assert result.valid
    assert not any("drift" in w.lower() for w in result.warnings)
    monkeypatch.setattr(validator_module, "_current_package_sha256", lambda: "0" * 64)
    result = validate_bundle(bundles["seed"])
    assert result.valid
    assert any(w.startswith("Code drift: ") for w in result.warnings)


# (b) The written evidence report renders byte-identical twice from one
# bundle.


def test_the_evidence_report_renders_again_from_the_bundle_byte_for_byte(
    bundles: dict[str, Path],
) -> None:
    """From controls.json, test_plan.json, the shipped catalogue, and the
    run's own created_at and runner_id (decision of 2026-11-02, Part 3)."""
    from agentevalops.plans import plan_from_document

    bundle = bundles["seed"]

    def read(name: str) -> Any:
        return json.loads((bundle / name).read_text("utf-8"))

    summary_doc = read("summary.json")
    summary = RunSummary(
        run_id=RunId(summary_doc["run_id"]),
        pass_k_by_category=[
            CategoryPassK(**c) for c in summary_doc["pass_k_by_category"]
        ],
    )
    again = render_evidence_report(
        load_catalogue(),
        plan_from_document(read("test_plan.json")),
        read("controls.json"),
        summary,
        runner_id=read("config.json")["runner_id"],
        created_at=read("metadata.json")["created_at"],
    )
    written = (bundle / "evidence_report.md").read_bytes()
    assert (again + "\n").encode("utf-8") == written
    assert read("metadata.json")["created_at"] in written.decode("utf-8")
