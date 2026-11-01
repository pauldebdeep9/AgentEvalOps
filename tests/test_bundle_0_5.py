"""Bundle format 0.5 (docs/adr/0006 D13, D16, D17, D18, D19, D20, D21; R24
slice 1). Bundles here are built end to end: a real orchestrator run,
written by BundleWriter, then validated."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from bundle_inputs import minimal_evidence

from agentevalops.agents.mock_runner import MockAgentRunner
from agentevalops.benchmarks.toy.adapter import ToyBenchmarkAdapter
from agentevalops.bundles.constants import (
    BUNDLE_FORMAT_VERSION,
    END_STATES_FILENAME,
    LEGACY_BUNDLE_FORMAT_VERSIONS,
    MANIFEST_FILENAME,
    TOOL_CALLS_FILENAME,
    required_files_for_version,
)
from agentevalops.bundles.manifest import write_manifest
from agentevalops.bundles.validator import validate_bundle
from agentevalops.bundles.writer import BundleWriter
from agentevalops.core.protocols import Scorer
from agentevalops.core.schemas import (
    EnvHandle,
    EvaluationResult,
    EvaluatorContext,
    PolicySpec,
    ResourceLimits,
    RunConfig,
    RunSummary,
    TaskSpec,
)
from agentevalops.core.types import BACKEND_LOCAL, AgentId, RunId, TaskId
from agentevalops.environments import toy_environment
from agentevalops.evaluators.deterministic import DeterministicEvaluator
from agentevalops.orchestration.local import LocalOrchestrator, _verdict
from agentevalops.policy.basic_checker import BasicPolicyChecker
from agentevalops.scorers.simple import SimpleScorer
from agentevalops.stores.memory import InMemoryTraceStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Benchmark:
    """Tasks with given metadata, each with the toy environment."""

    benchmark_id = "b05"
    benchmark_version = "0"

    def __init__(self, tasks: list[TaskSpec]) -> None:
        self._tasks = tasks

    def list_tasks(self, filter_spec=None):  # type: ignore[no-untyped-def]
        return list(self._tasks)

    def grade(self, task, output):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def prepare_environment(self, task):  # type: ignore[no-untyped-def]
        return EnvHandle(task_id=task.task_id, tools=toy_environment(task.task_id))

    def teardown(self, handle):  # type: ignore[no-untyped-def]
        pass


def _task(task_id: str, **metadata: Any) -> TaskSpec:
    return TaskSpec(
        task_id=TaskId(task_id), benchmark_id="b05", description="d", metadata=metadata
    )


def _orchestrator(
    benchmark: Any,
    *,
    runner: Any = None,
    evaluators: Any = None,
    repetitions: int = 1,
    policy: PolicySpec | None = None,
) -> LocalOrchestrator:
    return LocalOrchestrator(
        run_config=RunConfig(
            run_id=RunId("b05"),
            agent_id=AgentId("mock"),
            backend_id=BACKEND_LOCAL,
            resource_limits=ResourceLimits(),
            repetitions=repetitions,
        ),
        benchmark=benchmark,
        runner=runner or MockAgentRunner(),
        trace_store=InMemoryTraceStore(),
        evaluator=evaluators or DeterministicEvaluator(),
        policy_checker=BasicPolicyChecker(),
        policy_spec=policy,
    )


def _bundle(tmp_path: Path, orch: LocalOrchestrator) -> tuple[Path, RunSummary]:
    store = InMemoryTraceStore()
    orch._trace_store = store
    summary = asyncio.run(orch.run())
    out = BundleWriter(tmp_path / "bundle").write(
        run_config=orch._config,
        summary=summary,
        all_events=store.events(orch._config.run_id),
        benchmark_id="toy",
        benchmark_scenario="smoke",
        benchmark_options={"scenario": "smoke"},
        tool_calls=orch.tool_call_records,
        end_states=orch.end_states,
        evidence=minimal_evidence(summary),
    )
    return out, summary


def _lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _toy_bundle(tmp_path: Path, **kw: Any) -> tuple[Path, RunSummary]:
    return _bundle(tmp_path, _orchestrator(ToyBenchmarkAdapter("smoke"), **kw))


# ---------------------------------------------------------------------------
# The format
# ---------------------------------------------------------------------------


def test_a_fresh_bundle_carries_the_0_5_streams_and_validates(tmp_path: Path) -> None:
    """0.5's streams stay required at 0.6 (docs/adr/0007 D1)."""
    out, _ = _toy_bundle(tmp_path)
    manifest = json.loads((out / MANIFEST_FILENAME).read_text())
    assert BUNDLE_FORMAT_VERSION == "0.6"
    assert manifest["bundle_format_version"] == "0.6"
    for name in (TOOL_CALLS_FILENAME, END_STATES_FILENAME):
        assert name in manifest["required_files"]
        assert (out / name).exists()
    result = validate_bundle(out)
    assert result.valid, result.errors


def test_0_5_bundle_missing_tool_calls_fails_validation(tmp_path: Path) -> None:
    """Fail-first: with 0.5's required files not mapped, this validated."""
    out, _ = _toy_bundle(tmp_path)
    (out / TOOL_CALLS_FILENAME).unlink()
    write_manifest(out)  # a consistent manifest, so only the missing file is wrong
    result = validate_bundle(out)
    assert not result.valid
    assert any(TOOL_CALLS_FILENAME in e for e in result.errors)


def test_0_4_bundle_without_the_harness_streams_still_validates(
    tmp_path: Path,
) -> None:
    assert "0.4" in LEGACY_BUNDLE_FORMAT_VERSIONS
    out, _ = _toy_bundle(tmp_path)
    for name in (TOOL_CALLS_FILENAME, END_STATES_FILENAME):
        (out / name).unlink()
    write_manifest(out)
    manifest = json.loads((out / MANIFEST_FILENAME).read_text())
    manifest["bundle_format_version"] = "0.4"
    manifest["required_files"] = list(required_files_for_version("0.4"))
    (out / MANIFEST_FILENAME).write_text(json.dumps(manifest))
    result = validate_bundle(out)
    assert result.valid, result.errors


def test_required_files_by_version() -> None:
    assert TOOL_CALLS_FILENAME in required_files_for_version("0.5")
    assert TOOL_CALLS_FILENAME not in required_files_for_version("0.4")
    assert "replay_command.txt" in required_files_for_version("0.4")
    assert "replay_command.txt" not in required_files_for_version("0.3")
    assert required_files_for_version(None) == required_files_for_version("9.9")


def test_malformed_harness_stream_line_fails_validation(tmp_path: Path) -> None:
    out, _ = _toy_bundle(tmp_path)
    (out / END_STATES_FILENAME).write_text("not json\n[1]\n")
    write_manifest(out)
    errors = validate_bundle(out).errors
    assert any("end_states.jsonl line 1: invalid JSON" in e for e in errors)
    assert any("end_states.jsonl line 2: expected a JSON object" in e for e in errors)


def test_config_records_benchmark_options(tmp_path: Path) -> None:
    out, _ = _toy_bundle(tmp_path)
    config = json.loads((out / "config.json").read_text())
    assert config["benchmark_options"] == {"scenario": "smoke"}
    assert config["run_config"]["repetitions"] == 1
    assert config["run_config"]["seed"] == 0


# ---------------------------------------------------------------------------
# Harness streams, stamping and refs
# ---------------------------------------------------------------------------


def test_one_end_state_line_per_task_run_and_stamped_records(tmp_path: Path) -> None:
    script = [
        {"call": "calculator", "arguments": {"expression": "1+1"}},
        {"finish": "2"},
    ]
    benchmark = _Benchmark([_task("a"), _task("b")])
    runner = MockAgentRunner(scripts={"a": script, "b": script})
    out, _ = _bundle(tmp_path, _orchestrator(benchmark, runner=runner, repetitions=2))

    end_states = _lines(out / END_STATES_FILENAME)
    assert [(s["task_id"], s["repetition"]) for s in end_states] == [
        ("a", 0), ("a", 1), ("b", 0), ("b", 1),
    ]
    calls = _lines(out / TOOL_CALLS_FILENAME)
    assert [(c["task_id"], c["repetition"], c["index"]) for c in calls] == [
        ("a", 0, 0), ("a", 1, 0), ("b", 0, 0), ("b", 1, 0),
    ]
    traces = _lines(out / "traces.jsonl")
    assert {(e["task_id"], e["repetition"]) for e in traces} == {
        ("a", 0), ("a", 1), ("b", 0), ("b", 1),
    }
    assert validate_bundle(out).valid  # task_count 4 == distinct task runs 4


def test_denied_tool_citations_from_two_tasks_are_distinct() -> None:
    """Finding 1: both tasks call the denied tool as their own record 0.
    Fail-first: without the orchestrator's stamping the refs coincided.
    Since R25 the deny-list reads the harness records (D14), so the calls
    are scripted through the environment and the legacy citations, which
    cannot address a record, stay empty."""
    script = [{"call": "shell", "arguments": {}}, {"finish": "x"}]
    benchmark = _Benchmark([_task("a"), _task("b")])
    orch = _orchestrator(
        benchmark,
        runner=MockAgentRunner(scripts={"a": script, "b": script}),
        policy=PolicySpec(policy_id="p", deny_tool_ids=("shell",)),
    )
    verdict = asyncio.run(orch.run()).policy_verdict
    assert verdict is not None
    assert verdict.citations == []
    assert {(r.task_id, r.repetition, r.index) for r in verdict.refs} == {
        ("a", 0, 0),
        ("b", 0, 0),
    }
    assert {r.stream for r in verdict.refs} == {"tool_calls"}


def test_evaluations_name_their_task_run(tmp_path: Path) -> None:
    out, _ = _toy_bundle(tmp_path, repetitions=2)
    entries = json.loads((out / "evaluations.json").read_text())
    assert len(entries) == 4
    assert all({"task_id", "repetition", "evaluator_id"} <= set(e) for e in entries)


# ---------------------------------------------------------------------------
# Several evaluators per task (D13)
# ---------------------------------------------------------------------------


def _result(kind: str, *, passed: bool = True, evaluated: bool = True,
            applicable: bool = True) -> EvaluationResult:
    return EvaluationResult(
        evaluator_id=kind,
        evaluator_kind=kind,
        score=1.0 if passed else 0.0,
        passed=passed,
        evaluated=evaluated,
        applicable=applicable,
        not_evaluated_cause=None if evaluated else "infrastructure",
    )


def test_a_real_failure_wins_over_not_evaluated() -> None:
    """Fail-first: with the checks in the other order this read not evaluated."""
    verdict = _verdict([_result("s", evaluated=False), _result("t", passed=False)])
    assert (verdict.evaluated, verdict.passed) == (True, False)


def test_not_evaluated_wins_over_passes() -> None:
    verdict = _verdict([_result("s"), _result("t", evaluated=False)])
    assert verdict.evaluated is False
    assert verdict.not_evaluated_cause == "infrastructure"


def test_not_applicable_results_are_not_counted() -> None:
    """Fail-first: counted, the not-applicable failure failed the task."""
    verdict = _verdict(
        [_result("d"), _result("s", passed=False, applicable=False), _result("t")]
    )
    assert (verdict.evaluated, verdict.passed) == (True, True)


def test_a_single_counted_result_is_the_verdict() -> None:
    only = _result("d", passed=False)
    assert _verdict([only, _result("s", applicable=False)]) is only


def test_no_applicable_result_is_not_evaluated_for_configuration() -> None:
    verdict = _verdict([_result("s", applicable=False)])
    assert verdict.evaluated is False
    assert verdict.not_evaluated_cause == "configuration"


class _CapturingEvaluator:
    evaluator_id = "capture"
    evaluator_kind = "state_based"

    def __init__(self) -> None:
        self.contexts: list[EvaluatorContext] = []

    async def evaluate(self, task, output, trace, context):  # type: ignore[no-untyped-def]
        self.contexts.append(context)
        return _result("state_based")


def test_evaluators_get_the_task_runs_context() -> None:
    capture = _CapturingEvaluator()
    script = [
        {"call": "calculator", "arguments": {"expression": "2+2"}},
        {"finish": "4"},
    ]
    orch = _orchestrator(
        _Benchmark([_task("a")]),
        runner=MockAgentRunner(scripts={"a": script}),
        evaluators=[DeterministicEvaluator(), capture],
    )
    summary = asyncio.run(orch.run())
    (context,) = capture.contexts
    assert (context.task_id, context.repetition) == ("a", 0)
    assert [r.tool_name for r in context.records] == ["calculator"]
    assert context.end_state == {"schema": "inprocess-calls-v0", "calls": 1}
    (result,) = summary.task_results
    assert [e.evaluator_kind for e in result.evaluations] == [
        "deterministic", "state_based",
    ]


def test_declared_applicability_selects_evaluators() -> None:
    capture = _CapturingEvaluator()
    task = _task("a")
    task.applicable_evaluators = ("state_based",)
    orch = _orchestrator(
        _Benchmark([task]), evaluators=[DeterministicEvaluator(), capture]
    )
    (result,) = asyncio.run(orch.run()).task_results
    assert [e.evaluator_kind for e in result.evaluations] == ["state_based"]


def test_declared_kind_the_run_does_not_configure_is_not_evaluated() -> None:
    task = _task("a")
    task.applicable_evaluators = ("tool_use",)
    summary = asyncio.run(_orchestrator(_Benchmark([task])).run())
    (result,) = summary.task_results
    assert result.evaluation.evaluated is False
    assert result.evaluation.not_evaluated_cause == "configuration"
    assert summary.not_evaluated_by_cause == {"configuration": 1}


# ---------------------------------------------------------------------------
# ScoreResult's three-way split (D20) and not-evaluated causes (D16)
# ---------------------------------------------------------------------------


def test_score_result_splits_three_ways_and_meets_the_protocol() -> None:
    """Finding 2. Fail-first: the old scorer counted not-evaluated as failed."""
    scorer = SimpleScorer(RunId("r"))
    assert isinstance(scorer, Scorer)
    score = scorer.summarize([_result("d"), _result("d", evaluated=False)])
    assert (score.passed_tasks, score.failed_tasks, score.not_evaluated_tasks) == (
        1, 0, 1,
    )
    assert score.pass_rate == pytest.approx(0.5)
    assert score.run_id == "r"


def test_missing_environment_is_not_evaluated_for_configuration() -> None:
    class _NoTools(_Benchmark):
        def prepare_environment(self, task):  # type: ignore[no-untyped-def]
            return EnvHandle(task_id=task.task_id)

    summary = asyncio.run(_orchestrator(_NoTools([_task("a")])).run())
    (result,) = summary.task_results
    assert result.evaluation.not_evaluated_cause == "configuration"
    assert summary.not_evaluated_by_cause == {"configuration": 1}
    assert summary.score_result is not None
    assert summary.score_result.not_evaluated_tasks == 1


def test_summary_carries_an_empty_preflight_list_until_a_pack_runs_one(
    tmp_path: Path,
) -> None:
    out, _ = _toy_bundle(tmp_path)
    summary = json.loads((out / "summary.json").read_text())
    assert summary["preflight"] == []
    assert summary["not_evaluated_by_cause"] == {}
