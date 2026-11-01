"""``agentevalops gate --bundle``: fail-closed (ROADMAP R27 slice 4;
docs/adr/0005 D6; testing-framework 5.9.1).

The gate judges a bundle against the plan it registered: every hard
threshold must be met over scenarios that were all evaluated. A gated
category with a not-evaluated scenario fails, whatever the evaluated ones
did; a comparative threshold is reported and never gates, and the output
says so where it prints "below". Exit 0 when every hard gate is met, 1
when any fails, 2 when the gate cannot judge (no bundle, an invalid or
tampered bundle, no registered plan, nothing to gate, or a run that did
not follow its plan). The bundle is never modified.

Every guard has a fail-first test, written and failing before the rule.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import fast_yaml
import pytest
from typer.testing import CliRunner

from agentevalops.bundles.constants import TEST_PLAN_FILENAME
from agentevalops.bundles.manifest import write_manifest
from agentevalops.bundles.validator import validate_bundle
from agentevalops.cli import app
from agentevalops.plans.gate import EXIT_CANNOT_JUDGE, EXIT_FAIL, EXIT_PASS, judge

SYSTEM = Path("configs/seed_system.yaml")
RUN_CONFIG = Path("configs/agentic_baseline_mock.yaml")


# ---------------------------------------------------------------------------
# judge() over the documents on disk
# ---------------------------------------------------------------------------


def _threshold(category: str, min_pass_k: float, gate: str) -> dict[str, Any]:
    return {
        "category": category,
        "min_pass_k": min_pass_k,
        "min_pass_k_source": "pack",
        "gate": gate,
        "gate_source": "pack",
    }


def _roll_up(
    category: str,
    passed: int,
    scenarios: int,
    not_evaluated: int = 0,
    causes: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "category": category,
        "k": 2,
        "scenarios": scenarios,
        "passed": passed,
        "failed": scenarios - passed - not_evaluated,
        "not_evaluated": not_evaluated,
        "pass_k_rate": passed / scenarios,
        "not_evaluated_causes": causes or {},
    }


def _plan(
    *thresholds: dict[str, Any], selected: list[str] | None = None
) -> dict[str, Any]:
    return {"thresholds": list(thresholds), "selected": selected or ["a", "b"]}


def _summary(
    *roll_ups: dict[str, Any], task_ids: list[str] | None = None
) -> dict[str, Any]:
    return {
        "pass_k": [{"task_id": t} for t in (task_ids or ["a", "b"])],
        "pass_k_by_category": list(roll_ups),
    }


def test_every_hard_gate_met_passes() -> None:
    result = judge(
        _plan(_threshold("x", 0.5, "hard"), _threshold("y", 1.0, "hard")),
        _summary(_roll_up("x", 1, 2), _roll_up("y", 2, 2)),
    )
    assert result.passed and result.reasons == ()
    assert [line.verdict for line in result.lines] == ["met", "met"]


def test_a_hard_gate_below_its_threshold_fails() -> None:
    result = judge(_plan(_threshold("x", 0.75, "hard")), _summary(_roll_up("x", 1, 2)))
    assert not result.passed
    assert result.lines[0].verdict == "below (hard gate)"
    assert result.reasons == ("x: pass^k 0.5 is below 0.75 (hard gate)",)


def test_a_gated_category_with_a_not_evaluated_scenario_fails_closed() -> None:
    """R27 exit criterion, fail-first: every evaluated scenario passed and
    the rate meets the threshold, yet one gated scenario was not evaluated."""
    result = judge(
        _plan(_threshold("x", 0.5, "hard")),
        _summary(
            _roll_up("x", 1, 2, not_evaluated=1, causes={"environment_defect": 1})
        ),
    )
    assert not result.passed
    assert result.lines[0].verdict == (
        "not evaluated: 1 scenario (environment_defect); a hard gate fails closed"
    )
    assert result.reasons == (
        "x: 1 scenario not evaluated (environment_defect); a hard gate fails closed",
    )


def test_a_comparative_threshold_never_gates_and_says_so() -> None:
    """Owner's condition: "below" must not read as a tolerated failure."""
    result = judge(
        _plan(_threshold("x", 0.75, "comparative"), _threshold("y", 1.0, "hard")),
        _summary(_roll_up("x", 0, 2), _roll_up("y", 2, 2)),
    )
    assert result.passed
    assert result.lines[0].verdict == "below (comparative threshold; does not gate)"
    assert result.lines[0].gated is False and result.lines[1].gated is True


def test_a_comparative_threshold_with_a_not_evaluated_scenario_does_not_gate() -> None:
    result = judge(
        _plan(_threshold("x", 0.5, "comparative")),
        _summary(_roll_up("x", 1, 2, not_evaluated=1, causes={"infrastructure": 1})),
    )
    assert result.passed
    assert result.lines[0].verdict == (
        "not evaluated: 1 scenario (infrastructure); comparative threshold; "
        "does not gate"
    )


def test_a_hard_gate_over_no_scenario_fails_closed() -> None:
    result = judge(_plan(_threshold("x", 0.5, "hard")), _summary())
    assert not result.passed
    assert result.lines[0].verdict == "no scenario ran; a hard gate fails closed"
    assert result.reasons == ("x: no scenario ran; a hard gate fails closed",)


def test_causes_are_counted_in_the_verdict() -> None:
    result = judge(
        _plan(_threshold("x", 0.0, "hard")),
        _summary(
            _roll_up(
                "x", 0, 3, not_evaluated=3, causes={"environment_defect": 2, "mixed": 1}
            )
        ),
    )
    assert "3 scenarios (environment_defect x2, mixed)" in result.lines[0].verdict


def test_a_plan_without_thresholds_cannot_be_judged() -> None:
    from agentevalops.plans.gate import GateError

    with pytest.raises(GateError, match="no threshold"):
        judge(_plan(), _summary(_roll_up("x", 2, 2)))


def test_a_run_that_did_not_follow_its_plan_cannot_be_judged() -> None:
    from agentevalops.plans.gate import GateError

    with pytest.raises(GateError, match="did not follow"):
        judge(
            _plan(_threshold("x", 0.5, "hard"), selected=["a", "b"]),
            _summary(_roll_up("x", 1, 1), task_ids=["a"]),
        )


def test_a_summary_without_pass_k_cannot_be_judged() -> None:
    from agentevalops.plans.gate import GateError

    with pytest.raises(GateError, match="pass_k"):
        judge(_plan(_threshold("x", 0.5, "hard")), {"task_results": []})


def test_lines_follow_the_plans_threshold_order_and_carry_the_sources() -> None:
    plan = _plan(
        {**_threshold("y", 1.0, "hard"), "min_pass_k_source": "owner"},
        _threshold("x", 0.5, "comparative"),
    )
    result = judge(plan, _summary(_roll_up("x", 1, 2), _roll_up("y", 2, 2)))
    assert [
        (line.category, line.min_pass_k_source, line.gate_source)
        for line in result.lines
    ] == [
        ("y", "owner", "pack"),
        ("x", "pack", "pack"),
    ]
    assert result.lines[0].observed == 1.0 and result.lines[1].observed == 0.5


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


def _config(
    tmp_path: Path,
    mutate_run: Any = lambda d: None,
    mutate_system: Any = lambda d: None,
) -> Path:
    system = fast_yaml.safe_load(SYSTEM.read_text(encoding="utf-8"))
    mutate_system(system)
    (tmp_path / "system.yaml").write_text(fast_yaml.safe_dump(system), encoding="utf-8")
    run = fast_yaml.safe_load(RUN_CONFIG.read_text(encoding="utf-8"))
    run["system"] = "system.yaml"
    mutate_run(run)
    path = tmp_path / "run.yaml"
    path.write_text(fast_yaml.safe_dump(run), encoding="utf-8")
    return path


def _bundle(tmp_path: Path, config: Path) -> Path:
    out = tmp_path / "bundle"
    result = CliRunner().invoke(
        app, ["run", "--config", str(config), "--output", str(out)]
    )
    assert result.exit_code == 0, result.output
    return out


def _gate(bundle: Path) -> Any:
    return CliRunner().invoke(app, ["gate", "--bundle", str(bundle)])


def _snapshot(bundle: Path) -> dict[str, tuple[int, bytes]]:
    return {
        p.name: (p.stat().st_size, p.read_bytes()) for p in sorted(bundle.iterdir())
    }


def test_the_shipped_run_passes_the_gate(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, _config(tmp_path))
    before = _snapshot(bundle)
    result = _gate(bundle)
    assert result.exit_code == EXIT_PASS == 0, result.output
    assert "GATE: PASS" in result.output
    assert "task_execution" in result.output and "policy_compliance" in result.output
    assert "hard" in result.output and "comparative" in result.output
    assert "(pack)" in result.output
    assert _snapshot(bundle) == before
    assert validate_bundle(bundle).valid


def test_every_task_passes_but_one_gated_scenario_is_not_evaluated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R27 exit criterion, through the command: the test pack's ``add``
    passes and ``broken`` is defective, so it is never evaluated; with the
    owner's hard threshold at 0.5 the evaluated rate is met, and the gate
    must still fail."""
    from test_agentic_baseline import _TestPack

    from agentevalops import registry as registry_module
    from agentevalops.registry import KIND_DOMAIN_PACK

    fresh = registry_module.build_default_registry()
    fresh.register(KIND_DOMAIN_PACK, "test-pack", _TestPack)
    monkeypatch.setattr(registry_module, "default_registry", fresh)
    (tmp_path / "system.yaml").write_text(
        "system_id: t\npack_id: test-pack\ninvolvement: autonomous\n"
        "tools: [{name: calculator, permission: read}]\n"
        "intended_use: Add numbers.\n"
        "thresholds: {task_execution: {min_pass_k: 0.5, gate: hard}}\n",
        encoding="utf-8",
    )
    config = tmp_path / "c.yaml"
    config.write_text(
        "run_id: gate1\nbenchmark_id: agentic-baseline\nsystem: system.yaml\n"
        "benchmark_options:\n  pack: test-pack\n",
        encoding="utf-8",
    )
    bundle = _bundle(tmp_path, config)
    summary = json.loads((bundle / "summary.json").read_text(encoding="utf-8"))
    assert (
        summary["passed_tasks"],
        summary["failed_tasks"],
        summary["not_evaluated_tasks"],
    ) == (
        1,
        0,
        1,
    )
    result = _gate(bundle)
    assert result.exit_code == EXIT_FAIL == 1
    assert "GATE: FAIL" in result.output
    assert (
        "1 scenario not evaluated (environment_defect); a hard gate fails closed"
        in result.output
    )
    assert "not evaluated (" in result.output


def test_a_known_bad_run_fails_every_hard_gate_and_names_them(tmp_path: Path) -> None:
    config = _config(tmp_path, lambda d: d.__setitem__("mock_trajectory", "known_bad"))
    result = _gate(_bundle(tmp_path, config))
    assert result.exit_code == EXIT_FAIL
    assert "GATE: FAIL" in result.output
    for category in ("task_execution", "tool_calling", "policy_compliance"):
        assert f"{category}: pass^k 0.0 is below" in result.output
    assert "robustness: pass^k 0.0 is below" not in result.output  # comparative
    assert "below (comparative threshold; does not gate)" in result.output


def test_an_owner_hard_gate_on_robustness_fails_a_known_bad_run(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        lambda d: d.__setitem__("mock_trajectory", "known_bad"),
        lambda s: s.__setitem__("thresholds", {"robustness": {"gate": "hard"}}),
    )
    result = _gate(_bundle(tmp_path, config))
    assert result.exit_code == EXIT_FAIL
    assert "robustness: pass^k 0.0 is below 0.75 (hard gate)" in result.output
    assert "min 0.75 (pack, gate: owner)" in result.output


def test_a_bundle_without_a_plan_cannot_be_judged(tmp_path: Path) -> None:
    out = tmp_path / "toy"
    assert (
        CliRunner()
        .invoke(
            app, ["run", "--config", "configs/toy_smoke.yaml", "--output", str(out)]
        )
        .exit_code
        == 0
    )
    result = _gate(out)
    assert result.exit_code == EXIT_CANNOT_JUDGE == 2
    # From 0.6 the toy run registers the undeclared plan (docs/adr/0007 D2).
    assert "no test plan was declared" in result.output
    assert "GATE: CANNOT JUDGE" in result.output


def test_a_tampered_plan_cannot_be_judged(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, _config(tmp_path))
    plan_path = bundle / TEST_PLAN_FILENAME
    doc = json.loads(plan_path.read_text(encoding="utf-8"))
    doc["thresholds"] = [
        t for t in doc["thresholds"] if t["category"] != "policy_compliance"
    ]
    plan_path.write_text(json.dumps(doc), encoding="utf-8")
    write_manifest(bundle)
    result = _gate(bundle)
    assert result.exit_code == EXIT_CANNOT_JUDGE
    assert "after the plan was registered" in result.output
    assert "GATE: CANNOT JUDGE" in result.output


def test_a_missing_or_invalid_bundle_cannot_be_judged(tmp_path: Path) -> None:
    result = _gate(tmp_path / "nowhere")
    assert result.exit_code == EXIT_CANNOT_JUDGE
    bundle = _bundle(tmp_path, _config(tmp_path))
    (bundle / "summary.json").write_text("{}", encoding="utf-8")
    result = _gate(bundle)
    assert result.exit_code == EXIT_CANNOT_JUDGE
    assert "SHA-256 mismatch" in result.output


def test_a_run_outside_its_plan_cannot_be_judged(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, _config(tmp_path))
    plan_path = bundle / TEST_PLAN_FILENAME
    doc = json.loads(plan_path.read_text(encoding="utf-8"))
    dropped = doc["selected"][-1]
    doc["selected"] = doc["selected"][:-1]
    plan_path.write_text(json.dumps(doc), encoding="utf-8")
    metadata_path = bundle / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    import hashlib

    registered = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    metadata["test_plan_sha256"] = registered
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    # From 0.6 controls.json is tied to the plan too (docs/adr/0007 D1):
    # re-tie it, so the bundle is structurally valid and only the gate's
    # own check -- the run executed a scenario its plan did not select --
    # is left to catch it.
    controls_path = bundle / "controls.json"
    controls = json.loads(controls_path.read_text(encoding="utf-8"))
    controls["plan_sha256"] = registered
    controls["scenarios"][dropped]["selected"] = False
    controls_path.write_text(json.dumps(controls), encoding="utf-8")
    write_manifest(bundle)
    assert validate_bundle(bundle).valid
    result = _gate(bundle)
    assert result.exit_code == EXIT_CANNOT_JUDGE
    assert "did not follow" in result.output


def test_the_output_is_one_line_per_threshold_then_the_verdict(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, _config(tmp_path))
    output = _gate(bundle).output
    lines = output.splitlines()
    assert lines[-1] == "GATE: PASS"
    table = [
        line
        for line in lines
        if line.startswith(
            ("task_execution", "tool_calling", "policy_compliance", "robustness")
        )
    ]
    assert len(table) == 4
    for line in table:
        assert "met" in line
    assert all(
        "not evaluated" not in line or "not evaluated (" in line for line in lines
    )
