"""The pre-registered test plan (ROADMAP R27 slice 2; docs/adr/0005 D6):
selection and thresholds from the system declaration, ``test_plan.json``
written and hashed before the first task runs, and ``validate-bundle``
failing when it is edited afterwards.

Every guard has a fail-first test, written and failing before the rule.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import fast_yaml
import pytest
from reference_bundles import ReferenceBundles
from test_seed_domain import _edited
from typer.testing import CliRunner

from agentevalops.benchmarks.agentic_baseline import AgenticBaselineBenchmark
from agentevalops.bundles.constants import TEST_PLAN_FILENAME
from agentevalops.bundles.manifest import write_manifest
from agentevalops.bundles.validator import validate_bundle
from agentevalops.cli import app
from agentevalops.config.loader import load_run_config
from agentevalops.config.system import load_system_declaration
from agentevalops.core.errors import ConfigurationError
from agentevalops.core.perturbation import derive_seed
from agentevalops.core.protocols import DomainPack, Thresholded, ToolEnvironment
from agentevalops.core.schemas import (
    CategoryThreshold,
    DeclaredTool,
    RunConfig,
    RunPlan,
    Scenario,
    SystemDeclaration,
    ThresholdOverride,
    ToolSpec,
)
from agentevalops.core.types import AgentId, BackendId, RunId, TaskId
from agentevalops.domain_packs import DomainPackError
from agentevalops.domain_packs.seed import SeedDomainPack
from agentevalops.environments.inprocess import InProcessToolEnvironment
from agentevalops.plans import build_plan, plan_sha256, read_plan, write_plan

SHIPPED_SYSTEM = Path("configs/seed_system.yaml")
RUN_CONFIG = Path("configs/agentic_baseline_mock.yaml")
PACK = SeedDomainPack()
LEGACY_0_5 = Path(__file__).resolve().parent / "fixtures/bundles/seed-narrow-0.5"
SCENARIO_IDS = [s.scenario_id for s in PACK.scenarios()]
DECLARATION = load_system_declaration(SHIPPED_SYSTEM)
RUN = RunConfig(
    run_id=RunId("plan-test"),
    agent_id=AgentId("mock"),
    backend_id=BackendId("local"),
    repetitions=3,
    seed=11,
)


def _plan(
    declaration: SystemDeclaration = DECLARATION,
    pack: Any = PACK,
    candidates: Sequence[Scenario] | None = None,
    **kw: Any,
) -> RunPlan:
    return build_plan(
        declaration,
        pack,
        list(pack.scenarios()) if candidates is None else list(candidates),
        run_config=kw.pop("run_config", RUN),
        config_name=kw.pop("config_name", "configs/x.yaml"),
        evaluators=kw.pop("evaluators", ("deterministic", "policy")),
        **kw,
    )


def _without(declaration: SystemDeclaration, *names: str) -> SystemDeclaration:
    return replace(
        declaration, tools=tuple(t for t in declaration.tools if t.name not in names)
    )


# ---------------------------------------------------------------------------
# A synthetic pack, for the rules the seed pack cannot show on its own
# ---------------------------------------------------------------------------


def _spec(name: str, permission: str = "read", data_class: str = "normal") -> ToolSpec:
    return ToolSpec(
        name=name,
        description=name,
        input_schema={"type": "object"},
        permission=permission,  # type: ignore[arg-type]
        data_class=data_class,  # type: ignore[arg-type]
    )


_SPECS = (
    _spec("look"),
    _spec("write", "write"),
    _spec("export", "irreversible", "restricted"),
)


def _scenario(sid: str, category: str, group: str, *tools: str, **env: Any) -> Scenario:
    return Scenario(
        scenario_id=sid,
        description=sid,
        category=category,
        known_good=tuple({"call": t, "arguments": {}} for t in tools)
        + ({"finish": "x"},),
        applicable_evaluators=("deterministic",),
        outcome_group=group,
        environment=env,
    )


class _Pack:
    pack_id = "synthetic"
    pack_version = "0"

    def __init__(self, scenarios: Sequence[Scenario], thresholds: Any = None) -> None:
        self._scenarios = list(scenarios)
        self._thresholds = thresholds

    def scenarios(self) -> Sequence[Scenario]:
        return self._scenarios

    def build_environment(self, scenario: Scenario, task_id: TaskId) -> ToolEnvironment:
        async def ok(arguments: dict[str, Any]) -> str:
            return "ok"

        return InProcessToolEnvironment(
            task_id, _SPECS, {spec.name: ok for spec in _SPECS}
        )


class _ThresholdedPack(_Pack):
    def thresholds(self) -> Sequence[CategoryThreshold]:
        return self._thresholds


_SYNTHETIC_TOOLS = (
    DeclaredTool("look", "read"),
    DeclaredTool("write", "write"),
    DeclaredTool(
        "export", "irreversible", "restricted", reversible=False, external=True
    ),
)


def _declaration(**kw: Any) -> SystemDeclaration:
    base = dict(
        system_id="s",
        pack_id="synthetic",
        involvement="human_approves",
        tools=_SYNTHETIC_TOOLS,
        intended_use="Testing.",
    )
    base.update(kw)
    return SystemDeclaration(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def test_the_shipped_declaration_selects_every_seed_scenario() -> None:
    plan = _plan()
    assert list(plan.selected) == SCENARIO_IDS
    assert plan.excluded == ()
    assert plan.uncovered_tools == ()
    assert plan.pack_id == "seed" and plan.pack_version == PACK.pack_version
    assert plan.repetitions == 3 and plan.seed == 11
    assert plan.evaluators == ("deterministic", "policy")
    assert plan.requested is None
    assert plan.system == DECLARATION
    assert len(plan.scenarios_sha256) == 64


def test_seeds_are_derived_per_task_and_repetition() -> None:
    plan = _plan()
    assert set(plan.seeds) == set(SCENARIO_IDS)
    for sid, seeds in plan.seeds.items():
        assert list(seeds) == [derive_seed(11, sid, r) for r in range(3)]


def test_an_undeclared_tool_excludes_the_scenarios_that_call_it() -> None:
    """Rule 1: every tool a scenario's scripts call must be declared."""
    plan = _plan(_without(DECLARATION, "records_export"))
    excluded = {e.scenario_id: e.reason for e in plan.excluded}
    calls_export = {
        s.scenario_id
        for s in PACK.scenarios()
        if any(
            step.get("call") == "records_export"
            for step in (*s.known_good, *s.known_bad)
        )
    }
    assert calls_export == {"TC3", "PC3", "PC4", "PC5", "RB5"}
    assert set(excluded) == calls_export
    assert set(excluded.values()) == {"undeclared_tool:records_export"}
    assert list(plan.selected) == [s for s in SCENARIO_IDS if s not in calls_export]


def test_two_undeclared_tools_are_both_named() -> None:
    plan = _plan(_without(DECLARATION, "records_export", "policy_search"))
    reasons = {e.scenario_id: e.reason for e in plan.excluded}
    assert reasons["TC3"] == "undeclared_tool:policy_search,records_export"


@pytest.mark.parametrize("level", ["human_monitors", "autonomous"])
def test_without_a_person_to_approve_the_approval_scenarios_are_out(level: str) -> None:
    """Rule 2: only ``human_approves`` keeps scenarios that script approvals.
    Every seed scenario sends a notice, so none is left."""
    plan = _plan(replace(DECLARATION, involvement=level))  # type: ignore[arg-type]
    assert plan.selected == ()
    assert {e.reason for e in plan.excluded} == {"no_approver"}
    assert [e.scenario_id for e in plan.excluded] == SCENARIO_IDS


def test_an_approval_free_scenario_stays_in_without_an_approver() -> None:
    pack = _Pack([_scenario("a", "task_execution", "erroneous_action", "look")])
    plan = _plan(_declaration(involvement="autonomous"), pack)
    assert plan.selected == ("a",)


def test_a_scenario_with_approvals_needs_the_approver() -> None:
    pack = _Pack(
        [
            _scenario(
                "a",
                "task_execution",
                "erroneous_action",
                "export",
                approvals={"export": ["approve"]},
            )
        ]
    )
    assert _plan(_declaration(), pack).selected == ("a",)
    plan = _plan(_declaration(involvement="autonomous"), pack)
    assert plan.selected == () and plan.excluded[0].reason == "no_approver"


def test_data_breach_needs_a_restricted_or_external_tool() -> None:
    """Rule 3: an outcome group the declared tools cannot reach is out."""
    pack = _Pack(
        [_scenario("leak", "policy_compliance", "data_breach", "look", "write")]
    )
    internal = (DeclaredTool("look", "read"), DeclaredTool("write", "write"))
    plan = _plan(_declaration(tools=internal), pack)
    assert plan.selected == ()
    assert plan.excluded[0].reason == "unreachable:data_breach"
    external = (
        DeclaredTool("look", "read"),
        DeclaredTool("write", "write", external=True),
    )
    assert _plan(_declaration(tools=external), pack).selected == ("leak",)
    # A declared data class must be the pack's (checked below), so the
    # restricted tool here is the one the pack models as restricted.
    restricted = (
        DeclaredTool("look", "read"),
        DeclaredTool("write", "write"),
        DeclaredTool("export", "irreversible", "restricted", reversible=False),
    )
    assert _plan(_declaration(tools=restricted), pack).selected == ("leak",)


def test_unauthorised_action_needs_a_write_or_irreversible_tool() -> None:
    pack = _Pack(
        [_scenario("over", "policy_compliance", "unauthorised_action", "look")]
    )
    plan = _plan(_declaration(tools=(DeclaredTool("look", "read"),)), pack)
    assert plan.excluded[0].reason == "unreachable:unauthorised_action"
    assert _plan(_declaration(), pack).selected == ("over",)


def test_the_rules_apply_in_order_and_one_reason_is_recorded() -> None:
    pack = _Pack(
        [
            _scenario(
                "x",
                "policy_compliance",
                "data_breach",
                "look",
                "export",
                approvals={"export": ["approve"]},
            )
        ]
    )
    only_look = _declaration(
        tools=(DeclaredTool("look", "read"),), involvement="autonomous"
    )
    assert _plan(only_look, pack).excluded[0].reason == "undeclared_tool:export"


def test_a_requested_subset_is_the_candidate_set() -> None:
    benchmark = AgenticBaselineBenchmark(PACK, scenarios=["RB1", "TE1"])
    plan = _plan(candidates=benchmark.scenarios(), requested=("RB1", "TE1"))
    assert plan.selected == ("TE1", "RB1")  # the pack's order
    assert plan.requested == ("RB1", "TE1")
    assert plan.excluded == ()


# ---------------------------------------------------------------------------
# The declaration against the pack's tool specs
# ---------------------------------------------------------------------------


def test_a_declared_permission_that_contradicts_the_pack_is_an_error() -> None:
    tools = tuple(
        replace(t, permission="write", reversible=True)
        if t.name == "send_notice"
        else t
        for t in DECLARATION.tools
    )
    with pytest.raises(ConfigurationError, match="send_notice.*write.*irreversible"):
        _plan(replace(DECLARATION, tools=tools))


def test_a_declared_data_class_that_contradicts_the_pack_is_an_error() -> None:
    tools = tuple(
        replace(t, data_class="normal") if t.name == "records_export" else t
        for t in DECLARATION.tools
    )
    with pytest.raises(ConfigurationError, match="records_export.*normal.*restricted"):
        _plan(replace(DECLARATION, tools=tools))


def test_a_declared_tool_the_pack_does_not_model_is_recorded_not_rejected() -> None:
    extra = DeclaredTool("send_sms", "irreversible", reversible=False, external=True)
    plan = _plan(replace(DECLARATION, tools=(*DECLARATION.tools, extra)))
    assert plan.uncovered_tools == ("send_sms",)
    assert list(plan.selected) == SCENARIO_IDS


# ---------------------------------------------------------------------------
# Thresholds: the pack's, tightened by the owner, with sources
# ---------------------------------------------------------------------------


def test_the_seed_pack_declares_a_threshold_per_category() -> None:
    assert isinstance(PACK, Thresholded)
    by_category = {t.category: t for t in PACK.thresholds()}
    assert set(by_category) == {s.category for s in PACK.scenarios()}
    for threshold in by_category.values():
        assert 0.0 <= threshold.min_pass_k <= 1.0
        assert threshold.gate in ("hard", "comparative")


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda d: d["thresholds"].pop("robustness"), "robustness"),
        (
            lambda d: d["thresholds"].__setitem__(
                "speed", {"min_pass_k": 1, "gate": "hard"}
            ),
            "speed",
        ),
        (
            lambda d: d["thresholds"]["robustness"].__setitem__("min_pass_k", 2),
            "min_pass_k",
        ),
        (lambda d: d["thresholds"]["robustness"].__setitem__("gate", "soft"), "gate"),
        (lambda d: d["thresholds"]["robustness"].pop("gate"), "gate"),
        (lambda d: d["thresholds"]["robustness"].__setitem__("extra", 1), "extra"),
        (lambda d: d.__setitem__("thresholds", []), "mapping"),
    ],
)
def test_the_pack_rejects_a_malformed_thresholds_file(
    tmp_path: Path, mutate: Any, match: str
) -> None:
    with pytest.raises(DomainPackError, match=match):
        SeedDomainPack(_edited(tmp_path, "thresholds.yaml", mutate))


def test_without_overrides_every_threshold_is_the_packs() -> None:
    plan = _plan()
    pack_thresholds = {t.category: t for t in PACK.thresholds()}
    assert {t.category for t in plan.thresholds} == set(pack_thresholds)
    for t in plan.thresholds:
        assert t.min_pass_k == pack_thresholds[t.category].min_pass_k
        assert t.gate == pack_thresholds[t.category].gate
        assert (t.min_pass_k_source, t.gate_source) == ("pack", "pack")


def test_an_owner_threshold_overrides_the_packs_and_is_recorded_with_its_source() -> (
    None
):
    """R27 exit criterion."""
    pack_robustness = next(t for t in PACK.thresholds() if t.category == "robustness")
    assert pack_robustness.gate == "comparative" and pack_robustness.min_pass_k < 0.95
    decl = replace(
        DECLARATION, thresholds=(ThresholdOverride("robustness", min_pass_k=0.95),)
    )
    robustness = next(t for t in _plan(decl).thresholds if t.category == "robustness")
    assert robustness.min_pass_k == 0.95 and robustness.min_pass_k_source == "owner"
    assert robustness.gate == "comparative" and robustness.gate_source == "pack"


def test_the_owner_may_make_a_comparative_threshold_a_hard_gate() -> None:
    decl = replace(
        DECLARATION, thresholds=(ThresholdOverride("robustness", gate="hard"),)
    )
    robustness = next(t for t in _plan(decl).thresholds if t.category == "robustness")
    assert robustness.gate == "hard" and robustness.gate_source == "owner"
    assert robustness.min_pass_k_source == "pack"


def test_the_owner_may_not_loosen_a_value() -> None:
    decl = replace(
        DECLARATION, thresholds=(ThresholdOverride("tool_calling", min_pass_k=0.5),)
    )
    with pytest.raises(ConfigurationError, match="loosen"):
        _plan(decl)


def test_the_owner_may_not_loosen_a_gate() -> None:
    decl = replace(
        DECLARATION, thresholds=(ThresholdOverride("tool_calling", gate="comparative"),)
    )
    with pytest.raises(ConfigurationError, match="loosen"):
        _plan(decl)


def test_setting_the_packs_own_value_counts_as_the_owners() -> None:
    pack_tc = next(t for t in PACK.thresholds() if t.category == "tool_calling")
    decl = replace(
        DECLARATION,
        thresholds=(ThresholdOverride("tool_calling", min_pass_k=pack_tc.min_pass_k),),
    )
    tc = next(t for t in _plan(decl).thresholds if t.category == "tool_calling")
    assert tc.min_pass_k_source == "owner" and tc.gate_source == "pack"


def test_a_pack_without_thresholds_takes_only_complete_owner_thresholds() -> None:
    pack = _Pack([_scenario("a", "task_execution", "erroneous_action", "look")])
    assert not isinstance(pack, Thresholded)
    assert _plan(_declaration(), pack).thresholds == ()
    complete = _declaration(
        thresholds=(ThresholdOverride("task_execution", 1.0, "hard"),)
    )
    (only,) = _plan(complete, pack).thresholds
    assert (only.min_pass_k, only.min_pass_k_source, only.gate, only.gate_source) == (
        1.0,
        "owner",
        "hard",
        "owner",
    )
    partial = _declaration(thresholds=(ThresholdOverride("task_execution", 1.0),))
    with pytest.raises(ConfigurationError, match="both"):
        _plan(partial, pack)


def test_a_thresholded_pack_may_cover_only_some_categories() -> None:
    pack = _ThresholdedPack(
        [_scenario("a", "task_execution", "erroneous_action", "look")],
        thresholds=[CategoryThreshold("task_execution", 0.8, "hard")],
    )
    (only,) = _plan(_declaration(), pack).thresholds
    assert only.category == "task_execution" and only.min_pass_k == 0.8


# ---------------------------------------------------------------------------
# The file: contents, hash, round trip
# ---------------------------------------------------------------------------


def test_the_plan_file_round_trips_and_hashes_its_bytes(tmp_path: Path) -> None:
    plan = _plan()
    digest = write_plan(tmp_path, plan)
    path = tmp_path / TEST_PLAN_FILENAME
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest() == plan_sha256(path)
    doc = read_plan(tmp_path)
    assert doc["plan_version"] == "2"
    assert doc["declared"] is True
    assert doc["selected"] == SCENARIO_IDS
    assert doc["repetitions"] == 3 and doc["seed"] == 11
    assert doc["seeds"]["TE1"] == [derive_seed(11, "TE1", r) for r in range(3)]
    assert doc["pack"] == {
        "pack_id": "seed",
        "pack_version": PACK.pack_version,
        "scenarios_sha256": plan.scenarios_sha256,
        "data_sha256": PACK.data_sha256,
    }
    assert {t["category"] for t in doc["thresholds"]} == {
        t.category for t in plan.thresholds
    }
    assert doc["system_sha256"] == DECLARATION.sha256


def test_the_declared_intended_use_and_business_metrics_appear_unchanged(
    tmp_path: Path,
) -> None:
    """R27 exit criterion (testing-framework 3.7.1): recorded, never evaluated."""
    write_plan(tmp_path, _plan())
    doc = read_plan(tmp_path)
    system = doc["system"]
    assert system["intended_use"] == DECLARATION.intended_use
    assert system["out_of_scope"] == list(DECLARATION.out_of_scope)
    assert system["business_metrics"] == [
        {"name": m.name, "definition": m.definition}
        for m in DECLARATION.business_metrics
    ]
    shipped = fast_yaml.safe_load(SHIPPED_SYSTEM.read_text(encoding="utf-8"))
    assert system["intended_use"] == shipped["intended_use"].strip()
    assert system["business_metrics"][0]["definition"] == (
        shipped["business_metrics"][0]["definition"].strip()
    )
    assert system["involvement"] == "human_approves"


def test_read_plan_on_a_directory_without_one() -> None:
    assert read_plan(Path("configs")) is None


# ---------------------------------------------------------------------------
# Through the CLI: written and hashed before the first task runs
# ---------------------------------------------------------------------------


def _cli_config(
    tmp_path: Path,
    mutate_run: Any = lambda d: None,
    mutate_system: Any = lambda d: None,
) -> Path:
    system = fast_yaml.safe_load(SHIPPED_SYSTEM.read_text(encoding="utf-8"))
    mutate_system(system)
    (tmp_path / "system.yaml").write_text(fast_yaml.safe_dump(system), encoding="utf-8")
    run = fast_yaml.safe_load(RUN_CONFIG.read_text(encoding="utf-8"))
    run["system"] = "system.yaml"
    mutate_run(run)
    path = tmp_path / "run.yaml"
    path.write_text(fast_yaml.safe_dump(run), encoding="utf-8")
    return path


def _run(config: Path, out: Path) -> Any:
    return CliRunner().invoke(
        app, ["run", "--config", str(config), "--output", str(out)]
    )


def test_a_seed_run_registers_its_plan(tmp_path: Path) -> None:
    out = tmp_path / "bundle"
    result = _run(_cli_config(tmp_path), out)
    assert result.exit_code == 0, result.output
    assert "Test plan registered" in result.output
    plan_path = out / TEST_PLAN_FILENAME
    assert plan_path.exists()
    metadata = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["test_plan_sha256"] == plan_sha256(plan_path)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["files"][TEST_PLAN_FILENAME]["sha256"] == plan_sha256(plan_path)
    # From format 0.6 every bundle has a plan (docs/adr/0007 D1, D2).
    assert TEST_PLAN_FILENAME in manifest["required_files"]
    assert manifest["bundle_format_version"] == "0.6"
    validation = validate_bundle(out)
    assert validation.valid, validation.errors
    doc = read_plan(out)
    assert doc is not None and doc["selected"] == SCENARIO_IDS
    assert doc["run_id"] == "agentic-baseline-mock-001"
    assert doc["config_name"].endswith("run.yaml")
    assert doc["system_path"] == "system.yaml"


def test_editing_the_plan_after_the_run_fails_validation(
    tmp_path: Path, reference_bundles: ReferenceBundles
) -> None:
    """R27 exit criterion, fail-first."""
    out = reference_bundles["seed"].copy(tmp_path / "bundle")
    plan_path = out / TEST_PLAN_FILENAME
    doc = json.loads(plan_path.read_text(encoding="utf-8"))
    doc["thresholds"][0]["min_pass_k"] = 0.0
    plan_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    result = validate_bundle(out)
    assert not result.valid
    assert any(TEST_PLAN_FILENAME in e for e in result.errors)
    # Rewriting the manifest to match the edit does not help: the hash the
    # run registered before its first task lives in metadata.json.
    write_manifest(out)
    result = validate_bundle(out)
    assert not result.valid
    assert any("after the plan was registered" in e for e in result.errors)
    cli = CliRunner().invoke(app, ["validate-bundle", "--bundle", str(out)])
    assert cli.exit_code != 0
    assert TEST_PLAN_FILENAME in cli.output


def test_removing_the_plan_after_the_run_fails_validation(
    tmp_path: Path, reference_bundles: ReferenceBundles
) -> None:
    out = reference_bundles["seed"].copy(tmp_path / "bundle")
    (out / TEST_PLAN_FILENAME).unlink()
    write_manifest(out)
    result = validate_bundle(out)
    assert not result.valid
    assert any("missing" in e and TEST_PLAN_FILENAME in e for e in result.errors)


def test_the_plan_is_on_disk_with_its_final_hash_before_the_first_task_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentevalops import cli as cli_module

    out = tmp_path / "bundle"
    seen: list[str] = []
    original = cli_module._build_runner

    def build(loaded: Any, benchmark: Any = None) -> Any:
        runner = original(loaded, benchmark)
        inner = runner.run

        async def run(agent_input: Any, limits: Any) -> Any:
            seen.append(plan_sha256(out / TEST_PLAN_FILENAME))
            return await inner(agent_input, limits)

        runner.run = run  # type: ignore[method-assign]
        return runner

    monkeypatch.setattr(cli_module, "_build_runner", build)
    assert _run(_cli_config(tmp_path), out).exit_code == 0
    metadata = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
    assert seen and set(seen) == {metadata["test_plan_sha256"]}


def test_the_run_does_not_start_when_the_plan_cannot_be_written(tmp_path: Path) -> None:
    out = tmp_path / "bundle"
    out.write_text("a file where the bundle directory should be", encoding="utf-8")
    result = _run(_cli_config(tmp_path), out)
    assert result.exit_code == 1
    assert TEST_PLAN_FILENAME in result.output and "does not start" in result.output
    assert out.is_file()


def test_a_stale_plan_in_the_output_directory_is_a_bundle_file(tmp_path: Path) -> None:
    out = tmp_path / "bundle"
    out.mkdir()
    (out / TEST_PLAN_FILENAME).write_text("{}", encoding="utf-8")
    result = _run(_cli_config(tmp_path), out)
    assert result.exit_code == 1
    assert "already contains" in result.output


def test_a_declaration_that_selects_nothing_does_not_start(tmp_path: Path) -> None:
    out = tmp_path / "bundle"
    config = _cli_config(
        tmp_path, mutate_system=lambda d: d.__setitem__("involvement", "autonomous")
    )
    result = _run(config, out)
    assert result.exit_code == 1
    assert "selects no scenario" in result.output
    assert not out.exists()


def test_a_contradicting_declaration_does_not_start(tmp_path: Path) -> None:
    def mutate(system: dict[str, Any]) -> None:
        for tool in system["tools"]:
            if tool["name"] == "records_export":
                tool["data_class"] = "normal"

    result = _run(_cli_config(tmp_path, mutate_system=mutate), tmp_path / "bundle")
    assert result.exit_code == 1 and "records_export" in result.output


def test_the_run_is_narrowed_to_the_selected_scenarios(tmp_path: Path) -> None:
    def mutate(system: dict[str, Any]) -> None:
        system["tools"] = [t for t in system["tools"] if t["name"] != "records_export"]

    out = tmp_path / "bundle"
    result = _run(_cli_config(tmp_path, mutate_system=mutate), out)
    assert result.exit_code == 0, result.output
    assert "21 of 26 scenarios selected" in result.output
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    ran = {r["task_id"] for r in summary["task_results"]}
    doc = read_plan(out)
    assert doc is not None
    assert ran == set(doc["selected"]) and len(ran) == 21
    assert {e["scenario_id"] for e in doc["excluded"]} == {
        "TC3",
        "PC3",
        "PC4",
        "PC5",
        "RB5",
    }


def test_a_toy_run_registers_the_undeclared_plan_and_validates(
    reference_bundles: ReferenceBundles,
) -> None:
    """docs/adr/0007 D2: a run without a declaration registers a plan that
    says so -- no system, no pack, no threshold -- selecting its tasks."""
    toy = reference_bundles["toy"]
    out = toy.path
    assert "no system declared" in toy.output
    plan_path = out / TEST_PLAN_FILENAME
    metadata = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["test_plan_sha256"] == plan_sha256(plan_path)
    doc = read_plan(out)
    assert doc is not None
    assert (doc["plan_version"], doc["declared"]) == ("2", False)
    assert doc["system"] is None and doc["system_path"] is None
    assert doc["pack"] is None and doc["system_sha256"] is None
    assert doc["selected"] == ["toy-001", "toy-002"]
    assert doc["thresholds"] == [] and doc["excluded"] == []
    assert validate_bundle(out).valid


def test_an_unregistered_plan_file_is_a_warning_not_an_error(tmp_path: Path) -> None:
    """Up to format 0.5 a plan was optional: one no run registered is only a
    warning. From 0.6 every bundle registers its plan."""
    import hashlib
    import shutil

    out = tmp_path / "bundle"
    shutil.copytree(LEGACY_0_5, out)
    meta_path = out / "metadata.json"
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    metadata["test_plan_sha256"] = None
    meta_path.write_text(json.dumps(metadata), encoding="utf-8")
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["metadata.json"] = {
        "size_bytes": meta_path.stat().st_size,
        "sha256": hashlib.sha256(meta_path.read_bytes()).hexdigest(),
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = validate_bundle(out)
    assert result.valid, result.errors
    assert any("not registered" in w for w in result.warnings)


def test_without_an_output_directory_the_plan_is_built_but_not_written(
    tmp_path: Path,
) -> None:
    result = CliRunner().invoke(app, ["run", "--config", str(_cli_config(tmp_path))])
    assert result.exit_code == 0, result.output
    assert "not written" in result.output
    config = _cli_config(
        tmp_path, mutate_system=lambda d: d.__setitem__("involvement", "autonomous")
    )
    assert CliRunner().invoke(app, ["run", "--config", str(config)]).exit_code == 1


def test_the_shipped_config_loads_and_its_benchmark_narrows() -> None:
    loaded = load_run_config(RUN_CONFIG)
    assert loaded.system is not None
    benchmark = AgenticBaselineBenchmark(PACK)
    narrowed = benchmark.narrowed(["RB1"])
    assert [s.scenario_id for s in narrowed.scenarios()] == ["RB1"]
    assert narrowed.pack is benchmark.pack
    assert isinstance(narrowed.pack, DomainPack)


def test_the_plan_document_reads_back_into_the_plan(tmp_path: Path) -> None:
    """``plan_from_document`` inverts ``to_document`` (docs/adr/0007 D15):
    a bundle's reader works from the plan as registered."""
    from agentevalops.plans import plan_from_document

    plan = _plan()
    write_plan(tmp_path, plan)
    doc = read_plan(tmp_path)
    assert doc is not None
    assert plan_from_document(doc) == plan


def test_a_version_1_plan_reads_back_as_declared() -> None:
    from agentevalops.plans import plan_from_document

    doc = json.loads((LEGACY_0_5 / TEST_PLAN_FILENAME).read_text(encoding="utf-8"))
    assert doc["plan_version"] == "1" and "declared" not in doc
    plan = plan_from_document(doc)
    assert plan.declared is True and plan.data_sha256 is None
    assert plan.system is not None and plan.selected == ("TE1", "TC1", "PC1")
