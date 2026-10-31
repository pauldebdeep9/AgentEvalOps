"""ToolUseEvaluator -- the harness's record of the agent's tool calls
against what the scenario requires, forbids and orders (docs/adr/0006 D4,
D12, D13, D14)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agentevalops.core.schemas import (
    AgentOutput,
    EvaluationResult,
    EvaluatorContext,
    Ref,
    TaskSpec,
    ToolCallRecord,
    TraceEvent,
)
from agentevalops.environments import cross_check
from agentevalops.evaluators.templates import matches


class ToolUseEvaluator:
    """Judges a task run's tool calls from the harness records, never from
    the agent's own account of them (D14).

    ``task.metadata["tool_use"]`` declares what the scenario expects:

    - ``required``: calls that must have been made -- each a ``tool`` and,
      optionally, ``arguments`` the recorded arguments must contain (a
      template: named keys must match, others may differ). A call the
      environment refused or that failed does not count as made.
    - ``forbidden``: tool names, or ``tool``-and-``arguments`` patterns,
      that must not appear in the records at all -- an attempt counts,
      whether or not it ran.
    - ``order``: tool names whose first calls must come in that order.
      Only tools that were called take part; a missing one is
      ``required``'s finding, not a second one here.

    And always: the agent's self-reported ``AGENT_TOOL_CALL`` events must
    agree with the records, in order, name and arguments. A disagreement
    is a ``self_report_mismatch`` finding and fails the task run.

    A task without a ``tool_use`` block is not this evaluator's to judge:
    the result is not applicable (D13). ``refs`` address the records the
    verdict rests on in the ``tool_calls`` stream (D17).
    """

    evaluator_id: str = "tool-use-v1"
    evaluator_kind: str = "tool_use"

    async def evaluate(
        self,
        task: TaskSpec,
        output: AgentOutput,
        trace: list[TraceEvent],
        context: EvaluatorContext | None = None,
    ) -> EvaluationResult:
        spec = task.metadata.get("tool_use")
        if not isinstance(spec, Mapping) or not spec:
            return EvaluationResult(
                evaluator_id=self.evaluator_id,
                evaluator_kind=self.evaluator_kind,
                score=0.0,
                passed=False,
                applicable=False,
                notes="not applicable: the task declares no tool_use expectations",
            )
        records = _scoped(context)
        parts: list[str] = []
        refs: list[Ref] = []
        failed = False

        # --- required calls ------------------------------------------------
        for entry in _entries(spec.get("required")):
            tool, arguments = entry
            made = [
                r
                for r in records
                if r.tool_name == tool
                and r.executed
                and not r.error
                and (arguments is None or matches(arguments, r.arguments))
            ]
            if made:
                parts.append(
                    f"required {tool}{_show(arguments)}: index {made[0].index}"
                )
                refs.append(_ref(made[0]))
            else:
                parts.append(f"required {tool}{_show(arguments)}: missing")
                failed = True

        # --- forbidden calls -----------------------------------------------
        for entry in _entries(spec.get("forbidden")):
            tool, arguments = entry
            hits = [
                r
                for r in records
                if r.tool_name == tool
                and (arguments is None or matches(arguments, r.arguments))
            ]
            if hits:
                indices = ", ".join(str(r.index) for r in hits)
                parts.append(
                    f"forbidden {tool}{_show(arguments)}: called at index {indices}"
                )
                refs.extend(_ref(r) for r in hits)
                failed = True
            else:
                parts.append(f"forbidden {tool}{_show(arguments)}: not called")

        # --- order ---------------------------------------------------------
        order = spec.get("order") or []
        if isinstance(order, Sequence) and not isinstance(order, str) and order:
            firsts = [
                next((r for r in records if r.tool_name == str(tool)), None)
                for tool in order
            ]
            called = [r for r in firsts if r is not None]
            positions = [r.index for r in called]
            if positions == sorted(positions):
                parts.append(f"order {list(order)}: kept")
            else:
                seen = ", ".join(f"{r.tool_name}@{r.index}" for r in called)
                parts.append(f"order {list(order)}: broken ({seen})")
                refs.extend(_ref(r) for r in called)
                failed = True

        # --- D14: the agent's account against the harness's ----------------
        mismatched = cross_check(records, trace)
        if mismatched:
            detail = ", ".join(
                f"{m.kind} at {m.position} "
                f"(recorded {m.recorded}, reported {m.reported})"
                for m in mismatched
            )
            parts.append(f"self_report_mismatch: {detail}")
            by_position = {r.index: r for r in records}
            refs.extend(
                _ref(by_position[m.position])
                for m in mismatched
                if m.position in by_position
            )
            failed = True
        else:
            parts.append(f"self-reports match the {len(records)} harness record(s)")

        return EvaluationResult(
            evaluator_id=self.evaluator_id,
            evaluator_kind=self.evaluator_kind,
            score=0.0 if failed else 1.0,
            passed=not failed,
            notes="; ".join(parts),
            refs=list(dict.fromkeys(refs)),  # each record once, first mention first
        )


def _scoped(context: EvaluatorContext | None) -> list[ToolCallRecord]:
    """This task run's records, in call order."""
    if context is None:
        return []
    records = context.records
    if context.task_id is not None:
        records = tuple(
            r
            for r in records
            if r.task_id == context.task_id
            and (context.repetition is None or r.repetition == context.repetition)
        )
    return sorted(records, key=lambda r: r.index)


def _entries(raw: Any) -> list[tuple[str, dict[str, Any] | None]]:
    """``required`` and ``forbidden`` entries as (tool, arguments template)."""
    entries: list[tuple[str, dict[str, Any] | None]] = []
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        return entries
    for item in raw:
        if isinstance(item, str):
            entries.append((item, None))
        elif isinstance(item, Mapping):
            arguments = item.get("arguments")
            entries.append(
                (
                    str(item.get("tool", "")),
                    dict(arguments) if isinstance(arguments, Mapping) else None,
                )
            )
    return entries


def _ref(record: ToolCallRecord) -> Ref:
    return Ref("tool_calls", record.task_id, record.repetition, record.index)


def _show(arguments: dict[str, Any] | None) -> str:
    return "" if not arguments else f"{arguments}"
