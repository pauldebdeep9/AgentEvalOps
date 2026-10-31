"""Cross-check the agent's self-reported tool calls against the harness's
own records (docs/adr/0006 D14).

R23 builds it as a plain function over in-memory records -- enough for its
exit test. From R24 the evaluators and the policy checker reach the records
through ``EvaluatorContext``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from agentevalops.core.schemas import (
    FINISH_TOOL_SPEC,
    TOOL_NAME_PAYLOAD_KEY,
    ToolCallRecord,
    TraceEvent,
    TraceEventKind,
)

MismatchKind = Literal["different_call", "unreported_call", "unrecorded_report"]


@dataclass(frozen=True)
class Mismatch:
    """One disagreement between what the agent said it called and what the
    environment recorded. ``position`` is the call's place in order."""

    kind: MismatchKind
    position: int
    recorded: str | None
    reported: str | None


def _reports(events: Sequence[TraceEvent]) -> list[tuple[str, dict[str, Any]]]:
    reports = []
    for event in events:
        if event.kind != TraceEventKind.AGENT_TOOL_CALL:
            continue
        name = str(event.payload.get(TOOL_NAME_PAYLOAD_KEY, ""))
        if name == FINISH_TOOL_SPEC.name:  # the runner's control, never recorded
            continue
        args = event.payload.get("args", {})
        reports.append((name, dict(args) if isinstance(args, dict) else {}))
    return reports


def cross_check(
    records: Sequence[ToolCallRecord], events: Sequence[TraceEvent]
) -> list[Mismatch]:
    """Pair the agent's reported tool calls with the harness's records, in
    order, and return every disagreement. An empty list means they match.

    A reported call the environment never received (``unrecorded_report``)
    includes one whose arguments were malformed JSON: the runner reports the
    attempt, but nothing executed.
    """
    reports = _reports(events)
    ordered = sorted(records, key=lambda r: r.index)
    mismatches = []
    for position in range(max(len(ordered), len(reports))):
        record = ordered[position] if position < len(ordered) else None
        report = reports[position] if position < len(reports) else None
        if record is None and report is not None:
            mismatches.append(
                Mismatch("unrecorded_report", position, None, report[0])
            )
        elif report is None and record is not None:
            mismatches.append(
                Mismatch("unreported_call", position, record.tool_name, None)
            )
        elif record is not None and report is not None and (
            record.tool_name != report[0] or record.arguments != report[1]
        ):
            mismatches.append(
                Mismatch("different_call", position, record.tool_name, report[0])
            )
    return mismatches
