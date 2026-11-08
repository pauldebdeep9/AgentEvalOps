"""Reading and writing ``test_plan.json`` (docs/adr/0005 D6; R27)."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path
from typing import Any

from agentevalops.bundles.constants import TEST_PLAN_FILENAME
from agentevalops.core.schemas import (
    BusinessMetric,
    DeclaredTool,
    PlanExclusion,
    PlanThreshold,
    RunLimits,
    RunPlan,
    SystemDeclaration,
    ThresholdOverride,
)

__all__ = [
    "plan_from_document",
    "plan_sha256",
    "read_plan",
    "to_document",
    "write_plan",
]


def to_document(plan: RunPlan) -> dict[str, Any]:
    """The plan as the JSON document ``test_plan.json`` holds: the dataclass
    as data, with the pack's identity and data hash gathered under ``pack``
    and the declaration's own hash lifted to ``system_sha256``. An
    undeclared plan (``declared: false``, plan version "2") has ``null``
    for ``system``, ``system_path`` and ``system_sha256``, and for ``pack``
    when its benchmark has none (R29 (b2)). Plain
    JSON types only, so the file is readable without this package."""
    data = dataclasses.asdict(plan)
    pack = {
        "pack_id": data.pop("pack_id"),
        "pack_version": data.pop("pack_version"),
        "scenarios_sha256": data.pop("scenarios_sha256"),
        "data_sha256": data.pop("data_sha256"),
    }
    data["pack"] = None if pack["pack_id"] is None else pack
    data["system_sha256"] = None if plan.system is None else plan.system.sha256
    if plan.system is not None:
        # R35: an unset optional field of the declaration is left out, so
        # it changes no plan written before it existed.
        for f in dataclasses.fields(plan.system):
            unset = getattr(plan.system, f.name) is None
            if f.metadata.get("omit_when_none") and unset:
                data["system"].pop(f.name)
    return data


def plan_from_document(doc: dict[str, Any]) -> RunPlan:
    """The ``RunPlan`` a ``test_plan.json`` document holds -- the inverse of
    ``to_document``, so a reader of a bundle (the evidence report rendered
    again, R29's ``rescore``) works from the plan as registered. Reads plan
    versions "1" and "2"; a version "1" plan was always declared."""
    system = doc.get("system")
    declaration = None
    if isinstance(system, dict):
        declaration = SystemDeclaration(
            system_id=system["system_id"],
            pack_id=system["pack_id"],
            involvement=system["involvement"],
            tools=tuple(DeclaredTool(**tool) for tool in system["tools"]),
            intended_use=system["intended_use"],
            out_of_scope=tuple(system.get("out_of_scope") or ()),
            business_metrics=tuple(
                BusinessMetric(**metric)
                for metric in system.get("business_metrics") or ()
            ),
            repetitions=system.get("repetitions", 1),
            seed=system.get("seed", 0),
            bootstrap_samples=system.get("bootstrap_samples"),
            thresholds=tuple(
                ThresholdOverride(**override)
                for override in system.get("thresholds") or ()
            ),
            run_limits=(
                None
                if system.get("run_limits") is None
                else RunLimits(**system["run_limits"])
            ),
            sha256=system.get("sha256", ""),
        )
    pack = doc.get("pack") or {}
    requested = doc.get("requested")
    return RunPlan(
        plan_version=doc["plan_version"],
        run_id=doc["run_id"],
        config_name=doc["config_name"],
        created_at=doc["created_at"],
        platform_version=doc["platform_version"],
        system=declaration,
        system_path=doc.get("system_path"),
        pack_id=pack.get("pack_id"),
        pack_version=pack.get("pack_version"),
        scenarios_sha256=pack.get("scenarios_sha256"),
        repetitions=doc["repetitions"],
        seed=doc["seed"],
        evaluators=tuple(doc.get("evaluators") or ()),
        requested=None if requested is None else tuple(requested),
        selected=tuple(doc.get("selected") or ()),
        excluded=tuple(PlanExclusion(**e) for e in doc.get("excluded") or ()),
        seeds={k: tuple(v) for k, v in (doc.get("seeds") or {}).items()},
        thresholds=tuple(PlanThreshold(**t) for t in doc.get("thresholds") or ()),
        uncovered_tools=tuple(doc.get("uncovered_tools") or ()),
        declared=doc.get("declared", True),
        data_sha256=pack.get("data_sha256"),
    )


def write_plan(directory: Path, plan: RunPlan) -> str:
    """Write *plan* to ``<directory>/test_plan.json`` and return the
    sha256 of the bytes written, read back from disk -- the hash the run
    registers. Creates *directory*; raises ``OSError`` when it cannot write,
    which the caller treats as "the run does not start"."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / TEST_PLAN_FILENAME
    text = json.dumps(to_document(plan), indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")
    return plan_sha256(path)


def plan_sha256(path: Path) -> str:
    """sha256 of the file at *path*, as the manifest and the validator
    compute it."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_plan(directory: Path) -> dict[str, Any] | None:
    """The plan document in *directory*, or ``None`` when there is none.
    Raises ``ValueError`` for a file that is not a JSON object."""
    path = directory / TEST_PLAN_FILENAME
    if not path.exists():
        return None
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"{TEST_PLAN_FILENAME} must be a JSON object")
    return loaded
