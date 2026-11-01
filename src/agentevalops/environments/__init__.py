"""Harness-owned tool environments (docs/adr/0006, D2, D8).

The low tier between the adapters and ``core`` (D8): benchmarks build
environments from here, and runners fall back to the toy environment when
called directly. Imports nothing from ``agentevalops`` except ``core``.
"""

from agentevalops.environments.approver import (
    APPROVAL_ANSWERS,
    ApprovalSchedule,
    Approver,
    ScriptedApprover,
)
from agentevalops.environments.crosscheck import Mismatch, cross_check
from agentevalops.environments.faults import FaultSchedule, ScriptedFaults, garble
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
    "APPROVAL_ANSWERS",
    "ApprovalSchedule",
    "Approver",
    "CALCULATOR_TOOL_SPEC",
    "FaultSchedule",
    "InProcessToolEnvironment",
    "Mismatch",
    "ScriptedApprover",
    "ScriptedFaults",
    "ToolEnvironmentError",
    "ToolHandler",
    "cross_check",
    "garble",
    "run_calculator",
    "toy_environment",
]
