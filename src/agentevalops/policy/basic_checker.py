"""BasicPolicyChecker — post-run compliance against a PolicySpec."""

from __future__ import annotations

from agentevalops.core.schemas import (
    TOOL_NAME_PAYLOAD_KEY,
    AgentOutput,
    PolicySpec,
    PolicyVerdict,
    TraceEvent,
    TraceEventKind,
    Verdict,
)


class BasicPolicyChecker:
    """Checks three simple invariants after a run completes:

    1. **Cost ceiling** — if ``PolicySpec.max_cost_usd`` is set and
       ``output.total_cost_usd`` exceeds it, the verdict is ``FAIL``.
    2. **Tool deny-list** — if any ``AGENT_TOOL_CALL`` event used a tool
       in ``PolicySpec.deny_tool_ids``, the verdict is ``FAIL``.
    3. **Trace event ceiling** — if ``PolicySpec.max_trace_events`` is set
       and ``len(trace)`` exceeds it, the verdict is ``FAIL``.

    Otherwise the verdict is ``PASS`` — *unless no evaluation actually
    occurred* (see ``_no_evaluation_occurred`` below), in which case a
    would-be ``PASS`` is downgraded to ``WARN``. A genuine ``FAIL`` from
    checks 1-3 is never downgraded: real findings (e.g. a denied tool call
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
    ) -> PolicyVerdict:
        """Return a ``PolicyVerdict`` for the given run."""
        citations: list[int] = []
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

        # --- check 2: deny-list -------------------------------------------
        if policy.deny_tool_ids:
            for event in trace:
                if event.kind != TraceEventKind.AGENT_TOOL_CALL:
                    continue
                tool_id: str = event.payload.get(TOOL_NAME_PAYLOAD_KEY, "")
                if tool_id in policy.deny_tool_ids:
                    citations.append(event.step_index)
                    notes_parts.append(
                        f"denied tool '{tool_id}' at step"
                        f" {event.step_index}"
                    )

        # --- check 3: trace event ceiling ---------------------------------
        if (
            policy.max_trace_events is not None
            and len(trace) > policy.max_trace_events
        ):
            notes_parts.append(
                f"trace has {len(trace)} events, "
                f"exceeds max {policy.max_trace_events}"
            )

        verdict = Verdict.FAIL if notes_parts else Verdict.PASS
        notes = (
            "; ".join(notes_parts) if notes_parts else "all checks passed"
        )

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
                event.kind == TraceEventKind.AGENT_TERMINAL
                for event in trace
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
        )
