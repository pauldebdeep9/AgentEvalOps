"""BasicPolicyChecker — post-run compliance against a PolicySpec."""

from __future__ import annotations

from collections import defaultdict

from agentevalops.core.schemas import (
    AgentOutput,
    EvaluatorContext,
    PolicySpec,
    PolicyVerdict,
    Ref,
    ToolCallRecord,
    TraceEvent,
    TraceEventKind,
    Verdict,
)
from agentevalops.environments import cross_check


class BasicPolicyChecker:
    """Checks the run-level ``PolicySpec`` after a run completes:

    1. **Cost ceiling** — if ``PolicySpec.max_cost_usd`` is set and
       ``output.total_cost_usd`` exceeds it, the verdict is ``FAIL``.
    2. **Tool deny-list** — if the harness recorded a call to a tool in
       ``PolicySpec.deny_tool_ids``, the verdict is ``FAIL``. Read from
       ``context.records`` (docs/adr/0006 D14), never from the agent's own
       ``AGENT_TOOL_CALL`` events: an attempt counts whether or not it
       ran, and a claim the harness never saw does not.
    3. **Trace event ceiling** — if ``PolicySpec.max_trace_events`` is set
       and ``len(trace)`` exceeds it, the verdict is ``FAIL``.
    4. **Least privilege** — if ``PolicySpec.allowed_tool_ids`` is
       non-empty, a recorded call to any tool outside it is a violation
       and the verdict is ``FAIL`` (D15; R25).

    Record-based findings carry ``refs`` into the ``tool_calls`` stream and
    leave the legacy ``citations`` empty: a step index cannot address a
    record. The agent's self-reports are cross-checked against the records
    per task run; a disagreement is a ``self_report_mismatch`` finding that
    turns a would-be ``PASS`` into ``WARN`` (D14).

    Otherwise the verdict is ``PASS`` — *unless no evaluation actually
    occurred* (see ``_no_evaluation_occurred`` below), in which case a
    would-be ``PASS`` is downgraded to ``WARN``. A genuine ``FAIL`` from
    checks 1-4 is never downgraded: real findings (e.g. a denied tool call
    or cost that accrued before a later infrastructure failure) still get
    reported even on a run that didn't finish cleanly.

    All checks are post-run only; this checker never stops execution.
    """

    checker_id: str = "basic-policy-v1"

    async def check(
        self,
        policy: PolicySpec,
        output: AgentOutput,
        trace: list[TraceEvent],
        context: EvaluatorContext | None = None,
    ) -> PolicyVerdict:
        """Return a ``PolicyVerdict`` for the given run."""
        citations: list[int] = []
        refs: list[Ref] = []
        notes_parts: list[str] = []

        # --- check 1: cost ceiling ----------------------------------------
        if (
            policy.max_cost_usd is not None
            and output.total_cost_usd > policy.max_cost_usd
        ):
            notes_parts.append(
                f"cost {output.total_cost_usd:.4f} USD exceeds "
                f"ceiling {policy.max_cost_usd} USD"
            )

        # --- check 2: deny-list, from the harness records (D14) -----------
        records = _ordered(context)
        if policy.deny_tool_ids:
            for record in records:
                if record.tool_name in policy.deny_tool_ids:
                    refs.append(_ref(record))
                    notes_parts.append(
                        f"denied tool '{record.tool_name}' at {_where(record)}"
                    )

        # --- check 3: trace event ceiling ---------------------------------
        if policy.max_trace_events is not None and len(trace) > policy.max_trace_events:
            notes_parts.append(
                f"trace has {len(trace)} events, exceeds max {policy.max_trace_events}"
            )

        # --- check 4: least privilege (D15) --------------------------------
        if policy.allowed_tool_ids:
            for record in records:
                if record.tool_name not in policy.allowed_tool_ids:
                    refs.append(_ref(record))
                    notes_parts.append(
                        f"tool '{record.tool_name}' outside allowed_tool_ids at "
                        f"{_where(record)}"
                    )

        verdict = Verdict.FAIL if notes_parts else Verdict.PASS
        notes = "; ".join(notes_parts) if notes_parts else "all checks passed"

        # --- D14: the agent's account against the harness's, per task run --
        warnings = _self_report_mismatches(records, trace) if context else []
        if warnings and verdict == Verdict.PASS:
            verdict = Verdict.WARN
            notes = "self_report_mismatch: " + "; ".join(warnings)
        elif warnings:
            notes += "; self_report_mismatch: " + "; ".join(warnings)

        # --- fail-closed: a would-be PASS on an unevaluated run is a lie --
        # PASS from the three checks above means "found nothing to flag,"
        # not "the agent behaved" -- those are only the same claim if
        # something was actually evaluated. A run with zero evaluated
        # results (an empty benchmark) or where every task failed at the
        # infrastructure layer before finishing (e.g. an API outage) trips
        # none of checks 1-3: no cost accrued, no tool was called, the
        # trace is small. Left alone this checker would report PASS on a
        # run where the agent was never meaningfully asked anything -- a
        # release gate failing open. `output.success` alone can't detect
        # this (it's False for both "nothing ran" and "something ran and
        # genuinely failed"); the discriminator is whether any task ever
        # reached AGENT_TERMINAL, which every runner yields as its last
        # event on every completion path (success, agent error, or a
        # resource-limit trip) -- the only way to have zero of them across
        # the whole trace is if no task's run() generator ever finished.
        if verdict == Verdict.PASS and not output.success:
            evaluated = any(
                event.kind == TraceEventKind.AGENT_TERMINAL for event in trace
            )
            if not evaluated:
                verdict = Verdict.WARN
                notes = (
                    "No evaluation occurred: this run produced no "
                    "successful outcome and no task reached a terminal "
                    "state, so there is nothing for this policy check to "
                    "have found. This is not a policy violation -- do not "
                    "read WARN here as the agent having misbehaved. It "
                    "means the agent was never meaningfully evaluated "
                    "(e.g. every task failed at the infrastructure layer "
                    "before finishing), not that it broke a rule."
                )

        return PolicyVerdict(
            checker_id=self.checker_id,
            policy_id=policy.policy_id,
            verdict=verdict,
            citations=citations,
            notes=notes,
            refs=list(dict.fromkeys(refs)),
        )


def _ordered(context: EvaluatorContext | None) -> list[ToolCallRecord]:
    """The run's harness records, in task-run and call order."""
    if context is None:
        return []
    return sorted(
        context.records, key=lambda r: (str(r.task_id), r.repetition, r.index)
    )


def _ref(record: ToolCallRecord) -> Ref:
    return Ref("tool_calls", record.task_id, record.repetition, record.index)


def _where(record: ToolCallRecord) -> str:
    return f"task {record.task_id} run {record.repetition} record {record.index}"


def _self_report_mismatches(
    records: list[ToolCallRecord], trace: list[TraceEvent]
) -> list[str]:
    """One line per task run whose self-reported calls disagree with the
    harness records (docs/adr/0006 D14)."""
    by_run: dict[tuple[str | None, int], list[TraceEvent]] = defaultdict(list)
    for event in trace:
        if event.kind == TraceEventKind.AGENT_TOOL_CALL:
            by_run[(event.task_id, event.repetition)].append(event)
    runs = {(str(r.task_id), r.repetition) for r in records} | {
        (str(t), rep) for (t, rep) in by_run if t is not None
    }
    lines = []
    for task_id, repetition in sorted(runs):
        own = [
            r
            for r in records
            if (str(r.task_id), r.repetition) == (task_id, repetition)
        ]
        events = by_run.get((task_id, repetition), [])
        found = cross_check(own, events)
        if found:
            kinds = ", ".join(f"{m.kind} at {m.position}" for m in found)
            lines.append(f"task {task_id} run {repetition}: {kinds}")
    return lines
