"""Declarative content injection: the environment's injection slot
(docs/adr/0006 D6; ROADMAP R26; testing-framework 4.1.1).

A scenario schedules payloads as data -- "payload *P* in the result of tool
*T*" at its *n*-th call, or at every call when no call is given -- and the
environment appends each payload to the result the agent sees. This is the
indirect prompt injection carried by tool results and retrieved documents:
what the agent then does is what the evaluators judge.

Every injection is written to the harness record's ``injections`` field by
id, and the payload itself is in the recorded result, so a run's records
show each payload it received (R26's exit criterion). A payload reaches
only a result that executed without error: a refused or faulted call
carries nothing to inject into.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agentevalops.core.errors import ConfigurationError

#: One scheduled payload as data:
#: ``{"id": name, "tool": tool, "payload": text, "call": n}`` (``call`` optional).
InjectionSchedule = Sequence[Mapping[str, Any]]


class ScriptedInjections:
    """Payloads from a scenario's schedule, by tool and call number."""

    def __init__(self, schedule: InjectionSchedule | None = None) -> None:
        self._items: list[tuple[str, str, int | None, str]] = []
        seen: set[str] = set()
        for position, item in enumerate(schedule or ()):
            if not isinstance(item, Mapping):
                raise ConfigurationError(f"injection {position}: must be a mapping")
            ident = str(item.get("id", "")).strip()
            tool = str(item.get("tool", "")).strip()
            payload = item.get("payload")
            call = item.get("call")
            if not ident:
                raise ConfigurationError(f"injection {position}: needs an id")
            if ident in seen:
                raise ConfigurationError(f"injection {position}: id '{ident}' repeats")
            if not tool:
                raise ConfigurationError(f"injection {position}: needs a tool")
            if not isinstance(payload, str) or not payload.strip():
                raise ConfigurationError(
                    f"injection {position}: payload must be a non-empty string"
                )
            if call is not None and (
                isinstance(call, bool) or not isinstance(call, int) or call < 1
            ):
                raise ConfigurationError(
                    f"injection {position}: 'call' must be a positive integer or absent"
                )
            seen.add(ident)
            self._items.append((ident, tool, call, payload))

    def payloads_for(self, tool_name: str, call_number: int) -> list[tuple[str, str]]:
        """The (id, payload) pairs for this (1-based) call of the tool."""
        return [
            (ident, payload)
            for ident, tool, call, payload in self._items
            if tool == tool_name and (call is None or call == call_number)
        ]

    def schedule(self) -> list[dict[str, Any]]:
        return [
            {"id": ident, "tool": tool, "call": call, "payload": payload}
            for ident, tool, call, payload in self._items
        ]


__all__ = ["InjectionSchedule", "ScriptedInjections"]
