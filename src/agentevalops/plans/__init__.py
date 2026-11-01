"""The pre-registered test plan (docs/adr/0005 D6; ROADMAP R27).

``build_plan`` turns a system declaration and a domain pack into a
``TestPlan``: which scenarios the declaration selects, with the reason for
each exclusion; which thresholds apply, each value with its source; and
every (task, repetition) seed. ``write_plan`` writes it as
``test_plan.json`` and returns its sha256, which the CLI registers before
the first task runs and ``BundleWriter`` records in ``metadata.json``.

Imports ``core`` and ``bundles`` only (ARCHITECTURE.md §1).
"""

from agentevalops.plans.builder import build_plan
from agentevalops.plans.io import plan_sha256, read_plan, write_plan

__all__ = ["build_plan", "plan_sha256", "read_plan", "write_plan"]
