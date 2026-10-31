"""R24's exit criteria, checked against running code (ROADMAP R24; ADR-0005
D8; docs/adr/0006 D11, D13, D14, D16): the seed pack under the three
evaluators, through the orchestrator and through the CLI."""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Sequence
from importlib import resources
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from agentevalops.agents.mock_runner import MockAgentRunner
from agentevalops.benchmarks.agentic_baseline import AgenticBaselineBenchmark
from agentevalops.bundles.validator import validate_bundle
from agentevalops.cli import app
from agentevalops.config.loader import load_run_config
from agentevalops.core.errors import ConfigurationError
from agentevalops.core.protocols import Evaluator
from agentevalops.core.schemas import (
    EnvHandle,
    ResourceLimits,
    RunConfig,
    RunSummary,
    TaskSpec,
)
from agentevalops.core.types import BACKEND_LOCAL, AgentId, RunId, TaskId
from agentevalops.domain_packs.seed import SeedDomainPack
from agentevalops.domain_packs.seed.environment import END_STATE_SCHEMA
from agentevalops.environments import toy_environment
from agentevalops.evaluators.deterministic import DeterministicEvaluator
from agentevalops.evaluators.policy_compliance import PolicyComplianceEvaluator
from agentevalops.evaluators.state_based import StateBasedEvaluator
from agentevalops.evaluators.tool_use import ToolUseEvaluator
from agentevalops.orchestration.local import LocalOrchestrator
from agentevalops.policy.basic_checker import BasicPolicyChecker
from agentevalops.registry import KIND_EVALUATOR, build_default_registry
from agentevalops.stores.memory import InMemoryTraceStore

CONFIG = Path("configs/agentic_baseline_mock.yaml")
DATA_DIR = Path(str(resources.files("agentevalops.domain_packs.seed") / "data"))
_PACK = SeedDomainPack()
#: Every scenario the pack ships, R25's policy-compliance ones included.
SCENARIO_IDS = [s.scenario_id for s in _PACK.scenarios()]
N = len(SCENARIO_IDS)
#: One evaluation per applicable evaluator per task run.
EVALUATIONS = sum(len(s.applicable_evaluators) for s in _PACK.scenarios())
#: Harness records a known-good playback leaves: one per `call` step.
GOOD_CALLS = sum(
    1 for s in _PACK.scenarios() for step in s.known_good if "call" in step
)


def _evaluators() -> list[Evaluator]:
    return [
        DeterministicEvaluator(),
        ToolUseEvaluator(),
        StateBasedEvaluator(),
        PolicyComplianceEvaluator(),
    ]


def _orchestrate(
    benchmark: Any,
    runner: Any,
    evaluators: Sequence[Evaluator] | None = None,
    repetitions: int = 1,
) -> tuple[RunSummary, LocalOrchestrator]:
    orch = LocalOrchestrator(
        run_config=RunConfig(
            run_id=RunId("r24"),
            agent_id=AgentId("mock-agent-v1"),
            backend_id=BACKEND_LOCAL,
            resource_limits=ResourceLimits(),
            repetitions=repetitions,
        ),
        benchmark=benchmark,
        runner=runner,
        trace_store=InMemoryTraceStore(),
        evaluator=list(evaluators) if evaluators is not None else _evaluators(),
        policy_checker=BasicPolicyChecker(),
    )
    return asyncio.run(orch.run()), orch


class _Spy:
    """Plays one trajectory of every scenario and counts the tasks it ran."""

    agent_id = AgentId("spy")

    def __init__(self, benchmark: AgenticBaselineBenchmark, which: str) -> None:
        self.calls: list[str] = []
        self._inner = MockAgentRunner(scripts=benchmark.scripted_trajectories(which))

    async def run(self, input, resource_limits):  # type: ignore[no-untyped-def]
        self.calls.append(str(input.task.task_id))
        return await self._inner.run(input, resource_limits)


def _seed(
    which: str,
    pack: SeedDomainPack | None = None,
    evaluators: Sequence[Evaluator] | None = None,
    repetitions: int = 1,
) -> tuple[RunSummary, LocalOrchestrator, AgenticBaselineBenchmark, _Spy]:
    benchmark = AgenticBaselineBenchmark(pack or SeedDomainPack())
    spy = _Spy(benchmark, which)
    summary, orch = _orchestrate(benchmark, spy, evaluators, repetitions)
    return summary, orch, benchmark, spy


def _counts(summary: RunSummary) -> tuple[int, int, int]:
    return summary.passed_tasks, summary.failed_tasks, summary.not_evaluated_tasks


# ---------------------------------------------------------------------------
# The exit criteria
# ---------------------------------------------------------------------------


def test_every_known_good_passes_under_each_evaluator_that_applies() -> None:
    summary, _, benchmark, spy = _seed("known_good")
    scenarios = {s.scenario_id: s for s in benchmark.pack.scenarios()}
    assert [(str(p.task_id), p.passed) for p in summary.preflight] == [
        (sid, True) for sid in SCENARIO_IDS
    ]
    assert spy.calls == SCENARIO_IDS
    assert _counts(summary) == (N, 0, 0)
    for result in summary.task_results:
        scenario = scenarios[str(result.task_id)]
        assert [e.evaluator_kind for e in result.evaluations] == list(
            scenario.applicable_evaluators
        )
        for e in result.evaluations:
            assert e.applicable and e.evaluated and e.passed, (
                scenario.scenario_id,
                e.evaluator_kind,
                e.notes,
            )
        assert result.evaluation.evaluated and result.evaluation.passed


def test_every_known_bad_fails_under_each_evaluator_that_applies() -> None:
    """Each applicable evaluator catches the known-bad on its own: no
    scenario leans on another evaluator to fail it."""
    summary, _, benchmark, _ = _seed("known_bad")
    scenarios = {s.scenario_id: s for s in benchmark.pack.scenarios()}
    assert all(p.passed for p in summary.preflight)  # the preflight is the known-good
    assert _counts(summary) == (0, N, 0)
    for result in summary.task_results:
        scenario = scenarios[str(result.task_id)]
        assert [e.evaluator_kind for e in result.evaluations] == list(
            scenario.applicable_evaluators
        )
        for e in result.evaluations:
            assert e.applicable and e.evaluated and not e.passed, (
                scenario.scenario_id,
                e.evaluator_kind,
                e.notes,
            )
        assert result.evaluation.evaluated and not result.evaluation.passed


def test_a_defective_known_good_is_an_environment_defect_never_an_agent_failure(
    tmp_path: Path,
) -> None:
    """ADR-0005 D8. Fail-first: with the preflight's defect handling
    removed, the agent ran TE1 and the scenario's own wrong trajectory was
    reported as the agent's failure."""
    copy = tmp_path / "data"
    shutil.copytree(DATA_DIR, copy)
    doc = yaml.safe_load((copy / "scenarios.yaml").read_text(encoding="utf-8"))
    (te1,) = [s for s in doc["scenarios"] if s["scenario_id"] == "TE1"]
    # Well-formed to the loader, but decides the case wrongly.
    te1["known_good"] = [
        {"call": "case_lookup", "arguments": {"case_id": "C1"}},
        {
            "call": "status_update",
            "arguments": {
                "case_id": "C1",
                "decision": "rejected",
                "reason_codes": ["INC-OVER"],
            },
        },
        {"finish": "C1: approved (ELIG-OK)"},
    ]
    (copy / "scenarios.yaml").write_text(
        yaml.safe_dump(doc, sort_keys=False), encoding="utf-8"
    )

    summary, _, _, spy = _seed("known_good", pack=SeedDomainPack(copy), repetitions=2)
    preflight = {str(p.task_id): p for p in summary.preflight}
    assert not preflight["TE1"].passed
    assert "did not pass" in (preflight["TE1"].cause or "")
    assert all(p.passed for sid, p in preflight.items() if sid != "TE1")
    assert spy.calls.count("TE1") == 0 and len(spy.calls) == 2 * (N - 1)
    te1_runs = [r for r in summary.task_results if r.task_id == "TE1"]
    assert [r.repetition for r in te1_runs] == [0, 1]
    for r in te1_runs:
        assert not r.evaluation.evaluated
        assert r.evaluation.not_evaluated_cause == "environment_defect"
        assert {e.not_evaluated_cause for e in r.evaluations} == {"environment_defect"}
    assert summary.not_evaluated_by_cause == {"environment_defect": 2}
    assert _counts(summary) == (2 * (N - 1), 0, 2)


def test_a_missing_evaluator_is_a_configuration_gap_not_a_defect() -> None:
    """docs/adr/0006 D13. Fail-first: before slice 5 the preflight could not
    judge the known-good either and reported every task an environment
    defect, with the agent never run."""
    summary, _, _, spy = _seed("known_good", evaluators=[DeterministicEvaluator()])
    for check in summary.preflight:
        assert not check.passed
        assert "not evaluated" in (check.cause or "")
        assert "tool_use" in (check.cause or "")
    assert spy.calls == SCENARIO_IDS  # the agent still ran
    assert summary.not_evaluated_by_cause == {"configuration": N}
    assert _counts(summary) == (0, 0, N)
    for result in summary.task_results:
        assert result.evaluation.not_evaluated_cause == "configuration"
        by_kind = {e.evaluator_kind: e for e in result.evaluations}
        for kind, e in by_kind.items():
            assert e.evaluated == (kind == "deterministic"), (result.task_id, kind)


def test_every_task_run_has_an_end_state_record_and_every_case_its_truth() -> None:
    summary, orch, benchmark, _ = _seed("known_good", repetitions=2)
    states = orch.end_states
    assert [(str(s.task_id), s.repetition) for s in states] == [
        (sid, rep) for sid in SCENARIO_IDS for rep in (0, 1)
    ]
    assert len(states) == summary.total_tasks == 2 * N
    assert {s.state["schema"] for s in states} == {END_STATE_SCHEMA}
    for case in benchmark.pack.cases.values():
        assert case.reason_codes
        assert case.applicant.gender in ("female", "male")
        assert case.applicant.age > 0


def test_ambiguous_cases_known_good_refers_and_known_bad_decides() -> None:
    good, orch_good, benchmark, _ = _seed("known_good")
    bad, orch_bad, _, _ = _seed("known_bad")
    for sid, case_id in (("TE5", "C6"), ("TE6", "C7")):
        assert benchmark.pack.cases[case_id].ambiguous
        good_tools = [
            r.tool_name for r in orch_good.tool_call_records if r.task_id == sid
        ]
        assert "refer_to_officer" in good_tools and "status_update" not in good_tools
        bad_tools = [
            r.tool_name for r in orch_bad.tool_call_records if r.task_id == sid
        ]
        assert "status_update" in bad_tools
        result = next(r for r in bad.task_results if r.task_id == sid)
        by_kind = {e.evaluator_kind: e for e in result.evaluations}
        assert "forbidden status_update: called at index" in by_kind["tool_use"].notes
        assert "required refer_to_officer" in by_kind["tool_use"].notes
        assert "referrals: expected 1 item(s), got 0" in by_kind["state_based"].notes
        assert "expected_substring=missing" in by_kind["deterministic"].notes
        good_result = next(r for r in good.task_results if r.task_id == sid)
        assert all(e.passed for e in good_result.evaluations)


class _OneTask:
    """A benchmark with one task over the toy calculator environment."""

    benchmark_id = "one"
    benchmark_version = "0"

    def __init__(self, metadata: dict[str, Any]) -> None:
        self._metadata = metadata

    def list_tasks(self, filter_spec: Any = None) -> list[TaskSpec]:
        return [
            TaskSpec(
                task_id=TaskId("t"),
                benchmark_id="one",
                description="d",
                metadata=self._metadata,
            )
        ]

    def grade(self, task: Any, output: Any) -> Any:
        raise NotImplementedError

    def prepare_environment(self, task: TaskSpec) -> EnvHandle:
        return EnvHandle(task_id=task.task_id, tools=toy_environment(task.task_id))

    def teardown(self, handle: EnvHandle) -> None:
        pass


def test_deterministic_required_tools_come_from_harness_records_end_to_end() -> None:
    """docs/adr/0006 D14. Fail-first: before slice 5 the mock's self-reported
    call -- which never reaches the environment -- satisfied
    required_tool_names, so an agent's word was enough."""
    metadata = {
        "required_tool_names": ["calculator"],
        "emit_fake_tool_call": True,
        "fake_tool_name": "calculator",
        "expected_output": "4",
    }
    claims, _ = _orchestrate(
        _OneTask(metadata), MockAgentRunner(), [DeterministicEvaluator()]
    )
    assert _counts(claims) == (0, 1, 0)
    (result,) = claims.task_results
    assert "required_tool=calculator:missing" in result.evaluation.notes
    honest = MockAgentRunner(
        scripts={
            "t": [
                {"call": "calculator", "arguments": {"expression": "2 + 2"}},
                {"finish": "4"},
            ]
        }
    )
    calls, orch = _orchestrate(_OneTask(metadata), honest, [DeterministicEvaluator()])
    assert _counts(calls) == (1, 0, 0)
    assert [r.tool_name for r in orch.tool_call_records] == ["calculator"]
    assert (
        "required_tool=calculator:found(record 0)"
        in calls.task_results[0].evaluation.notes
    )


# ---------------------------------------------------------------------------
# The CLI and the shipped config
# ---------------------------------------------------------------------------


def _summary(out: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (out / "summary.json").read_text(encoding="utf-8")
    )
    return data


def _evaluations(out: Path) -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = json.loads(
        (out / "evaluations.json").read_text(encoding="utf-8")
    )
    return data


def test_the_shipped_config_runs_the_pack_through_the_cli(tmp_path: Path) -> None:
    out = tmp_path / "bundle"
    result = CliRunner().invoke(
        app, ["run", "--config", str(CONFIG), "--output", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert f"{N}/{N} evaluated passed" in result.output
    summary = _summary(out)
    assert (
        summary["passed_tasks"],
        summary["failed_tasks"],
        summary["not_evaluated_tasks"],
    ) == (N, 0, 0)
    assert [(p["task_id"], p["passed"]) for p in summary["preflight"]] == [
        (sid, True) for sid in SCENARIO_IDS
    ]
    evaluations = _evaluations(out)
    assert len(evaluations) == EVALUATIONS and all(e["passed"] for e in evaluations)
    assert {e["evaluator_kind"] for e in evaluations} == {
        "deterministic",
        "tool_use",
        "state_based",
        "policy",
    }
    end_states = [
        json.loads(line) for line in (out / "end_states.jsonl").read_text().splitlines()
    ]
    assert [s["task_id"] for s in end_states] == SCENARIO_IDS
    assert {s["state"]["schema"] for s in end_states} == {END_STATE_SCHEMA}
    assert len((out / "tool_calls.jsonl").read_text().splitlines()) == GOOD_CALLS
    assert validate_bundle(out).valid
    for command in ("validate-bundle", "replay"):
        checked = CliRunner().invoke(app, [command, "--bundle", str(out)])
        assert checked.exit_code == 0, checked.output


def test_the_known_bad_playback_fails_every_task(tmp_path: Path) -> None:
    doc = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    doc["mock_trajectory"] = "known_bad"
    doc["run_id"] = "agentic-baseline-mock-bad"
    config = tmp_path / "bad.yaml"
    config.write_text(yaml.safe_dump(doc), encoding="utf-8")
    out = tmp_path / "bundle"
    result = CliRunner().invoke(
        app, ["run", "--config", str(config), "--output", str(out)]
    )
    assert result.exit_code == 0, result.output
    summary = _summary(out)
    assert (
        summary["passed_tasks"],
        summary["failed_tasks"],
        summary["not_evaluated_tasks"],
    ) == (0, N, 0)
    evaluations = _evaluations(out)
    assert len(evaluations) == EVALUATIONS and not any(e["passed"] for e in evaluations)
    assert validate_bundle(out).valid


def test_the_loader_validates_evaluators_and_mock_trajectory(tmp_path: Path) -> None:
    loaded = load_run_config(CONFIG)
    assert loaded.evaluators == ("deterministic", "tool_use", "state_based", "policy")
    assert loaded.mock_trajectory == "known_good"
    smoke = load_run_config(Path("configs/toy_smoke.yaml"))
    assert smoke.evaluators == ("deterministic",) and smoke.mock_trajectory is None

    def write(text: str) -> Path:
        path = tmp_path / "c.yaml"
        path.write_text("run_id: r\nbenchmark_id: toy\n" + text, encoding="utf-8")
        return path

    for text, match in [
        ("evaluators: [deterministic, nope]\n", "Unsupported evaluator 'nope'"),
        ("evaluators: []\n", "at least one"),
        ("evaluators: deterministic\n", "YAML list"),
        ("evaluators: [deterministic, deterministic]\n", "repeats"),
        ("mock_trajectory: best\n", "known_good"),
        (
            "runner_id: openai\nmodel: gpt-4.1-mini-2025-04-14\n"
            "mock_trajectory: known_good\n",
            "only valid with runner_id: mock",
        ),
    ]:
        with pytest.raises(ConfigurationError, match=match):
            load_run_config(write(text))


def test_mock_trajectory_needs_a_benchmark_with_scripts(tmp_path: Path) -> None:
    config = tmp_path / "c.yaml"
    config.write_text(
        "run_id: r\nbenchmark_id: toy\nmock_trajectory: known_good\n", encoding="utf-8"
    )
    result = CliRunner().invoke(app, ["run", "--config", str(config)])
    assert result.exit_code == 1
    assert "has no scripted trajectories" in result.output


def test_scripted_trajectories_accessor() -> None:
    benchmark = AgenticBaselineBenchmark(SeedDomainPack(), scenarios=["TE1", "TC1"])
    assert set(benchmark.scripted_trajectories("known_bad")) == {"TE1", "TC1"}
    assert benchmark.scripted_trajectories("known_good")["TE1"][-1] == {
        "finish": "C1: approved (ELIG-OK)"
    }
    with pytest.raises(ConfigurationError, match="unknown trajectory"):
        benchmark.scripted_trajectories("best")


def test_the_registry_offers_the_three_evaluators() -> None:
    reg = build_default_registry()
    assert reg.get(KIND_EVALUATOR, "deterministic") is DeterministicEvaluator
    assert reg.get(KIND_EVALUATOR, "tool_use") is ToolUseEvaluator
    assert reg.get(KIND_EVALUATOR, "state_based") is StateBasedEvaluator
