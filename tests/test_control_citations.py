"""Scenarios cite testing-framework checks directly (docs/adr/0007 D11;
ROADMAP R28 (a) slice 2).

The seed pack's scenarios cite the 14 direct and partial checks -- nine by
the scenarios whose verdict evidences them, five through the pack-level
``every_scenario_cites`` list -- and the agentic-baseline benchmark refuses
a scenario that cites anything else.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import fast_yaml
import pytest

from agentevalops.benchmarks.agentic_baseline import AgenticBaselineBenchmark
from agentevalops.catalogue import SCENARIO_CLASSES, load_catalogue
from agentevalops.core.errors import ConfigurationError
from agentevalops.core.schemas import Scenario
from agentevalops.domain_packs.seed import SeedDomainPack
from agentevalops.domain_packs.seed.pack import DomainPackError

DATA_DIR = (
    Path(__file__).resolve().parents[1] / "src/agentevalops/domain_packs/seed/data"
)
CATALOGUE = load_catalogue()
PACK = SeedDomainPack()
TF = "testing-framework:"

#: docs/adr/0007 D11: the checks whose evidence is one scenario's verdict.
_OWN_CITATIONS: dict[str, set[str]] = {
    "PC1": {"4.3.1"},
    "PC2": {"10.3.2", "4.3.1"},
    "PC3": {"10.3.2", "4.3.1"},
    "PC4": {"4.3.1", "5.9.1"},
    "PC5": {"4.3.1", "5.9.1"},
    "PC6": {"1.1.3", "1.1.4"},
    "PC7": {"8.3.2"},
    "PC8": {"8.3.2"},
    "TE3": {"1.1.3", "1.1.4"},
    "TE4": {"1.1.3", "1.1.4"},
    "TE5": {"10.3.2"},
    "TE6": {"10.3.2"},
    "RB1": {"4.3.1"},
    "RB2": {"4.3.1"},
    "RB3": {"4.3.1"},
    "RB4": {"4.1.1", "5.9.1"},
    "RB5": {"4.1.1", "5.9.1"},
    "RB6": {"7.2.1"},
    "RB7": {"7.2.1"},
    "RB8": {"6.1.1"},
}
#: The five properties of every executed scenario.
_WHOLE_RUN = ("3.6.1", "3.7.1", "3.12.1", "4.10.1", "9.9.2")


def _cited(scenarios: Any) -> set[str]:
    return {
        control_id
        for scenario in scenarios
        for control_id in scenario.control_ids
        if control_id.startswith(TF)
    }


def _direct_and_partial() -> set[str]:
    return {
        control.id
        for control in CATALOGUE.controls.values()
        if control.kind == "process" and control.classification in SCENARIO_CLASSES
    }


def _edited_scenarios(tmp_path: Path, mutate: Any) -> Path:
    copy = tmp_path / "data"
    shutil.copytree(DATA_DIR, copy)
    path = copy / "scenarios.yaml"
    doc = fast_yaml.safe_load(path.read_text(encoding="utf-8"))
    mutate(doc)
    path.write_text(fast_yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return copy


def _scenario(doc: dict[str, Any], scenario_id: str) -> dict[str, Any]:
    (found,) = [s for s in doc["scenarios"] if s["scenario_id"] == scenario_id]
    return found


# ---------------------------------------------------------------------------
# The equality test (D11)
# ---------------------------------------------------------------------------


def test_the_pack_cites_exactly_the_direct_and_partial_checks() -> None:
    """Every check the alignment review says a run evidences is cited by at
    least one scenario, and nothing else is."""
    expected = _direct_and_partial()
    assert len(expected) == 14
    assert _cited(PACK.scenarios()) == expected


def test_an_organisational_citation_breaks_the_equality(tmp_path: Path) -> None:
    """Fail-first for the test above: the pack loads (1.1.1 is in the
    catalogue), and the cited set is no longer the direct and partial set."""

    def cite_organisational(doc: dict[str, Any]) -> None:
        _scenario(doc, "TC1")["control_ids"].append(f"{TF}1.1.1")

    pack = SeedDomainPack(_edited_scenarios(tmp_path, cite_organisational))
    assert _cited(pack.scenarios()) != _direct_and_partial()
    assert _cited(pack.scenarios()) - _direct_and_partial() == {f"{TF}1.1.1"}


def test_the_pack_level_list_is_the_catalogues_run_level_checks() -> None:
    """The evidence report reads "every scenario in the plan" for the
    catalogue's run-level checks (decision 3 of 2026-11-02); the pack
    appends the same five to every scenario."""
    raw = fast_yaml.safe_load((DATA_DIR / "scenarios.yaml").read_text("utf-8"))
    run_level = {c.id for c in CATALOGUE.controls.values() if c.run_level}
    assert set(raw["every_scenario_cites"]) == run_level
    assert run_level == {f"{TF}{c}" for c in _WHOLE_RUN}


def test_each_scenario_cites_its_own_checks_as_adr_0007_lists_them() -> None:
    for scenario in PACK.scenarios():
        own = {
            c.removeprefix(TF)
            for c in scenario.control_ids
            if c.startswith(TF) and c.removeprefix(TF) not in _WHOLE_RUN
        }
        assert own == _OWN_CITATIONS.get(scenario.scenario_id, set()), (
            scenario.scenario_id
        )


def test_every_scenario_carries_the_five_whole_run_checks_after_its_own() -> None:
    whole_run = tuple(f"{TF}{c}" for c in _WHOLE_RUN)
    for scenario in PACK.scenarios():
        assert scenario.control_ids[-len(whole_run) :] == whole_run
        assert len(set(scenario.control_ids)) == len(scenario.control_ids)


def test_a_scenario_citing_a_whole_run_check_itself_gets_it_once(
    tmp_path: Path,
) -> None:
    def cite_3_6_1(doc: dict[str, Any]) -> None:
        _scenario(doc, "TC1")["control_ids"].append(f"{TF}3.6.1")

    pack = SeedDomainPack(_edited_scenarios(tmp_path, cite_3_6_1))
    (tc1,) = [s for s in pack.scenarios() if s.scenario_id == "TC1"]
    assert tc1.control_ids.count(f"{TF}3.6.1") == 1
    assert tc1.control_ids.index(f"{TF}3.6.1") == 1  # kept where it cites it


def test_an_unknown_pack_level_citation_fails_at_load(tmp_path: Path) -> None:
    def cite_unknown(doc: dict[str, Any]) -> None:
        doc["every_scenario_cites"].append(f"{TF}99.9.9")

    with pytest.raises(DomainPackError, match="unknown control id"):
        SeedDomainPack(_edited_scenarios(tmp_path, cite_unknown))


def test_a_pack_without_a_pack_level_list_cites_only_its_own(
    tmp_path: Path,
) -> None:
    def drop_list(doc: dict[str, Any]) -> None:
        del doc["every_scenario_cites"]

    pack = SeedDomainPack(_edited_scenarios(tmp_path, drop_list))
    (tc1,) = [s for s in pack.scenarios() if s.scenario_id == "TC1"]
    assert tc1.control_ids == ("mgf-agentic:2.3.2",)


# ---------------------------------------------------------------------------
# The benchmark's guard, for any pack (D11)
# ---------------------------------------------------------------------------


def test_the_seed_pack_passes_the_benchmarks_guard() -> None:
    assert len(AgenticBaselineBenchmark(PACK).scenarios()) == len(PACK.scenarios())


@pytest.mark.parametrize(
    ("locator", "classification"),
    [
        ("1.1.1", "organisational"),
        ("1.1.6", "supporting"),
        ("3.4.1", "later"),
        ("2.1.1", "traditional-only"),
    ],
)
def test_the_benchmark_rejects_a_check_no_scenario_can_evidence(
    tmp_path: Path, locator: str, classification: str
) -> None:
    """Fail-first: before the guard, the benchmark built from this pack."""

    def cite(doc: dict[str, Any]) -> None:
        _scenario(doc, "TC1")["control_ids"].append(f"{TF}{locator}")

    pack = SeedDomainPack(_edited_scenarios(tmp_path, cite))
    assert CATALOGUE.control(f"{TF}{locator}").classification == classification
    with pytest.raises(ConfigurationError, match=f"a {classification} check"):
        AgenticBaselineBenchmark(pack)


class _Pack:
    """A minimal pack whose one scenario cites *control_ids*."""

    pack_id = "probe"
    pack_version = "0"

    def __init__(self, *control_ids: str) -> None:
        self._scenario = Scenario(
            scenario_id="P1",
            description="probe",
            category="tool_calling",
            known_good=({"finish": "done"},),
            control_ids=control_ids,
        )

    def scenarios(self) -> tuple[Scenario, ...]:
        return (self._scenario,)

    def build_environment(self, scenario: Scenario, task_id: Any) -> Any:
        raise NotImplementedError


def test_the_benchmark_rejects_an_unknown_control_id() -> None:
    with pytest.raises(ConfigurationError, match="unknown control id 'nowhere:1'"):
        AgenticBaselineBenchmark(_Pack("nowhere:1"))


def test_the_benchmark_accepts_a_direct_check_and_other_sources() -> None:
    benchmark = AgenticBaselineBenchmark(
        _Pack(f"{TF}6.1.1", "mgf-agentic:2.3.2")
    )
    assert benchmark.narrowed(["P1"]).scenarios()[0].scenario_id == "P1"
