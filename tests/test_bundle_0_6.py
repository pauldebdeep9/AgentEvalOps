"""Bundle format 0.6, the evidence pack (docs/adr/0007, with its 2026-11-02
amendments; ROADMAP R28 (b2)): the validator's new cross-file checks, each
fail-first; a broken file as a finding, never a crash; the CLI's evidence
inputs; the wording guard and the gate on real bundles; the undeclared
run's two reports; the legacy points a 0.5 bundle relies on.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

import fast_yaml
import pytest
from typer.testing import CliRunner

from agentevalops.bundles.constants import (
    MANIFEST_FILENAME,
    MANIFEST_SIGNATURE_FILENAME,
    required_files_for_version,
)
from agentevalops.bundles.manifest import write_manifest
from agentevalops.bundles.validator import validate_bundle
from agentevalops.catalogue import BUNDLE_0_6_FILES
from agentevalops.cli import app
from agentevalops.core.schemas import (
    AgentOutput,
    PolicySpec,
    TerminationReason,
    TraceEvent,
    TraceEventKind,
    Verdict,
)
from agentevalops.core.types import RunId
from agentevalops.domain_packs.seed import SeedDomainPack
from agentevalops.policy.basic_checker import BasicPolicyChecker
from agentevalops.reports.markdown import NO_PLAN_SENTENCE

ROOT = Path(__file__).resolve().parents[1]
LEGACY_0_5 = ROOT / "tests/fixtures/bundles/seed-narrow-0.5"
NARROWED = ["TE1", "TC1", "PC1"]


def _run(config: Path, out: Path) -> Path:
    result = CliRunner().invoke(
        app, ["run", "--config", str(config), "--output", str(out)]
    )
    assert result.exit_code == 0, result.output
    return out


@pytest.fixture(scope="module")
def bundles(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    tmp = tmp_path_factory.mktemp("b06")
    seed = fast_yaml.safe_load(
        (ROOT / "configs/agentic_baseline_mock.yaml").read_text("utf-8")
    )
    seed["benchmark_options"]["scenarios"] = NARROWED
    seed["system"] = str(ROOT / "configs/seed_system.yaml")
    narrowed = tmp / "narrowed.yaml"
    narrowed.write_text(fast_yaml.safe_dump(seed), encoding="utf-8")
    return {
        "toy": _run(ROOT / "configs/toy_smoke.yaml", tmp / "toy"),
        "seed": _run(ROOT / "configs/agentic_baseline_mock.yaml", tmp / "seed"),
        "narrowed": _run(narrowed, tmp / "narrowed"),
    }


def _copy(bundle: Path, tmp_path: Path) -> Path:
    copy = tmp_path / bundle.name
    shutil.copytree(bundle, copy)
    return copy


def _json(path: Path) -> Any:
    return json.loads(path.read_text("utf-8"))


def _edit_controls(bundle: Path, change: Any) -> None:
    path = bundle / "controls.json"
    controls = _json(path)
    change(controls)
    path.write_text(json.dumps(controls), encoding="utf-8")
    write_manifest(bundle)


# ---------------------------------------------------------------------------
# The required set and the catalogue's pointer list
# ---------------------------------------------------------------------------


def test_the_0_6_set_is_fourteen_files_with_the_manifest() -> None:
    assert len(required_files_for_version("0.6")) + 1 == 14


def test_the_catalogues_pointer_list_is_the_0_6_bundle() -> None:
    """The catalogue cannot import bundles; this holds its copy to them."""
    assert BUNDLE_0_6_FILES == {
        *required_files_for_version("0.6"),
        MANIFEST_FILENAME,
        MANIFEST_SIGNATURE_FILENAME,
    }


# ---------------------------------------------------------------------------
# The validator's 0.6 checks, each fail-first (docs/adr/0007 D1; decisions
# 1 and 5 of 2026-11-02)
# ---------------------------------------------------------------------------


def test_a_real_seed_bundle_passes_every_0_6_check(bundles: dict[str, Path]) -> None:
    result = validate_bundle(bundles["seed"])
    assert result.valid, result.errors
    assert result.checked_files == 13


def test_controls_tied_to_another_plan_fails(
    bundles: dict[str, Path], tmp_path: Path
) -> None:
    copy = _copy(bundles["seed"], tmp_path)
    _edit_controls(copy, lambda c: c.update(plan_sha256="f" * 64))
    result = validate_bundle(copy)
    assert any("not tied to the registered plan" in e for e in result.errors)


def test_a_selected_scenario_the_plan_did_not_select_fails(
    bundles: dict[str, Path], tmp_path: Path
) -> None:
    copy = _copy(bundles["narrowed"], tmp_path)
    _edit_controls(copy, lambda c: c["scenarios"]["RB1"].update(selected=True))
    result = validate_bundle(copy)
    assert any("selected scenarios are not the plan's" in e for e in result.errors)


def _first_end_state_ref(controls: dict[str, Any]) -> dict[str, Any]:
    return next(
        ref
        for control in controls["controls"]
        for verdict in control["verdicts"]
        for ref in verdict["end_states"]
    )


@pytest.mark.parametrize(
    "change",
    [{"index": 9999}, {"repetition": 7}, {"task_id": "XX9"}, {"stream": "trace"}],
)
def test_an_end_state_ref_that_is_not_a_real_line_fails(
    bundles: dict[str, Path], tmp_path: Path, change: dict[str, Any]
) -> None:
    copy = _copy(bundles["seed"], tmp_path)
    _edit_controls(copy, lambda c: _first_end_state_ref(c).update(change))
    result = validate_bundle(copy)
    assert any("not a line of end_states.jsonl" in e for e in result.errors)


def test_every_end_state_ref_points_at_its_line(bundles: dict[str, Path]) -> None:
    bundle = bundles["seed"]
    lines = [json.loads(line) for line in (bundle / "end_states.jsonl").open()]
    refs = [
        ref
        for control in _json(bundle / "controls.json")["controls"]
        for verdict in control["verdicts"]
        for ref in verdict["end_states"]
    ]
    assert refs
    for ref in refs:
        line = lines[ref["index"]]
        assert (line["task_id"], line["repetition"]) == (
            ref["task_id"],
            ref["repetition"],
        )


def test_a_signature_is_recognised_and_not_yet_verified(
    bundles: dict[str, Path], tmp_path: Path
) -> None:
    copy = _copy(bundles["seed"], tmp_path)
    (copy / MANIFEST_SIGNATURE_FILENAME).write_text("sig", encoding="utf-8")
    result = validate_bundle(copy)
    assert result.valid
    assert any("present but not verified" in w for w in result.warnings)
    manifest = _json(copy / MANIFEST_FILENAME)
    assert MANIFEST_SIGNATURE_FILENAME not in manifest["files"]
    assert MANIFEST_SIGNATURE_FILENAME not in manifest["required_files"]


# ---------------------------------------------------------------------------
# A broken file is a finding, never a crash
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("controls.json", "{not json"),
        ("controls.json", "[1, 2]"),
        ("test_plan.json", "{not json"),
        ("tool_calls.jsonl", "{not json}\n"),
        ("end_states.jsonl", "[1]\n"),
        ("traces.jsonl", "{not json}\n"),
    ],
)
def test_a_malformed_file_comes_back_as_a_finding(
    bundles: dict[str, Path], tmp_path: Path, name: str, content: str
) -> None:
    copy = _copy(bundles["seed"], tmp_path)
    (copy / name).write_text(content, encoding="utf-8")
    write_manifest(copy)
    result = validate_bundle(copy)  # must not raise
    assert not result.valid
    assert any(name in e for e in result.errors), result.errors


def test_cross_file_checks_still_run_when_an_optional_read_fails(
    bundles: dict[str, Path], tmp_path: Path
) -> None:
    """The validator reads through the full reader; when that fails on one
    file, it says so and the core cross-file checks still run."""
    copy = _copy(bundles["seed"], tmp_path)
    (copy / "tool_calls.jsonl").write_text("{not json}\n", encoding="utf-8")
    summary = _json(copy / "summary.json")
    summary["run_id"] = "someone-else"
    (copy / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    write_manifest(copy)
    result = validate_bundle(copy)
    assert any(
        "Cannot load the bundle for cross-file checks" in e for e in result.errors
    )
    assert any("run_id mismatch" in e for e in result.errors)


# ---------------------------------------------------------------------------
# The CLI's evidence inputs (decisions 5 and 7 of 2026-11-02)
# ---------------------------------------------------------------------------


def test_a_narrowed_seed_run_lists_every_pack_scenario_in_controls(
    bundles: dict[str, Path],
) -> None:
    controls = _json(bundles["narrowed"] / "controls.json")
    pack_ids = [s.scenario_id for s in SeedDomainPack().scenarios()]
    assert list(controls["scenarios"]) == pack_ids
    selected = [s for s, e in controls["scenarios"].items() if e["selected"]]
    assert selected == NARROWED
    reasons = {e["reason"] for e in controls["scenarios"].values() if not e["selected"]}
    assert reasons == {"not requested by the run config"}
    rb6 = next(c for c in controls["controls"] if c["id"] == "testing-framework:7.2.1")
    assert rb6["state"] == "not_in_plan"


def test_the_seed_runs_config_records_the_runner(bundles: dict[str, Path]) -> None:
    config = _json(bundles["seed"] / "config.json")
    assert config["runner_id"] == "mock"
    assert config["mock_trajectory"] == "known_good"
    assert config["evaluators"] == [
        "deterministic",
        "tool_use",
        "state_based",
        "policy",
    ]


def test_metadata_records_the_package_and_the_invocation(
    bundles: dict[str, Path],
) -> None:
    from agentevalops.bundles.provenance import package_sha256

    metadata = _json(bundles["seed"] / "metadata.json")
    assert metadata["package_sha256"] == package_sha256()
    for key in ("git_sha", "invocation_git_sha"):
        assert isinstance(metadata[key], str) and metadata[key]
    replay = (bundles["seed"] / "replay_command.txt").read_text("utf-8")
    for key in (
        "package_sha256",
        "invocation_git_sha",
        "invocation_git_dirty",
        "test_plan_sha256",
    ):
        assert f"\n{key}: " in replay


# ---------------------------------------------------------------------------
# The wording guard on a real bundle (docs/adr/0007 D7)
# ---------------------------------------------------------------------------


_CLAIM = re.compile(r"\b(compliant|certified)\b", re.IGNORECASE)


def test_no_file_of_a_seed_bundle_makes_a_claim(bundles: dict[str, Path]) -> None:
    for path in bundles["seed"].iterdir():
        assert not _CLAIM.search(path.name)
        assert "ai verify" not in path.name.lower()
        assert not _CLAIM.search(path.read_text("utf-8")), path.name


def test_the_framework_is_named_only_where_the_sources_are_cited(
    bundles: dict[str, Path],
) -> None:
    bundle = bundles["seed"]
    for path in bundle.iterdir():
        text = path.read_text("utf-8")
        if path.name == "evidence_report.md":
            sources = text.split("\n## Sources\n", 1)[1].split("\n## ", 1)[0]
            assert "AI Verify" not in text.replace(sources, "")
        elif path.name == "controls.json":
            controls = json.loads(text)
            cited = {
                f for s in controls["sources"] for f in (s["title"], s["publisher"])
            }
            stripped = text
            for name in cited:
                stripped = stripped.replace(name, "")
            assert "AI Verify" not in stripped
        else:
            assert "AI Verify" not in text, path.name


# ---------------------------------------------------------------------------
# The gate on 0.6 runs
# ---------------------------------------------------------------------------


def test_the_gate_cannot_judge_an_undeclared_0_6_run(bundles: dict[str, Path]) -> None:
    result = CliRunner().invoke(app, ["gate", "--bundle", str(bundles["toy"])])
    assert result.exit_code == 2
    assert "no test plan was declared" in result.output


def test_the_gate_passes_a_declared_0_6_run(bundles: dict[str, Path]) -> None:
    result = CliRunner().invoke(app, ["gate", "--bundle", str(bundles["seed"])])
    assert result.exit_code == 0, result.output
    assert "GATE: PASS" in result.output


# ---------------------------------------------------------------------------
# The undeclared run's reports (decision 6 of 2026-11-02, as amended)
# ---------------------------------------------------------------------------

#: The organisation's two columns: blank on purpose, exempt by name.
_ANSWER_COLUMNS = {"Implemented", "Elaboration"}


def _cells_that_must_not_be_empty(text: str) -> list[tuple[str, str]]:
    """Every (column, cell) of every Markdown table, except the answer
    columns."""
    cells: list[tuple[str, str]] = []
    header: list[str] = []
    for line in text.splitlines():
        if not line.startswith("|"):
            header = []
            continue
        row = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip())[1:-1]]
        if not header:
            header = row
            continue
        if set(line) <= {"|", "-", " ", ":"}:
            continue
        cells += [
            (col, cell) for col, cell in zip(header, row) if col not in _ANSWER_COLUMNS
        ]
    return cells


@pytest.mark.parametrize("report", ["report.md", "evidence_report.md"])
def test_an_undeclared_runs_report_says_so_once_and_shows_nothing_empty(
    bundles: dict[str, Path], report: str
) -> None:
    text = (bundles["toy"] / report).read_text("utf-8")
    assert text.count(NO_PLAN_SENTENCE) == 1
    assert not re.search(r"\b(None|null)\b", text)
    cells = _cells_that_must_not_be_empty(text)
    assert cells
    assert all(cell for _, cell in cells), [c for c in cells if not c[1]]


def test_the_answer_columns_are_the_only_blank_ones_and_are_blank(
    bundles: dict[str, Path],
) -> None:
    text = (bundles["toy"] / "evidence_report.md").read_text("utf-8")
    checks = text.split("## Testing framework checks, by principle", 1)[1]
    checks = checks.split("\n## ", 1)[0]
    rows = [
        line for line in checks.splitlines() if re.match(r"^\| \d+\.\d+\.\d+ \|", line)
    ]
    assert len(rows) == 112
    assert all(row.endswith("|  |  |") for row in rows)
    assert "left empty for them" in text


# ---------------------------------------------------------------------------
# Legacy points
# ---------------------------------------------------------------------------


def _reseal_0_5(bundle: Path, *names: str) -> None:
    """Re-checksum *names* in a 0.5 manifest, keeping its version."""
    manifest = _json(bundle / MANIFEST_FILENAME)
    for name in names:
        manifest["files"][name] = {
            "size_bytes": (bundle / name).stat().st_size,
            "sha256": hashlib.sha256((bundle / name).read_bytes()).hexdigest(),
        }
    (bundle / MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")


def test_a_0_5_trace_with_a_policy_verdict_event_still_validates(
    tmp_path: Path,
) -> None:
    """No bundle ever carried one, but the kind left the enum with 0.6: a
    0.5 trace that names it is still a valid 0.5 trace."""
    copy = _copy(LEGACY_0_5, tmp_path)
    traces = copy / "traces.jsonl"
    event = {
        "run_id": "seed-narrow-0.5",
        "step_index": 99,
        "kind": "policy.verdict",
        "payload": {},
        "timestamp": "2026-11-02T00:00:00+00:00",
        "cost_delta_usd": 0.0,
        "tokens_delta": 0,
        "span_context": None,
        "task_id": "TE1",
        "repetition": 0,
    }
    traces.write_text(traces.read_text("utf-8") + json.dumps(event) + "\n")
    summary = _json(copy / "summary.json")
    summary["task_results"][0]["event_count"] += 1
    summary["trace_event_count"] += 1
    (copy / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    _reseal_0_5(copy, "traces.jsonl", "summary.json")
    result = validate_bundle(copy)
    assert result.valid, result.errors
    assert result.bundle_format_version == "0.5"


def test_a_0_5_bundles_drift_check_still_compares_the_invocation_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Up to 0.5 git_sha was the invocation's checkout: its drift warning
    still compares against the current one, never a package hash."""
    from agentevalops.bundles import validator as validator_module

    copy = _copy(LEGACY_0_5, tmp_path)
    metadata = _json(copy / "metadata.json")
    metadata.update(git_sha="a" * 40, git_dirty=False)
    (copy / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    _reseal_0_5(copy, "metadata.json")

    def no_hash() -> str:
        raise AssertionError("a 0.5 bundle's drift check reads no package hash")

    monkeypatch.setattr(validator_module, "_current_package_sha256", no_hash)
    monkeypatch.setattr(
        validator_module, "_capture_git_provenance", lambda cwd=None: ("b" * 40, False)
    )
    result = validate_bundle(copy)
    assert result.valid
    assert any(
        "Code drift: this bundle was written at git_sha" in w for w in result.warnings
    )


def test_a_0_5_bundle_from_a_dirty_tree_is_inconclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 0.5 branch's third outcome, injected rather than read from this
    checkout (ROADMAP §0, the 2026-10-10 finding)."""
    from agentevalops.bundles import validator as validator_module

    copy = _copy(LEGACY_0_5, tmp_path)
    metadata = _json(copy / "metadata.json")
    metadata.update(git_sha="a" * 40, git_dirty=True)
    (copy / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    _reseal_0_5(copy, "metadata.json")
    monkeypatch.setattr(
        validator_module, "_capture_git_provenance", lambda cwd=None: ("a" * 40, False)
    )
    result = validate_bundle(copy)
    assert result.valid
    assert any(w.startswith("Code-drift check inconclusive") for w in result.warnings)


def test_a_0_6_bundle_with_no_package_hash_is_inconclusive(
    bundles: dict[str, Path], tmp_path: Path
) -> None:
    copy = _copy(bundles["seed"], tmp_path)
    metadata = _json(copy / "metadata.json")
    del metadata["package_sha256"]
    (copy / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    write_manifest(copy)
    result = validate_bundle(copy)
    assert any("records no package_sha256" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# The policy's trace ceiling counts the agent's events only (D3)
# ---------------------------------------------------------------------------


def test_three_agent_events_and_two_score_events_pass_a_ceiling_of_three() -> None:
    """Fail-first: counting every event, 5 > 3 failed."""
    run_id = RunId("ceiling")
    trace = [
        TraceEvent(run_id=run_id, step_index=i, kind=TraceEventKind.AGENT_PLAN)
        for i in range(3)
    ] + [
        TraceEvent(run_id=run_id, step_index=3 + i, kind=TraceEventKind.EVALUATOR_SCORE)
        for i in range(2)
    ]
    output = AgentOutput(success=True, termination_reason=TerminationReason.COMPLETED)
    verdict = asyncio.run(
        BasicPolicyChecker().check(
            PolicySpec(policy_id="p", max_trace_events=3), output, trace
        )
    )
    assert verdict.verdict != Verdict.FAIL, verdict.notes
    over = trace + [
        TraceEvent(run_id=run_id, step_index=5, kind=TraceEventKind.AGENT_PLAN)
    ]
    verdict = asyncio.run(
        BasicPolicyChecker().check(
            PolicySpec(policy_id="p", max_trace_events=3), output, over
        )
    )
    assert verdict.verdict == Verdict.FAIL
    assert "4 agent events" in verdict.notes
