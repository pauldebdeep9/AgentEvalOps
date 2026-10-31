"""Tests for BasicPolicyChecker."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from agentevalops.core.schemas import (
    TOOL_NAME_PAYLOAD_KEY,
    AgentOutput,
    EvaluatorContext,
    PolicySpec,
    Ref,
    TerminationReason,
    ToolCallRecord,
    TraceEvent,
    TraceEventKind,
    Verdict,
)
from agentevalops.core.types import RunId, TaskId
from agentevalops.policy.basic_checker import BasicPolicyChecker


def _ok_output(cost: float = 0.0) -> AgentOutput:
    return AgentOutput(
        success=True,
        termination_reason=TerminationReason.COMPLETED,
        total_cost_usd=cost,
        total_tokens=10,
        wall_seconds=0.01,
    )


def _tool_call_event(step: int, tool: str, task: str = "t") -> TraceEvent:
    return TraceEvent(
        run_id=RunId("r1"),
        step_index=step,
        kind=TraceEventKind.AGENT_TOOL_CALL,
        payload={TOOL_NAME_PAYLOAD_KEY: tool, "args": {}},
        task_id=TaskId(task),
    )


def _record(index: int, tool: str, task: str = "t") -> ToolCallRecord:
    return ToolCallRecord(
        task_id=TaskId(task),
        index=index,
        tool_name=tool,
        arguments={},
        permission="read",
        data_class="normal",
        executed=True,
        result="ok",
    )


def _context(*records: ToolCallRecord) -> EvaluatorContext:
    """A whole-run context (docs/adr/0006 D14): the records the harness
    kept, for the deny- and allow-lists."""
    return EvaluatorContext(records=records)


def _reports(*records: ToolCallRecord) -> list[TraceEvent]:
    """Self-reports that match *records*, so the cross-check stays quiet."""
    return [_tool_call_event(r.index, r.tool_name, str(r.task_id)) for r in records]


def test_checker_id() -> None:
    assert BasicPolicyChecker().checker_id == "basic-policy-v1"


def test_pass_no_constraints() -> None:
    checker = BasicPolicyChecker()
    policy = PolicySpec(policy_id="p1")
    result = asyncio.run(checker.check(policy, _ok_output(), []))
    assert result.verdict == Verdict.PASS


def test_pass_cost_under_ceiling() -> None:
    checker = BasicPolicyChecker()
    policy = PolicySpec(policy_id="p1", max_cost_usd=1.0)
    result = asyncio.run(checker.check(policy, _ok_output(cost=0.5), []))
    assert result.verdict == Verdict.PASS


def test_fail_cost_exceeds_ceiling() -> None:
    checker = BasicPolicyChecker()
    policy = PolicySpec(policy_id="p1", max_cost_usd=1.0)
    result = asyncio.run(checker.check(policy, _ok_output(cost=1.01), []))
    assert result.verdict == Verdict.FAIL
    assert "cost" in result.notes


def test_fail_denied_tool_used() -> None:
    """The deny-list reads the harness records (docs/adr/0006 D14) and
    cites them through refs; the legacy citations stay empty."""
    checker = BasicPolicyChecker()
    policy = PolicySpec(policy_id="p1", deny_tool_ids=("bash", "curl"))
    bash = _record(0, "bash")
    result = asyncio.run(
        checker.check(policy, _ok_output(), _reports(bash), _context(bash))
    )
    assert result.verdict == Verdict.FAIL
    assert result.refs == [Ref("tool_calls", TaskId("t"), 0, 0)]
    assert result.citations == []
    assert "denied tool 'bash' at task t run 0 record 0" in result.notes


def test_a_self_report_without_a_record_does_not_trip_the_deny_list() -> None:
    """Fail-first: before R25 the AGENT_TOOL_CALL event alone made this
    FAIL with citation 0. The agent's word is not a call (D14); the
    unmatched report is a WARN."""
    checker = BasicPolicyChecker()
    policy = PolicySpec(policy_id="p1", deny_tool_ids=("bash",))
    trace = [_tool_call_event(0, "bash")]
    result = asyncio.run(checker.check(policy, _ok_output(), trace, _context()))
    assert result.verdict == Verdict.WARN
    assert result.refs == [] and result.citations == []
    assert "self_report_mismatch: task t run 0: unrecorded_report at 0" in result.notes


def test_pass_allowed_tool_used() -> None:
    checker = BasicPolicyChecker()
    policy = PolicySpec(policy_id="p1", deny_tool_ids=("bash",))
    python = _record(0, "python")
    result = asyncio.run(
        checker.check(policy, _ok_output(), _reports(python), _context(python))
    )
    assert result.verdict == Verdict.PASS


def test_fail_combines_cost_and_tool() -> None:
    checker = BasicPolicyChecker()
    policy = PolicySpec(policy_id="p1", max_cost_usd=0.5, deny_tool_ids=("bash",))
    bash = _record(1, "bash")
    result = asyncio.run(
        checker.check(policy, _ok_output(cost=1.0), _reports(bash), _context(bash))
    )
    assert result.verdict == Verdict.FAIL
    assert result.refs == [Ref("tool_calls", TaskId("t"), 0, 1)]
    assert "cost" in result.notes


# ---------------------------------------------------------------------------
# allowed_tool_ids gets its consumer (docs/adr/0006 D15; R25)
# ---------------------------------------------------------------------------


def test_fail_tool_outside_allowed_tool_ids() -> None:
    """Fail-first: before R25 nothing read allowed_tool_ids and this
    passed."""
    checker = BasicPolicyChecker()
    policy = PolicySpec(policy_id="p1", allowed_tool_ids=("read_file",))
    calls = (_record(0, "read_file"), _record(1, "shell"))
    result = asyncio.run(
        checker.check(policy, _ok_output(), _reports(*calls), _context(*calls))
    )
    assert result.verdict == Verdict.FAIL
    assert result.refs == [Ref("tool_calls", TaskId("t"), 0, 1)]
    assert (
        "tool 'shell' outside allowed_tool_ids at task t run 0 record 1" in result.notes
    )


def test_pass_within_allowed_tool_ids_and_empty_list_allows_all() -> None:
    checker = BasicPolicyChecker()
    call = _record(0, "read_file")
    within = PolicySpec(policy_id="p1", allowed_tool_ids=("read_file", "run_tests"))
    result = asyncio.run(
        checker.check(within, _ok_output(), _reports(call), _context(call))
    )
    assert result.verdict == Verdict.PASS
    anything = PolicySpec(policy_id="p1")
    result = asyncio.run(
        checker.check(anything, _ok_output(), _reports(call), _context(call))
    )
    assert result.verdict == Verdict.PASS


def test_a_record_is_cited_once_even_when_both_lists_name_it() -> None:
    checker = BasicPolicyChecker()
    policy = PolicySpec(policy_id="p1", allowed_tool_ids=("a",), deny_tool_ids=("b",))
    b = _record(0, "b")
    result = asyncio.run(checker.check(policy, _ok_output(), _reports(b), _context(b)))
    assert result.verdict == Verdict.FAIL
    assert result.refs == [Ref("tool_calls", TaskId("t"), 0, 0)]


def test_mismatch_is_reported_beside_a_real_fail_and_grouped_per_task_run() -> None:
    checker = BasicPolicyChecker()
    policy = PolicySpec(policy_id="p1", deny_tool_ids=("bash",))
    bash = _record(0, "bash", task="a")
    quiet = _record(0, "python", task="b")
    trace = _reports(bash)  # task b's call is unreported
    result = asyncio.run(
        checker.check(policy, _ok_output(), trace, _context(bash, quiet))
    )
    assert result.verdict == Verdict.FAIL
    assert "denied tool 'bash'" in result.notes
    assert "self_report_mismatch: task b run 0: unreported_call at 0" in result.notes


def test_no_context_means_no_records_and_no_cross_check() -> None:
    checker = BasicPolicyChecker()
    policy = PolicySpec(
        policy_id="p1", deny_tool_ids=("bash",), allowed_tool_ids=("x",)
    )
    result = asyncio.run(
        checker.check(policy, _ok_output(), [_tool_call_event(0, "bash")])
    )
    assert result.verdict == Verdict.PASS


def test_pass_notes_message() -> None:
    checker = BasicPolicyChecker()
    policy = PolicySpec(policy_id="p1")
    result = asyncio.run(checker.check(policy, _ok_output(), []))
    assert result.notes == "all checks passed"


# ---------------------------------------------------------------------------
# WBS 5: max_trace_events check
# ---------------------------------------------------------------------------


def _plain_events(n: int) -> list[TraceEvent]:
    return [
        TraceEvent(
            run_id=RunId("r1"),
            step_index=i,
            kind=TraceEventKind.AGENT_PLAN,
            payload={},
        )
        for i in range(n)
    ]


def test_fail_trace_events_exceeded() -> None:
    checker = BasicPolicyChecker()
    policy = PolicySpec(policy_id="p1", max_trace_events=2)
    result = asyncio.run(checker.check(policy, _ok_output(), _plain_events(5)))
    assert result.verdict == Verdict.FAIL
    assert "5" in result.notes
    assert "2" in result.notes


def test_pass_trace_events_at_limit() -> None:
    checker = BasicPolicyChecker()
    policy = PolicySpec(policy_id="p1", max_trace_events=5)
    result = asyncio.run(checker.check(policy, _ok_output(), _plain_events(5)))
    assert result.verdict == Verdict.PASS


def test_pass_no_max_trace_events() -> None:
    checker = BasicPolicyChecker()
    policy = PolicySpec(policy_id="p1")
    result = asyncio.run(checker.check(policy, _ok_output(), _plain_events(100)))
    assert result.verdict == Verdict.PASS


# ---------------------------------------------------------------------------
# Fail-closed: a would-be PASS on an unevaluated run must not be PASS
#
# Before this fix, BasicPolicyChecker.check() only ever inspected cost, the
# tool deny-list, and trace length -- it never asked whether anything was
# actually evaluated. A run with zero evaluated results (an empty benchmark)
# or where every task failed at the infrastructure layer before finishing
# (e.g. an API outage -- see ROADMAP.md §0's 2026-08-03 infra-failure gap and
# tests/fixtures/bundles/partial-infra-failure-2026-08-03/) tripped none of
# the three checks and silently returned Verdict.PASS, "all checks passed" --
# a release gate failing open. See core/schemas.py's Verdict docstring
# ("Binary-plus-warn") for why the fix uses the pre-existing, previously
# unused WARN member rather than FAIL or a new schema field: WARN doesn't
# conflate "no evaluation occurred" with "the agent violated policy," and
# needs no bundle-contract change since it was already part of the enum.
# ---------------------------------------------------------------------------


def _terminal_event(step: int, success: bool) -> TraceEvent:
    return TraceEvent(
        run_id=RunId("r1"),
        step_index=step,
        kind=TraceEventKind.AGENT_TERMINAL,
        payload={
            "success": success,
            "termination_reason": "completed" if success else "agent_error",
        },
    )


def test_warn_zero_evaluated_results() -> None:
    """Mirrors exactly what orchestration/local.py's _combined_output()
    computes for an empty task_results list (bool([]) is False, so
    all_success is False): success=False, empty trace. Must not PASS.
    """
    checker = BasicPolicyChecker()
    policy = PolicySpec(policy_id="p1", max_cost_usd=1.0)
    output = AgentOutput(success=False, termination_reason=TerminationReason.COMPLETED)
    result = asyncio.run(checker.check(policy, output, []))
    assert result.verdict == Verdict.WARN
    assert "No evaluation occurred" in result.notes


def test_warn_all_tasks_infra_failed_real_fixture() -> None:
    """Driven by the real, preserved partial-infra-failure bundle (not a
    synthetic trace): two AGENT_PLAN events and nothing else, because both
    toy tasks raised InfrastructureError (429) before either reached a
    terminal state. This is what check() would actually receive as `trace`
    for that run -- must not PASS.
    """
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "bundles"
        / "partial-infra-failure-2026-08-03"
        / "traces.jsonl"
    )
    trace: list[TraceEvent] = []
    for line in fixture.read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        trace.append(
            TraceEvent(
                run_id=RunId(d["run_id"]),
                step_index=d["step_index"],
                kind=TraceEventKind(d["kind"]),
                payload=d["payload"],
                cost_delta_usd=d["cost_delta_usd"],
                tokens_delta=d["tokens_delta"],
            )
        )
    assert len(trace) == 2  # sanity: really the fixture's real content

    checker = BasicPolicyChecker()
    policy = PolicySpec(policy_id="p1", max_cost_usd=1.0)
    output = AgentOutput(success=False, termination_reason=TerminationReason.COMPLETED)
    result = asyncio.run(checker.check(policy, output, trace))
    assert result.verdict == Verdict.WARN
    assert "No evaluation occurred" in result.notes


def test_pass_genuinely_evaluated_successful_run_unaffected() -> None:
    """Regression check: a run that actually completed (AGENT_TERMINAL
    present, success=True) must still PASS.
    """
    checker = BasicPolicyChecker()
    policy = PolicySpec(policy_id="p1", max_cost_usd=1.0)
    trace = [
        TraceEvent(
            run_id=RunId("r1"),
            step_index=0,
            kind=TraceEventKind.AGENT_PLAN,
            payload={},
        ),
        _terminal_event(1, success=True),
    ]
    result = asyncio.run(checker.check(policy, _ok_output(), trace))
    assert result.verdict == Verdict.PASS
    assert result.notes == "all checks passed"


def test_pass_not_downgraded_when_task_genuinely_completed_but_failed() -> None:
    """success=False alone must not trigger the WARN path -- only
    success=False AND no AGENT_TERMINAL anywhere in the trace should. A task
    that genuinely ran to completion (here: a real but wrong/failed answer)
    always emits AGENT_TERMINAL as its last event and must still be judged
    by the normal three checks, not downgraded to WARN.
    """
    checker = BasicPolicyChecker()
    policy = PolicySpec(policy_id="p1", max_cost_usd=1.0)
    trace = [
        TraceEvent(
            run_id=RunId("r1"),
            step_index=0,
            kind=TraceEventKind.AGENT_PLAN,
            payload={},
        ),
        _terminal_event(1, success=False),
    ]
    output = AgentOutput(
        success=False, termination_reason=TerminationReason.AGENT_ERROR
    )
    result = asyncio.run(checker.check(policy, output, trace))
    assert result.verdict == Verdict.PASS
    assert result.notes == "all checks passed"


def test_fail_not_masked_by_unevaluated_run() -> None:
    """A genuine finding from checks 1-3 must still surface as FAIL even on
    a run with zero AGENT_TERMINAL events -- the fail-closed fix only
    touches the would-be-PASS path, and must never suppress a real
    violation (e.g. cost that accrued before a later infrastructure
    failure) just because the run didn't finish cleanly.
    """
    checker = BasicPolicyChecker()
    policy = PolicySpec(policy_id="p1", max_cost_usd=0.5)
    trace = [
        TraceEvent(
            run_id=RunId("r1"),
            step_index=0,
            kind=TraceEventKind.AGENT_PLAN,
            payload={},
        ),
    ]
    output = AgentOutput(
        success=False,
        termination_reason=TerminationReason.COMPLETED,
        total_cost_usd=1.0,
    )
    result = asyncio.run(checker.check(policy, output, trace))
    assert result.verdict == Verdict.FAIL
    assert "cost" in result.notes
