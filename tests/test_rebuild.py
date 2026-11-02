"""Rebuilding a bundle's run from its config.json and test_plan.json
(replay/rebuild.py; ADR-0008 R1; R29 (b1) slice 3).

The rebuilt tasks must equal the tasks the run used -- captured here from
the orchestrator during the run itself -- for a toy run, a full seed run, a
known-bad run and a narrowed seed run. What cannot be rebuilt is a
RebuildError, which rescore turns into "cannot rescore" (exit 2).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import fast_yaml
import pytest
from typer.testing import CliRunner

from agentevalops.cli import app
from agentevalops.core.schemas import TaskSpec
from agentevalops.orchestration.local import LocalOrchestrator
from agentevalops.replay.rebuild import RebuildError, rebuild_run

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "configs/agentic_baseline_mock.yaml"


def _seed_config(tmp: Path, name: str, **changes: Any) -> Path:
    doc = fast_yaml.safe_load(SEED.read_text("utf-8"))
    doc["system"] = str(ROOT / "configs/seed_system.yaml")
    for key, value in changes.items():
        if key == "scenarios":
            doc["benchmark_options"]["scenarios"] = value
        else:
            doc[key] = value
    path = tmp / f"{name}.yaml"
    path.write_text(fast_yaml.safe_dump(doc), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def runs(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, tuple[Path, list[TaskSpec]]]:
    """Each run's bundle and the tasks its orchestrator actually ran."""
    tmp = tmp_path_factory.mktemp("rebuild")
    configs = {
        "toy": ROOT / "configs/toy_smoke.yaml",
        "seed": SEED,
        "known-bad": _seed_config(tmp, "bad", mock_trajectory="known_bad"),
        "narrowed": _seed_config(tmp, "narrow", scenarios=["TE1", "RB8", "PC7"]),
    }
    seen: list[list[TaskSpec]] = []
    original = LocalOrchestrator.run

    async def spy(self: LocalOrchestrator) -> Any:
        seen.append(list(self._benchmark.list_tasks()))
        return await original(self)

    out: dict[str, tuple[Path, list[TaskSpec]]] = {}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(LocalOrchestrator, "run", spy)
        for name, config in configs.items():
            bundle = tmp / name
            result = CliRunner().invoke(
                app, ["run", "--config", str(config), "--output", str(bundle)]
            )
            assert result.exit_code == 0, result.output
            out[name] = (bundle, seen[-1])
    return out


def _docs(bundle: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        json.loads((bundle / "config.json").read_text("utf-8")),
        json.loads((bundle / "test_plan.json").read_text("utf-8")),
    )


@pytest.mark.parametrize("name", ["toy", "seed", "known-bad", "narrowed"])
def test_the_rebuilt_tasks_are_the_tasks_the_run_used(
    runs: dict[str, tuple[Path, list[TaskSpec]]], name: str
) -> None:
    bundle, used = runs[name]
    rebuilt = rebuild_run(*_docs(bundle))
    assert list(rebuilt.tasks) == used
    assert [str(t.task_id) for t in rebuilt.tasks] == list(rebuilt.plan.selected)


def test_a_narrowed_run_still_describes_the_whole_pack(
    runs: dict[str, tuple[Path, list[TaskSpec]]],
) -> None:
    bundle, _ = runs["narrowed"]
    rebuilt = rebuild_run(*_docs(bundle))
    assert [str(t.task_id) for t in rebuilt.tasks] == ["TE1", "PC7", "RB8"]
    assert len(rebuilt.descriptors) == 26
    controls = json.loads((bundle / "controls.json").read_text("utf-8"))
    assert list(controls["scenarios"]) == list(rebuilt.descriptors)
    assert set(rebuilt.scenario_info) == {"TE1", "PC7", "RB8"}


def test_the_toy_run_rebuilds_without_a_pack(
    runs: dict[str, tuple[Path, list[TaskSpec]]],
) -> None:
    rebuilt = rebuild_run(*_docs(runs["toy"][0]))
    assert rebuilt.plan.declared is False
    assert list(rebuilt.descriptors) == ["toy-001", "toy-002"]
    assert rebuilt.scenario_info == {}


# What cannot be rebuilt -- each a RebuildError, rescore's exit 2.


def test_a_benchmark_that_is_not_installed(
    runs: dict[str, tuple[Path, list[TaskSpec]]],
) -> None:
    config, plan = _docs(runs["toy"][0])
    config["benchmark_id"] = "no-such-benchmark"
    with pytest.raises(RebuildError, match="'no-such-benchmark' .* is not installed"):
        rebuild_run(config, plan)


def test_a_domain_pack_that_is_not_installed(
    runs: dict[str, tuple[Path, list[TaskSpec]]],
) -> None:
    config, plan = _docs(runs["seed"][0])
    config["benchmark_options"]["pack"] = "no-such-pack"
    with pytest.raises(RebuildError, match="not installed"):
        rebuild_run(config, plan)


def test_options_the_benchmark_refuses(
    runs: dict[str, tuple[Path, list[TaskSpec]]],
) -> None:
    config, plan = _docs(runs["toy"][0])
    config["benchmark_options"]["unknown_option"] = 1
    with pytest.raises(RebuildError, match="does not accept"):
        rebuild_run(config, plan)


def test_a_selected_scenario_the_installed_pack_lacks(
    runs: dict[str, tuple[Path, list[TaskSpec]]],
) -> None:
    config, plan = _docs(runs["narrowed"][0])
    plan["selected"] = ["TE1", "ZZ9"]
    with pytest.raises(RebuildError, match="lacks a selected scenario"):
        rebuild_run(config, plan)


def test_tasks_that_are_not_the_plans(
    runs: dict[str, tuple[Path, list[TaskSpec]]],
) -> None:
    config, plan = _docs(runs["toy"][0])
    plan["selected"] = ["toy-001"]
    with pytest.raises(RebuildError, match="are not the plan's"):
        rebuild_run(config, plan)


def test_seeds_that_are_not_the_runs(
    runs: dict[str, tuple[Path, list[TaskSpec]]],
) -> None:
    config, plan = _docs(runs["seed"][0])
    plan["seeds"]["TE1"] = [12345]
    with pytest.raises(RebuildError, match="seeds for 'TE1'"):
        rebuild_run(config, plan)
    config, plan = _docs(runs["seed"][0])
    config["run_config"]["seed"] = plan["seed"] + 1
    with pytest.raises(RebuildError, match="is not the plan's"):
        rebuild_run(config, plan)


def test_an_unreadable_config(runs: dict[str, tuple[Path, list[TaskSpec]]]) -> None:
    config, plan = _docs(runs["toy"][0])
    config["run_config"]["surprise"] = 1
    with pytest.raises(RebuildError, match="cannot be read"):
        rebuild_run(config, plan)
