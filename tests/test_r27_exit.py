"""ROADMAP R27's exit criteria, each checked against running code
(docs/adr/0005 D6; docs/adr/0006 D6, D18, D20; testing-framework 3.6.1,
3.7.1, 4.10.1, 5.9.1).

One test per criterion, in the row's order; the sixth, the v0.3 tag, is
the owner's. Each test uses the slice tests' own helpers, so what the
row claims is checked the way the slices check it, not re-described.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import fast_yaml
import pytest
from flaky_runner import FlakyRunner, passes
from test_gate import _bundle, _config, _gate
from test_r24_exit import _evaluators
from typer.testing import CliRunner

from agentevalops.agents.mock_runner import MockAgentRunner
from agentevalops.benchmarks.agentic_baseline import AgenticBaselineBenchmark
from agentevalops.bundles.constants import TEST_PLAN_FILENAME
from agentevalops.bundles.manifest import write_manifest
from agentevalops.bundles.validator import validate_bundle
from agentevalops.cli import app
from agentevalops.config.system import load_system_declaration
from agentevalops.core.perturbation import derive_seed
from agentevalops.core.protocols import Thresholded
from agentevalops.core.schemas import RunConfig, ThresholdOverride
from agentevalops.core.types import AgentId, BackendId, RunId
from agentevalops.domain_packs.seed import SeedDomainPack
from agentevalops.orchestration.local import LocalOrchestrator
from agentevalops.plans import build_plan, read_plan, write_plan
from agentevalops.policy.basic_checker import BasicPolicyChecker
from agentevalops.stores.memory import InMemoryTraceStore

SYSTEM = Path("configs/seed_system.yaml")
PACK = SeedDomainPack()
SCENARIO_IDS = [s.scenario_id for s in PACK.scenarios()]


def test_editing_test_plan_json_after_the_run_makes_validate_bundle_fail(
    tmp_path: Path,
) -> None:
    """Criterion 1 (fail-first in tests/test_test_plan.py): the hash
    registered before the first task ran is in metadata.json, so an edit
    fails validation even after the manifest is rewritten to match."""
    bundle = _bundle(tmp_path, _config(tmp_path))
    assert validate_bundle(bundle).valid
    plan_path = bundle / TEST_PLAN_FILENAME
    doc = json.loads(plan_path.read_text(encoding="utf-8"))
    doc["thresholds"][0]["min_pass_k"] = 0.0
    plan_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    write_manifest(bundle)
    result = validate_bundle(bundle)
    assert not result.valid
    assert any("after the plan was registered" in e for e in result.errors)
    cli = CliRunner().invoke(app, ["validate-bundle", "--bundle", str(bundle)])
    assert cli.exit_code == 1


def test_a_threshold_set_in_system_yaml_overrides_the_packs_and_records_its_source(
    tmp_path: Path,
) -> None:
    """Criterion 2 (testing-framework 3.6.1), end to end: the owner's value
    is in the registered plan with source ``owner``; the pack's value and
    its source are what the plan records for every other category."""
    assert isinstance(PACK, Thresholded)
    pack_robustness = next(t for t in PACK.thresholds() if t.category == "robustness")
    owner_value = round(min(1.0, pack_robustness.min_pass_k + 0.2), 2)
    config = _config(
        tmp_path,
        mutate_system=lambda s: s.__setitem__(
            "thresholds", {"robustness": {"min_pass_k": owner_value, "gate": "hard"}}
        ),
    )
    bundle = _bundle(tmp_path, config)
    plan = read_plan(bundle)
    assert plan is not None
    by_category = {t["category"]: t for t in plan["thresholds"]}
    assert by_category["robustness"] == {
        "category": "robustness",
        "min_pass_k": owner_value,
        "min_pass_k_source": "owner",
        "gate": "hard",
        "gate_source": "owner",
    }
    for category, threshold in by_category.items():
        if category != "robustness":
            pack_threshold = next(
                t for t in PACK.thresholds() if t.category == category
            )
            assert threshold["min_pass_k"] == pack_threshold.min_pass_k
            assert threshold["gate"] == pack_threshold.gate
            assert (threshold["min_pass_k_source"], threshold["gate_source"]) == (
                "pack",
                "pack",
            )
    output = _gate(bundle).output
    assert f"min {owner_value} (owner)" in output
    assert "GATE: PASS" in output


def test_a_declared_intended_use_and_business_metric_appear_unchanged_in_the_plan(
    tmp_path: Path,
) -> None:
    """Criterion 3 (testing-framework 3.7.1): recorded verbatim, never
    evaluated -- in the plan the run registered, and repeated by the
    report."""
    shipped = fast_yaml.safe_load(SYSTEM.read_text(encoding="utf-8"))
    bundle = _bundle(tmp_path, _config(tmp_path))
    plan = read_plan(bundle)
    assert plan is not None
    system = plan["system"]
    assert system["intended_use"] == shipped["intended_use"].strip()
    assert system["out_of_scope"] == [s.strip() for s in shipped["out_of_scope"]]
    assert system["business_metrics"] == [
        {"name": m["name"].strip(), "definition": m["definition"].strip()}
        for m in shipped["business_metrics"]
    ]
    report = (bundle / "report.md").read_text(encoding="utf-8")
    assert system["intended_use"] in report
    assert system["business_metrics"][0]["definition"] in report
    assert "recorded, not evaluated" in report
    evaluations = json.loads((bundle / "evaluations.json").read_text(encoding="utf-8"))
    assert not any(
        "business" in json.dumps(e).lower() or "intended_use" in json.dumps(e)
        for e in evaluations
    )


def test_pass_k_at_k_5_on_a_seeded_flaky_agent_is_the_analytic_value() -> None:
    """Criterion 4 (docs/adr/0006 D6, D18): the agent decides each run from
    the run's own seed, so the exact set of scenarios passing all five
    repetitions follows from the seeds, and so does every count."""
    benchmark = AgenticBaselineBenchmark(PACK)
    config = RunConfig(
        run_id=RunId("r27"),
        agent_id=AgentId("flaky"),
        backend_id=BackendId("local"),
        repetitions=5,
        seed=27,
    )
    orch = LocalOrchestrator(
        run_config=config,
        benchmark=benchmark,
        runner=FlakyRunner(0.75, benchmark),
        trace_store=InMemoryTraceStore(),
        evaluator=_evaluators(),
        policy_checker=BasicPolicyChecker(),
    )
    summary = asyncio.run(orch.run())
    analytic = {
        sid: [passes(derive_seed(27, sid, r), 0.75) for r in range(5)]
        for sid in SCENARIO_IDS
    }
    assert {r.task_id: (r.k, r.passed, r.failed, r.pass_k) for r in summary.pass_k} == {
        sid: (5, sum(runs), 5 - sum(runs), all(runs)) for sid, runs in analytic.items()
    }
    plan = build_plan(
        load_system_declaration(SYSTEM), PACK, benchmark.scenarios(), run_config=config
    )
    assert {sid: list(seeds) for sid, seeds in plan.seeds.items()} == {
        sid: [derive_seed(27, sid, r) for r in range(5)] for sid in SCENARIO_IDS
    }


def test_gate_exits_non_zero_with_all_evaluated_passed_and_one_not_evaluated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Criterion 5 (fail-first in tests/test_gate.py): the seed pack with
    TE1 made defective in a copy of the data, under the pack's own hard
    gate on task execution."""
    from test_seed_domain import _edited

    def break_te1(doc: dict) -> None:  # type: ignore[type-arg]
        for scenario in doc["scenarios"]:
            if scenario["scenario_id"] == "TE1":
                scenario["known_good"], scenario["known_bad"] = (
                    scenario["known_bad"],
                    scenario["known_good"],
                )

    data_dir = _edited(tmp_path, "scenarios.yaml", break_te1)
    from agentevalops import registry as registry_module
    from agentevalops.registry import KIND_DOMAIN_PACK

    fresh = registry_module.build_default_registry()
    fresh.register(
        KIND_DOMAIN_PACK, "seed-te1-broken", lambda: SeedDomainPack(data_dir)
    )
    monkeypatch.setattr(registry_module, "default_registry", fresh)

    def narrow(run: dict) -> None:  # type: ignore[type-arg]
        run["benchmark_options"] = {
            "pack": "seed-te1-broken",
            "scenarios": ["TE1", "TE2", "TE3", "TE4"],
        }

    config = _config(
        tmp_path,
        mutate_run=narrow,
        mutate_system=lambda system: system.__setitem__("pack_id", "seed-te1-broken"),
    )
    bundle = _bundle(tmp_path, config)
    summary = json.loads((bundle / "summary.json").read_text(encoding="utf-8"))
    assert summary["failed_tasks"] == 0
    assert (
        summary["passed_tasks"]
        == summary["total_tasks"] - summary["not_evaluated_tasks"]
    )
    assert summary["not_evaluated_by_cause"] == {"environment_defect": 1}
    result = _gate(bundle)
    assert result.exit_code != 0
    assert result.exit_code == 1
    assert (
        "task_execution: 1 scenario not evaluated (environment_defect); "
        "a hard gate fails closed" in result.output
    )
    assert "GATE: FAIL" in result.output


def test_the_plan_is_registered_before_the_first_task_and_the_run_refuses_without_it(
    tmp_path: Path,
) -> None:
    """The row's text, beyond its criteria: ``test_plan.json`` hashed before
    the first task runs, and the seed pack never running without one."""
    plan_written = tmp_path / "plan-only"
    config = load_system_declaration(SYSTEM)
    run_config = RunConfig(
        run_id=RunId("x"), agent_id=AgentId("mock"), backend_id=BackendId("local")
    )
    digest = write_plan(
        plan_written, build_plan(config, PACK, PACK.scenarios(), run_config=run_config)
    )
    assert len(digest) == 64
    without = _config(tmp_path, mutate_run=lambda d: d.pop("system"))
    result = CliRunner().invoke(
        app, ["run", "--config", str(without), "--output", str(tmp_path / "no-plan")]
    )
    assert result.exit_code == 1 and "system" in result.output
    assert not (tmp_path / "no-plan").exists()
    bundle = _bundle(tmp_path, _config(tmp_path))
    metadata = json.loads((bundle / "metadata.json").read_text(encoding="utf-8"))
    assert isinstance(metadata["test_plan_sha256"], str)
    doc = read_plan(bundle)
    assert doc is not None and doc["selected"] == SCENARIO_IDS


def test_not_evaluated_never_appears_without_a_cause(tmp_path: Path) -> None:
    """Owner's condition (2026-11-01): in summary.json, the CLI line, the
    report and the gate's output."""
    declaration = replace(
        load_system_declaration(SYSTEM),
        thresholds=(ThresholdOverride("task_execution", gate="hard"),),
    )
    assert declaration.thresholds[0].gate == "hard"
    from test_agentic_baseline import _TestPack

    benchmark = AgenticBaselineBenchmark(_TestPack())
    orch = LocalOrchestrator(
        run_config=RunConfig(
            run_id=RunId("cause"),
            agent_id=AgentId("mock"),
            backend_id=BackendId("local"),
            repetitions=2,
        ),
        benchmark=benchmark,
        runner=MockAgentRunner(),
        trace_store=InMemoryTraceStore(),
        evaluator=_evaluators(),
        policy_checker=BasicPolicyChecker(),
    )
    summary = asyncio.run(orch.run())
    broken = next(r for r in summary.pass_k if r.task_id == "broken")
    assert broken.pass_k is None and broken.not_evaluated_cause == "environment_defect"
    (roll_up,) = summary.pass_k_by_category
    assert roll_up.not_evaluated_causes == {"environment_defect": 1}
