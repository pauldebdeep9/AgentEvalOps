"""The run-level budget (R35; ROADMAP §0, external review finding 9).

``ResourceLimits`` caps each task run; ``RunLimits`` caps the whole run's
spend, summed over its task runs. ``None`` means off. The caps come from
the run config, or -- like ``repetitions`` and ``seed`` -- from the system
declaration when there is one, never both. Unset, they are left out of
every record, so a run without a budget writes what it wrote before.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import fast_yaml
import pytest
from reference_bundles import CONFIGS, ReferenceBundles
from typer.testing import CliRunner

from agentevalops.bundles.serializers import from_jsonable, to_jsonable
from agentevalops.cli import app
from agentevalops.config.loader import load_run_config
from agentevalops.core.errors import ConfigurationError
from agentevalops.core.schemas import (
    RunBudget,
    RunConfig,
    RunLimits,
    RunSummary,
)
from agentevalops.core.types import AgentId, BackendId, RunId
from agentevalops.plans.io import plan_from_document, to_document

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
    out = tmp_path / "off"
    result = CliRunner().invoke(
        app,
        ["run", "--config", str(_toy(tmp_path, run_limits={})), "--output", str(out)],
    )
    assert result.exit_code == 0, result.output
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
