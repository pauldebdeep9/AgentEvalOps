"""Tests for minieval.policy.

All outputs and traces are built by hand, never produced by MockRunner —
per CLAUDE.md's constructed-inputs rule, so a runner bug cannot surface as
a policy-checker failure.
"""

from __future__ import annotations

from minieval.policy import check_policy
from minieval.schemas import (
    TOOL_NAME_PAYLOAD_KEY,
    AgentOutput,
    PolicySpec,
    RunId,
    TaskId,
    TerminationReason,
    TraceEvent,
    TraceEventKind,
    Verdict,
)

RUN = RunId("policy-test")
TASK_ID = TaskId("T01")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _output(*, cost_usd: float = 0.0) -> AgentOutput:
    return AgentOutput(
        success=True,
        termination_reason=TerminationReason.COMPLETED,
        final_answer="done",
        cost_usd=cost_usd,
    )


def _tool_call(step_index: int, tool_name: str) -> TraceEvent:
    return TraceEvent(
        run_id=RUN,
        task_id=TASK_ID,
        attempt=0,
        step_index=step_index,
        kind=TraceEventKind.AGENT_TOOL_CALL,
        payload={TOOL_NAME_PAYLOAD_KEY: tool_name},
    )


# ---------------------------------------------------------------------------
# deny_tool_names
# ---------------------------------------------------------------------------


def test_no_denied_tool_called_passes_with_no_citations() -> None:
    spec = PolicySpec(deny_tool_names=("post_invoice",))
    trace = [_tool_call(0, "lookup_po")]

    result = check_policy(spec, _output(), trace)

    assert result.verdict is Verdict.PASS
    assert result.citations == []


def test_one_denied_tool_called_fails_and_names_it() -> None:
    spec = PolicySpec(deny_tool_names=("post_invoice",))
    trace = [_tool_call(0, "post_invoice")]

    result = check_policy(spec, _output(), trace)

    assert result.verdict is Verdict.FAIL
    assert "post_invoice" in result.notes


def test_denied_tool_called_three_times_cites_all_three() -> None:
    """The count matters, not just non-emptiness: citing only the first
    violation is the natural wrong implementation, and a report showing
    one of three understates the problem."""
    spec = PolicySpec(deny_tool_names=("post_invoice",))
    trace = [
        _tool_call(0, "post_invoice"),
        _tool_call(1, "post_invoice"),
        _tool_call(2, "post_invoice"),
    ]

    result = check_policy(spec, _output(), trace)

    assert result.verdict is Verdict.FAIL
    assert result.citations == [0, 1, 2]


def test_two_different_denied_tools_both_named_in_note() -> None:
    spec = PolicySpec(deny_tool_names=("post_invoice", "delete_po"))
    trace = [_tool_call(0, "post_invoice"), _tool_call(1, "delete_po")]

    result = check_policy(spec, _output(), trace)

    assert result.verdict is Verdict.FAIL
    assert "post_invoice" in result.notes
    assert "delete_po" in result.notes


def test_non_denied_call_contributes_no_citation_alongside_denied_one() -> None:
    """Citations point at violations, not at every tool call in the trace."""
    spec = PolicySpec(deny_tool_names=("post_invoice",))
    trace = [_tool_call(0, "lookup_po"), _tool_call(1, "post_invoice")]

    result = check_policy(spec, _output(), trace)

    assert result.verdict is Verdict.FAIL
    assert result.citations == [1]


# ---------------------------------------------------------------------------
# max_cost_usd
# ---------------------------------------------------------------------------


def test_max_cost_usd_none_never_checked_even_at_high_cost() -> None:
    spec = PolicySpec(max_cost_usd=None)

    result = check_policy(spec, _output(cost_usd=1_000_000.0), [])

    assert result.verdict is Verdict.PASS
    assert result.notes == ""


def test_cost_under_ceiling_passes() -> None:
    spec = PolicySpec(max_cost_usd=1.0)

    result = check_policy(spec, _output(cost_usd=0.5), [])

    assert result.verdict is Verdict.PASS


def test_cost_over_ceiling_warns_not_fails() -> None:
    """A cost overrun is an observation, not a rule the agent broke.
    ResourceLimits.max_cost_usd stops a run mid-attempt; PolicySpec's
    field of the same name only reports after the fact. Asserting WARN
    here, not FAIL, is what stops someone unifying the two later."""
    spec = PolicySpec(max_cost_usd=1.0)

    result = check_policy(spec, _output(cost_usd=1.5), [])

    assert result.verdict is Verdict.WARN


def test_cost_exactly_at_ceiling_passes() -> None:
    """The boundary, asserted rather than left to whichever comparison
    operator got typed: at the ceiling is not over it."""
    spec = PolicySpec(max_cost_usd=1.0)

    result = check_policy(spec, _output(cost_usd=1.0), [])

    assert result.verdict is Verdict.PASS


# ---------------------------------------------------------------------------
# Precedence: FAIL beats WARN, and the lesser finding must not disappear
# ---------------------------------------------------------------------------


def test_denied_tool_and_cost_overrun_together_fails() -> None:
    spec = PolicySpec(deny_tool_names=("post_invoice",), max_cost_usd=1.0)
    trace = [_tool_call(0, "post_invoice")]

    result = check_policy(spec, _output(cost_usd=1.5), trace)

    assert result.verdict is Verdict.FAIL


def test_fail_still_reports_cost_overrun_in_notes() -> None:
    """The real test: an implementation that returns as soon as it finds a
    FAIL passes every other test in this file while silently dropping the
    cost finding. The lesser finding must not disappear because a worse
    one exists."""
    spec = PolicySpec(deny_tool_names=("post_invoice",), max_cost_usd=1.0)
    trace = [_tool_call(0, "post_invoice")]

    result = check_policy(spec, _output(cost_usd=1.5), trace)

    assert result.verdict is Verdict.FAIL
    assert "post_invoice" in result.notes
    assert "1.5" in result.notes


# ---------------------------------------------------------------------------
# Empty spec
# ---------------------------------------------------------------------------


def test_empty_spec_passes_with_no_citations_or_notes() -> None:
    """Asymmetric with the evaluator, on purpose: a policy that forbids
    nothing is a real, satisfied policy, not a mis-specified one — unlike
    a task with no evaluator-side criteria, which raises
    ConfigurationError because there is nothing to grade. An empty
    PolicySpec has something to grade; it just grades to PASS. Calling
    check_policy here and asserting on its result (rather than expecting
    an exception) is itself the proof this does not raise."""
    spec = PolicySpec()
    trace = [_tool_call(0, "lookup_po")]

    result = check_policy(spec, _output(), trace)

    assert result.verdict is Verdict.PASS
    assert result.citations == []
    assert result.notes == ""


# ---------------------------------------------------------------------------
# policy_id
# ---------------------------------------------------------------------------


def test_policy_id_echoed_on_pass() -> None:
    spec = PolicySpec(policy_id="finance-v1")

    result = check_policy(spec, _output(), [])

    assert result.policy_id == "finance-v1"


def test_policy_id_echoed_on_fail() -> None:
    spec = PolicySpec(policy_id="finance-v1", deny_tool_names=("post_invoice",))
    trace = [_tool_call(0, "post_invoice")]

    result = check_policy(spec, _output(), trace)

    assert result.policy_id == "finance-v1"
