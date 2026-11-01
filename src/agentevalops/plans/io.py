"""Reading and writing ``test_plan.json`` (docs/adr/0005 D6; R27)."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path
from typing import Any

from agentevalops.bundles.constants import TEST_PLAN_FILENAME
from agentevalops.core.schemas import RunPlan

__all__ = ["plan_sha256", "read_plan", "to_document", "write_plan"]


def to_document(plan: RunPlan) -> dict[str, Any]:
    """The plan as the JSON document ``test_plan.json`` holds: the dataclass
    as data, with the pack's identity gathered under ``pack`` and the
    declaration's own hash lifted to ``system_sha256``. Plain JSON types
    only, so the file is readable without this package."""
    data = dataclasses.asdict(plan)
    data["pack"] = {
        "pack_id": data.pop("pack_id"),
        "pack_version": data.pop("pack_version"),
        "scenarios_sha256": data.pop("scenarios_sha256"),
    }
    data["system_sha256"] = plan.system.sha256
    return data


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
