"""Unit tests for ToyBenchmarkAdapter, tasks, and scenario registry."""

from __future__ import annotations

import pytest

from agentevalops.benchmarks.toy.adapter import ToyBenchmarkAdapter
from agentevalops.benchmarks.toy.scenarios import (
    SCENARIO_DESCRIPTIONS,
    SCENARIO_TASKS,
    SUPPORTED_SCENARIOS,
)
from agentevalops.benchmarks.toy.tasks import (
    FAILURE_TASKS,
    MIXED_TASKS,
    SMOKE_TASKS,
)
from agentevalops.core.errors import ConfigurationError
from agentevalops.core.protocols import BenchmarkAdapter
from agentevalops.core.schemas import AgentInput, AgentOutput, TerminationReason
from agentevalops.core.types import TaskId

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _success_output(answer: str = "mock answer") -> AgentOutput:
    return AgentOutput(
        success=True,
        termination_reason=TerminationReason.COMPLETED,
        final_answer=answer,
    )


def _fail_output() -> AgentOutput:
    return AgentOutput(
        success=False,
        termination_reason=TerminationReason.AGENT_ERROR,
    )


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_toy_adapter_satisfies_benchmark_adapter_protocol() -> None:
    adapter = ToyBenchmarkAdapter()
    assert isinstance(adapter, BenchmarkAdapter)


def test_toy_adapter_has_benchmark_id() -> None:
    assert ToyBenchmarkAdapter.benchmark_id == "toy"


def test_toy_adapter_has_benchmark_version() -> None:
    assert ToyBenchmarkAdapter.benchmark_version == "0.2.0"


# ---------------------------------------------------------------------------
# Default scenario
# ---------------------------------------------------------------------------


def test_default_scenario_is_smoke() -> None:
    adapter = ToyBenchmarkAdapter()
    assert adapter.scenario == "smoke"


def test_default_scenario_returns_two_tasks() -> None:
    adapter = ToyBenchmarkAdapter()
    assert len(list(adapter.list_tasks())) == 2


def test_default_scenario_task_ids() -> None:
    adapter = ToyBenchmarkAdapter()
    ids = {t.task_id for t in adapter.list_tasks()}
    assert TaskId("toy-001") in ids
    assert TaskId("toy-002") in ids


def test_default_scenario_all_have_toy_benchmark_id() -> None:
    adapter = ToyBenchmarkAdapter()
    for task in adapter.list_tasks():
        assert task.benchmark_id == "toy"


# ---------------------------------------------------------------------------
# Unknown scenario
# ---------------------------------------------------------------------------


def test_unknown_scenario_raises_configuration_error() -> None:
    with pytest.raises(ConfigurationError, match="Unknown toy scenario"):
        ToyBenchmarkAdapter(scenario="nonexistent")


def test_unknown_scenario_message_lists_supported() -> None:
    with pytest.raises(ConfigurationError, match="smoke"):
        ToyBenchmarkAdapter(scenario="bogus")


# ---------------------------------------------------------------------------
# Supported scenarios set
# ---------------------------------------------------------------------------


def test_supported_scenarios_contains_all_expected() -> None:
    assert SUPPORTED_SCENARIOS >= {
        "smoke",
        "failure",
        "policy_violation",
        "trace_limit",
        "mixed",
    }


def test_all_scenarios_have_task_lists() -> None:
    for scenario in SUPPORTED_SCENARIOS:
        assert scenario in SCENARIO_TASKS
        assert len(SCENARIO_TASKS[scenario]) > 0


def test_all_scenarios_have_descriptions() -> None:
    for scenario in SUPPORTED_SCENARIOS:
        assert scenario in SCENARIO_DESCRIPTIONS
        assert len(SCENARIO_DESCRIPTIONS[scenario]) > 0


# ---------------------------------------------------------------------------
# Smoke scenario
# ---------------------------------------------------------------------------


def test_smoke_scenario_task_count() -> None:
    adapter = ToyBenchmarkAdapter(scenario="smoke")
    assert len(list(adapter.list_tasks())) == 2


def test_smoke_task_ids_stable() -> None:
    adapter = ToyBenchmarkAdapter(scenario="smoke")
    ids1 = [t.task_id for t in adapter.list_tasks()]
    ids2 = [t.task_id for t in adapter.list_tasks()]
    assert ids1 == ids2


def test_smoke_tasks_have_expected_output_metadata() -> None:
    adapter = ToyBenchmarkAdapter(scenario="smoke")
    for task in adapter.list_tasks():
        assert "expected_output" in task.metadata, (
            f"Task {task.task_id} missing expected_output"
        )


def test_smoke_tasks_have_match_mode_exact() -> None:
    adapter = ToyBenchmarkAdapter(scenario="smoke")
    for task in adapter.list_tasks():
        assert task.metadata.get("match_mode") == "exact", (
            f"Task {task.task_id} expected match_mode=exact"
        )


def test_smoke_tasks_descriptions_non_empty() -> None:
    adapter = ToyBenchmarkAdapter(scenario="smoke")
    for task in adapter.list_tasks():
        assert len(task.description) > 0


# ---------------------------------------------------------------------------
# Failure scenario
# ---------------------------------------------------------------------------


def test_failure_scenario_task_count() -> None:
    adapter = ToyBenchmarkAdapter(scenario="failure")
    assert len(list(adapter.list_tasks())) == 2


def test_failure_tasks_have_mock_answer_or_expected_substring() -> None:
    """Failure tasks must provide a mechanism that causes evaluation to fail."""
    adapter = ToyBenchmarkAdapter(scenario="failure")
    for task in adapter.list_tasks():
        has_mock_answer = "mock_answer" in task.metadata
        has_expected_substring = "expected_substring" in task.metadata
        assert has_mock_answer or has_expected_substring, (
            f"Failure task {task.task_id} has no failure mechanism"
        )


def test_failure_task_ids_unique() -> None:
    ids = [t.task_id for t in FAILURE_TASKS]
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# Mixed scenario
# ---------------------------------------------------------------------------


def test_mixed_scenario_task_count() -> None:
    adapter = ToyBenchmarkAdapter(scenario="mixed")
    assert len(list(adapter.list_tasks())) == 3


def test_mixed_tasks_ids_unique() -> None:
    ids = [t.task_id for t in MIXED_TASKS]
    assert len(ids) == len(set(ids))


def test_mixed_tasks_have_nonempty_descriptions() -> None:
    for task in MIXED_TASKS:
        assert len(task.description) > 0


# ---------------------------------------------------------------------------
# policy_violation and trace_limit use smoke tasks
# ---------------------------------------------------------------------------


def test_policy_violation_scenario_uses_smoke_tasks() -> None:
    pv_tasks = list(ToyBenchmarkAdapter(scenario="policy_violation").list_tasks())
    smoke_tasks = list(ToyBenchmarkAdapter(scenario="smoke").list_tasks())
    assert [t.task_id for t in pv_tasks] == [t.task_id for t in smoke_tasks]


def test_trace_limit_scenario_uses_smoke_tasks() -> None:
    tl_tasks = list(ToyBenchmarkAdapter(scenario="trace_limit").list_tasks())
    smoke_tasks = list(ToyBenchmarkAdapter(scenario="smoke").list_tasks())
    assert [t.task_id for t in tl_tasks] == [t.task_id for t in smoke_tasks]


# ---------------------------------------------------------------------------
# filter_spec
# ---------------------------------------------------------------------------


def test_filter_by_task_id_smoke() -> None:
    adapter = ToyBenchmarkAdapter(scenario="smoke")
    tasks = list(adapter.list_tasks({"task_ids": [TaskId("toy-001")]}))
    assert len(tasks) == 1
    assert tasks[0].task_id == TaskId("toy-001")


def test_filter_by_unknown_task_id_returns_empty() -> None:
    adapter = ToyBenchmarkAdapter(scenario="smoke")
    tasks = list(adapter.list_tasks({"task_ids": [TaskId("nonexistent")]}))
    assert tasks == []


def test_filter_spec_none_returns_all() -> None:
    adapter = ToyBenchmarkAdapter(scenario="smoke")
    assert len(list(adapter.list_tasks(None))) == 2


# ---------------------------------------------------------------------------
# grade()
# ---------------------------------------------------------------------------


def test_grade_passes_when_agent_succeeds() -> None:
    adapter = ToyBenchmarkAdapter()
    task = list(adapter.list_tasks())[0]
    result = adapter.grade(task, _success_output())
    assert result.passed is True
    assert result.score == 1.0


def test_grade_fails_when_agent_fails() -> None:
    adapter = ToyBenchmarkAdapter()
    task = list(adapter.list_tasks())[0]
    result = adapter.grade(task, _fail_output())
    assert result.passed is False
    assert result.score == 0.0


def test_grade_evaluator_id_is_toy_grade() -> None:
    adapter = ToyBenchmarkAdapter()
    task = list(adapter.list_tasks())[0]
    result = adapter.grade(task, _success_output())
    assert result.evaluator_id == "toy-grade"


def test_grade_does_not_check_answer_content() -> None:
    """grade() is success-flag only; wrong answer still grades as passed."""
    adapter = ToyBenchmarkAdapter()
    task = list(adapter.list_tasks())[0]
    # Return a clearly wrong answer; grade() should still pass (agent succeeded).
    result = adapter.grade(task, _success_output(answer="completely wrong"))
    assert result.passed is True


# ---------------------------------------------------------------------------
# AgentInput preparation (simulate what orchestrator does)
# ---------------------------------------------------------------------------


def test_task_provides_expected_output_for_agent_input() -> None:
    """Ensure task metadata gives MockAgentRunner everything it needs."""
    adapter = ToyBenchmarkAdapter(scenario="smoke")
    for task in adapter.list_tasks():
        agent_input = AgentInput(task=task, context={"run_id": "test"})
        assert agent_input.task.metadata.get("expected_output") is not None


def test_failure_tasks_provide_mock_answer() -> None:
    adapter = ToyBenchmarkAdapter(scenario="failure")
    for task in adapter.list_tasks():
        agent_input = AgentInput(task=task, context={"run_id": "test"})
        # Either mock_answer or expected_substring must be set
        meta = agent_input.task.metadata
        assert "mock_answer" in meta or "expected_substring" in meta


# ---------------------------------------------------------------------------
# SMOKE_TASKS module constant
# ---------------------------------------------------------------------------


def test_smoke_tasks_constant_has_two_entries() -> None:
    assert len(SMOKE_TASKS) == 2


def test_smoke_tasks_constant_ids_match_adapter() -> None:
    adapter_ids = {
        t.task_id for t in ToyBenchmarkAdapter(scenario="smoke").list_tasks()
    }
    const_ids = {t.task_id for t in SMOKE_TASKS}
    assert adapter_ids == const_ids


# ---------------------------------------------------------------------------
# Task metadata completeness across all scenarios
# ---------------------------------------------------------------------------


def test_all_tasks_have_nonempty_task_id() -> None:
    for scenario in SUPPORTED_SCENARIOS:
        for task in SCENARIO_TASKS[scenario]:
            assert task.task_id, f"Empty task_id in scenario {scenario}"


def test_all_tasks_have_toy_benchmark_id() -> None:
    for scenario in SUPPORTED_SCENARIOS:
        for task in SCENARIO_TASKS[scenario]:
            assert task.benchmark_id == "toy", (
                f"task {task.task_id} in {scenario} has wrong benchmark_id"
            )


def test_all_tasks_have_nonempty_description() -> None:
    for scenario in SUPPORTED_SCENARIOS:
        for task in SCENARIO_TASKS[scenario]:
            assert task.description, (
                f"Empty description in task {task.task_id} ({scenario})"
            )


def test_smoke_tasks_have_no_mock_answer() -> None:
    """Smoke tasks must NOT override the answer — they should always pass."""
    for task in SMOKE_TASKS:
        assert "mock_answer" not in task.metadata, (
            f"Smoke task {task.task_id} has mock_answer override"
        )
