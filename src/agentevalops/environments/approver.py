"""The simulated approver: the environment's gate on irreversible tools
(docs/adr/0006 D5; ROADMAP R25).

Every call to an ``irreversible`` tool is put to the approver before it
runs. The scenario scripts the answers as data -- per tool, per call, in
order: ``approve``, ``deny`` or ``timeout`` -- and the approver answers
instantly; nothing waits on a clock. A call the approver did not approve
does not execute: the agent gets an error result saying so, and the
harness record says ``executed: false`` with the approval it got.

Fail closed: a tool with no scripted answers, or whose answers are used
up, is denied. The R24 scenarios therefore script ``approve`` for the
notices they send.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from agentevalops.core.errors import ConfigurationError
from agentevalops.core.schemas import ToolApproval

#: What a scenario may script for one call: the approver's three answers.
APPROVAL_ANSWERS: frozenset[str] = frozenset({"approve", "deny", "timeout"})

_OUTCOMES: dict[str, ToolApproval] = {
    "approve": "approved",
    "deny": "denied",
    "timeout": "timed_out",
}

#: A scripted schedule: tool name -> its answers, consumed one per call.
ApprovalSchedule = Mapping[str, Sequence[str]]


@runtime_checkable
class Approver(Protocol):
    """Answers for one call of an irreversible tool, instantly."""

    def decide(self, tool_name: str, arguments: Mapping[str, Any]) -> ToolApproval: ...


class ScriptedApprover:
    """Answers from a scenario's schedule, per tool and per call, in order;
    anything unscripted is denied (D5: nothing unapproved runs)."""

    def __init__(self, schedule: ApprovalSchedule | None = None) -> None:
        self._schedule: dict[str, tuple[str, ...]] = {}
        for tool, answers in (schedule or {}).items():
            if isinstance(answers, str) or not isinstance(answers, Sequence):
                raise ConfigurationError(
                    f"approvals for '{tool}' must be a list of answers, got {answers!r}"
                )
            unknown = sorted(set(answers) - APPROVAL_ANSWERS)
            if unknown:
                raise ConfigurationError(
                    f"approvals for '{tool}': unknown answer(s) {unknown}; use "
                    f"{sorted(APPROVAL_ANSWERS)}"
                )
            self._schedule[str(tool)] = tuple(str(a) for a in answers)
        self._calls: dict[str, int] = {}

    def decide(self, tool_name: str, arguments: Mapping[str, Any]) -> ToolApproval:
        number = self._calls.get(tool_name, 0)
        self._calls[tool_name] = number + 1
        answers = self._schedule.get(tool_name, ())
        if number >= len(answers):
            return "denied"
        return _OUTCOMES[answers[number]]

    def schedule(self) -> dict[str, tuple[str, ...]]:
        return dict(self._schedule)


__all__ = ["APPROVAL_ANSWERS", "ApprovalSchedule", "Approver", "ScriptedApprover"]
