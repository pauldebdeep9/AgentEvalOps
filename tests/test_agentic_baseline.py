"""The agentic-baseline benchmark and the known-good preflight (docs/adr/0006
D16, D23; R24 slice 3). Uses a test-only pack; the seed domain is slice 4."""

from __future__ import annotations

import asyncio
import json
import pathlib
from typing import Any

import pytest
from typer.testing import CliRunner

from agentevalops import registry as registry_module
from agentevalops.agents.mock_runner import MockAgentRunner
from agentevalops.benchmarks.agentic_baseline import AgenticBaselineBenchmark
from agentevalops.benchmarks.toy.adapter import ToyBenchmarkAdapter
from agentevalops.bundles.validator import validate_bundle
from agentevalops.cli import app
from agentevalops.core.errors import ConfigurationError
from agentevalops.core.protocols import DomainPack, KnownGoodSource, ToolEnvironment
from agentevalops.core.schemas import (
    PreflightResult,
    ResourceLimits,
    RunConfig,
    RunSummary,
    Scenario,
)
from agentevalops.core.types import BACKEND_LOCAL, AgentId, RunId, TaskId
from agentevalops.environments import toy_environment
from agentevalops.evaluators.deterministic import DeterministicEvaluator
from agentevalops.orchestration.local import LocalOrchestrator
from agentevalops.policy.basic_checker import BasicPolicyChecker
from agentevalops.registry import KIND_BENCHMARK, KIND_DOMAIN_PACK
from agentevalops.stores.memory import InMemoryTraceStore

_SOLVE = (
    {"call": "calculator", "arguments": {"expression": "2 + 2"}},
    {"finish": "4"},
)


class _TestPack:
    pack_id = "test-pack"
    pack_version = "0.0.1"

    def scenarios(self) -> list[Scenario]:
        expected = {"expected_output": "4", "match_mode": "exact"}
        return [
            Scenario(
                scenario_id="add",
                description="Add two and two.",
                category="task_execution",
                known_good=_SOLVE,
                known_bad=({"finish": "5"},),
                applicable_evaluators=("deterministic",),
                control_ids=("mgf-agentic:2.3.2",),
                expected=expected,
            ),
            Scenario(
                scenario_id="broken",
                description="A scenario whose known-good is wrong.",
                category="task_execution",
                known_good=({"finish": "5"},),
                applicable_evaluators=("deterministic",),
                expected=expected,
            ),
        ]

    def build_environment(self, scenario: Scenario, task_id: TaskId) -> ToolEnvironment:
        return toy_environment(task_id)


class _SpyRunner:
    """The 'agent': runs each scenario's known-good, and counts its calls."""

    agent_id = AgentId("spy")

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._inner = MockAgentRunner(scripts={"add": _SOLVE, "broken": _SOLVE})

    async def run(self, input, resource_limits):  # type: ignore[no-untyped-def]
        self.calls.append(str(input.task.task_id))
        return await self._inner.run(input, resource_limits)


def _run(
    benchmark: Any, runner: Any, repetitions: int = 1
) -> tuple[RunSummary, LocalOrchestrator]:
    orch = LocalOrchestrator(
        run_config=RunConfig(
            run_id=RunId("ab"),
            agent_id=AgentId("spy"),
            backend_id=BACKEND_LOCAL,
            resource_limits=ResourceLimits(),
            repetitions=repetitions,
        ),
        benchmark=benchmark,
        runner=runner,
        trace_store=InMemoryTraceStore(),
        evaluator=DeterministicEvaluator(),
        policy_checker=BasicPolicyChecker(),
    )
    return asyncio.run(orch.run()), orch


# ---------------------------------------------------------------------------
# The benchmark
# ---------------------------------------------------------------------------


def test_one_task_per_scenario_carrying_its_declarations() -> None:
    benchmark = AgenticBaselineBenchmark(_TestPack())
    tasks = {str(t.task_id): t for t in benchmark.list_tasks()}
    assert list(tasks) == ["add", "broken"]
    add = tasks["add"]
    assert add.benchmark_id == "agentic-baseline"
    assert add.applicable_evaluators == ("deterministic",)
    assert add.metadata["expected_output"] == "4"
    assert add.metadata["control_ids"] == ["mgf-agentic:2.3.2"]
    assert (add.metadata["pack_id"], add.metadata["category"]) == (
        "test-pack",
        "task_execution",
    )


def test_scenarios_option_limits_the_run_and_unknown_ids_are_refused() -> None:
    only = AgenticBaselineBenchmark(_TestPack(), scenarios=["add"])
    assert [str(t.task_id) for t in only.list_tasks()] == ["add"]
    with pytest.raises(ConfigurationError, match="no scenario"):
        AgenticBaselineBenchmark(_TestPack(), scenarios=["nope"])


def test_the_benchmark_needs_a_domain_pack() -> None:
    assert isinstance(_TestPack(), DomainPack)
    with pytest.raises(ConfigurationError, match="domain pack"):
        AgenticBaselineBenchmark("test-pack")  # type: ignore[arg-type]


def test_only_benchmarks_with_scripts_are_known_good_sources() -> None:
    assert isinstance(AgenticBaselineBenchmark(_TestPack()), KnownGoodSource)
    assert not isinstance(ToyBenchmarkAdapter(), KnownGoodSource)


def test_registry_resolves_a_pack_name_from_yaml_options() -> None:
    reg = registry_module.build_default_registry()
    reg.register(KIND_DOMAIN_PACK, "test-pack", _TestPack)
    factory = reg.get(KIND_BENCHMARK, "agentic-baseline")
    benchmark = factory(pack="test-pack", scenarios=["add"])
    assert isinstance(benchmark, AgenticBaselineBenchmark)
    assert benchmark.pack.pack_id == "test-pack"
    with pytest.raises(ConfigurationError, match="needs benchmark_options.pack"):
        factory()
    with pytest.raises(ConfigurationError):
        factory(pack="no-such-pack")
    reg.register(KIND_DOMAIN_PACK, "not-a-pack", object)
    with pytest.raises(ConfigurationError, match="does not satisfy"):
        factory(pack="not-a-pack")


# ---------------------------------------------------------------------------
# The preflight
# ---------------------------------------------------------------------------


def test_a_solvable_scenario_passes_preflight_and_the_agent_runs() -> None:
    runner = _SpyRunner()
    summary, _ = _run(AgenticBaselineBenchmark(_TestPack(), scenarios=["add"]), runner)
    assert summary.preflight == [PreflightResult(TaskId("add"), True)]
    assert runner.calls == ["add"]
    assert summary.passed_tasks == 1


def test_a_defective_scenario_is_not_evaluated_and_the_agent_never_runs() -> None:
    """Fail-first: with the defective check removed, the agent ran the broken
    scenario (and passed it), hiding the defect."""
    runner = _SpyRunner()
    summary, _ = _run(AgenticBaselineBenchmark(_TestPack()), runner, repetitions=2)
    preflight = {str(p.task_id): p for p in summary.preflight}
    assert preflight["add"].passed and not preflight["broken"].passed
    assert "did not pass" in (preflight["broken"].cause or "")
    assert runner.calls.count("broken") == 0
    assert runner.calls.count("add") == 2
    broken = [r for r in summary.task_results if r.task_id == "broken"]
    assert [r.repetition for r in broken] == [0, 1]
    for result in broken:
        assert result.evaluation.not_evaluated_cause == "environment_defect"
    assert summary.not_evaluated_by_cause == {"environment_defect": 2}


def test_the_preflight_stays_out_of_the_runs_harness_streams() -> None:
    benchmark = AgenticBaselineBenchmark(_TestPack(), scenarios=["add"])
    _, orch = _run(benchmark, _SpyRunner())
    assert [(r.task_id, r.repetition) for r in orch.tool_call_records] == [("add", 0)]
    assert len(orch.end_states) == 1


def test_toy_runs_have_no_preflight() -> None:
    summary, _ = _run(ToyBenchmarkAdapter(), MockAgentRunner())
    assert summary.preflight == []


# ---------------------------------------------------------------------------
# End to end through the CLI
# ---------------------------------------------------------------------------


def test_cli_runs_a_pack_and_records_the_preflight(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    fresh = registry_module.build_default_registry()
    fresh.register(KIND_DOMAIN_PACK, "test-pack", _TestPack)
    monkeypatch.setattr(registry_module, "default_registry", fresh)
    # docs/adr/0005 D6 (R27): an agentic-baseline run needs a declaration.
    (tmp_path / "system.yaml").write_text(
        "system_id: t\npack_id: test-pack\ninvolvement: autonomous\n"
        "tools: [{name: calculator, permission: read}]\n"
        "intended_use: Add numbers.\n",
        encoding="utf-8",
    )
    config = tmp_path / "c.yaml"
    config.write_text(
        "run_id: ab1\nbenchmark_id: agentic-baseline\nsystem: system.yaml\n"
        "benchmark_options:\n  pack: test-pack\n  scenarios: [add, broken]\n",
        encoding="utf-8",
    )
    out = tmp_path / "bundle"
    args = ["run", "--config", str(config), "--output", str(out)]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    summary = json.loads((out / "summary.json").read_text())
    assert [(p["task_id"], p["passed"]) for p in summary["preflight"]] == [
        ("add", True),
        ("broken", False),
    ]
    assert summary["not_evaluated_by_cause"] == {"environment_defect": 1}
    assert validate_bundle(out).valid
