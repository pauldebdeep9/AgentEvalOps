"""The generic benchmark behind every domain pack (docs/adr/0006 D23).

One task per scenario -- the orchestrator owns repetitions (D18). Each task
carries the scenario's applicable evaluators and expected values; each
task's environment is a fresh one from the pack. The benchmark also
provides each scenario's known-good script, so the orchestrator can prove
the scenario solvable before the agent runs (D16).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from agentevalops.core.errors import ConfigurationError
from agentevalops.core.protocols import DomainPack
from agentevalops.core.schemas import (
    AgentOutput,
    EnvHandle,
    EvaluationResult,
    Scenario,
    TaskSpec,
)
from agentevalops.core.types import TaskId


class AgenticBaselineBenchmark:
    """Runs one domain pack's scenarios.

    Takes the pack itself. From YAML, ``benchmark_options.pack`` names a
    registered domain pack and the registry's factory resolves it -- this
    module imports no registry, so the adapter families stay independent.
    ``scenarios`` optionally limits the run to some scenario ids.
    """

    benchmark_id: str = "agentic-baseline"
    benchmark_version: str = "0.1.0"

    def __init__(
        self,
        pack: DomainPack,
        scenarios: Sequence[str] | None = None,
    ) -> None:
        if not isinstance(pack, DomainPack):
            raise ConfigurationError(
                "agentic-baseline needs a domain pack, got "
                f"{type(pack).__name__}"
            )
        self._pack = pack
        available = {s.scenario_id: s for s in self._pack.scenarios()}
        if scenarios is not None:
            unknown = sorted(set(scenarios) - set(available))
            if unknown:
                raise ConfigurationError(
                    f"pack '{self._pack.pack_id}' has no scenario(s) {unknown}"
                )
            chosen = [s for s in self._pack.scenarios() if s.scenario_id in scenarios]
        else:
            chosen = list(self._pack.scenarios())
        self._scenarios = {s.scenario_id: s for s in chosen}

    @property
    def pack(self) -> DomainPack:
        return self._pack

    def _scenario(self, task: TaskSpec) -> Scenario:
        try:
            return self._scenarios[str(task.task_id)]
        except KeyError:
            raise ConfigurationError(
                f"task '{task.task_id}' is not a scenario of this run"
            ) from None

    def list_tasks(
        self, filter_spec: dict[str, Any] | None = None
    ) -> Iterable[TaskSpec]:
        return [
            TaskSpec(
                task_id=TaskId(s.scenario_id),
                benchmark_id=self.benchmark_id,
                description=s.description,
                metadata={
                    **s.expected,
                    "pack_id": self._pack.pack_id,
                    "pack_version": self._pack.pack_version,
                    "category": s.category,
                    "control_ids": list(s.control_ids),
                },
                applicable_evaluators=s.applicable_evaluators,
            )
            for s in self._scenarios.values()
        ]

    def grade(self, task: TaskSpec, output: AgentOutput) -> EvaluationResult:
        raise NotImplementedError(
            "agentic-baseline is graded by its evaluators, not by grade()"
        )

    def prepare_environment(self, task: TaskSpec) -> EnvHandle:
        scenario = self._scenario(task)
        return EnvHandle(
            task_id=task.task_id,
            tools=self._pack.build_environment(scenario, task.task_id),
        )

    def teardown(self, handle: EnvHandle) -> None:
        """In-process environments hold nothing to release."""

    def known_good_script(self, task: TaskSpec) -> Sequence[dict[str, Any]] | None:
        return self._scenario(task).known_good

    def scripted_trajectories(self, which: str) -> dict[str, Sequence[dict[str, Any]]]:
        """Every task's ``known_good`` or ``known_bad`` trajectory, keyed by
        task id -- what ``MockAgentRunner(scripts=...)`` takes, so a config
        can play a whole pack back (``mock_trajectory`` in YAML)."""
        if which not in ("known_good", "known_bad"):
            raise ConfigurationError(
                f"unknown trajectory '{which}': use 'known_good' or 'known_bad'"
            )
        return {
            scenario_id: getattr(scenario, which)
            for scenario_id, scenario in self._scenarios.items()
        }
