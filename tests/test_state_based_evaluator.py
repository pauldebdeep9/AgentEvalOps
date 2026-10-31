"""StateBasedEvaluator and the template matcher (R24 slice 5; docs/adr/0006
D7, D13)."""

from __future__ import annotations

import asyncio
from typing import Any

from agentevalops.core.schemas import (
    AgentOutput,
    EndStateRecord,
    EvaluationResult,
    EvaluatorContext,
    TaskSpec,
    TerminationReason,
)
from agentevalops.core.types import TaskId
from agentevalops.evaluators.state_based import StateBasedEvaluator
from agentevalops.evaluators.templates import matches, mismatches

T = TaskId("t")
_OUT = AgentOutput(
    success=True, termination_reason=TerminationReason.COMPLETED, final_answer="x"
)
_ACTUAL: dict[str, Any] = {
    "schema": "seed-case-handling-v0",
    "decisions": [{"case_id": "C1", "status": "approved", "reason_codes": ["ELIG-OK"]}],
    "notices": [{"case_id": "C1", "ai_disclosure": True, "body": "long text"}],
    "referrals": [],
    "exports": [],
}


def _task(expected: Any = None) -> TaskSpec:
    metadata = {} if expected is None else {"expected_end_state": expected}
    return TaskSpec(task_id=T, benchmark_id="b", description="d", metadata=metadata)


def _context(state: dict[str, Any] | None, task_id: TaskId = T) -> EvaluatorContext:
    states = () if state is None else (EndStateRecord(task_id, 0, state),)
    return EvaluatorContext(end_states=states, task_id=T, repetition=0)


def _run(task: TaskSpec, context: EvaluatorContext | None) -> EvaluationResult:
    return asyncio.run(StateBasedEvaluator().evaluate(task, _OUT, [], context))


def test_ids() -> None:
    ev = StateBasedEvaluator()
    assert (ev.evaluator_id, ev.evaluator_kind) == ("state-based-v1", "state_based")


def test_not_applicable_without_an_expected_end_state() -> None:
    for task in (_task(), _task([]), _task("x")):
        result = _run(task, _context(_ACTUAL))
        assert (result.applicable, result.passed, result.evaluated) == (
            False,
            False,
            True,
        )


def test_a_template_that_the_end_state_satisfies_passes() -> None:
    expected = {
        "decisions": [{"case_id": "C1", "status": "approved"}],
        "notices": [{"case_id": "C1", "ai_disclosure": True}],
        "exports": [],
    }
    result = _run(_task(expected), _context(_ACTUAL))
    assert result.passed and result.score == 1.0
    assert "3 key(s) checked, schema seed-case-handling-v0" in result.notes


def test_every_departure_is_named_by_path() -> None:
    expected = {
        "decisions": [{"case_id": "C1", "status": "rejected"}],
        "notices": [],
        "referrals": [{"case_id": "C1"}],
        "audit": True,
    }
    result = _run(_task(expected), _context(_ACTUAL))
    assert not result.passed and result.score == 0.0
    assert result.notes.split("; ") == [
        "decisions[0].status: expected 'rejected', got 'approved'",
        "notices: expected 0 item(s), got 1",
        "referrals: expected 1 item(s), got 0",
        "audit: missing",
    ]


def test_no_end_state_for_this_task_run_fails() -> None:
    task = _task({"exports": []})
    assert "no end state was recorded" in _run(task, None).notes
    assert not _run(task, _context(None)).passed
    assert not _run(task, _context(_ACTUAL, task_id=TaskId("other"))).passed


# ---------------------------------------------------------------------------
# The template rule itself
# ---------------------------------------------------------------------------


def test_templates_name_keys_lists_in_full_and_scalars_exactly() -> None:
    assert mismatches({"a": 1}, {"a": 1, "b": 2}) == []
    assert mismatches({"a": {"b": [1, 2]}}, {"a": {"b": [1, 2], "c": 3}}) == []
    assert mismatches([{"x": 1}], [{"x": 1, "y": 2}]) == []
    assert mismatches({"a": [1]}, {"a": [1, 2]}) == ["a: expected 1 item(s), got 2"]
    assert mismatches({"a": {"b": 1}}, {"a": {"b": 2}}) == ["a.b: expected 1, got 2"]
    assert mismatches({"a": {"b": 1}}, {"a": [1]}) == ["a: expected a mapping, got [1]"]
    assert mismatches({"a": [1]}, {"a": "1"}) == ["a: expected a list, got '1'"]
    assert mismatches({"a": [1]}, {"a": {"0": 1}}) == [
        "a: expected a list, got {'0': 1}"
    ]
    assert mismatches("x", "y") == ["<root>: expected 'x', got 'y'"]
    assert mismatches({"a": [[1, 2]]}, {"a": [[1, 3]]}) == [
        "a[0][1]: expected 2, got 3"
    ]
    long = "y" * 80
    assert mismatches("x", long) == [f"<root>: expected 'x', got '{'y' * 56}..."]


def test_booleans_do_not_match_numbers() -> None:
    assert mismatches({"on": True}, {"on": True}) == []
    assert mismatches({"on": True}, {"on": 1}) == ["on: expected True, got 1"]
    assert mismatches({"n": 1}, {"n": True}) == ["n: expected 1, got True"]
    assert matches({"on": False}, {"on": False}) and not matches(
        {"on": False}, {"on": 0}
    )
