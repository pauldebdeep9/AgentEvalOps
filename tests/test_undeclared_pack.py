"""An undeclared plan records the pack its run ran on (R29 (b2)).

A run with no system declaration still runs on its benchmark's domain pack
when the benchmark has one. Its plan says nothing was declared, and records
the pack -- id, version, scenarios hash, data hash -- as a declared plan
does, so that rescore can tell whether the pack it rebuilds is the one the
run used (ADR-0008 R2). Only a benchmark without a pack, the toy, records
none. The pack block is plan version 2's own field: a value, not a format
change.

The shipped agentic-baseline benchmark refuses to run undeclared (the
config loader, ADR-0005 D6); a benchmark that runs on a pack under another
id does not, and this is it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import fast_yaml
import pytest
from reference_bundles import CONFIGS, ReferenceBundles
from typer.testing import CliRunner

from agentevalops import registry as registry_module
from agentevalops.benchmarks.agentic_baseline import AgenticBaselineBenchmark
from agentevalops.cli import app
from agentevalops.core.schemas import ResourceLimits, RunConfig
from agentevalops.core.types import AgentId, BackendId, RunId
from agentevalops.domain_packs.seed import SeedDomainPack
from agentevalops.plans import (
    pack_data_sha256,
    scenarios_sha256,
    undeclared_plan,
)
from agentevalops.plans.io import to_document
from agentevalops.registry import KIND_BENCHMARK

PACK = SeedDomainPack()
PACKED = "packed-baseline"


def _seed_pack_block() -> dict[str, Any]:
    return {
        "pack_id": PACK.pack_id,
        "pack_version": PACK.pack_version,
        "scenarios_sha256": scenarios_sha256(PACK),
        "data_sha256": pack_data_sha256(PACK),
    }


def _config() -> RunConfig:
    return RunConfig(
        run_id=RunId("u"),
        agent_id=AgentId("mock"),
        backend_id=BackendId("local"),
        resource_limits=ResourceLimits(),
    )


def test_the_undeclared_plan_records_the_pack_it_is_given() -> None:
    plan = undeclared_plan(_config(), ["TE1"], pack=PACK)
    assert plan.declared is False and plan.system is None
    assert to_document(plan)["pack"] == _seed_pack_block()
    assert _seed_pack_block()["data_sha256"] is not None


def test_without_a_pack_the_plan_records_none() -> None:
    assert to_document(undeclared_plan(_config(), ["toy-001"]))["pack"] is None


def test_a_toy_run_records_no_pack(reference_bundles: ReferenceBundles) -> None:
    plan = json.loads(
        (reference_bundles["toy"].path / "test_plan.json").read_text("utf-8")
    )
    assert plan["declared"] is False and plan["pack"] is None


def test_an_undeclared_run_on_a_pack_records_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-first: before R29 (b2) this plan's pack was null."""
    fresh = registry_module.build_default_registry()
    fresh.register(
        KIND_BENCHMARK,
        PACKED,
        lambda scenarios=None: AgenticBaselineBenchmark(
            SeedDomainPack(), scenarios=scenarios
        ),
    )
    monkeypatch.setattr(registry_module, "default_registry", fresh)
    doc = fast_yaml.safe_load(
        (CONFIGS / "agentic_baseline_mock.yaml").read_text("utf-8")
    )
    del doc["system"]
    doc["run_id"] = "undeclared-pack"
    doc["benchmark_id"] = PACKED
    doc["benchmark_options"] = {"scenarios": ["TE1", "TC1"]}
    config = tmp_path / "run.yaml"
    config.write_text(fast_yaml.safe_dump(doc), encoding="utf-8")
    out = tmp_path / "bundle"
    result = CliRunner().invoke(
        app, ["run", "--config", str(config), "--output", str(out)]
    )
    assert result.exit_code == 0, result.output
    plan = json.loads((out / "test_plan.json").read_text("utf-8"))
    assert plan["declared"] is False and plan["system"] is None
    assert plan["selected"] == ["TE1", "TC1"]
    assert plan["pack"] == _seed_pack_block()
