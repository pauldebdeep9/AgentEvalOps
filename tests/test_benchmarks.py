"""Tests for ToyBenchmarkAdapter."""

from __future__ import annotations

from agentevalops.benchmarks.toy.adapter import ToyBenchmarkAdapter
from agentevalops.core.schemas import AgentOutput, TerminationReason
from agentevalops.core.types import TaskId


def _success_output() -> AgentOutput:
    return AgentOutput(
        success=True,
        termination_reason=TerminationReason.COMPLETED,
        final_answer="mock answer",
        total_cost_usd=0.0,
        total_tokens=10,
        wall_seconds=0.01,
    )


def _fail_output() -> AgentOutput:
    return AgentOutput(
        success=False,
        termination_reason=TerminationReason.AGENT_ERROR,
        error="boom",
    )


def test_list_tasks_returns_two() -> None:
    adapter = ToyBenchmarkAdapter()
    tasks = list(adapter.list_tasks())
    assert len(tasks) == 2


def test_list_tasks_all_have_toy_benchmark_id() -> None:
    adapter = ToyBenchmarkAdapter()
    for task in adapter.list_tasks():
        assert task.benchmark_id == "toy"


def test_list_tasks_ids() -> None:
    adapter = ToyBenchmarkAdapter()
    ids = {t.task_id for t in adapter.list_tasks()}
    assert TaskId("toy-001") in ids
    assert TaskId("toy-002") in ids


def test_list_tasks_filter_by_task_id() -> None:
    adapter = ToyBenchmarkAdapter()
    tasks = list(adapter.list_tasks({"task_ids": [TaskId("toy-001")]}))
    assert len(tasks) == 1
    assert tasks[0].task_id == TaskId("toy-001")


def test_grade_success() -> None:
    adapter = ToyBenchmarkAdapter()
    task = list(adapter.list_tasks())[0]
    result = adapter.grade(task, _success_output())
    assert result.passed is True
    assert result.score == 1.0


def test_grade_failure() -> None:
    adapter = ToyBenchmarkAdapter()
    task = list(adapter.list_tasks())[0]
    result = adapter.grade(task, _fail_output())
    assert result.passed is False
    assert result.score == 0.0


def test_benchmark_id_and_version() -> None:
    adapter = ToyBenchmarkAdapter()
    assert adapter.benchmark_id == "toy"
    assert adapter.benchmark_version == "0.2.0"
