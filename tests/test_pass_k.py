"""Repetitions and pass^k (ROADMAP R27 slice 3; docs/adr/0006 D18, D20).

pass^k groups task runs by task id: a scenario passes only if every one
of its k repetitions passed; one not-evaluated repetition makes the
scenario not evaluated, never failed, and its cause travels with it into
summary.json, the CLI line and the report. No confidence intervals (the
W25 cut): raw counts and k.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import fast_yaml
import pytest
from flaky_runner import FlakyRunner, passes
from test_agentic_baseline import _TestPack
from test_bundle_0_5 import _Benchmark, _orchestrator, _task
from test_r24_exit import _evaluators, _orchestrate
from typer.testing import CliRunner

from agentevalops.agents.mock_runner import MockAgentRunner
from agentevalops.benchmarks.agentic_baseline import AgenticBaselineBenchmark
from agentevalops.bundles.serializers import to_jsonable
from agentevalops.cli import app, category_lines
from agentevalops.config.system import load_system_declaration
from agentevalops.core.perturbation import derive_seed
from agentevalops.core.schemas import (
    AgentOutput,
    CategoryPassK,
    EvaluationResult,
    PassKResult,
    RunConfig,
    RunSummary,
    TaskRunResult,
    TerminationReason,
    ThresholdOverride,
)
from agentevalops.core.types import AgentId, BackendId, RunId, TaskId
from agentevalops.domain_packs.seed import SeedDomainPack
from agentevalops.orchestration.local import LocalOrchestrator
from agentevalops.plans import build_plan
from agentevalops.policy.basic_checker import BasicPolicyChecker
from agentevalops.reports.markdown import render_report
from agentevalops.scorers.pass_k import (
    NOT_EVALUATED_MIXED,
    pass_k_by_category,
    pass_k_results,
)
from agentevalops.stores.memory import InMemoryTraceStore

PACK = SeedDomainPack()
SCENARIO_IDS = [s.scenario_id for s in PACK.scenarios()]
CATEGORIES = {s.scenario_id: s.category for s in PACK.scenarios()}
SYSTEM = Path("configs/seed_system.yaml")
RUN_CONFIG = Path("configs/agentic_baseline_mock.yaml")


# ---------------------------------------------------------------------------
# The scorer on hand-built task runs
# ---------------------------------------------------------------------------


def _evaluation(
    passed: bool = True, evaluated: bool = True, cause: str | None = None
) -> EvaluationResult:
    return EvaluationResult(
        evaluator_id="d",
        evaluator_kind="deterministic",
        score=1.0 if passed else 0.0,
        passed=passed,
        evaluated=evaluated,
        not_evaluated_cause=cause,
    )


def _run(
    task_id: str,
    repetition: int,
    *,
    passed: bool = True,
    evaluated: bool = True,
    cause: str | None = None,
) -> TaskRunResult:
    return TaskRunResult(
        task_id=TaskId(task_id),
        output=AgentOutput(
            success=True, termination_reason=TerminationReason.COMPLETED
        ),
        evaluation=_evaluation(passed, evaluated, cause),
        event_count=0,
        repetition=repetition,
    )


def test_every_repetition_passed() -> None:
    results = pass_k_results([_run("a", 0), _run("a", 1), _run("a", 2)])
    assert results == [
        PassKResult(task_id="a", k=3, passed=3, failed=0, not_evaluated=0, pass_k=True)
    ]


def test_one_failed_repetition_fails_the_scenario() -> None:
    (result,) = pass_k_results([_run("a", 0), _run("a", 1, passed=False), _run("a", 2)])
    assert (result.k, result.passed, result.failed, result.pass_k) == (3, 2, 1, False)
    assert result.not_evaluated_cause is None


def test_one_not_evaluated_repetition_makes_the_scenario_not_evaluated() -> None:
    """docs/adr/0006 D20: not evaluated is not failed, and never without a
    cause."""
    (result,) = pass_k_results(
        [
            _run("a", 0),
            _run("a", 1, evaluated=False, cause="environment_defect"),
            _run("a", 2, passed=False),
        ]
    )
    assert (result.passed, result.failed, result.not_evaluated) == (1, 1, 1)
    assert result.pass_k is None
    assert result.not_evaluated_cause == "environment_defect"


def test_the_cause_is_the_first_repetitions_or_mixed() -> None:
    same = pass_k_results(
        [
            _run("a", 1, evaluated=False, cause="infrastructure"),
            _run("a", 0, evaluated=False, cause="infrastructure"),
        ]
    )
    assert same[0].not_evaluated_cause == "infrastructure"
    mixed = pass_k_results(
        [
            _run("a", 1, evaluated=False, cause="configuration"),
            _run("a", 0, evaluated=False, cause="infrastructure"),
        ]
    )
    assert mixed[0].not_evaluated_cause == NOT_EVALUATED_MIXED == "mixed"


def test_scenarios_keep_their_first_seen_order_and_their_own_k() -> None:
    results = pass_k_results([_run("b", 0), _run("a", 0), _run("b", 1)])
    assert [(r.task_id, r.k) for r in results] == [("b", 2), ("a", 1)]


def test_the_category_roll_up() -> None:
    results = pass_k_results(
        [
            _run("a", 0),
            _run("a", 1),
            _run("b", 0, passed=False),
            _run("b", 1),
            _run("c", 0, evaluated=False, cause="environment_defect"),
            _run("c", 1),
            _run("d", 0, evaluated=False, cause="infrastructure"),
            _run("d", 1, evaluated=False, cause="configuration"),
            _run("e", 0),
            _run("e", 1),
        ]
    )
    categories = {"a": "x", "b": "x", "c": "x", "d": "x", "e": "y"}
    by_category = pass_k_by_category(results, categories)
    assert by_category == [
        CategoryPassK(
            category="x",
            k=2,
            scenarios=4,
            passed=1,
            failed=1,
            not_evaluated=2,
            pass_k_rate=0.25,
            not_evaluated_causes={"environment_defect": 1, "mixed": 1},
        ),
        CategoryPassK(
            category="y",
            k=2,
            scenarios=1,
            passed=1,
            failed=0,
            not_evaluated=0,
            pass_k_rate=1.0,
            not_evaluated_causes={},
        ),
    ]


def test_a_task_without_a_category_is_left_out_of_the_roll_up() -> None:
    assert pass_k_by_category(pass_k_results([_run("a", 0)]), {}) == []


# ---------------------------------------------------------------------------
# Through the orchestrator
# ---------------------------------------------------------------------------


def test_the_seed_pack_at_k_3_passes_every_scenario_three_times() -> None:
    benchmark = AgenticBaselineBenchmark(PACK)
    runner = MockAgentRunner(scripts=benchmark.scripted_trajectories("known_good"))
    summary, _ = _orchestrate(benchmark, runner, repetitions=3)
    assert [r.task_id for r in summary.pass_k] == SCENARIO_IDS
    assert all((r.k, r.passed, r.pass_k) == (3, 3, True) for r in summary.pass_k)
    assert [
        (c.category, c.scenarios, c.pass_k_rate) for c in summary.pass_k_by_category
    ] == [
        ("task_execution", 6, 1.0),
        ("tool_calling", 4, 1.0),
        ("policy_compliance", 8, 1.0),
        ("robustness", 8, 1.0),
    ]
    document = to_jsonable(summary)
    assert document["pass_k"][0] == {
        "task_id": "TE1",
        "k": 3,
        "passed": 3,
        "failed": 0,
        "not_evaluated": 0,
        "pass_k": True,
        "not_evaluated_cause": None,
    }
    assert document["pass_k_by_category"][3]["not_evaluated_causes"] == {}


def test_the_orchestrator_hands_the_runner_its_seed_and_repetition() -> None:
    seen: list[tuple[str, int, int]] = []

    class _Watch:
        agent_id = AgentId("watch")

        def __init__(self) -> None:
            self._inner = MockAgentRunner(
                scripts=benchmark.scripted_trajectories("known_good")
            )

        async def run(self, input, limits):  # type: ignore[no-untyped-def]
            seen.append(
                (
                    str(input.task.task_id),
                    input.context["repetition"],
                    input.context["seed"],
                )
            )
            return await self._inner.run(input, limits)

    benchmark = AgenticBaselineBenchmark(PACK, scenarios=["TE1", "RB1"])
    orch = LocalOrchestrator(
        run_config=RunConfig(
            run_id=RunId("seeds"),
            agent_id=AgentId("watch"),
            backend_id=BackendId("local"),
            repetitions=2,
            seed=42,
        ),
        benchmark=benchmark,
        runner=_Watch(),
        trace_store=InMemoryTraceStore(),
        evaluator=_evaluators(),
        policy_checker=BasicPolicyChecker(),
    )
    asyncio.run(orch.run())
    assert seen == [
        (sid, r, derive_seed(42, sid, r)) for sid in ("TE1", "RB1") for r in (0, 1)
    ]


def test_pass_k_at_k_5_on_a_seeded_flaky_agent_matches_the_analytic_value() -> None:
    """R27 exit criterion: the flaky agent passes a run when the number drawn
    from the run's own seed is below p, so which scenarios pass all five
    repetitions follows from the seeds alone, and the scorer must report
    exactly that."""
    benchmark = AgenticBaselineBenchmark(PACK)
    runner = FlakyRunner(0.7, benchmark)
    orch = LocalOrchestrator(
        run_config=RunConfig(
            run_id=RunId("flaky"),
            agent_id=AgentId("flaky"),
            backend_id=BackendId("local"),
            repetitions=5,
            seed=5,
        ),
        benchmark=benchmark,
        runner=runner,
        trace_store=InMemoryTraceStore(),
        evaluator=_evaluators(),
        policy_checker=BasicPolicyChecker(),
    )
    summary = asyncio.run(orch.run())
    expected = {
        sid: [passes(derive_seed(5, sid, r), 0.7) for r in range(5)]
        for sid in SCENARIO_IDS
    }
    assert sorted(runner.seeds) == sorted(
        derive_seed(5, sid, r) for sid in SCENARIO_IDS for r in range(5)
    )
    assert {r.task_id: r.pass_k for r in summary.pass_k} == {
        sid: all(runs) for sid, runs in expected.items()
    }
    assert {r.task_id: r.passed for r in summary.pass_k} == {
        sid: sum(runs) for sid, runs in expected.items()
    }
    assert all(r.k == 5 and r.not_evaluated == 0 for r in summary.pass_k)
    assert 0 < sum(1 for r in summary.pass_k if r.pass_k) < len(SCENARIO_IDS)
    by_category = {c.category: c for c in summary.pass_k_by_category}
    for category, roll_up in by_category.items():
        own = [sid for sid in SCENARIO_IDS if CATEGORIES[sid] == category]
        assert roll_up.scenarios == len(own)
        assert roll_up.passed == sum(1 for sid in own if all(expected[sid]))
        assert roll_up.pass_k_rate == roll_up.passed / roll_up.scenarios


def test_over_many_scenarios_pass_k_approaches_p_to_the_k() -> None:
    """The same agent on 400 independent tasks: the fraction passing all 5
    repetitions is p^5 to within sampling error, and is the same number on
    every run because the seeds are."""
    tasks = [
        _task(f"t{i}", expected_output="4", match_mode="exact") for i in range(400)
    ]
    rates = []
    for _ in range(2):
        orch = _orchestrator(_Benchmark(tasks), runner=FlakyRunner(0.8), repetitions=5)
        orch._config = replace(orch._config, seed=2026)
        summary = asyncio.run(orch.run())
        rate = sum(1 for r in summary.pass_k if r.pass_k) / len(summary.pass_k)
        rates.append(rate)
    assert rates[0] == rates[1]
    assert abs(rates[0] - 0.8**5) < 0.05


def test_a_defective_scenario_is_not_evaluated_with_its_cause_at_every_k() -> None:
    summary, _ = _orchestrate(
        AgenticBaselineBenchmark(_TestPack()), MockAgentRunner(), repetitions=2
    )
    by_task = {r.task_id: r for r in summary.pass_k}
    assert by_task["add"].pass_k is True
    assert by_task["broken"].pass_k is None
    assert by_task["broken"].not_evaluated_cause == "environment_defect"
    (roll_up,) = summary.pass_k_by_category
    assert (roll_up.scenarios, roll_up.passed, roll_up.not_evaluated) == (2, 1, 1)
    assert roll_up.not_evaluated_causes == {"environment_defect": 1}


# ---------------------------------------------------------------------------
# The CLI line and the report: 4 of 5, and never "not evaluated" alone
# ---------------------------------------------------------------------------


def _four_of_five() -> tuple[RunSummary, AgenticBaselineBenchmark]:
    ids = ["TE1", "TE2", "TE3", "TE4", "TE5"]
    benchmark = AgenticBaselineBenchmark(PACK, scenarios=ids)
    scripts = benchmark.scripted_trajectories("known_good")
    scripts["TE3"] = benchmark.scripted_trajectories("known_bad")["TE3"]
    summary, _ = _orchestrate(
        benchmark, MockAgentRunner(scripts=scripts), repetitions=2
    )
    return summary, benchmark


def test_the_cli_line_shows_four_of_five() -> None:
    summary, _ = _four_of_five()
    (line,) = category_lines(summary, {sid: "task_execution" for sid in SCENARIO_IDS})
    assert "8/10 evaluated passed (0 not evaluated)" in line
    assert "pass^2 4/5 scenarios" in line
    assert "not evaluated (" not in line


def test_the_cli_line_names_the_cause_of_a_not_evaluated_scenario() -> None:
    summary, _ = _orchestrate(
        AgenticBaselineBenchmark(_TestPack()), MockAgentRunner(), repetitions=2
    )
    (line,) = category_lines(
        summary, {"add": "task_execution", "broken": "task_execution"}
    )
    assert "pass^2 1/2 scenarios, 1 not evaluated (environment_defect)" in line


def _info(benchmark: Any) -> dict[str, dict[str, Any]]:
    from agentevalops.cli import _task_info

    return _task_info(benchmark)


def test_the_report_has_a_repeatability_section() -> None:
    summary, benchmark = _four_of_five()
    config = RunConfig(
        run_id=RunId("x"), agent_id=AgentId("mock"), backend_id=BackendId("local")
    )
    report = render_report(config, summary, [], "c.yaml", _info(benchmark))
    assert "## Repeatability (pass^k, k = 2)" in report
    assert "| TE3 | task_execution | 2 | 0 | 2 | 0 | no |" in report
    assert "| TE1 | task_execution | 2 | 2 | 0 | 0 | yes |" in report
    assert "task_execution: 4/5 scenarios passed all 2 repetitions" in report


def test_the_report_names_the_cause_of_a_not_evaluated_scenario() -> None:
    benchmark = AgenticBaselineBenchmark(_TestPack())
    summary, _ = _orchestrate(benchmark, MockAgentRunner(), repetitions=2)
    config = RunConfig(
        run_id=RunId("x"), agent_id=AgentId("mock"), backend_id=BackendId("local")
    )
    report = render_report(config, summary, [], "c.yaml", _info(benchmark))
    assert (
        "| broken | task_execution | 2 | 0 | 0 | 2 | "
        "not evaluated (environment_defect) |"
    ) in report
    assert "1 not evaluated (environment_defect)" in report
    section = report.split("## Repeatability", 1)[1].split("\n## ", 1)[0]
    for line in section.splitlines():
        if "not evaluated" in line:
            assert "not evaluated (" in line or "its cause is named" in line, line


def test_the_report_repeats_the_plan_and_marks_comparative_thresholds(
    tmp_path: Path,
) -> None:
    """Owner's condition (R27): where the report prints "below" it says a
    comparative threshold does not gate, so "below" is never read as a
    tolerated failure; the plan's intended use and business metrics are
    repeated (testing-framework 3.7.1)."""
    declaration = load_system_declaration(SYSTEM)
    declaration = replace(
        declaration,
        thresholds=(ThresholdOverride("task_execution", min_pass_k=1.0),),
    )
    benchmark = AgenticBaselineBenchmark(PACK)
    config = RunConfig(
        run_id=RunId("x"),
        agent_id=AgentId("flaky"),
        backend_id=BackendId("local"),
        repetitions=2,
        seed=3,
    )
    plan = build_plan(declaration, PACK, benchmark.scenarios(), run_config=config)
    orch = LocalOrchestrator(
        run_config=config,
        benchmark=benchmark,
        runner=FlakyRunner(0.6, benchmark),
        trace_store=InMemoryTraceStore(),
        evaluator=_evaluators(),
        policy_checker=BasicPolicyChecker(),
    )
    summary = asyncio.run(orch.run())
    by_category = {c.category: c for c in summary.pass_k_by_category}
    assert by_category["robustness"].pass_k_rate < 0.75  # below the pack's comparative
    assert (
        by_category["task_execution"].pass_k_rate < 1.0
    )  # below the owner's hard gate
    report = render_report(config, summary, [], "c.yaml", _info(benchmark), plan=plan)
    assert "## Test plan (pre-registered)" in report
    assert declaration.intended_use in report
    assert declaration.business_metrics[0].definition in report
    assert "recorded, not evaluated" in report
    assert "| robustness | 0.75 | pack | comparative | pack |" in report
    assert "below (comparative threshold; does not gate)" in report
    assert "| task_execution | 1.0 | owner | hard | pack |" in report
    assert "below (hard gate)" in report
    for line in report.splitlines():
        if "| below" in line or "below (" in line:
            assert "does not gate" in line or "hard gate" in line, line


def test_the_report_plan_section_lists_exclusions_and_not_evaluated_categories() -> (
    None
):
    declaration = load_system_declaration(SYSTEM)
    declaration = replace(
        declaration,
        tools=tuple(t for t in declaration.tools if t.name != "records_export"),
    )
    benchmark = AgenticBaselineBenchmark(_TestPack())
    config = RunConfig(
        run_id=RunId("x"),
        agent_id=AgentId("mock"),
        backend_id=BackendId("local"),
        repetitions=2,
    )
    plan = build_plan(declaration, PACK, PACK.scenarios(), run_config=config)
    summary, _ = _orchestrate(benchmark, MockAgentRunner(), repetitions=2)
    report = render_report(config, summary, [], "c.yaml", _info(benchmark), plan=plan)
    assert "| PC5 | undeclared_tool:records_export |" in report
    assert (
        "not evaluated: 1 scenario (environment_defect); a hard gate fails closed"
        in report
    )
    assert "no scenario ran" in report


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


def test_a_cli_run_with_repetitions_reports_pass_k_everywhere(tmp_path: Path) -> None:
    system = fast_yaml.safe_load(SYSTEM.read_text(encoding="utf-8"))
    system["repetitions"] = 2
    (tmp_path / "system.yaml").write_text(fast_yaml.safe_dump(system), encoding="utf-8")
    run = fast_yaml.safe_load(RUN_CONFIG.read_text(encoding="utf-8"))
    run["system"] = "system.yaml"
    run["benchmark_options"]["scenarios"] = ["TE1", "TC1", "PC1", "RB1"]
    config = tmp_path / "run.yaml"
    config.write_text(fast_yaml.safe_dump(run), encoding="utf-8")
    out = tmp_path / "bundle"
    result = CliRunner().invoke(
        app, ["run", "--config", str(config), "--output", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert "pass^2 1/1 scenarios" in result.output
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert len(summary["task_results"]) == 8
    assert {r["task_id"]: r["k"] for r in summary["pass_k"]} == {
        "TE1": 2,
        "TC1": 2,
        "PC1": 2,
        "RB1": 2,
    }
    assert all(r["not_evaluated_cause"] is None for r in summary["pass_k"])
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "## Repeatability (pass^k, k = 2)" in report
    assert "## Test plan (pre-registered)" in report
    assert "| robustness | 0.75 | pack | comparative | pack | 1.0 | met |" in report
    plan = json.loads((out / "test_plan.json").read_text(encoding="utf-8"))
    assert plan["repetitions"] == 2 and len(plan["seeds"]["TE1"]) == 2


@pytest.mark.parametrize("k", [1, 2])
def test_k_is_the_runs_repetitions(k: int) -> None:
    summary, _ = _orchestrate(
        AgenticBaselineBenchmark(PACK, scenarios=["TE1"]),
        MockAgentRunner(
            scripts=AgenticBaselineBenchmark(PACK).scripted_trajectories("known_good")
        ),
        repetitions=k,
    )
    assert summary.pass_k[0].k == k
    assert summary.pass_k_by_category[0].k == k
