"""The pack's data hash, the scenario descriptors and the reader's optional
fields (docs/adr/0007 D9, D5, D15; ROADMAP R28 (b1) slice 3). None of them
reaches a bundle until format 0.6 (R28 (b2)).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import fast_yaml
import pytest
from typer.testing import CliRunner

from agentevalops.benchmarks.agentic_baseline import AgenticBaselineBenchmark
from agentevalops.bundles.reader import BundleReader
from agentevalops.cli import app
from agentevalops.core.errors import BundleError
from agentevalops.core.protocols import DataHashed
from agentevalops.domain_packs.seed import SeedDomainPack
from agentevalops.plans import scenario_descriptors

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "src/agentevalops/domain_packs/seed/data"
PACK = SeedDomainPack()


def _copy(tmp_path: Path) -> Path:
    copy = tmp_path / "data"
    shutil.copytree(DATA_DIR, copy)
    return copy


# ---------------------------------------------------------------------------
# DataHashed (D9)
# ---------------------------------------------------------------------------


def test_the_seed_pack_names_its_data_hash() -> None:
    assert isinstance(PACK, DataHashed)
    assert len(PACK.data_sha256) == 64
    assert SeedDomainPack().data_sha256 == PACK.data_sha256


def test_a_pack_without_the_attribute_is_not_data_hashed() -> None:
    class Bare:
        pack_id = "bare"
        pack_version = "0"

    assert not isinstance(Bare(), DataHashed)


def test_key_order_comments_and_layout_do_not_change_the_hash(tmp_path: Path) -> None:
    copy = _copy(tmp_path)
    for name in ("policy.yaml", "cases.yaml"):
        doc = fast_yaml.safe_load((copy / name).read_text(encoding="utf-8"))
        text = fast_yaml.safe_dump(doc, sort_keys=True, default_flow_style=False)
        (copy / name).write_text(f"# rewritten\n\n{text}\n\n", encoding="utf-8")
    assert (copy / "policy.yaml").read_bytes() != (
        DATA_DIR / "policy.yaml"
    ).read_bytes()
    assert SeedDomainPack(copy).data_sha256 == PACK.data_sha256


def test_a_changed_value_changes_the_hash_though_the_scenarios_do_not(
    tmp_path: Path,
) -> None:
    """ADR-0007 finding 5: the scenarios' hash misses policy.yaml; this
    one does not."""
    copy = _copy(tmp_path)
    doc = fast_yaml.safe_load((copy / "policy.yaml").read_text(encoding="utf-8"))
    doc["reason_codes"]["ELIG-OK"] = "Eligible on every clause"
    (copy / "policy.yaml").write_text(fast_yaml.safe_dump(doc), encoding="utf-8")
    edited = SeedDomainPack(copy)
    assert list(edited.scenarios()) == list(PACK.scenarios())
    assert edited.data_sha256 != PACK.data_sha256


# ---------------------------------------------------------------------------
# Scenario descriptors (D5, decision 5)
# ---------------------------------------------------------------------------


def test_a_narrowed_benchmark_still_yields_every_descriptor() -> None:
    narrowed = AgenticBaselineBenchmark(PACK).narrowed(["TC1", "PC7"])
    assert [s.scenario_id for s in narrowed.scenarios()] == ["TC1", "PC7"]
    descriptors = scenario_descriptors(narrowed.pack)
    assert list(descriptors) == [s.scenario_id for s in PACK.scenarios()]
    assert len(descriptors) == 26


def test_a_descriptor_carries_what_coverage_reads() -> None:
    descriptors = scenario_descriptors(PACK)
    rb6 = next(s for s in PACK.scenarios() if s.scenario_id == "RB6")
    assert descriptors["RB6"] == {
        "category": "robustness",
        "outcome_group": rb6.outcome_group,
        "variant": dict(rb6.variant),
        "control_ids": list(rb6.control_ids),
    }
    assert descriptors["RB6"]["variant"]["kind"] == "counterfactual"
    assert "testing-framework:7.2.1" in descriptors["RB6"]["control_ids"]


# ---------------------------------------------------------------------------
# The reader's optional fields (D15)
# ---------------------------------------------------------------------------


def _run(tmp_path: Path, config: str) -> Path:
    out = tmp_path / Path(config).stem
    result = CliRunner().invoke(
        app, ["run", "--config", str(ROOT / config), "--output", str(out)]
    )
    assert result.exit_code == 0, result.output
    return out


@pytest.fixture(scope="module")
def bundles(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    tmp = tmp_path_factory.mktemp("bundles")
    return {
        "toy": _run(tmp, "configs/toy_smoke.yaml"),
        "seed": _run(tmp, "configs/agentic_baseline_mock.yaml"),
    }


def test_a_seed_bundle_loads_its_plan_and_streams(bundles: dict[str, Path]) -> None:
    loaded = BundleReader(bundles["seed"]).read()
    assert loaded.metadata["schema_version"] == "0.5"
    assert loaded.test_plan is not None
    assert loaded.test_plan["selected"][0] == "TE1"
    assert loaded.controls is None
    assert len(loaded.tool_calls) > 0
    assert len(loaded.end_states) == 26


def test_a_toy_bundle_has_no_plan(bundles: dict[str, Path]) -> None:
    loaded = BundleReader(bundles["toy"]).read()
    assert loaded.test_plan is None and loaded.controls is None
    assert isinstance(loaded.tool_calls, list)
    assert isinstance(loaded.end_states, list)


def test_a_bundle_before_the_streams_reads_empty(
    bundles: dict[str, Path], tmp_path: Path
) -> None:
    """A seed bundle made to look like format 0.4: no plan, no streams."""
    copy = tmp_path / "bundle"
    shutil.copytree(bundles["seed"], copy)
    for name in ("test_plan.json", "tool_calls.jsonl", "end_states.jsonl"):
        (copy / name).unlink()
    manifest = json.loads((copy / "manifest.json").read_text(encoding="utf-8"))
    manifest["bundle_format_version"] = "0.4"
    (copy / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    loaded = BundleReader(copy).read()
    assert (loaded.test_plan, loaded.controls) == (None, None)
    assert (loaded.tool_calls, loaded.end_states) == ([], [])


def test_a_synthetic_controls_file_is_loaded(
    bundles: dict[str, Path], tmp_path: Path
) -> None:
    copy = tmp_path / "bundle"
    shutil.copytree(bundles["seed"], copy)
    controls: dict[str, Any] = {"controls_version": "1", "controls": []}
    (copy / "controls.json").write_text(json.dumps(controls), encoding="utf-8")
    assert BundleReader(copy).read().controls == controls


@pytest.mark.parametrize(
    ("name", "content"),
    [("controls.json", "[1, 2]"), ("tool_calls.jsonl", "{not json}\n")],
)
def test_a_malformed_optional_file_is_an_error_unless_asked_not_to_read_it(
    bundles: dict[str, Path], tmp_path: Path, name: str, content: str
) -> None:
    """Fail-first: the reader raises on a malformed optional file -- and
    the validator's reader, which skips them, still loads the bundle, so
    its cross-file checks run as before."""
    copy = tmp_path / "bundle"
    shutil.copytree(bundles["seed"], copy)
    (copy / name).write_text(content, encoding="utf-8")
    with pytest.raises(BundleError, match=name.replace(".", r"\.")):
        BundleReader(copy).read()
    loaded = BundleReader(copy, optional_files=False).read()
    assert (loaded.controls, loaded.tool_calls) == (None, [])
