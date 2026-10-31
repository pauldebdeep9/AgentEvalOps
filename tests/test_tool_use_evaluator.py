"""ToolUseEvaluator (R24 slice 5; docs/adr/0006 D13, D14, D17)."""

from __future__ import annotations

import asyncio
from typing import Any

from agentevalops.core.schemas import (
    AgentOutput,
    EvaluationResult,
    EvaluatorContext,
    Ref,
    TaskSpec,
    TerminationReason,
    ToolCallRecord,
    TraceEvent,
    TraceEventKind,
)
from agentevalops.core.types import RunId, TaskId
from agentevalops.evaluators.tool_use import ToolUseEvaluator

T = TaskId("t")
_OUT = AgentOutput(
    success=True, termination_reason=TerminationReason.COMPLETED, final_answer="x"
)


def _record(
    index: int,
    tool: str,
    arguments: dict[str, Any] | None = None,
    *,
    task_id: TaskId = T,
    repetition: int = 0,
    executed: bool = True,
    error: bool = False,
) -> ToolCallRecord:
    return ToolCallRecord(
        task_id=task_id,
        repetition=repetition,
        index=index,
        tool_name=tool,
        arguments=arguments or {},
        permission="read",
        data_class="normal",
        executed=executed,
        result="error" if error else "ok",
        error=error,
    )


def _report(tool: str, arguments: dict[str, Any] | None = None) -> TraceEvent:
    return TraceEvent(
        run_id=RunId("r"),
        step_index=0,
        kind=TraceEventKind.AGENT_TOOL_CALL,
        payload={"tool_name": tool, "args": arguments or {}},
    )


def _task(tool_use: dict[str, Any] | None = None) -> TaskSpec:
    metadata = {} if tool_use is None else {"tool_use": tool_use}
    return TaskSpec(task_id=T, benchmark_id="b", description="d", metadata=metadata)


def _run(
    task: TaskSpec,
    records: list[ToolCallRecord],
    trace: list[TraceEvent] | None = None,
    context: EvaluatorContext | None = None,
) -> EvaluationResult:
    """Evaluate; the self-reports match *records* unless *trace* is given."""
    if trace is None:
        trace = [
            _report(r.tool_name, r.arguments)
            for r in sorted(records, key=lambda r: r.index)
        ]
    if context is None:
        context = EvaluatorContext(records=tuple(records), task_id=T, repetition=0)
    return asyncio.run(ToolUseEvaluator().evaluate(task, _OUT, trace, context))


def test_ids() -> None:
    ev = ToolUseEvaluator()
    assert (ev.evaluator_id, ev.evaluator_kind) == ("tool-use-v1", "tool_use")


def test_not_applicable_without_a_tool_use_block() -> None:
    for task in (_task(), _task({})):
        result = _run(task, [_record(0, "a")])
        assert (result.applicable, result.passed, result.evaluated) == (
            False,
            False,
            True,
        )
        assert "not applicable" in result.notes


def test_required_call_matches_by_tool_and_argument_template() -> None:
    task = _task({"required": [{"tool": "lookup", "arguments": {"id": "C1"}}]})
    result = _run(
        task, [_record(0, "policy"), _record(1, "lookup", {"id": "C1", "x": 1})]
    )
    assert result.passed and result.score == 1.0
    assert "required lookup{'id': 'C1'}: index 1" in result.notes
    assert result.refs == [Ref("tool_calls", T, 0, 1)]


def test_required_call_missing_or_with_other_arguments_fails() -> None:
    task = _task({"required": [{"tool": "lookup", "arguments": {"id": "C1"}}]})
    assert not _run(task, [_record(0, "lookup", {"id": "C2"})]).passed
    result = _run(task, [_record(0, "policy")])
    assert not result.passed and "required lookup{'id': 'C1'}: missing" in result.notes
    assert result.refs == []


def test_a_call_the_environment_refused_or_that_failed_was_not_made() -> None:
    task = _task({"required": ["lookup"]})
    assert _run(task, [_record(0, "lookup")]).passed
    assert not _run(task, [_record(0, "lookup", executed=False, error=True)]).passed
    assert not _run(task, [_record(0, "lookup", error=True)]).passed


def test_forbidden_calls_by_name_and_by_pattern() -> None:
    by_name = _task({"forbidden": ["export"]})
    assert _run(by_name, [_record(0, "lookup")]).passed
    result = _run(
        by_name, [_record(0, "lookup"), _record(1, "export"), _record(2, "export")]
    )
    assert not result.passed
    assert "forbidden export: called at index 1, 2" in result.notes
    assert result.refs == [Ref("tool_calls", T, 0, 1), Ref("tool_calls", T, 0, 2)]
    # An attempt counts, whether or not it ran.
    assert not _run(by_name, [_record(0, "export", executed=False, error=True)]).passed

    by_pattern = _task(
        {"forbidden": [{"tool": "notice", "arguments": {"ai_disclosure": False}}]}
    )
    assert _run(by_pattern, [_record(0, "notice", {"ai_disclosure": True})]).passed
    assert not _run(by_pattern, [_record(0, "notice", {"ai_disclosure": False})]).passed


def test_order_of_first_calls() -> None:
    task = _task({"order": ["a", "b", "c"]})
    assert _run(
        task, [_record(0, "a"), _record(1, "b"), _record(2, "a"), _record(3, "c")]
    ).passed
    result = _run(task, [_record(0, "b"), _record(1, "a"), _record(2, "c")])
    assert not result.passed
    assert "order ['a', 'b', 'c']: broken (a@1, b@0, c@2)" in result.notes
    assert [r.index for r in result.refs] == [1, 0, 2]
    # A tool never called is required's finding, not order's.
    assert _run(task, [_record(0, "a"), _record(1, "b")]).passed
    assert _run(_task({"order": "a"}), [_record(0, "b"), _record(1, "a")]).passed


def test_a_self_report_mismatch_is_a_finding() -> None:
    """docs/adr/0006 D14. Fail-first: without the cross-check an agent that
    made the required call but reported another passed."""
    task = _task({"required": ["a"]})
    records = [_record(0, "a")]
    assert _run(task, records).passed
    unreported = _run(task, records, trace=[])
    assert not unreported.passed
    assert "self_report_mismatch: unreported_call at 0" in unreported.notes
    assert unreported.refs == [Ref("tool_calls", T, 0, 0)]
    different = _run(task, records, trace=[_report("b")])
    assert "different_call at 0 (recorded a, reported b)" in different.notes
    unrecorded = _run(task, records, trace=[_report("a"), _report("b")])
    assert "unrecorded_report at 1" in unrecorded.notes
    # Position 1 has no record to point at; the required match still cites 0.
    assert unrecorded.refs == [Ref("tool_calls", T, 0, 0)]
    assert "self-reports match the 1 harness record(s)" in _run(task, records).notes


def test_records_are_scoped_to_the_task_run() -> None:
    task = _task({"required": ["a"]})
    other = _record(0, "a", task_id=TaskId("other"))
    later = _record(0, "a", repetition=1)
    context = EvaluatorContext(records=(other, later), task_id=T, repetition=0)
    assert not _run(task, [], trace=[], context=context).passed
    context = EvaluatorContext(
        records=(other, later, _record(0, "a")), task_id=T, repetition=0
    )
    assert _run(task, [], trace=[_report("a")], context=context).passed


def test_no_context_means_no_records() -> None:
    result = asyncio.run(
        ToolUseEvaluator().evaluate(_task({"required": ["a"]}), _OUT, [], None)
    )
    assert not result.passed and "required a: missing" in result.notes


def test_entries_may_be_names_or_mappings_and_junk_is_ignored() -> None:
    task = _task({"required": ["a", 5, {"tool": "b", "arguments": "not a mapping"}]})
    assert _run(task, [_record(0, "a"), _record(1, "b", {"k": 1})]).passed
    assert _run(_task({"required": "a"}), [_record(0, "b")]).passed  # not a list
