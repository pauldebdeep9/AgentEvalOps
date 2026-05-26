"""Toy benchmark scenario registry.

A *scenario* is a named collection of tasks and a short description of the
expected run outcome.  Scenarios let a single benchmark adapter serve
multiple demo workflows without duplicating task definitions.

Supported scenarios
-------------------
smoke
    Two exact-match tasks that pass in the default MockAgentRunner happy
    mode.  The standard "everything works" baseline.

failure
    Two tasks whose ``mock_answer`` does not match the evaluation criterion.
    Both fail the DeterministicEvaluator even though the agent itself
    completes normally (success=True, termination_reason=COMPLETED).
    Demonstrates evaluation failure without agent error.

policy_violation
    Same tasks as ``smoke``.  The policy failure is driven entirely by
    config — set ``mock_cost_per_task_usd`` above the policy ceiling in the
    YAML.

trace_limit
    Same tasks as ``smoke``.  The policy failure is driven by
    ``policy.max_trace_events`` being lower than the actual trace count.

mixed
    Three tasks: two pass (exact-match and substring-match), one fails
    (wrong mock answer).  Useful for testing aggregate scorer behaviour.
"""

from __future__ import annotations

from agentevalops.benchmarks.toy.tasks import (
    FAILURE_TASKS,
    MIXED_TASKS,
    SMOKE_TASKS,
)
from agentevalops.core.schemas import TaskSpec

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

SUPPORTED_SCENARIOS: frozenset[str] = frozenset(
    {
        "smoke",
        "failure",
        "policy_violation",
        "trace_limit",
        "mixed",
    }
)

#: Maps each scenario name to its canonical task list.
SCENARIO_TASKS: dict[str, list[TaskSpec]] = {
    "smoke": SMOKE_TASKS,
    "failure": FAILURE_TASKS,
    # policy_violation and trace_limit use the same smoke tasks;
    # their distinct behaviour is entirely config-driven.
    "policy_violation": SMOKE_TASKS,
    "trace_limit": SMOKE_TASKS,
    "mixed": MIXED_TASKS,
}

#: One-line human description of each scenario's expected outcome.
SCENARIO_DESCRIPTIONS: dict[str, str] = {
    "smoke": "All tasks pass; policy passes.",
    "failure": (
        "All tasks fail evaluation (wrong mock answer); "
        "policy passes."
    ),
    "policy_violation": (
        "Tasks pass; policy fails because cost exceeds the "
        "configured ceiling."
    ),
    "trace_limit": (
        "Tasks pass; policy fails because the trace-event count "
        "exceeds the configured ceiling."
    ),
    "mixed": "Two tasks pass, one fails; useful for scorer tests.",
}
