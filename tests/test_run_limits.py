"""The run-level budget (R35; ROADMAP §0, external review finding 9).

``ResourceLimits`` caps each task run; ``RunLimits`` caps the whole run's
spend, summed over its task runs. ``None`` means off. The caps come from
the run config, or -- like ``repetitions`` and ``seed`` -- from the system
declaration when there is one, never both. Unset, they are left out of
every record, so a run without a budget writes what it wrote before.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import fast_yaml
import pytest
from reference_bundles import (
    CARRIED_SCENARIOS,
    CONFIGS,
    ReferenceBundles,
    _carried_config,
    _registry,
    edited_seed_data,
)
from typer.testing import CliRunner

from agentevalops import registry as registry_module
from agentevalops.agents.mock_runner import MockAgentRunner
from agentevalops.bundles.manifest import write_manifest
from agentevalops.bundles.serializers import from_jsonable, to_jsonable
from agentevalops.bundles.validator import validate_bundle
from agentevalops.cli import app
from agentevalops.config.loader import load_run_config
from agentevalops.core.errors import ConfigurationError, InfrastructureError
from agentevalops.core.schemas import (
    AgentInput,
    ResourceLimits,
    RunBudget,
    RunConfig,
    RunLimits,
    RunSummary,
)
from agentevalops.core.types import AgentId, BackendId, RunId
from agentevalops.domain_packs.seed import SeedDomainPack
from agentevalops.plans.io import plan_from_document, to_document
from agentevalops.registry import KIND_DOMAIN_PACK, KIND_RUNNER

FIELDS = ("max_run_cost_usd", "max_run_tokens")


def _toy(tmp_path: Path, **changes: Any) -> Path:
    doc = fast_yaml.safe_load((CONFIGS / "toy_smoke.yaml").read_text("utf-8"))
    doc.update(changes)
    path = tmp_path / "toy.yaml"
    path.write_text(fast_yaml.safe_dump(doc), encoding="utf-8")
    return path


def _seed(tmp_path: Path, run: dict[str, Any], system: dict[str, Any]) -> Path:
    declared = fast_yaml.safe_load((CONFIGS / "seed_system.yaml").read_text("utf-8"))
    declared.update(system)
    (tmp_path / "system.yaml").write_text(
        fast_yaml.safe_dump(declared), encoding="utf-8"
    )
    doc = fast_yaml.safe_load(
        (CONFIGS / "agentic_baseline_mock.yaml").read_text("utf-8")
    )
    doc["system"] = "system.yaml"
    doc.update(run)
    path = tmp_path / "seed.yaml"
    path.write_text(fast_yaml.safe_dump(doc), encoding="utf-8")
    return path


def _config(tmp_path: Path, home: str, limits: Any) -> Path:
    if home == "run config":
        return _toy(tmp_path, run_limits=limits)
    return _seed(tmp_path, {}, {"run_limits": limits})


HOMES = ("run config", "system declaration")


# ---------------------------------------------------------------------------
# The two homes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("home", HOMES)
@pytest.mark.parametrize("name", FIELDS)
def test_a_non_numeric_cap_raises_configuration_error_naming_the_field(
    tmp_path: Path, home: str, name: str
) -> None:
    config = _config(tmp_path, home, {name: "lots"})
    with pytest.raises(ConfigurationError, match=f"run_limits.{name}"):
        load_run_config(config)


@pytest.mark.parametrize("home", HOMES)
def test_a_cap_is_read_from_either_home(tmp_path: Path, home: str) -> None:
    config = _config(tmp_path, home, {"max_run_cost_usd": 0.5, "max_run_tokens": 200})
    limits = load_run_config(config).run_config.run_limits
    assert limits == RunLimits(max_run_cost_usd=0.5, max_run_tokens=200)


@pytest.mark.parametrize("home", HOMES)
@pytest.mark.parametrize(
    ("limits", "message"),
    [
        ({"max_run_cost": 1.0}, "unknown key"),
        ({"max_run_cost_usd": 0}, "max_run_cost_usd must be"),
        ({"max_run_tokens": -5}, "max_run_tokens must be"),
    ],
)
def test_a_misspelt_or_non_positive_cap_is_refused(
    tmp_path: Path, home: str, limits: dict[str, Any], message: str
) -> None:
    """Fail-first: before R35 the run config ignored run_limits entirely,
    and system.yaml refused it as an unknown key."""
    with pytest.raises(ConfigurationError, match=message):
        load_run_config(_config(tmp_path, home, limits))


def test_a_cap_in_both_homes_is_refused(tmp_path: Path) -> None:
    config = _seed(
        tmp_path,
        {"run_limits": {"max_run_tokens": 100}},
        {"run_limits": {"max_run_tokens": 200}},
    )
    with pytest.raises(ConfigurationError, match="run_limits is set in the system"):
        load_run_config(config)


@pytest.mark.parametrize("limits", [None, {}, {"max_run_cost_usd": None}])
def test_an_empty_budget_is_off(tmp_path: Path, limits: Any) -> None:
    assert (
        load_run_config(_toy(tmp_path, run_limits=limits)).run_config.run_limits is None
    )


# ---------------------------------------------------------------------------
# Off changes nothing
# ---------------------------------------------------------------------------


def test_run_limits_default_to_off_and_change_no_existing_run(
    reference_bundles: ReferenceBundles, tmp_path: Path
) -> None:
    """No shipped config sets a budget, and with none every record is what
    it was: no run_limits in config.json or the plan, no run_budget in
    summary.json; a toy run with an explicitly empty budget has the same
    verdicts, byte for byte, as the shared toy run."""
    for name in ("toy_smoke.yaml", "agentic_baseline_mock.yaml"):
        assert load_run_config(CONFIGS / name).run_config.run_limits is None
    for kind in ("toy", "seed"):
        bundle = reference_bundles[kind].path
        assert "run_limits" not in (bundle / "config.json").read_text("utf-8")
        assert "run_limits" not in (bundle / "test_plan.json").read_text("utf-8")
        assert "run_budget" not in (bundle / "summary.json").read_text("utf-8")
        assert "Budget  :" not in reference_bundles[kind].output
        assert "- Run budget:" not in (bundle / "report.md").read_text("utf-8")
    out = tmp_path / "off"
    result = CliRunner().invoke(
        app,
        ["run", "--config", str(_toy(tmp_path, run_limits={})), "--output", str(out)],
    )
    assert result.exit_code == 0, result.output
    assert "Budget  :" not in result.output
    assert "- Run budget:" not in (out / "report.md").read_text("utf-8")
    shared = reference_bundles["toy"].path
    assert (out / "evaluations.json").read_bytes() == (
        shared / "evaluations.json"
    ).read_bytes()
    verdicts = ("passed_tasks", "failed_tasks", "not_evaluated_tasks", "pass_k")
    ours, theirs = (
        json.loads((b / "summary.json").read_text("utf-8")) for b in (out, shared)
    )
    assert {k: ours[k] for k in verdicts} == {k: theirs[k] for k in verdicts}


# ---------------------------------------------------------------------------
# The records, with and without a budget
# ---------------------------------------------------------------------------


def test_a_summary_round_trips_with_and_without_its_run_budget() -> None:
    plain = RunSummary(run_id=RunId("r"))
    assert "run_budget" not in to_jsonable(plain)
    assert from_jsonable(RunSummary, to_jsonable(plain)) == plain
    capped = RunSummary(
        run_id=RunId("r"),
        run_budget=RunBudget(
            cap_usd=0.5, cap_tokens=None, spent_usd=0.5, spent_tokens=0, tripped=True
        ),
    )
    data = to_jsonable(capped)
    assert data["run_budget"] == {
        "cap_usd": 0.5,
        "cap_tokens": None,
        "spent_usd": 0.5,
        "spent_tokens": 0,
        "tripped": True,
    }
    assert from_jsonable(RunSummary, data) == capped


def test_a_run_config_records_its_budget_only_when_it_has_one() -> None:
    config = RunConfig(
        run_id=RunId("r"), agent_id=AgentId("a"), backend_id=BackendId("local")
    )
    assert "run_limits" not in to_jsonable(config)
    capped = RunConfig(
        run_id=RunId("r"),
        agent_id=AgentId("a"),
        backend_id=BackendId("local"),
        run_limits=RunLimits(max_run_tokens=100),
    )
    data = to_jsonable(capped)
    assert data["run_limits"] == {"max_run_cost_usd": None, "max_run_tokens": 100}
    assert from_jsonable(RunConfig, data) == capped


def test_a_declared_budget_rides_the_plan(tmp_path: Path) -> None:
    from agentevalops.config.system import load_system_declaration
    from agentevalops.domain_packs.seed import SeedDomainPack
    from agentevalops.plans import build_plan

    pack = SeedDomainPack()
    for limits in (None, {"max_run_cost_usd": 0.5}):
        system = {} if limits is None else {"run_limits": limits}
        config = load_run_config(_seed(tmp_path, {}, system))
        assert config.system is not None
        plan = build_plan(
            load_system_declaration(tmp_path / "system.yaml"),
            pack,
            pack.scenarios(),
            run_config=config.run_config,
        )
        doc = to_document(plan)
        assert ("run_limits" in doc["system"]) is (limits is not None)
        assert plan_from_document(json.loads(json.dumps(doc))).system == plan.system


# ---------------------------------------------------------------------------
# Enforcement (R35 slice 2). The scripted mock reports the cost the config
# gives it per task run (mock_cost_per_task_usd) and no tokens; the toy's
# unscripted mock reports 10 tokens per task run.
# ---------------------------------------------------------------------------

FIVE = ["TE1", "TE3", "TC1", "PC5", "RB5"]
#: Exact in binary, so the spend lands on a cap of a whole number of task
#: runs exactly; the float-drift test covers a price that is not.
PRICE = 0.25


def _priced_seed(
    tmp: Path,
    name: str,
    limits: dict[str, Any] | None,
    *,
    price: float = PRICE,
    scenarios: list[str] | None = FIVE,
    **run: Any,
) -> Path:
    """A seed run at *price* a task run, over *scenarios* (None: the whole
    pack), its budget *limits* declared."""
    system = {} if limits is None else {"run_limits": limits}
    folder = tmp / name
    folder.mkdir()
    options: dict[str, Any] = {"pack": "seed"}
    if scenarios is not None:
        options["scenarios"] = scenarios
    config = _seed(
        folder,
        {
            "run_id": name,
            "mock_cost_per_task_usd": price,
            "benchmark_options": options,
            **run,
        },
        system,
    )
    out = tmp / f"{name}-bundle"
    result = CliRunner().invoke(
        app, ["run", "--config", str(config), "--output", str(out)]
    )
    assert result.exit_code == 0, result.output
    (out.parent / f"{name}.out").write_text(result.output, encoding="utf-8")
    return out


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """A five-scenario seed run at $0.25 a task run: capped at $0.50, and
    without a cap for comparison."""
    tmp = tmp_path_factory.mktemp("budget")
    return {
        "capped": _priced_seed(tmp, "capped", {"max_run_cost_usd": 2 * PRICE}),
        "uncapped": _priced_seed(tmp, "uncapped", None),
    }


def _results(bundle: Path) -> list[dict[str, Any]]:
    return json.loads((bundle / "summary.json").read_text("utf-8"))["task_results"]


def _lines(bundle: Path, name: str) -> list[dict[str, Any]]:
    text = (bundle / name).read_text("utf-8")
    return [json.loads(line) for line in text.splitlines() if line]


def test_a_capped_run_stops_scheduling_and_marks_the_rest_run_budget_exhausted(
    runs: dict[str, Path],
) -> None:
    """Five task runs, a cap sized for two: the first two run, the budget is
    then reached, and the other three never start."""
    bundle = runs["capped"]
    results = _results(bundle)
    assert [r["task_id"] for r in results] == FIVE
    started, stopped = results[:2], results[2:]
    assert all(r["evaluation"]["evaluated"] for r in started)
    for r in stopped:
        assert r["evaluation"]["not_evaluated_cause"] == "run_budget_exhausted"
        assert {e["not_evaluated_cause"] for e in r["evaluations"]} == {
            "run_budget_exhausted"
        }
    detail = "run budget reached before this task run started (cap $0.5000)"
    assert all(
        detail in e["notes"] for r in stopped for e in r["evaluations"]
    ), stopped[0]["evaluations"]
    never = {r["task_id"] for r in stopped}
    for stream in ("tool_calls.jsonl", "end_states.jsonl"):
        assert not never & {x["task_id"] for x in _lines(bundle, stream)}
    agent = {
        e["task_id"]
        for e in _lines(bundle, "traces.jsonl")
        if e["kind"] != "evaluator.score"
    }
    assert not never & agent
    summary = json.loads((bundle / "summary.json").read_text("utf-8"))
    assert summary["run_budget"] == {
        "cap_usd": 0.5,
        "cap_tokens": None,
        "spent_usd": 0.5,
        "spent_tokens": 0,
        "tripped": True,
    }
    assert summary["not_evaluated_by_cause"] == {"run_budget_exhausted": 3}
    line = "cap $0.5000; spent $0.5000 and 0 tokens; reached: 3 task run(s) not started"
    assert f"Budget  : {line}" in (bundle.parent / "capped.out").read_text("utf-8")
    assert f"- Run budget:    {line}" in (bundle / "report.md").read_text("utf-8")
    config = json.loads((bundle / "config.json").read_text("utf-8"))
    assert config["run_config"]["run_limits"] == {
        "max_run_cost_usd": 0.5,
        "max_run_tokens": None,
    }


def test_in_flight_runs_finish_before_the_stop(tmp_path: Path) -> None:
    """Two task runs at a time and a cap one run's cost reaches: both runs
    already going when the first lands finish and are judged; the run then
    starts nothing more."""
    bundle = _priced_seed(
        tmp_path, "inflight", {"max_run_cost_usd": PRICE}, max_concurrent_tasks=2
    )
    assert validate_bundle(bundle).errors == []
    results = _results(bundle)
    assert [r["evaluation"]["evaluated"] for r in results] == [
        True,
        True,
        False,
        False,
        False,
    ]
    assert {r["evaluation"]["not_evaluated_cause"] for r in results[2:]} == {
        "run_budget_exhausted"
    }
    budget = json.loads((bundle / "summary.json").read_text("utf-8"))["run_budget"]
    assert budget["spent_usd"] == 2 * PRICE > budget["cap_usd"]
    assert budget["tripped"] is True


def test_the_capped_bundle_validates_and_the_gate_fails_closed(
    runs: dict[str, Path],
) -> None:
    bundle = runs["capped"]
    assert validate_bundle(bundle).errors == []
    gate = CliRunner().invoke(app, ["gate", "--bundle", str(bundle)])
    assert gate.exit_code == 1, gate.output
    assert "GATE: FAIL" in gate.output
    assert "not evaluated (run_budget_exhausted); a hard gate fails closed" in (
        gate.output
    )


def test_a_spend_exactly_at_the_cap_reads_reached_with_no_run_stopped(
    tmp_path: Path,
) -> None:
    """Five task runs at $0.25 and a $1.25 cap: the fifth starts at $1.00,
    and the spend lands exactly on the cap with nothing left to start. The
    cap is reached, yet no task run was stopped, so the budget did not trip.
    Fail-first: the line said "not reached"."""
    bundle = _priced_seed(tmp_path, "exact", {"max_run_cost_usd": 5 * PRICE})
    assert validate_bundle(bundle).errors == []
    assert all(r["evaluation"]["evaluated"] for r in _results(bundle))
    budget = json.loads((bundle / "summary.json").read_text("utf-8"))["run_budget"]
    assert budget["spent_usd"] == budget["cap_usd"] == 5 * PRICE
    assert budget["tripped"] is False
    line = "cap $1.2500; spent $1.2500 and 0 tokens; reached; no task run stopped"
    assert f"Budget  : {line}" in (tmp_path / "exact.out").read_text("utf-8")
    assert f"- Run budget:    {line}" in (bundle / "report.md").read_text("utf-8")


def test_a_loose_cap_changes_no_verdict(runs: dict[str, Path], tmp_path: Path) -> None:
    loose = _priced_seed(tmp_path, "loose", {"max_run_cost_usd": 100.0})
    uncapped = runs["uncapped"]
    assert (loose / "evaluations.json").read_bytes() == (
        uncapped / "evaluations.json"
    ).read_bytes()
    summary = json.loads((loose / "summary.json").read_text("utf-8"))
    assert summary["run_budget"]["tripped"] is False
    assert summary["run_budget"]["spent_usd"] == 5 * PRICE
    assert "run_budget" not in (uncapped / "summary.json").read_text("utf-8")


def test_a_token_cap_trips_independently_of_the_cost_cap(tmp_path: Path) -> None:
    """Toy runs report 10 tokens each and cost nothing: a 20-token cap stops
    the run after two task runs while its cost cap is nowhere near."""
    config = _toy(
        tmp_path,
        repetitions=5,
        run_limits={"max_run_tokens": 20, "max_run_cost_usd": 100.0},
    )
    out = tmp_path / "tokens"
    result = CliRunner().invoke(
        app, ["run", "--config", str(config), "--output", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert validate_bundle(out).errors == []
    results = _results(out)
    assert [r["evaluation"]["evaluated"] for r in results] == [True, True] + [False] * 8
    assert [r["repetition"] for r in results] == [0, 1, 2, 3, 4] * 2
    assert {r["evaluation"]["not_evaluated_cause"] for r in results[2:]} == {
        "run_budget_exhausted"
    }
    budget = json.loads((out / "summary.json").read_text("utf-8"))["run_budget"]
    assert budget == {
        "cap_usd": 100.0,
        "cap_tokens": 20,
        "spent_usd": 0.0,
        "spent_tokens": 20,
        "tripped": True,
    }
    line = (
        "cap $100.0000, 20 tokens; spent $0.0000 and 20 tokens; "
        "reached: 8 task run(s) not started"
    )
    assert f"Budget  : {line}" in result.output
    assert f"- Run budget:    {line}" in (out / "report.md").read_text("utf-8")
    detail = (
        "run budget reached before this task run started (cap $100.0000, 20 tokens)"
    )
    assert all(detail in e["notes"] for r in results[2:] for e in r["evaluations"])
    rescore = CliRunner().invoke(app, ["rescore", "--bundle", str(out)])
    assert rescore.exit_code == 0, rescore.output
    assert rescore.output.rstrip().endswith("RESCORE: MATCH")

    def higher(config: dict[str, Any]) -> None:
        config["run_config"]["run_limits"]["max_run_tokens"] = 21

    errors = _forge(out, tmp_path, {"config.json": higher})
    assert _fits(errors, "finished ($0.0000, 20 tokens) reaches none of config.json's")


def test_a_token_only_cap_reached_exactly_stops_nothing(tmp_path: Path) -> None:
    """A token cap and no cost cap: four toy task runs at 10 tokens reach a
    40-token cap exactly, with nothing left to start."""
    config = _toy(tmp_path, repetitions=2, run_limits={"max_run_tokens": 40})
    out = tmp_path / "tokens-only"
    result = CliRunner().invoke(
        app, ["run", "--config", str(config), "--output", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert validate_bundle(out).errors == []
    assert all(r["evaluation"]["evaluated"] for r in _results(out))
    line = "cap 40 tokens; spent $0.0000 and 40 tokens; reached; no task run stopped"
    assert f"Budget  : {line}" in result.output
    assert f"- Run budget:    {line}" in (out / "report.md").read_text("utf-8")


def test_rescore_on_a_capped_bundle_reports_match(runs: dict[str, Path]) -> None:
    """The task runs the budget stopped are carried as recorded (ADR-0008
    R6), and the budget is derived again from the summary's totals."""
    result = CliRunner().invoke(app, ["rescore", "--bundle", str(runs["capped"])])
    assert result.exit_code == 0, result.output
    assert result.output.rstrip().endswith("RESCORE: MATCH")
    assert "Files:    every recomputed file is byte-identical" in result.output
    for task_id in FIVE[2:]:
        assert (
            f"task run {task_id}/0, stopped before the agent finished "
            "(run_budget_exhausted): its recorded result"
        ) in result.output


def test_float_drift_does_not_let_a_run_past_the_cap(tmp_path: Path) -> None:
    """Ten task runs at $0.10 add up to 0.9999999999999999 in floats: a
    $1.00 cap is reached after the tenth, and the eleventh does not start.
    Fail-first: the eleventh started, and the line said $1.1000."""
    bundle = _priced_seed(
        tmp_path, "drift", {"max_run_cost_usd": 1.0}, price=0.1, scenarios=None
    )
    assert validate_bundle(bundle).errors == []
    evaluated = [r["evaluation"]["evaluated"] for r in _results(bundle)]
    assert evaluated == [True] * 10 + [False] * 16
    line = (
        "cap $1.0000; spent $1.0000 and 0 tokens; "
        "reached: 16 task run(s) not started"
    )
    assert f"Budget  : {line}" in (tmp_path / "drift.out").read_text("utf-8")


def test_a_defective_scenario_keeps_its_cause_under_a_tripped_budget(
    tmp_path: Path,
) -> None:
    """A scenario that failed its preflight is stopped for that, whether or
    not the budget would have stopped it: RB5, last, is defective in an
    edited pack, and the budget is reached after the first task run."""

    def break_rb5(doc: dict[str, Any]) -> None:
        for scenario in doc["scenarios"]:
            if scenario["scenario_id"] == "RB5":
                scenario["known_good"], scenario["known_bad"] = (
                    scenario["known_bad"],
                    scenario["known_good"],
                )

    pack = "seed-rb5-broken"
    data = edited_seed_data(tmp_path, "scenarios.yaml", break_rb5)
    fresh = registry_module.build_default_registry()
    fresh.register(KIND_DOMAIN_PACK, pack, lambda: SeedDomainPack(data))
    config = _carried_config(tmp_path, "defect", pack)
    system_path = tmp_path / "defect-system.yaml"
    system = fast_yaml.safe_load(system_path.read_text("utf-8"))
    system["run_limits"] = {"max_run_cost_usd": PRICE}
    system_path.write_text(fast_yaml.safe_dump(system), encoding="utf-8")
    doc = fast_yaml.safe_load(config.read_text("utf-8"))
    doc["mock_cost_per_task_usd"] = PRICE
    config.write_text(fast_yaml.safe_dump(doc), encoding="utf-8")
    out = tmp_path / "defect-bundle"
    with _registry(fresh):
        result = CliRunner().invoke(
            app, ["run", "--config", str(config), "--output", str(out)]
        )
    assert result.exit_code == 0, result.output
    assert validate_bundle(out).errors == []
    results = _results(out)
    assert [r["task_id"] for r in results] == CARRIED_SCENARIOS
    assert [r["evaluation"]["not_evaluated_cause"] for r in results] == [
        None,
        "run_budget_exhausted",
        "run_budget_exhausted",
        "environment_defect",
    ]
    assert "reached: 2 task run(s) not started" in result.output


def test_a_budget_that_stops_only_comparative_scenarios_passes_the_gate(
    tmp_path: Path,
) -> None:
    """The gate is unchanged: a hard-gated scenario not run fails it closed,
    and a comparative category does not gate, whatever stopped its
    scenarios. The seed pack orders the eight robustness scenarios last; a
    cap of eighteen task runs stops just those, and the gate passes."""
    bundle = _priced_seed(
        tmp_path, "comparative", {"max_run_cost_usd": 18 * PRICE}, scenarios=None
    )
    stopped = [
        r["task_id"] for r in _results(bundle) if not r["evaluation"]["evaluated"]
    ]
    assert stopped == [f"RB{n}" for n in range(1, 9)]
    gate = CliRunner().invoke(app, ["gate", "--bundle", str(bundle)])
    assert gate.exit_code == 0, gate.output
    assert "GATE: PASS" in gate.output
    assert "does not gate" in gate.output


# ---------------------------------------------------------------------------
# validate-bundle holds a budget stop to its records (ADR-0008 R6): rescore
# carries it as recorded, so each rule has an edit it catches, on a copy.
# Fail-first: each edit below validated before the rules existed.
# ---------------------------------------------------------------------------

STOPPED = "TC1"


def _forge(
    source: Path, tmp_path: Path, changes: dict[str, Callable[[Any], Any]]
) -> list[str]:
    bundle = tmp_path / "forged"
    shutil.copytree(source, bundle)
    for file, change in changes.items():
        path = bundle / file
        if file.endswith(".jsonl"):
            rows = change(_lines(bundle, file))
            path.write_text("".join(json.dumps(x) + "\n" for x in rows), "utf-8")
        else:
            doc = json.loads(path.read_text("utf-8"))
            change(doc)
            path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    write_manifest(bundle)
    return validate_bundle(bundle).errors


def _fits(errors: list[str], message: str) -> bool:
    return any("carried result does not fit" in e and message in e for e in errors)


def _stopped(summary: dict[str, Any], task_id: str = STOPPED) -> dict[str, Any]:
    (run,) = [r for r in summary["task_results"] if r["task_id"] == task_id]
    return run


def test_a_budget_stopped_run_with_a_tool_call_and_an_end_state(
    runs: dict[str, Path], tmp_path: Path
) -> None:
    def call(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [*rows, {**rows[0], "task_id": STOPPED, "repetition": 0}]

    def state(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [*rows, {"task_id": STOPPED, "repetition": 0, "state": {}}]

    errors = _forge(
        runs["capped"],
        tmp_path,
        {"tool_calls.jsonl": call, "end_states.jsonl": state},
    )
    assert _fits(errors, f"at {STOPPED}/0: stopped by the run's budget before")
    assert _fits(errors, "has tool calls, an end state for it")


def test_a_budget_stopped_run_with_an_agent_event(
    runs: dict[str, Path], tmp_path: Path
) -> None:
    def event(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        first = next(e for e in rows if e["kind"] == "agent.plan")
        return [*rows, {**first, "task_id": STOPPED, "repetition": 0}]

    errors = _forge(runs["capped"], tmp_path, {"traces.jsonl": event})
    assert _fits(errors, "has agent trace events for it")


def test_a_budget_stop_that_one_evaluator_evaluated(
    runs: dict[str, Path], tmp_path: Path
) -> None:
    """PC5, stopped, has three evaluators: one of them marked evaluated."""

    def one(summary: dict[str, Any]) -> None:
        entry = _stopped(summary, "PC5")["evaluations"][0]
        entry["evaluated"], entry["not_evaluated_cause"] = True, None

    errors = _forge(runs["capped"], tmp_path, {"summary.json": one})
    assert _fits(errors, "at PC5/0: not evaluated (run_budget_exhausted) by some")


def test_a_budget_stop_whose_output_says_otherwise(
    runs: dict[str, Path], tmp_path: Path
) -> None:
    def completed(summary: dict[str, Any]) -> None:
        _stopped(summary)["output"]["termination_reason"] = "completed"

    errors = _forge(runs["capped"], tmp_path, {"summary.json": completed})
    assert _fits(
        errors,
        "not evaluated (run_budget_exhausted), but its output's "
        "termination_reason is 'completed'",
    )


def test_a_budget_stop_in_a_run_without_a_budget(
    runs: dict[str, Path], tmp_path: Path
) -> None:
    """The cap and the budget struck from the records, the stopped runs
    kept: the stop has no reason left in the bundle."""

    def no_limits(config: dict[str, Any]) -> None:
        del config["run_config"]["run_limits"]

    def no_budget(summary: dict[str, Any]) -> None:
        del summary["run_budget"]

    errors = _forge(
        runs["capped"],
        tmp_path,
        {"config.json": no_limits, "summary.json": no_budget},
    )
    assert _fits(
        errors,
        f"at {STOPPED}/0: stopped by the run's budget, but config.json "
        "records no run_limits",
    )


def test_a_budget_stop_below_a_raised_cap(
    runs: dict[str, Path], tmp_path: Path
) -> None:
    """The cap raised to $100 where the run spent $0.50: the recorded spend
    reaches no cap, so nothing could have stopped the run."""

    def raised(config: dict[str, Any]) -> None:
        config["run_config"]["run_limits"]["max_run_cost_usd"] = 100.0

    def budget(summary: dict[str, Any]) -> None:
        summary["run_budget"]["cap_usd"] = 100.0

    errors = _forge(
        runs["capped"], tmp_path, {"config.json": raised, "summary.json": budget}
    )
    assert _fits(
        errors,
        f"at {STOPPED}/0: stopped by the run's budget, but the spend of the task "
        "runs that finished ($0.5000, 0 tokens) reaches none of config.json's caps",
    )


def test_a_budget_stop_justified_by_a_lifted_total(
    runs: dict[str, Path], tmp_path: Path
) -> None:
    """The cap raised to $0.90 and summary.json's total lifted to match: the
    finished runs' outputs, tied to their agent.terminal events, still say
    $0.50. Fail-first: the lifted total vouched for the stop."""

    def raised(config: dict[str, Any]) -> None:
        config["run_config"]["run_limits"]["max_run_cost_usd"] = 0.9

    def lifted(summary: dict[str, Any]) -> None:
        summary["total_cost_usd"] = 0.9
        summary["run_budget"]["cap_usd"] = summary["run_budget"]["spent_usd"] = 0.9

    errors = _forge(
        runs["capped"], tmp_path, {"config.json": raised, "summary.json": lifted}
    )
    assert _fits(
        errors,
        "the spend of the task runs that finished ($0.5000, 0 tokens) reaches "
        "none of config.json's caps",
    )


def test_a_budget_stopped_run_whose_output_records_spend(
    runs: dict[str, Path], tmp_path: Path
) -> None:
    """A run that never started spent nothing: $0.40 written into a stopped
    run's carried output, the totals to match, is refused. Fail-first: it
    validated, and rescore carried the $0.40 into a MATCH."""

    def spent(summary: dict[str, Any]) -> None:
        _stopped(summary)["output"]["total_cost_usd"] = 0.4
        summary["total_cost_usd"] = 0.9
        summary["run_budget"]["spent_usd"] = 0.9

    errors = _forge(runs["capped"], tmp_path, {"summary.json": spent})
    assert _fits(
        errors,
        f"at {STOPPED}/0: stopped by the run's budget before it started, but its "
        "output records spend ($0.4000, 0 tokens)",
    )


def test_a_budget_stopped_run_whose_output_records_tokens(
    runs: dict[str, Path], tmp_path: Path
) -> None:
    """Tokens alone are spend too."""

    def tokens(summary: dict[str, Any]) -> None:
        _stopped(summary)["output"]["total_tokens"] = 5
        summary["total_tokens"] = 5
        summary["run_budget"]["spent_tokens"] = 5

    errors = _forge(runs["capped"], tmp_path, {"summary.json": tokens})
    assert _fits(
        errors,
        f"at {STOPPED}/0: stopped by the run's budget before it started, but its "
        "output records spend ($0.0000, 5 tokens)",
    )


def test_a_finished_run_no_evaluator_judged_counts_toward_the_budget(
    tmp_path: Path,
) -> None:
    """Only the deterministic evaluator configured: TE3 declares none of it,
    so it finishes, spends $0.25, and nothing judges it (configuration). Its
    spend is a finished run's, and with TE1's reaches the $0.50 cap: the
    honest bundle validates and rescores as MATCH."""
    bundle = _priced_seed(
        tmp_path,
        "unjudged",
        {"max_run_cost_usd": 2 * PRICE},
        evaluators=["deterministic"],
    )
    results = _results(bundle)
    budget = {"run_budget_exhausted"}
    assert [{e["not_evaluated_cause"] for e in r["evaluations"]} for r in results] == [
        {None, "configuration"},
        {"configuration"},
        budget,
        budget,
        budget,
    ]
    unjudged = results[1]
    assert not any(e["evaluated"] for e in unjudged["evaluations"])
    assert unjudged["output"]["termination_reason"] == "completed"
    assert unjudged["output"]["total_cost_usd"] == PRICE
    assert validate_bundle(bundle).errors == []
    rescore = CliRunner().invoke(app, ["rescore", "--bundle", str(bundle)])
    assert rescore.exit_code == 0, rescore.output
    assert rescore.output.rstrip().endswith("RESCORE: MATCH")


class _TE3SandboxFails(MockAgentRunner):
    """The mock, except that TE3's sandbox goes away before the agent runs."""

    async def run(self, input: AgentInput, limits: ResourceLimits) -> Any:
        if str(input.task.task_id) == "TE3":
            raise InfrastructureError("the sandbox went away")
        return await super().run(input, limits)


def test_spend_forged_onto_an_infrastructure_stop_justifies_no_budget_stop(
    tmp_path: Path,
) -> None:
    """TE1 and TC1 finish at $0.25 each, TE3's infrastructure fails between
    them, and the $0.50 cap stops PC5 and RB5. $0.40 written onto the
    infrastructure stop, the cap raised to $0.90 and the totals to match:
    only the runs that finished count, and they spent $0.50."""
    fresh = registry_module.build_default_registry()
    fresh.register(KIND_RUNNER, "mock", _TE3SandboxFails, replace=True)
    with _registry(fresh):
        bundle = _priced_seed(tmp_path, "infra", {"max_run_cost_usd": 2 * PRICE})
    assert [r["evaluation"]["not_evaluated_cause"] for r in _results(bundle)] == [
        None,
        "infrastructure",
        None,
        "run_budget_exhausted",
        "run_budget_exhausted",
    ]
    assert validate_bundle(bundle).errors == []

    def raised(config: dict[str, Any]) -> None:
        config["run_config"]["run_limits"]["max_run_cost_usd"] = 0.9

    def spent(summary: dict[str, Any]) -> None:
        _stopped(summary, "TE3")["output"]["total_cost_usd"] = 0.4
        summary["total_cost_usd"] = 0.9
        summary["run_budget"]["cap_usd"] = summary["run_budget"]["spent_usd"] = 0.9

    errors = _forge(bundle, tmp_path, {"config.json": raised, "summary.json": spent})
    assert _fits(
        errors,
        "at PC5/0: stopped by the run's budget, but the spend of the task runs "
        "that finished ($0.5000, 0 tokens) reaches none of config.json's caps",
    )
