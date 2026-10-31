"""Harness-owned tool environments (docs/adr/0006, D2, D8).

The low tier between the adapters and ``core`` (D8): benchmarks build
environments from here, and runners fall back to the toy environment when
called directly. Imports nothing from ``agentevalops`` except ``core``.
"""

from agentevalops.environments.crosscheck import Mismatch, cross_check
from agentevalops.environments.inprocess import (
    InProcessToolEnvironment,
    ToolEnvironmentError,
    ToolHandler,
)
from agentevalops.environments.toy import (
    CALCULATOR_TOOL_SPEC,
    run_calculator,
    toy_environment,
)

__all__ = [
    "CALCULATOR_TOOL_SPEC",
    "InProcessToolEnvironment",
    "Mismatch",
    "ToolEnvironmentError",
    "ToolHandler",
    "cross_check",
    "run_calculator",
    "toy_environment",
]
