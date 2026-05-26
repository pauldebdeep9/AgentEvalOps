"""Tests that all protocols are importable and mock classes can satisfy them.

Protocol conformance is tested two ways:
1. ``isinstance`` checks via ``@runtime_checkable`` (structural attribute check).
2. Calling the synchronous methods on mocks and asserting correct output.

Async protocol methods (``run``, ``evaluate``, etc.) are declared on mocks
but not invoked here — async integration tests belong in a later WBS phase.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

import pytest

from agentevalops.core.errors import (
    AgentEvalOpsError,
    BundleError,
    ConfigurationError,
    InfrastructureError,
    PolicyError,
    ResourceLimitExceeded,
    RunError,
)
from agentevalops.core.protocols import (
    AgentRunner,
    ArtifactStore,
    BenchmarkAdapter,
    CloudBackend,
    Evaluator,
    PolicyChecker,
    ReportGenerator,
    Scorer,
    TraceStore,
)
from agentevalops.core.schemas import (
    AgentInput,
    AgentOutput,
    EvaluationResult,
    PolicySpec,
    PolicyVerdict,
    ResourceLimits,
    ScoreResult,
    TaskSpec,
    TerminationReason,
    TraceEvent,
    TraceEventKind,
    Verdict,
)
from agentevalops.core.types import BACKEND_LOCAL, AgentId, BackendId, RunId, TaskId

# ---------------------------------------------------------------------------
# Import smoke tests
# ---------------------------------------------------------------------------


def test_all_protocols_importable() -> None:
    for cls in (
        AgentRunner,
        BenchmarkAdapter,
        TraceStore,
        ArtifactStore,
        Evaluator,
        Scorer,
        PolicyChecker,
        ReportGenerator,
        CloudBackend,
    ):
        assert cls is not None


def test_all_errors_importable() -> None:
    for cls in (
        AgentEvalOpsError,
        ConfigurationError,
        RunError,
        ResourceLimitExceeded,
        InfrastructureError,
        BundleError,
        PolicyError,
    ):
        assert issubclass(cls, Exception)


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class TestErrorHierarchy:
    def test_resource_limit_is_run_error(self) -> None:
        assert issubclass(ResourceLimitExceeded, RunError)

    def test_infra_error_is_run_error(self) -> None:
        assert issubclass(InfrastructureError, RunError)

    def test_run_error_is_base_error(self) -> None:
        assert issubclass(RunError, AgentEvalOpsError)

    def test_bundle_error_is_base_error(self) -> None:
        assert issubclass(BundleError, AgentEvalOpsError)

    def test_policy_error_is_base_error(self) -> None:
        assert issubclass(PolicyError, AgentEvalOpsError)

    def test_configuration_error_is_base_error(self) -> None:
        assert issubclass(ConfigurationError, AgentEvalOpsError)

    def test_errors_are_catchable_as_base(self) -> None:
        with pytest.raises(AgentEvalOpsError):
            raise ResourceLimitExceeded("token budget exhausted")

    def test_infra_error_message(self) -> None:
        err = InfrastructureError("sandbox OOM")
        assert "sandbox OOM" in str(err)


# ---------------------------------------------------------------------------
# Mock implementations
# ---------------------------------------------------------------------------


class MockBenchmarkAdapter:
    benchmark_id = "toy"
    benchmark_version = "0.1.0"

    def list_tasks(
        self, filter_spec: dict[str, Any] | None = None
    ) -> Iterable[TaskSpec]:
        return [
            TaskSpec(TaskId("t-1"), "toy", "Fix the off-by-one error"),
            TaskSpec(TaskId("t-2"), "toy", "Add a missing null check"),
        ]

    def grade(self, task: TaskSpec, output: AgentOutput) -> EvaluationResult:
        return EvaluationResult(
            evaluator_id="toy-grade",
            evaluator_kind="deterministic",
            score=1.0 if output.success else 0.0,
            passed=output.success,
        )


class MockScorer:
    scorer_id = "pass-at-1"

    def summarize(
        self,
        scores: Sequence[EvaluationResult],
        baseline: ScoreResult | None = None,
    ) -> ScoreResult:
        total = len(scores)
        passed = sum(1 for s in scores if s.passed)
        agg = passed / total if total > 0 else 0.0
        return ScoreResult(
            scorer_id=self.scorer_id,
            run_id=RunId("run-test"),
            task_scores=list(scores),
            aggregate_score=agg,
        )


class MockCloudBackend:
    backend_id: BackendId = BACKEND_LOCAL

    async def submit_job(self, spec: dict[str, Any]) -> str:
        return "job-mock-1"

    async def job_status(self, handle: str) -> dict[str, Any]:
        return {"status": "completed", "handle": handle}

    async def cancel_job(self, handle: str) -> None:
        pass

    async def fetch_secret(self, ref: str) -> str:
        return "mock-secret-value"

    async def export_telemetry(self, run_id: RunId) -> None:
        pass


class MockAgentRunner:
    agent_id: AgentId = AgentId("mock-runner")

    async def run(self, input: AgentInput, resource_limits: ResourceLimits) -> Any:
        # Declared to satisfy the protocol shape; async iteration tested later.
        raise NotImplementedError("use in async tests only")


class MockEvaluator:
    evaluator_id = "mock-eval"
    evaluator_kind = "deterministic"

    async def evaluate(
        self,
        task: TaskSpec,
        output: AgentOutput,
        trace: list[TraceEvent],
    ) -> EvaluationResult:
        raise NotImplementedError("use in async tests only")


class MockPolicyChecker:
    checker_id = "mock-policy"

    async def check(
        self,
        policy: PolicySpec,
        output: AgentOutput,
        trace: list[TraceEvent],
    ) -> PolicyVerdict:
        raise NotImplementedError("use in async tests only")


class MockReportGenerator:
    report_id = "mock-report"

    async def render(self, result: ScoreResult, trace: list[TraceEvent]) -> str:
        raise NotImplementedError("use in async tests only")


class MockTraceStore:
    async def append(self, run_id: RunId, event: TraceEvent) -> None:
        pass

    def stream(self, run_id: RunId) -> Any:
        raise NotImplementedError("use in async tests only")

    async def finalize(self, run_id: RunId) -> None:
        pass


class MockArtifactStore:
    async def put(self, run_id: RunId, path: str, content: bytes) -> str:
        return f"local://{run_id}/{path}"

    async def get(self, ref: str) -> bytes:
        return b""

    async def list(self, run_id: RunId) -> list[str]:
        return []


# ---------------------------------------------------------------------------
# isinstance conformance (runtime_checkable — checks attribute presence)
# ---------------------------------------------------------------------------


def test_mock_benchmark_adapter_isinstance() -> None:
    assert isinstance(MockBenchmarkAdapter(), BenchmarkAdapter)


def test_mock_scorer_isinstance() -> None:
    assert isinstance(MockScorer(), Scorer)


def test_mock_cloud_backend_isinstance() -> None:
    assert isinstance(MockCloudBackend(), CloudBackend)


def test_mock_agent_runner_isinstance() -> None:
    assert isinstance(MockAgentRunner(), AgentRunner)


def test_mock_evaluator_isinstance() -> None:
    assert isinstance(MockEvaluator(), Evaluator)


def test_mock_policy_checker_isinstance() -> None:
    assert isinstance(MockPolicyChecker(), PolicyChecker)


def test_mock_report_generator_isinstance() -> None:
    assert isinstance(MockReportGenerator(), ReportGenerator)


def test_mock_trace_store_isinstance() -> None:
    assert isinstance(MockTraceStore(), TraceStore)


def test_mock_artifact_store_isinstance() -> None:
    assert isinstance(MockArtifactStore(), ArtifactStore)


# ---------------------------------------------------------------------------
# Functional tests for synchronous protocol methods
# ---------------------------------------------------------------------------


class TestBenchmarkAdapter:
    def test_list_tasks_returns_task_specs(self) -> None:
        adapter = MockBenchmarkAdapter()
        tasks = list(adapter.list_tasks())
        assert len(tasks) == 2
        assert all(isinstance(t, TaskSpec) for t in tasks)

    def test_list_tasks_with_filter(self) -> None:
        adapter = MockBenchmarkAdapter()
        tasks = list(adapter.list_tasks(filter_spec={"difficulty": "easy"}))
        assert len(tasks) == 2

    def test_grade_success(self) -> None:
        adapter = MockBenchmarkAdapter()
        task = TaskSpec(TaskId("t-1"), "toy", "Fix bug")
        output = AgentOutput(
            success=True, termination_reason=TerminationReason.COMPLETED
        )
        result = adapter.grade(task, output)
        assert result.passed is True
        assert result.score == pytest.approx(1.0)

    def test_grade_failure(self) -> None:
        adapter = MockBenchmarkAdapter()
        task = TaskSpec(TaskId("t-1"), "toy", "Fix bug")
        output = AgentOutput(
            success=False, termination_reason=TerminationReason.AGENT_ERROR
        )
        result = adapter.grade(task, output)
        assert result.passed is False
        assert result.score == pytest.approx(0.0)


class TestScorer:
    def test_all_pass(self) -> None:
        scorer = MockScorer()
        scores = [
            EvaluationResult("e", "deterministic", 1.0, True),
            EvaluationResult("e", "deterministic", 1.0, True),
        ]
        result = scorer.summarize(scores)
        assert result.aggregate_score == pytest.approx(1.0)

    def test_mixed(self) -> None:
        scorer = MockScorer()
        scores = [
            EvaluationResult("e", "deterministic", 1.0, True),
            EvaluationResult("e", "deterministic", 0.0, False),
            EvaluationResult("e", "deterministic", 1.0, True),
        ]
        result = scorer.summarize(scores)
        assert result.aggregate_score == pytest.approx(2 / 3)

    def test_empty_scores(self) -> None:
        scorer = MockScorer()
        result = scorer.summarize([])
        assert result.aggregate_score == 0.0

    def test_result_contains_task_scores(self) -> None:
        scorer = MockScorer()
        scores = [EvaluationResult("e", "deterministic", 1.0, True)]
        result = scorer.summarize(scores)
        assert len(result.task_scores) == 1

    def test_baseline_delta_is_none_without_baseline(self) -> None:
        scorer = MockScorer()
        result = scorer.summarize([EvaluationResult("e", "deterministic", 1.0, True)])
        assert result.baseline_delta is None


# ---------------------------------------------------------------------------
# Core module-level import
# ---------------------------------------------------------------------------


def test_core_init_re_exports_protocols() -> None:
    import agentevalops.core as core

    assert core.AgentRunner is AgentRunner
    assert core.Scorer is Scorer
    assert core.CloudBackend is CloudBackend


def test_core_init_re_exports_schemas() -> None:
    import agentevalops.core as core

    assert core.TaskSpec is TaskSpec
    assert core.TraceEventKind is TraceEventKind
    assert core.Verdict is Verdict


def test_core_init_re_exports_errors() -> None:
    import agentevalops.core as core

    assert core.AgentEvalOpsError is AgentEvalOpsError
    assert core.ResourceLimitExceeded is ResourceLimitExceeded
