"""Control coverage (plans/coverage.py; docs/adr/0007 D5, D6; ROADMAP R28
(a) slice 4), on synthetic plans, summaries and catalogues. The writer
does not call it until R28 (b)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import evidence_inputs as inputs
import pytest

from agentevalops.catalogue import load_catalogue
from agentevalops.domain_packs.seed import SeedDomainPack
from agentevalops.plans import COVERAGE_STATES, control_coverage
from agentevalops.plans.coverage import NO_PLAN, NOT_REQUESTED, NOT_RUN

#: Which scenario cites which control, and how each scenario went:
#: doc:1 by a passing scenario; doc:2 by a failing one; doc:3 by one not
#: evaluated; doc:4 by one the plan excluded and one never requested;
#: doc:5 by none; doc:6 by a passing one and one not evaluated; doc:7 by a
#: selected one the run never reached.
_DESCRIPTORS = {
    "S1": inputs.descriptor("doc:1", "doc:6", "tf:1.1.1"),
    "S2": inputs.descriptor("doc:2", "tf:1.2.1"),
    "S3": inputs.descriptor("doc:3", "doc:6"),
    "S4": inputs.descriptor("doc:4"),
    "S5": inputs.descriptor("doc:4"),
    "S6": inputs.descriptor("doc:7"),
}
_OUTCOMES = {"S1": "passed", "S2": "failed", "S3": "not_evaluated"}


def _coverage(tmp_path: Path, **kwargs: Any) -> dict[str, Any]:
    return control_coverage(
        inputs.catalogue(tmp_path),
        inputs.plan(
            selected=("S1", "S2", "S3", "S6"),
            excluded=(("S4", "declares no export tool"),),
            requested=("S1", "S2", "S3", "S4", "S6"),
        ),
        _DESCRIPTORS,
        inputs.summary(_OUTCOMES),
        **{"end_states": inputs.end_states(_OUTCOMES), **kwargs},
    )


def _control(result: dict[str, Any], control_id: str) -> dict[str, Any]:
    (found,) = [c for c in result["controls"] if c["id"] == control_id]
    return found


# ---------------------------------------------------------------------------
# The five states (D6)
# ---------------------------------------------------------------------------


def test_a_control_cited_only_by_a_passing_scenario_is_covered(tmp_path: Path) -> None:
    control = _control(_coverage(tmp_path), "doc:1")
    assert control["state"] == "covered"
    (verdict,) = control["verdicts"]
    assert (verdict["scenario_id"], verdict["pass_k"], verdict["k"]) == ("S1", True, 2)


def test_a_failing_scenario_still_covers_with_its_verdict_beside_it(
    tmp_path: Path,
) -> None:
    control = _control(_coverage(tmp_path), "doc:2")
    assert control["state"] == "covered"
    assert [v["pass_k"] for v in control["verdicts"]] == [False]
    assert control["verdicts"][0]["failed"] == 2


def test_a_control_cited_only_by_a_not_evaluated_scenario_is_uncovered(
    tmp_path: Path,
) -> None:
    """R28's exit criterion: uncovered, with the cause."""
    control = _control(_coverage(tmp_path), "doc:3")
    assert control["state"] == "uncovered"
    assert control["not_evaluated"] == {"S3": "infrastructure"}
    assert control["verdicts"][0]["pass_k"] is None
    assert control["verdicts"][0]["refs"] == []


def test_a_selected_scenario_the_run_never_reached_leaves_its_control_uncovered(
    tmp_path: Path,
) -> None:
    control = _control(_coverage(tmp_path), "doc:7")
    assert control["state"] == "uncovered"
    assert control["not_evaluated"] == {"S6": NOT_RUN}


def test_a_control_cited_only_outside_the_selection_is_not_in_plan(
    tmp_path: Path,
) -> None:
    control = _control(_coverage(tmp_path), "doc:4")
    assert control["state"] == "not_in_plan"
    assert control["excluded"] == {
        "S4": "declares no export tool",
        "S5": NOT_REQUESTED,
    }
    assert control["verdicts"] == []


def test_a_control_no_scenario_cites_is_not_in_pack(tmp_path: Path) -> None:
    """Decision 2 of 2026-11-02: "not in pack", never "undeclared"."""
    control = _control(_coverage(tmp_path), "doc:5")
    assert control["state"] == "not_in_pack"
    assert control["declared_by"] == []


def test_mixed_verdicts_are_partly_covered(tmp_path: Path) -> None:
    control = _control(_coverage(tmp_path), "doc:6")
    assert control["state"] == "partly_covered"
    assert [v["scenario_id"] for v in control["verdicts"]] == ["S1", "S3"]
    assert control["not_evaluated"] == {"S3": "infrastructure"}


def test_every_state_is_reached_and_none_other(tmp_path: Path) -> None:
    states = {c["state"] for c in _coverage(tmp_path)["controls"]}
    assert states == set(COVERAGE_STATES)


# ---------------------------------------------------------------------------
# No aggregate, refs, descriptors, shape
# ---------------------------------------------------------------------------


def test_no_control_carries_an_aggregate(tmp_path: Path) -> None:
    """D6: a control with several citing scenarios lists each verdict;
    nothing is summed into a per-control pass or fail."""
    keys = {
        "id",
        "source",
        "kind",
        "label",
        "state",
        "declared_by",
        "excluded",
        "not_evaluated",
        "verdicts",
    }
    for control in _coverage(tmp_path)["controls"]:
        extra = {"classification", "principle", "evidence"}
        assert set(control) == (keys | extra if control["kind"] == "process" else keys)


def test_refs_are_the_union_across_repetitions_in_order(tmp_path: Path) -> None:
    (verdict,) = _control(_coverage(tmp_path), "doc:1")["verdicts"]
    assert verdict["refs"] == [
        {"stream": "tool_calls", "task_id": "S1", "repetition": r, "index": i}
        for r in (0, 1)
        for i in (2, 4)
    ] + [
        {"stream": "trace", "task_id": "S1", "repetition": r, "index": 7}
        for r in (0, 1)
    ]
    # S1's two runs are the first two lines of end_states.jsonl.
    assert verdict["end_states"] == [
        {"stream": "end_states", "task_id": "S1", "repetition": r, "index": r}
        for r in (0, 1)
    ]


def test_an_end_state_ref_is_the_records_line(tmp_path: Path) -> None:
    """Decision 1 of 2026-11-02: the ref's index is the line in
    end_states.jsonl, so it follows the records' order."""
    records = list(reversed(inputs.end_states(_OUTCOMES)))
    (verdict,) = _control(_coverage(tmp_path, end_states=records), "doc:2")["verdicts"]
    assert [r["index"] for r in verdict["end_states"]] == [
        len(records) - 1 - 2,
        len(records) - 1 - 3,
    ]


def test_end_states_is_always_present(tmp_path: Path) -> None:
    """An empty list when no state-based evaluation judged a recorded end
    state: S3 was not evaluated, and with no records nothing is pointed at."""
    result = _coverage(tmp_path)
    assert _control(result, "doc:3")["verdicts"][0]["end_states"] == []
    bare = _coverage(tmp_path, end_states=())
    for control in bare["controls"]:
        assert all(v["end_states"] == [] for v in control["verdicts"])


def test_scenarios_list_every_pack_scenario_once_marked(tmp_path: Path) -> None:
    """Decision 5 of 2026-11-02: the whole pack, each selected or not, with
    the plan's reason where it has one."""
    result = _coverage(tmp_path)
    assert list(result["scenarios"]) == ["S1", "S2", "S3", "S4", "S5", "S6"]
    assert result["scenarios"]["S2"] == {
        "category": "tool_calling",
        "outcome_group": "erroneous_action",
        "variant": {},
        "control_ids": ["doc:2", "tf:1.2.1"],
        "selected": True,
        "reason": None,
        "k": 2,
        "passed": 0,
        "failed": 2,
        "not_evaluated": 0,
        "pass_k": False,
        "not_evaluated_cause": None,
    }
    excluded = result["scenarios"]["S4"]
    assert (excluded["selected"], excluded["reason"]) == (
        False,
        "declares no export tool",
    )
    assert excluded["pass_k"] is None and excluded["k"] is None
    assert result["scenarios"]["S5"]["reason"] == NOT_REQUESTED
    assert result["scenarios"]["S6"]["not_evaluated_cause"] == NOT_RUN
    selected = [s for s, e in result["scenarios"].items() if e["selected"]]
    assert selected == ["S1", "S2", "S3", "S6"]


def test_the_document_ties_to_the_plan_and_pins_the_catalogue(tmp_path: Path) -> None:
    result = _coverage(tmp_path, plan_sha256="b" * 64)
    assert result["controls_version"] == "1"
    assert result["run_id"] == inputs.RUN_ID
    assert result["plan_sha256"] == "b" * 64
    assert result["declared"] is True
    assert [s["id"] for s in result["sources"]] == ["doc", "tf"]
    assert result["sources"][0]["published"] == "2026-01-15"
    assert [f["name"] for f in result["catalogue"]["files"]] == [
        "a-doc.yaml",
        "b-tf.yaml",
    ]
    assert all(len(f["sha256"]) == 64 for f in result["catalogue"]["files"])


def test_a_process_entry_repeats_its_catalogue_fields(tmp_path: Path) -> None:
    control = _control(_coverage(tmp_path), "tf:1.1.1")
    assert control["classification"] == "direct"
    assert control["principle"] == 1
    assert control["evidence"] == ["scenarios", "artefact:test_plan.json#thresholds"]


def test_controls_come_in_source_then_natural_locator_order(tmp_path: Path) -> None:
    ids = [c["id"] for c in _coverage(tmp_path)["controls"]]
    assert ids[:7] == [f"doc:{n}" for n in range(1, 8)]
    assert ids[7:] == [
        "tf:1.1.1",
        "tf:1.2.1",
        "tf:1.10.1",
        "tf:2.1.1",
        "tf:2.2.1",
        "tf:2.3.1",
    ]


def test_two_computations_are_identical(tmp_path: Path) -> None:
    """No timestamp of its own (D5): a pure function of its inputs."""
    first = json.dumps(_coverage(tmp_path), sort_keys=True)
    assert json.dumps(_coverage(tmp_path), sort_keys=True) == first


def test_a_run_with_no_plan_selects_nothing_and_says_why(tmp_path: Path) -> None:
    result = control_coverage(
        inputs.catalogue(tmp_path), None, _DESCRIPTORS, inputs.summary(_OUTCOMES)
    )
    assert result["declared"] is False
    assert {e["reason"] for e in result["scenarios"].values()} == {NO_PLAN}
    assert not any(e["selected"] for e in result["scenarios"].values())
    assert {c["state"] for c in result["controls"]} == {"not_in_plan", "not_in_pack"}
    assert all(c["verdicts"] == [] for c in result["controls"])


# ---------------------------------------------------------------------------
# Over the shipped catalogue and the seed pack's descriptors
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def seed_descriptors() -> dict[str, dict[str, Any]]:
    return {
        s.scenario_id: inputs.descriptor(*s.control_ids, category=s.category)
        for s in SeedDomainPack().scenarios()
    }


def test_a_passing_seed_run_covers_the_fourteen_checks_and_no_others(
    seed_descriptors: dict[str, dict[str, Any]],
) -> None:
    selected = tuple(seed_descriptors)
    result = control_coverage(
        load_catalogue(),
        inputs.plan(selected=selected),
        seed_descriptors,
        inputs.summary(dict.fromkeys(selected, "passed"), k=1),
    )
    process = [c for c in result["controls"] if c["kind"] == "process"]
    assert len(process) == 112
    covered = {c["id"] for c in process if c["state"] == "covered"}
    assert len(covered) == 14
    assert {c["state"] for c in process if c["id"] not in covered} == {"not_in_pack"}
    ids = [c["id"].removeprefix("testing-framework:") for c in process]
    assert ids.index("3.9.1") < ids.index("3.10.1") < ids.index("3.12.1")
    assert ids.index("9.12.1") < ids.index("10.1.1") < ids.index("11.1.1")
