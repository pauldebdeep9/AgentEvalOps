"""An in-process tool environment that keeps its own call records (ADR-0006 D2, D4)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from agentevalops.core.errors import ConfigurationError
from agentevalops.core.schemas import (
    RESERVED_TOOL_NAMES,
    EndState,
    ToolApproval,
    ToolCallRecord,
    ToolResult,
    ToolSpec,
)
from agentevalops.core.types import TaskId
from agentevalops.environments.approver import Approver, ScriptedApprover
from agentevalops.environments.faults import ScriptedFaults, garble
from agentevalops.environments.injections import ScriptedInjections

#: A tool's implementation: arguments in, the text the agent sees out.
#: Raising is allowed -- the environment turns it into a recorded error.
ToolHandler = Callable[[dict[str, Any]], Awaitable[str]]


class ToolEnvironmentError(ConfigurationError):
    """An environment was declared wrongly (a reserved or duplicate tool
    name, or a tool with no handler)."""


class InProcessToolEnvironment:
    """Runs tools as Python callables and records every call itself.

    The record is the harness's, not the agent's: it is written here, for
    every call -- an unknown tool and a failing handler included -- and a
    runner cannot add to it or edit it (ADR-0006 D4). ``call`` never raises
    on a bad call; the agent gets an error result and the record says so.

    Every call to an ``irreversible`` tool goes to the ``approver`` first
    (D5). Not approved -- denied, or timed out, both answered instantly --
    means the call does not execute: the agent is told the action was not
    taken and why, and the record says ``executed: false`` with the
    approval it got. With no approver given, nothing irreversible is
    approved.

    Scheduled ``faults`` (D6) apply after the gate: a timeout or transient
    error stops the call before the handler, a malformed result lets the
    handler run and garbles what the agent sees; every fault is written to
    the record's ``fault`` field. Scheduled ``injections`` (D6) append
    their payload to a result that executed without error, and the record's
    ``injections`` field names them.
    """

    def __init__(
        self,
        task_id: TaskId,
        specs: Sequence[ToolSpec],
        handlers: Mapping[str, ToolHandler],
        *,
        repetition: int = 0,
        end_state: Callable[[], EndState] | None = None,
        approver: Approver | None = None,
        faults: ScriptedFaults | None = None,
        injections: ScriptedInjections | None = None,
    ) -> None:
        names = [spec.name for spec in specs]
        reserved = sorted(set(names) & RESERVED_TOOL_NAMES)
        if reserved:
            raise ToolEnvironmentError(
                f"tool name(s) {reserved} are reserved for the runners' own "
                "controls (docs/adr/0006 D3)"
            )
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ToolEnvironmentError(f"duplicate tool name(s): {duplicates}")
        unhandled = sorted(set(names) - set(handlers))
        if unhandled:
            raise ToolEnvironmentError(f"tool(s) with no handler: {unhandled}")
        self._task_id = task_id
        self._repetition = repetition
        self._specs = tuple(specs)
        self._by_name = {spec.name: spec for spec in specs}
        self._handlers = dict(handlers)
        self._end_state = end_state
        self._approver: Approver = approver or ScriptedApprover()
        self._faults = faults or ScriptedFaults()
        self._injections = injections or ScriptedInjections()
        self._calls: dict[str, int] = {}
        self._records: list[ToolCallRecord] = []

    async def list_tools(self) -> Sequence[ToolSpec]:
        return self._specs

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        spec = self._by_name.get(name)
        handler = self._handlers.get(name)
        executed = False
        approval: ToolApproval = "not_required"
        fault: str | None = None
        injected: tuple[str, ...] = ()
        self._calls[name] = self._calls.get(name, 0) + 1
        if spec is None or handler is None:
            content, error = f"error: unknown tool '{name}'", True
        elif (
            spec.permission == "irreversible"
            and (approval := self._approver.decide(name, arguments)) != "approved"
        ):
            # docs/adr/0006 D5: nothing unapproved runs.
            why = "timed out" if approval == "timed_out" else "denied"
            content, error = f"error: {name} was not taken: approval {why}", True
        elif (fault := self._faults.fault_for(name, self._calls[name])) in (
            "timeout",
            "transient_error",
        ):
            # docs/adr/0006 D6: the call never reaches the handler.
            what = (
                "timed out"
                if fault == "timeout"
                else "is temporarily unavailable; try again"
            )
            content, error = f"error: {name} {what}", True
        else:
            executed = True
            try:
                content, error = await handler(dict(arguments)), False
            except Exception as exc:  # noqa: BLE001 - recorded, never raised
                content, error = f"error: {exc}", True
            if fault == "malformed_result" and not error:
                content = garble(content)
            if not error:
                # docs/adr/0006 D6: injected content rides on the result.
                payloads = self._injections.payloads_for(name, self._calls[name])
                if payloads:
                    content = "\n".join([content, *(text for _, text in payloads)])
                    injected = tuple(ident for ident, _ in payloads)
        self._records.append(
            ToolCallRecord(
                task_id=self._task_id,
                repetition=self._repetition,
                index=len(self._records),
                tool_name=name,
                arguments=dict(arguments),
                permission=spec.permission if spec else None,
                data_class=spec.data_class if spec else None,
                executed=executed,
                result=content,
                error=error,
                approval=approval,
                fault=fault,
                injections=injected,
            )
        )
        return ToolResult(content=content, error=error)

    async def snapshot(self) -> EndState:
        if self._end_state is not None:
            return self._end_state()
        return {"schema": "inprocess-calls-v0", "calls": len(self._records)}

    def records(self) -> Sequence[ToolCallRecord]:
        return tuple(self._records)
