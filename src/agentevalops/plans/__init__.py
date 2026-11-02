"""The pre-registered test plan (docs/adr/0005 D6; ROADMAP R27).

``build_plan`` turns a system declaration and a domain pack into a
``RunPlan``: which scenarios the declaration selects, with the reason for
each exclusion; which thresholds apply, each value with its source; and
every (task, repetition) seed. ``write_plan`` writes it as
``test_plan.json`` and returns its sha256, which the CLI registers before
the first task runs and ``BundleWriter`` records in ``metadata.json``.
``judge`` is the fail-closed gate over a finished bundle (``gate.py``).
``control_coverage`` joins the plan's selection with each scenario's
pass^k and the control catalogue, into ``controls.json``'s content
(``coverage.py``; docs/adr/0007 D5, D6).

Imports ``core``, ``bundles`` and ``catalogue`` (ARCHITECTURE.md §1;
docs/adr/0007 D13).
"""

from agentevalops.plans.builder import build_plan, undeclared_plan
from agentevalops.plans.coverage import (
    COVERAGE_STATES,
    EvidenceInputs,
    benchmark_descriptors,
    control_coverage,
    scenario_descriptors,
)
from agentevalops.plans.gate import GateError, GateResult, judge
from agentevalops.plans.io import (
    plan_from_document,
    plan_sha256,
    read_plan,
    write_plan,
)

__all__ = [
    "COVERAGE_STATES",
    "EvidenceInputs",
    "benchmark_descriptors",
    "GateError",
    "GateResult",
    "build_plan",
    "control_coverage",
    "judge",
    "plan_from_document",
    "plan_sha256",
    "read_plan",
    "scenario_descriptors",
    "undeclared_plan",
    "write_plan",
]
