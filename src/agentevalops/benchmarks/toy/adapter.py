"""ToyBenchmarkAdapter — scenario-aware reference implementation of BenchmarkAdapter.

This adapter is the canonical example of how to implement the BenchmarkAdapter
protocol for AgentEvalOps.  It is intentionally simple so that contributors
can see the full contract without distraction.

Responsibilities
----------------
* Expose benchmark metadata (``benchmark_id``, ``benchmark_version``,
  ``scenario``).
* Load or construct task specs for the requested scenario.
* Provide expected-output / acceptance metadata in each ``TaskSpec``.
* Implement the benchmark's ``grade()`` method for post-hoc offline scoring.

Non-responsibilities
--------------------
* Complex scoring — that belongs to
  :class:`~agentevalops.evaluators.deterministic.DeterministicEvaluator`.
* Policy checking — that belongs to
  :class:`~agentevalops.policy.basic_checker.BasicPolicyChecker`.
* Orchestration logic.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from agentevalops.benchmarks.toy.scenarios import (
    SCENARIO_TASKS,
    SUPPORTED_SCENARIOS,
)
from agentevalops.core.errors import ConfigurationError
from agentevalops.core.schemas import AgentOutput, EvaluationResult, TaskSpec


class ToyBenchmarkAdapter:
    """Scenario-aware reference benchmark adapter.

    Parameters
    ----------
    scenario:
        One of the supported scenario names (``"smoke"``, ``"failure"``,
        ``"policy_violation"``, ``"trace_limit"``, ``"mixed"``).  Defaults
        to ``"smoke"``.

    Raises
    ------
    ConfigurationError
        If *scenario* is not one of the supported values.
    """

    benchmark_id: str = "toy"
    benchmark_version: str = "0.2.0"

    def __init__(self, scenario: str = "smoke") -> None:
        if scenario not in SUPPORTED_SCENARIOS:
            raise ConfigurationError(
                f"Unknown toy scenario '{scenario}'. "
                f"Supported: {sorted(SUPPORTED_SCENARIOS)}"
            )
        self._scenario = scenario

    @property
    def scenario(self) -> str:
        """The active scenario name."""
        return self._scenario

    def list_tasks(
        self,
        filter_spec: dict[str, Any] | None = None,
    ) -> Iterable[TaskSpec]:
        """Return the scenario's task list, optionally filtered by ID.

        Parameters
        ----------
        filter_spec:
            If provided and contains a ``"task_ids"`` key, only tasks whose
            :attr:`~agentevalops.core.schemas.TaskSpec.task_id` appears in
            that list are returned.
        """
        tasks = SCENARIO_TASKS[self._scenario]
        if filter_spec and "task_ids" in filter_spec:
            ids = set(filter_spec["task_ids"])
            return [t for t in tasks if t.task_id in ids]
        return list(tasks)

    def grade(self, task: TaskSpec, output: AgentOutput) -> EvaluationResult:
        """Apply the toy benchmark's official grading: did the agent succeed?

        The toy benchmark's "official" grade is simply whether the agent run
        completed without an error (``output.success``).  Richer criteria
        such as expected-output matching are implemented separately by
        :class:`~agentevalops.evaluators.deterministic.DeterministicEvaluator`
        so that platform-level evaluation logic is kept out of the adapter.
        """
        passed = output.success
        return EvaluationResult(
            evaluator_id="toy-grade",
            evaluator_kind="deterministic",
            score=1.0 if passed else 0.0,
            passed=passed,
            notes="toy-grade: agent success flag",
        )
