"""Tests for minieval.benchmarks.toy.

This module is data, not logic: the tests check that the data is
internally coherent (ids, descriptions, criteria keys) and that each task
actually exercises what it claims to — either from the criteria alone, or,
where the claim is about runtime behaviour, by running it through
MockRunner. Constructed inputs otherwise; MockRunner is used only for the
behavioural section below.
"""

from __future__ import annotations

import asyncio
import re

from minieval.benchmarks import toy
from minieval.errors import InfrastructureError
from minieval.runners.mock import MOCK_WRONG_ANSWER, MockRunner
from minieval.schemas import (
    ResourceLimits,
    RunId,
    Stability,
    TaskSpec,
    TraceEvent,
    TraceEventKind,
)

_MOCK_RUNNER_KEYS = {
    "mock_answer",
    "mock_tools",
    "mock_fail",
    "mock_extra_steps",
    "mock_pass_on",
}
_EVALUATOR_KEYS = {"expected_substring", "required_tool_names", "required_trace_kinds"}
_KNOWN_CRITERIA_KEYS = _MOCK_RUNNER_KEYS | _EVALUATOR_KEYS

_STABILITY_TABLE_LINE = re.compile(r"^(T\d{2}(?:,\s*T\d{2})*)\s+([A-Z_]+)$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _task(task_id: str) -> TaskSpec:
    tasks = {str(t.task_id): t for t in toy.toy_tasks()}
    return tasks[task_id]


def _parse_stability_table(docstring: str) -> dict[str, str]:
    """Pull the ``T0X ... STABLITY_NAME`` lines out of toy.py's module
    docstring, so the coverage tests check the table as written rather
    than a copy of it re-typed here."""
    mapping: dict[str, str] = {}
    for line in docstring.splitlines():
        match = _STABILITY_TABLE_LINE.match(line.strip())
        if not match:
            continue
        for task_id in match.group(1).split(","):
            mapping[task_id.strip()] = match.group(2)
    return mapping


async def _collect(
    runner: MockRunner, task: TaskSpec, limits: ResourceLimits
) -> list[TraceEvent]:
    return [event async for event in runner.run(task, limits)]


def _run_task(task: TaskSpec, *, attempt: int = 0) -> list[TraceEvent]:
    runner = MockRunner(run_id=RunId("toy-test"), attempt=attempt)
    return asyncio.run(_collect(runner, task, ResourceLimits()))


async def _collect_or_raise(
    runner: MockRunner, task: TaskSpec, limits: ResourceLimits
) -> tuple[list[TraceEvent], InfrastructureError | None]:
    events: list[TraceEvent] = []
    try:
        async for event in runner.run(task, limits):
            events.append(event)
    except InfrastructureError as exc:
        return events, exc
    return events, None


def _run_task_or_raise(
    task: TaskSpec, *, attempt: int = 0
) -> tuple[list[TraceEvent], InfrastructureError | None]:
    runner = MockRunner(run_id=RunId("toy-test"), attempt=attempt)
    return asyncio.run(_collect_or_raise(runner, task, ResourceLimits()))


# ---------------------------------------------------------------------------
# Structural
# ---------------------------------------------------------------------------


def test_task_ids_are_unique_and_match_naming_scheme() -> None:
    ids = [str(t.task_id) for t in toy.toy_tasks()]
    assert len(ids) == len(set(ids))
    assert set(ids) == {f"T0{i}" for i in range(1, 10)}


def test_every_task_has_a_non_empty_description() -> None:
    for task in toy.toy_tasks():
        assert task.description.strip()


def test_every_criteria_key_is_read_by_something() -> None:
    """Catches a criterion nothing reads: a task that looks like it checks
    something and does not."""
    for task in toy.toy_tasks():
        unknown = set(task.criteria) - _KNOWN_CRITERIA_KEYS
        assert not unknown, f"{task.task_id} has unrecognised criteria keys: {unknown}"


# ---------------------------------------------------------------------------
# Coverage of the design
# ---------------------------------------------------------------------------


def test_stability_table_names_all_nine_tasks_once_each() -> None:
    mapping = _parse_stability_table(toy.__doc__ or "")
    assert sorted(mapping) == [f"T0{i}" for i in range(1, 10)]


def test_stability_table_covers_all_four_stability_classes() -> None:
    """A benchmark that only proves things pass has not tested anything,
    so the classes covered are asserted as a set, not just a count."""
    mapping = _parse_stability_table(toy.__doc__ or "")
    assert set(mapping.values()) == {
        Stability.STABLE_PASS.name,
        Stability.STABLE_FAIL.name,
        Stability.NOT_EVALUATED.name,
        Stability.FLAKY.name,
    }


# ---------------------------------------------------------------------------
# Per-task claims
# ---------------------------------------------------------------------------


def test_t04_answer_does_not_match_its_expected_substring() -> None:
    task = _task("T04")
    assert task.criteria["expected_substring"] not in task.criteria["mock_answer"]


def test_t05_answer_matches_but_required_tool_is_missing() -> None:
    """T05 must fail for exactly one reason. If its answer ever stops
    matching, it becomes a duplicate of T04 and tests nothing new."""
    task = _task("T05")
    assert task.criteria["expected_substring"] in task.criteria["mock_answer"]
    required = set(task.criteria["required_tool_names"])
    available = set(task.criteria["mock_tools"])
    assert not required.issubset(available)


def test_t06_satisfies_its_own_criteria_but_calls_post_invoice() -> None:
    """T06 must fail on policy alone, the same reasoning as T05 in reverse."""
    task = _task("T06")
    assert task.criteria["expected_substring"] in task.criteria["mock_answer"]
    required = set(task.criteria["required_tool_names"])
    available = set(task.criteria["mock_tools"])
    assert required.issubset(available)
    assert "post_invoice" in available


def test_t07_satisfies_its_own_criteria() -> None:
    """The step budget must be the only thing that can fail T07."""
    task = _task("T07")
    assert task.criteria["expected_substring"] in task.criteria["mock_answer"]
    required = set(task.criteria["required_tool_names"])
    available = set(task.criteria["mock_tools"])
    assert required.issubset(available)


def test_t08_uses_infra_fail_mode_with_zero_tools() -> None:
    task = _task("T08")
    assert task.criteria["mock_fail"] == "infra"
    assert task.criteria.get("mock_tools", []) == []


def test_t09_pass_on_does_not_cover_every_attempt_in_range_three() -> None:
    task = _task("T09")
    pass_on = set(task.criteria["mock_pass_on"])
    assert not set(range(3)).issubset(pass_on)


# ---------------------------------------------------------------------------
# Behavioural — the one place MockRunner is used, because the claim is
# about what the tasks actually produce, not what they declare
# ---------------------------------------------------------------------------


def test_t07_terminal_steps_exceed_default_max_steps() -> None:
    events = _run_task(_task("T07"))
    assert events[-1].payload["steps"] > ResourceLimits().max_steps


def test_t08_raises_infrastructure_error_with_no_terminal() -> None:
    events, exc = _run_task_or_raise(_task("T08"))
    assert isinstance(exc, InfrastructureError)
    assert all(e.kind is not TraceEventKind.AGENT_TERMINAL for e in events)


def test_t09_final_answers_differ_across_attempts_per_pass_on() -> None:
    task = _task("T09")
    pass_on = set(task.criteria["mock_pass_on"])
    base_answer = task.criteria["mock_answer"]
    for attempt in range(3):
        final_answer = _run_task(task, attempt=attempt)[-1].payload["final_answer"]
        if attempt in pass_on:
            assert final_answer == base_answer
        else:
            assert final_answer == MOCK_WRONG_ANSWER
