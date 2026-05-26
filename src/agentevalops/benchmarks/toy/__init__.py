"""Toy benchmark package — reference implementation of BenchmarkAdapter."""

from agentevalops.benchmarks.toy.adapter import ToyBenchmarkAdapter
from agentevalops.benchmarks.toy.scenarios import (
    SCENARIO_DESCRIPTIONS,
    SCENARIO_TASKS,
    SUPPORTED_SCENARIOS,
)
from agentevalops.benchmarks.toy.tasks import (
    FAILURE_TASKS,
    MIXED_TASKS,
    SMOKE_TASKS,
)

__all__ = [
    "ToyBenchmarkAdapter",
    "SUPPORTED_SCENARIOS",
    "SCENARIO_TASKS",
    "SCENARIO_DESCRIPTIONS",
    "SMOKE_TASKS",
    "FAILURE_TASKS",
    "MIXED_TASKS",
]
