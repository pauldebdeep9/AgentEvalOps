"""Post-hoc policy grading, distinct from ``ResourceLimits``.

``ResourceLimits`` is enforced by the orchestrator *during* an attempt and
stops it. ``PolicySpec`` is graded *after* the attempt ends and never
intercepts anything — a denied tool call still happens; the point of this
module is that the trace proves it happened. That split is one of the four
ideas DESIGN.md §4 says this project exists to teach, and it is why
``check_policy`` takes a finished ``AgentOutput`` and a complete trace
rather than running alongside the attempt.

``PolicySpec.max_cost_usd`` and ``ResourceLimits.max_cost_usd`` share a name
and do different jobs on purpose: the latter stops a run mid-attempt, this
module's version only observes and reports a ``Verdict.WARN`` after the
fact. A cost overrun is a fact about the run worth surfacing, not a rule
the agent broke, so it is graded differently from a denied tool call —
``Verdict.FAIL`` is reserved for ``deny_tool_names``. Where both fire, the
verdict is ``FAIL`` (precedence: FAIL beats WARN beats PASS) and the notes
still mention the cost overrun — the lesser finding must not disappear
because a worse one exists.

An empty ``PolicySpec`` (no deny list, ``max_cost_usd=None``) returns
``Verdict.PASS`` with no citations, and that is correct, not vacuous: a
policy that forbids nothing is a real, satisfied policy. This is the
opposite judgement from ``evaluate.py``'s vacuous-criteria guard, where a
task with no evaluator-side criteria is a mis-specified task and raises
``ConfigurationError`` — a policy checking nothing and a task testing
nothing are not the same kind of empty.
"""

from __future__ import annotations

from minieval.schemas import (
    TOOL_NAME_PAYLOAD_KEY,
    AgentOutput,
    PolicySpec,
    PolicyVerdict,
    TraceEvent,
    TraceEventKind,
    Verdict,
)


def check_policy(
    spec: PolicySpec, output: AgentOutput, trace: list[TraceEvent]
) -> PolicyVerdict:
    """Grade one attempt's trace and output against ``spec``."""
    denied_calls = [
        e
        for e in trace
        if e.kind is TraceEventKind.AGENT_TOOL_CALL
        and e.payload[TOOL_NAME_PAYLOAD_KEY] in spec.deny_tool_names
    ]

    citations: list[int] = []
    notes: list[str] = []
    verdict = Verdict.PASS

    if denied_calls:
        verdict = Verdict.FAIL
        citations.extend(e.step_index for e in denied_calls)
        denied_names: list[str] = [
            e.payload[TOOL_NAME_PAYLOAD_KEY] for e in denied_calls
        ]
        counts = {name: denied_names.count(name) for name in sorted(set(denied_names))}
        notes.append(
            "denied tool calls made: "
            + ", ".join(f"{name} x{count}" for name, count in counts.items())
        )

    if spec.max_cost_usd is not None and output.cost_usd > spec.max_cost_usd:
        if verdict is not Verdict.FAIL:
            verdict = Verdict.WARN
        notes.append(
            f"cost_usd {output.cost_usd} exceeds max_cost_usd {spec.max_cost_usd}"
        )

    return PolicyVerdict(
        policy_id=spec.policy_id,
        verdict=verdict,
        citations=citations,
        notes="; ".join(notes),
    )
