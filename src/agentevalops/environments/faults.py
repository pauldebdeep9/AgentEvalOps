"""Declarative tool faults: the environment's fault slot (docs/adr/0006 D6;
ROADMAP R26).

A scenario schedules faults as data -- "fault *kind* on tool *T* at its
*n*-th call" -- and no random number decides one. Three kinds:

- ``timeout``: the call never returns; the handler does not run, the agent
  gets an error result saying so, the record says ``executed: false``.
- ``transient_error``: the tool is briefly unavailable; same shape, and the
  next call runs normally unless another fault is scheduled.
- ``malformed_result``: the handler runs -- any state change stands -- but
  the agent receives a garbled result.

Every fault is written to the harness record's ``fault`` field, so a run's
records show each fault it received (R26's exit criterion). The slot comes
after the approval gate: a call the approver refused gets no fault.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agentevalops.core.errors import ConfigurationError
from agentevalops.core.schemas import FAULT_KINDS

#: One scheduled fault as data: ``{"tool": name, "call": n, "kind": kind}``.
FaultSchedule = Sequence[Mapping[str, Any]]


class ScriptedFaults:
    """Faults from a scenario's schedule, keyed by tool and call number."""

    def __init__(self, schedule: FaultSchedule | None = None) -> None:
        self._faults: dict[tuple[str, int], str] = {}
        for position, item in enumerate(schedule or ()):
            if not isinstance(item, Mapping):
                raise ConfigurationError(f"fault {position}: must be a mapping")
            tool = str(item.get("tool", ""))
            kind = str(item.get("kind", ""))
            call = item.get("call", 1)
            if not tool:
                raise ConfigurationError(f"fault {position}: needs a tool")
            if kind not in FAULT_KINDS:
                raise ConfigurationError(
                    f"fault {position}: unknown kind '{kind}'; "
                    f"use {sorted(FAULT_KINDS)}"
                )
            if isinstance(call, bool) or not isinstance(call, int) or call < 1:
                raise ConfigurationError(
                    f"fault {position}: 'call' must be a positive integer (1 = first)"
                )
            if (tool, call) in self._faults:
                raise ConfigurationError(
                    f"fault {position}: call {call} of '{tool}' already has a fault"
                )
            self._faults[(tool, call)] = kind

    def fault_for(self, tool_name: str, call_number: int) -> str | None:
        """The fault scheduled for this (1-based) call of the tool, if any."""
        return self._faults.get((tool_name, call_number))

    def schedule(self) -> list[dict[str, Any]]:
        return [
            {"tool": tool, "call": call, "kind": kind}
            for (tool, call), kind in sorted(self._faults.items())
        ]


def garble(content: str) -> str:
    """A malformed version of a tool result: cut short and ended with
    bytes that no well-formed result carries."""
    head = content[: max(1, len(content) // 2)]
    return f"{head}��{{{{"


__all__ = ["FaultSchedule", "ScriptedFaults", "garble"]
