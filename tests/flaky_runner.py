"""A test-only runner that passes each task run with probability ``p``,
decided by the run's own seed (docs/adr/0006 D6; ROADMAP R27).

The orchestrator hands every task run its derived seed in
``AgentInput.context["seed"]``. ``FlakyRunner`` draws one number from
``random.Random(seed)`` and plays the task's known-good trajectory when it
is below ``p``, its known-bad one otherwise -- or, for a benchmark without
scripts, answers correctly or wrongly. Deterministic given the seed, so a
test can compute from the same seeds exactly which runs pass, and hence
the exact pass^k the scorer must report.
"""

from __future__ import annotations

import random
from dataclasses import replace
from typing import Any

from agentevalops.agents.mock_runner import MockAgentRunner
from agentevalops.core.types import AgentId

__all__ = ["FlakyRunner", "passes"]


def passes(seed: int, p: float) -> bool:
    """Whether the run seeded *seed* passes under probability *p*."""
    return random.Random(seed).random() < p


class FlakyRunner:
    agent_id = AgentId("flaky")

    def __init__(self, p: float, benchmark: Any = None) -> None:
        self.p = p
        self.seeds: list[int] = []
        if benchmark is not None and hasattr(benchmark, "scripted_trajectories"):
            self._good = MockAgentRunner(
                scripts=benchmark.scripted_trajectories("known_good")
            )
            self._bad = MockAgentRunner(
                scripts=benchmark.scripted_trajectories("known_bad")
            )
            self._scripted = True
        else:
            self._good = MockAgentRunner()
            self._bad = MockAgentRunner()
            self._scripted = False

    async def run(self, input, resource_limits):  # type: ignore[no-untyped-def]
        seed = int(input.context["seed"])
        self.seeds.append(seed)
        if passes(seed, self.p):
            return await self._good.run(input, resource_limits)
        if self._scripted:
            return await self._bad.run(input, resource_limits)
        wrong = replace(
            input.task, metadata={**input.task.metadata, "mock_answer": "wrong answer"}
        )
        return await self._bad.run(replace(input, task=wrong), resource_limits)
