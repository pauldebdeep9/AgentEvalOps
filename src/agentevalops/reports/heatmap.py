"""Where runs first fail: a transition matrix over six harness stages
(ROADMAP R37).

The only home of the mapping. It is derived at render time from records
every 0.5+ bundle already holds -- the task runs' evaluations and the refs
the failing ones carry, ``tool_calls.jsonl`` and the trace -- so it needs
no new file and no schema change, and a re-render reproduces it. Core
schemas only: ``reports`` never imports ``orchestration``.

**Stages, in this order.** ``parse`` (the task handed over; ``agent.plan``
when present), ``issue`` (a tool call as the environment received it),
``gate`` (its approval step, when one was required), ``exec`` (the
environment ran it: executed, a fault, an error), ``handle`` (what the
agent did with the result) and ``finish`` (the terminal event). Choosing a
tool and writing its arguments are one observed call, so both are
``issue``.

**Which runs enter.** Failed task runs only -- evaluated and not passed.
A run not evaluated has a cause, not a stage; it is counted apart. Every
failed run lands in exactly one cell, so the cells sum to the failed runs.

**The column: the first failure, by fixed precedence,** from the failing
evaluations' refs.

1. A cited tool call: the smallest cited index, its stage from the record.
   Denied and not executed: ``gate``. A fault, an error, or not executed:
   ``exec``. Executed cleanly: ``issue`` when a ``tool_use`` evaluation
   cites it, else ``handle`` (a ``state_based`` or ``policy`` judgement of
   what the agent did with a clean result).
2. Else a cited trace event: the smallest cited step, its stage by kind --
   plan ``parse``, tool call ``issue``, tool result ``exec``, observation
   ``handle``, final answer or terminal ``finish``.
3. Else ``unattributed``: counted, never dropped.

**The row: the stage that completed just before,** from the run's own
records in order: ``parse``; then per call ``issue``, ``gate`` when
approved, ``exec`` when the call ran, ``handle`` when an observation
follows its result; then ``finish``. So a first call's ``issue`` follows
``parse``, call k's follows the ``exec`` or ``handle`` of call k-1, and an
``exec`` follows its ``gate`` when gated, else its ``issue``. A failure
with nothing before it (a cited plan) is on the ``start`` row; an
unattributed run is on the row of the last stage it completed.

Trace tool-call events are matched to the harness's records in order by
tool name: a runner's ``finish`` control, or a call whose arguments never
reached the environment, has no record and shifts nothing.

**Determinism.** Stages in the fixed order, a cell's runs sorted
lexically, ties between refs broken by (stream, index); no clock.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from agentevalops.core.schemas import (
    EvaluationResult,
    TaskRunResult,
    ToolCallRecord,
    TraceEvent,
    TraceEventKind,
)

__all__ = [
    "COLUMNS",
    "ROWS",
    "STAGES",
    "Cell",
    "TransitionMatrix",
    "transition_matrix",
]

STAGES: tuple[str, ...] = ("parse", "issue", "gate", "exec", "handle", "finish")
#: The row of a failure that nothing completed before.
START = "start"
#: The column of a failed run whose failing evaluations cite no record.
UNATTRIBUTED = "unattributed"
ROWS: tuple[str, ...] = (START, *STAGES)
COLUMNS: tuple[str, ...] = (*STAGES, UNATTRIBUTED)

#: The trace's agent events, and the stage each one is.
_TRACE_STAGE: dict[TraceEventKind, str] = {
    TraceEventKind.AGENT_PLAN: "parse",
    TraceEventKind.AGENT_TOOL_CALL: "issue",
    TraceEventKind.AGENT_TOOL_RESULT: "exec",
    TraceEventKind.AGENT_OBSERVATION: "handle",
    TraceEventKind.AGENT_FINAL_ANSWER: "finish",
    TraceEventKind.AGENT_TERMINAL: "finish",
}

#: A call's stages within its slot, in order; 4 is a trace tool call with
#: no harness record, after the slot's own stages.
_SUB: dict[str, int] = {"issue": 0, "gate": 1, "exec": 2, "handle": 3}
_UNMATCHED = 4

#: A point on a run's timeline: (call position, stage within it); parse is
#: before every call, finish after all of them.
_Key = tuple[int, int]
_PARSE: _Key = (-1, 0)


@dataclass(frozen=True)
class Cell:
    """The failed task runs that moved from ``row`` to a first failure in
    ``column``: one label each, ``scenario:tool`` or the scenario alone,
    sorted."""

    row: str
    column: str
    labels: tuple[str, ...]

    @property
    def count(self) -> int:
        return len(self.labels)


@dataclass(frozen=True)
class TransitionMatrix:
    """Every failed task run in one cell; the cells that hold any, in
    ``ROWS`` by ``COLUMNS`` order. ``task_runs``, ``failed`` and
    ``not_evaluated`` count the runs the matrix was derived from."""

    cells: tuple[Cell, ...]
    task_runs: int
    failed: int
    not_evaluated: int

    def labels(self, row: str, column: str) -> tuple[str, ...]:
        for cell in self.cells:
            if (cell.row, cell.column) == (row, column):
                return cell.labels
        return ()

    def count(self, row: str, column: str) -> int:
        return len(self.labels(row, column))


class _Run:
    """One task run's records, in order, and the stages it completed."""

    def __init__(
        self, task: str, calls: Sequence[ToolCallRecord], events: Sequence[TraceEvent]
    ) -> None:
        self.task = task
        self.calls = sorted(calls, key=lambda c: c.index)
        self.position = {c.index: p for p, c in enumerate(self.calls)}
        self.events = sorted(
            (e for e in events if e.kind in _TRACE_STAGE), key=lambda e: e.step_index
        )
        self.by_step = {e.step_index: e for e in self.events}
        self.finish: _Key = (len(self.calls), 0)
        # Each trace event's slot: the position of the latest tool call event
        # matched to a harness record, by tool name and in order (-1: none).
        self.slot: dict[int, int] = {}
        self.matched: dict[int, int] = {}
        pointer, current = 0, -1
        for event in self.events:
            if (
                event.kind is TraceEventKind.AGENT_TOOL_CALL
                and pointer < len(self.calls)
                and event.payload.get("tool_name") == self.calls[pointer].tool_name
            ):
                self.matched[event.step_index] = pointer
                current, pointer = pointer, pointer + 1
            self.slot[event.step_index] = current
        self.completed = self._completed()

    def _completed(self) -> list[tuple[_Key, str]]:
        done: dict[_Key, str] = {_PARSE: "parse"}
        for p, call in enumerate(self.calls):
            done[(p, _SUB["issue"])] = "issue"
            if call.approval == "approved":
                done[(p, _SUB["gate"])] = "gate"
            if call.executed or call.fault is not None or call.error:
                done[(p, _SUB["exec"])] = "exec"
        for event in self.events:
            if event.kind is TraceEventKind.AGENT_OBSERVATION:
                done[(self.slot[event.step_index], _SUB["handle"])] = "handle"
            elif event.kind is TraceEventKind.AGENT_TERMINAL:
                done[self.finish] = "finish"
        return sorted(done.items())

    def before(self, key: _Key) -> str:
        """The stage completed latest before *key*, or ``start``."""
        row = START
        for done, stage in self.completed:
            if done >= key:
                break
            row = stage
        return row

    def tool(self, position: int) -> str:
        return f"{self.task}:{self.calls[position].tool_name}"

    def trace_point(self, event: TraceEvent, stage: str) -> tuple[_Key, str]:
        """Where a cited trace event of *stage* sits, and its label."""
        step, slot = event.step_index, self.slot[event.step_index]
        if stage == "parse":
            return _PARSE, self.task
        if stage == "finish":
            return self.finish, self.task
        if stage == "issue" and step in self.matched:
            return (self.matched[step], _SUB["issue"]), self.tool(self.matched[step])
        sub = _UNMATCHED if stage == "issue" else _SUB[stage]
        name = event.payload.get("tool_name")
        if isinstance(name, str) and name:
            return (slot, sub), f"{self.task}:{name}"
        return (slot, sub), self.tool(slot) if slot >= 0 else self.task


def _failing(result: TaskRunResult) -> list[EvaluationResult]:
    return [
        e
        for e in (result.evaluations or [result.evaluation])
        if e.evaluated and not e.passed
    ]


def _place(result: TaskRunResult, run: _Run) -> tuple[str, str, str]:
    """(row, column, label) of one failed task run."""
    own = (run.task, result.repetition)
    cited_calls: list[tuple[int, str]] = []
    cited_steps: list[int] = []
    for evaluation in _failing(result):
        for ref in evaluation.refs:
            if ref.task_id is not None and (str(ref.task_id), ref.repetition) != own:
                continue
            if ref.stream == "tool_calls" and ref.index in run.position:
                cited_calls.append((ref.index, evaluation.evaluator_kind))
            elif ref.stream == "trace" and ref.index in run.by_step:
                cited_steps.append(ref.index)
    if cited_calls:
        index = min(i for i, _ in cited_calls)
        position = run.position[index]
        call = run.calls[position]
        if call.approval == "denied" and not call.executed:
            stage = "gate"
        elif call.fault is not None or call.error or not call.executed:
            stage = "exec"
        elif any(kind == "tool_use" for i, kind in cited_calls if i == index):
            stage = "issue"
        else:
            stage = "handle"
        key = (position, _SUB[stage])
        return run.before(key), stage, run.tool(position)
    if cited_steps:
        event = run.by_step[min(cited_steps)]
        stage = _TRACE_STAGE[event.kind]
        key, label = run.trace_point(event, stage)
        return run.before(key), stage, label
    last = run.completed[-1][1]
    return last, UNATTRIBUTED, run.task


def transition_matrix(
    task_results: Sequence[TaskRunResult],
    tool_calls: Sequence[ToolCallRecord],
    events: Sequence[TraceEvent],
) -> TransitionMatrix:
    """Every failed task run of *task_results* in the cell of its first
    failure, from the run's own *tool_calls* and trace *events*."""
    calls: dict[tuple[str, int], list[ToolCallRecord]] = {}
    for call in tool_calls:
        calls.setdefault((str(call.task_id), call.repetition), []).append(call)
    trace: dict[tuple[str, int], list[TraceEvent]] = {}
    for event in events:
        if event.task_id is not None:
            trace.setdefault((str(event.task_id), event.repetition), []).append(event)
    placed: dict[tuple[str, str], list[str]] = {}
    failed = not_evaluated = 0
    for result in task_results:
        if not result.evaluation.evaluated:
            not_evaluated += 1
            continue
        if result.evaluation.passed:
            continue
        failed += 1
        own = (str(result.task_id), result.repetition)
        run = _Run(own[0], calls.get(own, []), trace.get(own, []))
        row, column, label = _place(result, run)
        placed.setdefault((row, column), []).append(label)
    cells = tuple(
        Cell(row, column, tuple(sorted(placed[(row, column)])))
        for row in ROWS
        for column in COLUMNS
        if (row, column) in placed
    )
    return TransitionMatrix(
        cells=cells,
        task_runs=len(task_results),
        failed=failed,
        not_evaluated=not_evaluated,
    )
