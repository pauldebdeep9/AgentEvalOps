"""Where runs first fail: the transition matrix (ROADMAP R37, slice 1).

``reports/heatmap.py`` places every failed task run in one cell: its
column is the stage of the first failure, judged from the failing
evaluations' record citations by a fixed precedence (a cited tool call,
else a cited trace event, else unattributed); its row is the stage that
completed just before, rebuilt from the run's own records. A library the
writer does not call yet; these tests build the records by hand.
"""

from __future__ import annotations

import random
from typing import Any

import pytest

from agentevalops.core.schemas import (
    AgentOutput,
    EvaluationResult,
    Ref,
    TaskRunResult,
    TerminationReason,
    ToolCallRecord,
    TraceEvent,
    TraceEventKind,
)
from agentevalops.core.types import RunId, TaskId
from agentevalops.reports.heatmap import (
    COLUMNS,
    ROWS,
    TransitionMatrix,
    transition_matrix,
)

K = TraceEventKind


def _call(
    index: int,
    tool: str = "case_lookup",
    task: str = "TE1",
    rep: int = 0,
    *,
    executed: bool = True,
    approval: Any = "not_required",
    fault: str | None = None,
    error: bool = False,
) -> ToolCallRecord:
    return ToolCallRecord(
        task_id=TaskId(task),
        index=index,
        tool_name=tool,
        arguments={},
        permission=None,
        data_class=None,
        executed=executed,
        result="",
        error=error,
        approval=approval,
        fault=fault,
        repetition=rep,
    )


def _event(
    step: int, kind: K, task: str = "TE1", rep: int = 0, **payload: Any
) -> TraceEvent:
    return TraceEvent(
        run_id=RunId("r"),
        step_index=step,
        kind=kind,
        payload=payload,
        task_id=TaskId(task),
        repetition=rep,
    )


def _trace(
    tools: tuple[str, ...] = ("case_lookup",),
    task: str = "TE1",
    rep: int = 0,
    *,
    observe: bool = False,
) -> list[TraceEvent]:
    """plan, then per tool a call, its result and (with *observe*) an
    observation, then the final answer and the terminal event."""
    kinds: list[tuple[K, dict[str, Any]]] = [(K.AGENT_PLAN, {"plan": "p"})]
    for tool in tools:
        kinds.append((K.AGENT_TOOL_CALL, {"tool_name": tool, "args": {}}))
        kinds.append((K.AGENT_TOOL_RESULT, {"tool_name": tool, "result": ""}))
        if observe:
            kinds.append((K.AGENT_OBSERVATION, {"observation": "o"}))
    kinds.append((K.AGENT_FINAL_ANSWER, {"answer": "a"}))
    kinds.append((K.AGENT_TERMINAL, {"success": True}))
    return [
        _event(step, kind, task, rep, **payload)
        for step, (kind, payload) in enumerate(kinds)
    ]


def _ref(stream: Any, index: int, task: str = "TE1", rep: int = 0) -> Ref:
    return Ref(stream=stream, task_id=TaskId(task), repetition=rep, index=index)


def _eval(kind: str, *refs: Ref, passed: bool = False) -> EvaluationResult:
    return EvaluationResult(
        evaluator_id=f"{kind}-v1",
        evaluator_kind=kind,
        score=1.0 if passed else 0.0,
        passed=passed,
        refs=list(refs),
    )


def _run(
    *evaluations: EvaluationResult,
    task: str = "TE1",
    rep: int = 0,
    evaluated: bool = True,
) -> TaskRunResult:
    verdict = EvaluationResult(
        evaluator_id="verdict",
        evaluator_kind="verdict",
        score=0.0,
        passed=evaluated and all(e.passed for e in evaluations),
        evaluated=evaluated,
    )
    return TaskRunResult(
        task_id=TaskId(task),
        output=AgentOutput(
            success=True, termination_reason=TerminationReason.COMPLETED
        ),
        evaluation=verdict,
        event_count=0,
        evaluations=list(evaluations),
        repetition=rep,
    )


def _only(matrix: TransitionMatrix) -> tuple[str, str, tuple[str, ...]]:
    (cell,) = matrix.cells
    return cell.row, cell.column, cell.labels


# ---------------------------------------------------------------------------
# 1. A cited tool call: the stage from the record itself
# ---------------------------------------------------------------------------


def test_a_denied_call_fails_in_gate_after_its_issue() -> None:
    """Fail-first: no module."""
    calls = [_call(0, "records_export", approval="denied", executed=False)]
    run = _run(_eval("policy", _ref("tool_calls", 0)))
    matrix = transition_matrix([run], calls, _trace(("records_export",)))
    assert _only(matrix) == ("issue", "gate", ("TE1:records_export",))


@pytest.mark.parametrize(
    ("approval", "row"), [("approved", "gate"), ("not_required", "issue")]
)
def test_a_faulted_call_fails_in_exec_after_its_gate_or_its_issue(
    approval: str, row: str
) -> None:
    calls = [_call(0, approval=approval, fault="timeout")]
    run = _run(_eval("tool_use", _ref("tool_calls", 0)))
    assert _only(transition_matrix([run], calls, _trace())) == (
        row,
        "exec",
        ("TE1:case_lookup",),
    )


@pytest.mark.parametrize(
    "call",
    [
        _call(0, error=True),
        _call(0, executed=False),
        _call(0, executed=False, approval="timed_out"),
    ],
)
def test_an_erroring_or_unexecuted_call_fails_in_exec(call: ToolCallRecord) -> None:
    run = _run(_eval("state_based", _ref("tool_calls", 0)))
    assert _only(transition_matrix([run], [call], _trace())) == (
        "issue",
        "exec",
        ("TE1:case_lookup",),
    )


def test_a_clean_call_cited_by_tool_use_fails_in_issue() -> None:
    """A first call's issue follows parse."""
    run = _run(_eval("tool_use", _ref("tool_calls", 0)))
    assert _only(transition_matrix([run], [_call(0)], _trace())) == (
        "parse",
        "issue",
        ("TE1:case_lookup",),
    )


@pytest.mark.parametrize("kind", ["state_based", "policy"])
def test_a_clean_call_cited_by_state_based_or_policy_fails_in_handle(kind: str) -> None:
    run = _run(_eval(kind, _ref("tool_calls", 0)))
    assert _only(transition_matrix([run], [_call(0)], _trace())) == (
        "exec",
        "handle",
        ("TE1:case_lookup",),
    )


def test_a_clean_call_cited_by_tool_use_and_policy_is_an_issue() -> None:
    run = _run(
        _eval("policy", _ref("tool_calls", 0)), _eval("tool_use", _ref("tool_calls", 0))
    )
    assert _only(transition_matrix([run], [_call(0)], _trace()))[1] == "issue"


def test_the_smallest_cited_call_index_decides() -> None:
    """Calls 1 (faulted) and 2 (denied) are both cited: call 1 is first,
    and its exec, ungated, follows its own issue."""
    calls = [
        _call(0),
        _call(1, "status_update", fault="malformed"),
        _call(2, "official_notice", approval="denied", executed=False),
    ]
    run = _run(
        _eval("policy", _ref("tool_calls", 2)),
        _eval("tool_use", _ref("tool_calls", 1)),
    )
    trace = _trace(("case_lookup", "status_update", "official_notice"))
    assert _only(transition_matrix([run], calls, trace)) == (
        "issue",
        "exec",
        ("TE1:status_update",),
    )


def test_a_cited_tool_call_outranks_a_cited_trace_event() -> None:
    """The plan (step 0) is cited before the call in time; the call wins."""
    run = _run(
        _eval("deterministic", _ref("trace", 0)),
        _eval("tool_use", _ref("tool_calls", 0)),
    )
    assert _only(transition_matrix([run], [_call(0)], _trace()))[1] == "issue"


# ---------------------------------------------------------------------------
# 1b. The preference: a cited call that went wrong comes first (R42, D-R42-4)
# ---------------------------------------------------------------------------


def test_the_heatmap_prefers_the_problematic_cited_record() -> None:
    """tool_use cites both calls -- the satisfied lookup and the later
    status update, which ran but faulted. The faulted call is the first
    failure: the run moves off parse -> issue, to issue -> exec. (Fail-first:
    the smallest cited index put it at parse -> issue.)"""
    calls = [_call(0), _call(1, "status_update", fault="malformed")]
    run = _run(_eval("tool_use", _ref("tool_calls", 0), _ref("tool_calls", 1)))
    trace = _trace(("case_lookup", "status_update"))
    assert _only(transition_matrix([run], calls, trace)) == (
        "issue",
        "exec",
        ("TE1:status_update",),
    )


@pytest.mark.parametrize(
    ("later", "column"),
    [
        # Each way a call goes wrong; the first two ran all the same.
        (_call(1, "status_update", fault="timeout"), "exec"),
        (_call(1, "status_update", error=True), "exec"),
        (_call(1, "status_update", executed=False), "exec"),
        (_call(1, "status_update", approval="denied", executed=False), "gate"),
    ],
    ids=["fault", "error", "not-executed", "denied"],
)
def test_each_kind_of_problem_is_preferred_over_an_earlier_clean_call(
    later: ToolCallRecord, column: str
) -> None:
    """The clean lookup is cited by tool_use, the later call by policy: the
    later call is the first failure, staged from its own record."""
    run = _run(
        _eval("tool_use", _ref("tool_calls", 0)),
        _eval("policy", _ref("tool_calls", 1)),
    )
    trace = _trace(("case_lookup", "status_update"))
    assert _only(transition_matrix([run], [_call(0), later], trace)) == (
        "issue",
        column,
        ("TE1:status_update",),
    )


def test_all_clean_citations_keep_the_smallest_index() -> None:
    """No cited call went wrong: the smallest cited index decides, with the
    clean-call rules (tool_use's citation makes it an issue)."""
    calls = [_call(0), _call(1, "status_update")]
    run = _run(
        _eval("policy", _ref("tool_calls", 1)),
        _eval("tool_use", _ref("tool_calls", 0), _ref("tool_calls", 1)),
    )
    trace = _trace(("case_lookup", "status_update"))
    assert _only(transition_matrix([run], calls, trace)) == (
        "parse",
        "issue",
        ("TE1:case_lookup",),
    )


def test_an_uncited_problem_does_not_move_the_run() -> None:
    """The faulted call is in the run's records but no failing evaluation
    cites it: the preference reads cited records only."""
    calls = [_call(0), _call(1, "status_update", fault="timeout")]
    run = _run(_eval("tool_use", _ref("tool_calls", 0)))
    trace = _trace(("case_lookup", "status_update"))
    assert _only(transition_matrix([run], calls, trace)) == (
        "parse",
        "issue",
        ("TE1:case_lookup",),
    )


def test_the_preference_reads_tool_call_refs_only() -> None:
    """No call is cited; the plan (step 0) and the faulted call's result
    (step 2) are. Trace refs keep the smallest step: the plan."""
    calls = [_call(0, fault="timeout")]
    run = _run(_eval("deterministic", _ref("trace", 2), _ref("trace", 0)))
    assert _only(transition_matrix([run], calls, _trace())) == (
        "start",
        "parse",
        ("TE1",),
    )


# ---------------------------------------------------------------------------
# 2. Else a cited trace event: the stage by its kind
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("step", "row", "column", "label"),
    [
        (0, "start", "parse", "TE1"),  # agent.plan: nothing came before
        (1, "parse", "issue", "TE1:case_lookup"),  # agent.tool_call
        (2, "issue", "exec", "TE1:case_lookup"),  # agent.tool_result
        (3, "exec", "finish", "TE1"),  # agent.final_answer
        (4, "exec", "finish", "TE1"),  # agent.terminal
    ],
)
def test_a_cited_trace_event_fails_in_the_stage_of_its_kind(
    step: int, row: str, column: str, label: str
) -> None:
    run = _run(_eval("deterministic", _ref("trace", step)))
    assert _only(transition_matrix([run], [_call(0)], _trace())) == (
        row,
        column,
        (label,),
    )


def test_a_cited_observation_fails_in_handle_after_exec() -> None:
    trace = _trace(observe=True)  # plan, call, result, observation, ...
    run = _run(_eval("deterministic", _ref("trace", 3)))
    assert _only(transition_matrix([run], [_call(0)], trace)) == (
        "exec",
        "handle",
        ("TE1:case_lookup",),
    )


def test_the_smallest_cited_trace_step_decides() -> None:
    run = _run(_eval("deterministic", _ref("trace", 4), _ref("trace", 2)))
    assert _only(transition_matrix([run], [_call(0)], _trace()))[1] == "exec"


def test_a_finish_without_calls_follows_parse() -> None:
    run = _run(_eval("deterministic", _ref("trace", 2)))
    assert _only(transition_matrix([run], [], _trace(()))) == (
        "parse",
        "finish",
        ("TE1",),
    )


# ---------------------------------------------------------------------------
# 3. No citation at all
# ---------------------------------------------------------------------------


def test_a_failed_run_citing_nothing_is_unattributed_never_dropped() -> None:
    """A failing evaluation that cites no record (here a hand-built
    state_based result with no refs; since R42 the real one cites the
    terminal): the run counts, in the unattributed column, on the row of the
    last stage it completed."""
    run = _run(_eval("state_based"))
    assert _only(transition_matrix([run], [_call(0)], _trace())) == (
        "finish",
        "unattributed",
        ("TE1",),
    )


# ---------------------------------------------------------------------------
# The predecessor rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("observe", "row"), [(False, "exec"), (True, "handle")])
def test_call_k_follows_call_k_minus_ones_exec_or_its_handle(
    observe: bool, row: str
) -> None:
    """With no observation between them, the last stage call 0 completed is
    its exec; with one, its handle."""
    calls = [_call(0), _call(1, "status_update")]
    run = _run(_eval("tool_use", _ref("tool_calls", 1)))
    trace = _trace(("case_lookup", "status_update"), observe=observe)
    assert _only(transition_matrix([run], calls, trace)) == (
        row,
        "issue",
        ("TE1:status_update",),
    )


def test_a_call_after_a_denied_call_follows_that_calls_issue() -> None:
    calls = [
        _call(0, "records_export", approval="denied", executed=False),
        _call(1, "status_update"),
    ]
    run = _run(_eval("tool_use", _ref("tool_calls", 1)))
    trace = _trace(("records_export", "status_update"))
    assert _only(transition_matrix([run], calls, trace))[0] == "issue"


def test_a_finish_tool_call_the_environment_never_saw_is_not_a_harness_call() -> None:
    """A runner's ``finish`` control is a trace tool call with no harness
    record: it does not shift which record later events belong to."""
    trace = [
        _event(0, K.AGENT_PLAN),
        _event(1, K.AGENT_TOOL_CALL, tool_name="case_lookup"),
        _event(2, K.AGENT_TOOL_RESULT, tool_name="case_lookup"),
        _event(3, K.AGENT_TOOL_CALL, tool_name="finish"),
        _event(4, K.AGENT_TOOL_RESULT, tool_name="finish"),
        _event(5, K.AGENT_TERMINAL),
    ]
    issue = _run(_eval("deterministic", _ref("trace", 3)))
    assert _only(transition_matrix([issue], [_call(0)], trace)) == (
        "exec",
        "issue",
        ("TE1:finish",),
    )


def test_a_call_that_never_reached_the_environment_shifts_nothing() -> None:
    """A malformed call (no harness record) before a recorded one: trace
    events are matched to records by tool name, not position, so the
    recorded call's issue still follows parse (self-mutation pass: the
    finish test alone could not tell name from position)."""
    trace = [
        _event(0, K.AGENT_PLAN),
        _event(1, K.AGENT_TOOL_CALL, tool_name="status_update"),
        _event(2, K.AGENT_TOOL_RESULT, tool_name="status_update"),
        _event(3, K.AGENT_TOOL_CALL, tool_name="case_lookup"),
        _event(4, K.AGENT_TOOL_RESULT, tool_name="case_lookup"),
        _event(5, K.AGENT_TERMINAL),
    ]
    run = _run(_eval("deterministic", _ref("trace", 3)))
    assert _only(transition_matrix([run], [_call(0)], trace)) == (
        "parse",
        "issue",
        ("TE1:case_lookup",),
    )


# ---------------------------------------------------------------------------
# Which runs enter, and determinism
# ---------------------------------------------------------------------------


def _mixed() -> tuple[list[TaskRunResult], list[ToolCallRecord], list[TraceEvent]]:
    """Seven task runs: four failed (two of them in one cell), two passed,
    one not evaluated."""
    runs, calls, events = [], [], []
    for task, tool in (
        ("TE3", "case_lookup"),
        ("PC5", "case_lookup"),
        ("TC1", "status_update"),
    ):
        calls.append(_call(0, tool, task))
        events += _trace((tool,), task)
        runs.append(_run(_eval("tool_use", _ref("tool_calls", 0, task)), task=task))
    calls.append(_call(0, "records_export", "RB5", approval="denied", executed=False))
    events += _trace(("records_export",), "RB5")
    runs.append(_run(_eval("policy", _ref("tool_calls", 0, "RB5")), task="RB5"))
    for task in ("TE1", "TE2"):
        calls.append(_call(0, "case_lookup", task))
        events += _trace(("case_lookup",), task)
        runs.append(_run(_eval("tool_use", passed=True), task=task))
    runs.append(_run(task="PC1", evaluated=False))
    return runs, calls, events


def test_every_failed_run_lands_in_exactly_one_cell() -> None:
    """The invariant: the cells' counts sum to the failed task runs; passed
    and not-evaluated runs are not in the matrix, and are counted apart."""
    matrix = transition_matrix(*_mixed())
    assert (matrix.task_runs, matrix.failed, matrix.not_evaluated) == (7, 4, 1)
    assert sum(cell.count for cell in matrix.cells) == matrix.failed
    assert matrix.count("parse", "issue") == 3
    assert matrix.count("issue", "gate") == 1
    assert matrix.count("parse", "exec") == 0


def test_a_cell_lists_its_runs_lexically() -> None:
    matrix = transition_matrix(*_mixed())
    assert matrix.labels("parse", "issue") == (
        "PC5:case_lookup",
        "TC1:status_update",
        "TE3:case_lookup",
    )


def test_cells_follow_the_fixed_stage_order() -> None:
    matrix = transition_matrix(*_mixed())
    order = [(ROWS.index(c.row), COLUMNS.index(c.column)) for c in matrix.cells]
    assert order == sorted(order)
    assert ROWS == ("start", "parse", "issue", "gate", "exec", "handle", "finish")
    assert COLUMNS == (
        "parse",
        "issue",
        "gate",
        "exec",
        "handle",
        "finish",
        "unattributed",
    )


def test_the_matrix_does_not_depend_on_the_order_of_its_inputs() -> None:
    runs, calls, events = _mixed()
    expected = transition_matrix(runs, calls, events)
    rng = random.Random(3)
    for _ in range(5):
        shuffled = [list(x) for x in (runs, calls, events)]
        for part in shuffled:
            rng.shuffle(part)
        assert transition_matrix(*shuffled) == expected


def test_repetitions_are_separate_runs() -> None:
    calls = [_call(0, rep=0), _call(0, rep=1, approval="denied", executed=False)]
    events = _trace(rep=0) + _trace(rep=1)
    runs = [
        _run(_eval("tool_use", _ref("tool_calls", 0, rep=0)), rep=0),
        _run(_eval("policy", _ref("tool_calls", 0, rep=1)), rep=1),
    ]
    matrix = transition_matrix(runs, calls, events)
    assert (matrix.count("parse", "issue"), matrix.count("issue", "gate")) == (1, 1)
