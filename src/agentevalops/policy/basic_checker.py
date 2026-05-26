"""BasicPolicyChecker — post-run compliance against a PolicySpec."""

from __future__ import annotations

from agentevalops.core.schemas import (
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

    Otherwise the verdict is ``PASS``.
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
                tool_id: str = event.payload.get("tool", "")
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
        return PolicyVerdict(
            checker_id=self.checker_id,
            policy_id=policy.policy_id,
            verdict=verdict,
            citations=citations,
            notes=notes,
        )
