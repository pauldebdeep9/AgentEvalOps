"""Deterministic evaluator: grades one attempt's output against its task.

A pure function over ``(task, output, trace)`` — no I/O, no model calls, and
no imports from ``runners`` or ``benchmarks``, so it is fully testable
against hand-built traces without ever running an agent.

Four checks, AND-combined:

- completion — the attempt must have reached ``TerminationReason.COMPLETED``.
  Not criteria-driven; it runs on every task.
- ``expected_substring`` — the answer must contain it, case-sensitive.
- ``required_tool_names`` — every name listed must appear among the
  ``AGENT_TOOL_CALL`` events actually in the trace. Read from the trace, not
  from ``TaskSpec.criteria``'s ``mock_`` keys: those record what the runner
  was told to do, and checking declared intent instead of what happened
  would pass any task whose config merely looks right, which is the failure
  T05 exists to catch. Subset semantics — extra calls, duplicates, and call
  order are all fine; only presence of each required name matters.
- ``required_trace_kinds`` — every kind listed must appear at least once in
  the trace. An unrecognised kind is a mis-specified task and raises
  ``ConfigurationError``, the same reasoning as the vacuous-criteria case
  below.

A criterion absent from a task is *skipped*, not passed — skipped and
passed are deliberately different code paths, because a task can specify
three of these four keys and the vacuous-criteria guard (which only catches
*zero* evaluator-side keys) will not notice the fourth going unchecked.

Every check that runs contributes citations, whether it passes or fails —
a failed check's citations are the more interesting evidence, and are what
W8's report will point a reader at.
"""

from __future__ import annotations

from collections.abc import Callable

from minieval.errors import ConfigurationError
from minieval.schemas import (
    TOOL_NAME_PAYLOAD_KEY,
    AgentOutput,
    EvaluationResult,
    TaskSpec,
    TerminationReason,
    TraceEvent,
    TraceEventKind,
)

_CheckResult = tuple[bool, list[int], str]
"""(passed, citations, note) — note is "" when the check passed."""


def _check_expected_substring(
    task: TaskSpec, output: AgentOutput, trace: list[TraceEvent]
) -> _CheckResult:
    expected: str = task.criteria["expected_substring"]
    final_answer_event = next(
        (e for e in trace if e.kind is TraceEventKind.AGENT_FINAL_ANSWER), None
    )
    citations = (
        [final_answer_event.step_index] if final_answer_event is not None else []
    )
    if output.final_answer is None or expected not in output.final_answer:
        return (
            False,
            citations,
            f"expected_substring {expected!r} not found in "
            f"final_answer={output.final_answer!r}",
        )
    return True, citations, ""


def _check_required_tool_names(
    task: TaskSpec, output: AgentOutput, trace: list[TraceEvent]
) -> _CheckResult:
    required: list[str] = task.criteria["required_tool_names"]
    calls: list[tuple[int, str]] = [
        (e.step_index, e.payload[TOOL_NAME_PAYLOAD_KEY])
        for e in trace
        if e.kind is TraceEventKind.AGENT_TOOL_CALL
    ]
    called_names = {name for _, name in calls}
    citations = [step for step, name in calls if name in required]
    missing = [name for name in required if name not in called_names]
    if missing:
        return (
            False,
            citations,
            f"required_tool_names missing {missing}; "
            f"tool calls actually made: {sorted(called_names)}",
        )
    return True, citations, ""


def _check_required_trace_kinds(
    task: TaskSpec, output: AgentOutput, trace: list[TraceEvent]
) -> _CheckResult:
    required: list[str] = task.criteria["required_trace_kinds"]
    citations: list[int] = []
    missing: list[str] = []
    for kind_value in required:
        try:
            kind = TraceEventKind(kind_value)
        except ValueError as exc:
            raise ConfigurationError(
                f"{task.task_id}: unrecognised required_trace_kinds value "
                f"{kind_value!r}"
            ) from exc
        match = next((e for e in trace if e.kind is kind), None)
        if match is None:
            missing.append(kind_value)
        else:
            citations.append(match.step_index)
    if missing:
        return False, citations, f"required_trace_kinds missing: {missing}"
    return True, citations, ""


_CHECKS: dict[
    str, Callable[[TaskSpec, AgentOutput, list[TraceEvent]], _CheckResult]
] = {
    "expected_substring": _check_expected_substring,
    "required_tool_names": _check_required_tool_names,
    "required_trace_kinds": _check_required_trace_kinds,
}
_EVALUATOR_CRITERIA_KEYS = frozenset(_CHECKS)
"""Criteria keys this evaluator knows how to check — derived from ``_CHECKS``
so the vacuous-criteria guard below can never fall behind a widened dispatch
table. A task whose criteria contain none of these has nothing to grade and
would pass vacuously — a green result that measured nothing, which is worse
than a red one. See ``DeterministicEvaluator.evaluate``.
"""


class DeterministicEvaluator:
    """Grades completion plus whichever criteria-driven checks apply.

    Never sets ``EvaluationResult.evaluated=False`` — that field is the
    orchestrator's decision, not this class's, and is right there on the
    dataclass tempting you to set it. By the time ``evaluate()`` runs, the
    attempt was handed to it to judge, so it was evaluated by definition.
    """

    def __init__(self) -> None:
        self.evaluator_id: str = "deterministic"

    def evaluate(
        self, task: TaskSpec, output: AgentOutput, trace: list[TraceEvent]
    ) -> EvaluationResult:
        if not _EVALUATOR_CRITERIA_KEYS & task.criteria.keys():
            raise ConfigurationError(
                f"{task.task_id}: criteria contain none of "
                f"{sorted(_EVALUATOR_CRITERIA_KEYS)} — nothing to evaluate"
            )

        passed = True
        citations: list[int] = []
        notes: list[str] = []

        if output.termination_reason is not TerminationReason.COMPLETED:
            passed = False
            notes.append(
                "did not complete: termination_reason="
                f"{output.termination_reason.value}"
            )

        for key, check in _CHECKS.items():
            if key not in task.criteria:
                continue
            check_passed, check_citations, note = check(task, output, trace)
            citations.extend(check_citations)
            if not check_passed:
                passed = False
                notes.append(note)

        return EvaluationResult(
            evaluator_id=self.evaluator_id,
            passed=passed,
            citations=citations,
            notes="; ".join(notes),
        )
