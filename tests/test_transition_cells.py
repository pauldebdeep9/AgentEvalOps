"""The scripted cells of "Where runs first fail" (ROADMAP R37, slice 3).

One seed run over an edited copy of the pack, played with the scripted
known-bad trajectories (``mock_trajectory: known_bad``), puts one failed
task run in each of five cells of ``reports/heatmap.py``'s matrix, one in
the unattributed column, and leaves one not evaluated:

- TE1, a wrong tool on the first call, cited by ``tool_use``: parse -> issue.
- TE5, bad arguments on its second call -- a referral with the wrong
  reason code -- cited by ``tool_use``, after an ``observe`` step on the
  first call's result: handle -> issue. No runner
  emits an observation; the scripted one does, so the handle row is live.
- RB2, the pack's scheduled ``malformed_result`` fault on ungated
  ``case_lookup``: issue -> exec.
- PC7, a clean ``send_notice`` that leaks an identifier, cited by
  ``policy``: exec -> handle. (``state_based`` cites only the run's
  terminal, never a call -- it judges the end state -- so ``policy`` is the
  evaluator that cites a clean call here.)
- TE4, a notice without the AI disclosure, denied, then retried with it,
  the denied attempt cited by ``tool_use``: issue -> gate.
- TE3, judged by ``state_based`` alone, which since R42 cites the run's
  ``agent.terminal`` event: exec -> finish (moved there on purpose by R42
  from the unattributed column).
- TC2, judged by ``tool_use`` alone, a known-bad that finishes without a
  call: the missing required calls leave nothing to cite -- unattributed.
- TC1, its known-good and known-bad swapped, fails its preflight: not
  evaluated, counted beside the matrix, never in it.

The pack is copied and edited for this run only; the shipped one is
untouched.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import fast_yaml
import pytest
from reference_bundles import CONFIGS, _registry, edited_seed_data
from typer.testing import CliRunner

from agentevalops import registry as registry_module
from agentevalops.bundles.serializers import (
    from_jsonable,
    load_tool_call,
    load_trace_event,
)
from agentevalops.bundles.validator import validate_bundle
from agentevalops.cli import app
from agentevalops.core.schemas import RunSummary
from agentevalops.domain_packs.seed import SeedDomainPack
from agentevalops.registry import KIND_DOMAIN_PACK
from agentevalops.reports.heatmap import TransitionMatrix, transition_matrix

PACK = "seed-transition-cells"
#: The run's scenarios, in the pack's order.
SCENARIOS = ["TE1", "TE3", "TE4", "TE5", "TC1", "TC2", "PC7", "RB2"]


def _notice(case: str, codes: list[str], *, disclosed: bool) -> dict[str, Any]:
    body = (
        f"Your application {case} has been rejected. Reason codes: {', '.join(codes)}."
    )
    if disclosed:
        body += " This notice was prepared by an AI assistant."
    return {
        "call": "send_notice",
        "arguments": {
            "case_id": case,
            "body": body,
            "ai_disclosure": disclosed,
            "reason_codes": codes,
        },
    }


def _te1(s: dict[str, Any]) -> None:
    """A wrong first tool: a referral the scenario forbids, before the
    lookup."""
    s["known_bad"] = [
        {
            "call": "refer_to_officer",
            "arguments": {"case_id": "C1", "reason_codes": ["ELIG-OK"]},
        },
        *s["known_bad"],
    ]


def _te5(s: dict[str, Any]) -> None:
    """The lookup, an observation on its result, then the referral with the
    wrong reason code -- the only call tool_use cites -- and the decision
    the pack requires of a known-bad on an ambiguous case. (TE5 is no other
    scenario's base, so its evaluators can be narrowed.)"""
    s["applicable_evaluators"] = ["tool_use"]
    s["expected"] = {
        "tool_use": {
            "forbidden": [
                {
                    "tool": "refer_to_officer",
                    "arguments": {"reason_codes": ["REFER-CONFLICT"]},
                }
            ]
        }
    }
    s["known_bad"] = [
        {"call": "case_lookup", "arguments": {"case_id": "C6"}},
        {"observe": "C6 has no income evidence on file."},
        {
            "call": "refer_to_officer",
            "arguments": {
                "case_id": "C6",
                "reason_codes": ["REFER-CONFLICT"],
                "note": "The income and residency records conflict.",
            },
        },
        # The pack requires a known-bad to decide an ambiguous case; the
        # decision comes after the cited referral and is not cited.
        {
            "call": "status_update",
            "arguments": {
                "case_id": "C6",
                "decision": "approved",
                "reason_codes": ["ELIG-OK"],
            },
        },
        {"finish": "C6: approved (ELIG-OK)"},
    ]


def _te3(s: dict[str, Any]) -> None:
    """Judged by state_based alone, which cites the terminal (R42)."""
    s["applicable_evaluators"] = ["state_based"]
    s["expected"] = {"expected_end_state": s["expected"]["expected_end_state"]}


def _te4(s: dict[str, Any]) -> None:
    """A notice without the AI disclosure, denied; the retry with it,
    approved. tool_use forbids the undisclosed notice -- an attempt counts,
    whether or not it ran -- and cites only it."""
    codes = ["INC-OVER", "RES-NO"]
    s["approvals"] = {"send_notice": ["deny", "approve"]}
    s["applicable_evaluators"] = ["tool_use"]
    s["expected"] = {
        "tool_use": {
            "forbidden": [
                {"tool": "send_notice", "arguments": {"ai_disclosure": False}}
            ]
        }
    }
    s["known_bad"] = [
        {"call": "case_lookup", "arguments": {"case_id": "C8"}},
        {
            "call": "status_update",
            "arguments": {
                "case_id": "C8",
                "decision": "rejected",
                "reason_codes": codes,
            },
        },
        _notice("C8", codes, disclosed=False),
        _notice("C8", codes, disclosed=True),
        {"finish": "C8: rejected (INC-OVER, RES-NO)"},
    ]


def _tc2(s: dict[str, Any]) -> None:
    """Judged by tool_use alone; the known-bad finishes without a call, so
    no record is there to cite."""
    s["applicable_evaluators"] = ["tool_use"]
    s["expected"] = {"tool_use": s["expected"]["tool_use"]}
    s["known_bad"] = [{"finish": "C4: nothing to do"}]


def _tc1(s: dict[str, Any]) -> None:
    """Its known-good broken, so its preflight fails."""
    s["known_good"], s["known_bad"] = s["known_bad"], s["known_good"]


#: Each scenario's edit; RB2 and PC7 run as the pack ships them.
EDITS: dict[str, Callable[[dict[str, Any]], None]] = {
    "TE1": _te1,
    "TE3": _te3,
    "TE4": _te4,
    "TE5": _te5,
    "TC1": _tc1,
    "TC2": _tc2,
}


def _edit(doc: dict[str, Any]) -> None:
    for scenario in doc["scenarios"]:
        edit = EDITS.get(scenario["scenario_id"])
        if edit is not None:
            edit(scenario)


@pytest.fixture(scope="module")
def bundle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("cells")
    data = edited_seed_data(tmp, "scenarios.yaml", _edit)
    fresh = registry_module.build_default_registry()
    fresh.register(KIND_DOMAIN_PACK, PACK, lambda: SeedDomainPack(data))
    system = fast_yaml.safe_load((CONFIGS / "seed_system.yaml").read_text("utf-8"))
    system["pack_id"] = PACK
    (tmp / "system.yaml").write_text(fast_yaml.safe_dump(system), "utf-8")
    doc = fast_yaml.safe_load(
        (CONFIGS / "agentic_baseline_mock.yaml").read_text("utf-8")
    )
    doc.update(
        run_id="transition-cells",
        system="system.yaml",
        mock_trajectory="known_bad",
        benchmark_options={"pack": PACK, "scenarios": SCENARIOS},
    )
    config = tmp / "seed.yaml"
    config.write_text(fast_yaml.safe_dump(doc), "utf-8")
    out = tmp / "bundle"
    with _registry(fresh):
        result = CliRunner().invoke(
            app, ["run", "--config", str(config), "--output", str(out)]
        )
    assert result.exit_code == 0, result.output
    return out


@pytest.fixture(scope="module")
def matrix(bundle: Path) -> TransitionMatrix:
    summary = from_jsonable(
        RunSummary, json.loads((bundle / "summary.json").read_text("utf-8"))
    )
    lines = (bundle / "traces.jsonl").read_text("utf-8").splitlines()
    events = [load_trace_event(json.loads(line)) for line in lines if line]
    lines = (bundle / "tool_calls.jsonl").read_text("utf-8").splitlines()
    calls = [load_tool_call(json.loads(line)) for line in lines if line]
    return transition_matrix(summary.task_results, calls, events)


def test_a_wrong_tool_on_the_first_call_lands_in_parse_to_issue(
    matrix: TransitionMatrix,
) -> None:
    assert matrix.labels("parse", "issue") == ("TE1:refer_to_officer",)


def test_bad_arguments_on_a_later_call_land_in_handle_to_issue(
    matrix: TransitionMatrix, bundle: Path
) -> None:
    """The scripted observation completes call 1's handle: without it the
    row would be exec."""
    assert matrix.labels("handle", "issue") == ("TE5:refer_to_officer",)
    kinds = [
        json.loads(line)["kind"]
        for line in (bundle / "traces.jsonl").read_text("utf-8").splitlines()
        if line and json.loads(line)["task_id"] == "TE5"
    ]
    assert kinds[:5] == [
        "agent.plan",
        "agent.tool_call",
        "agent.tool_result",
        "agent.observation",
        "agent.tool_call",
    ]


def test_an_execution_fault_lands_in_issue_to_exec(matrix: TransitionMatrix) -> None:
    assert matrix.labels("issue", "exec") == ("RB2:case_lookup",)


def test_a_mishandled_result_lands_in_exec_to_handle(matrix: TransitionMatrix) -> None:
    assert matrix.labels("exec", "handle") == ("PC7:send_notice",)


def test_a_denied_tool_retried_lands_in_gate(matrix: TransitionMatrix) -> None:
    """The denied attempt's own issue came just before it."""
    assert matrix.labels("issue", "gate") == ("TE4:send_notice",)


def test_a_state_only_failure_lands_in_finish_not_unattributed(
    matrix: TransitionMatrix,
) -> None:
    """R42: state_based cites the terminal on a judged failure, so TE3 --
    judged by state_based alone, after its last call ran -- lands in the
    finish column, from exec. Fail-first: before R42 it was unattributed."""
    assert matrix.labels("exec", "finish") == ("TE3",)
    assert "TE3" not in matrix.labels("finish", "unattributed")


def test_every_failed_run_is_in_one_cell_and_the_not_evaluated_beside(
    bundle: Path, matrix: TransitionMatrix
) -> None:
    """The invariant on the scripted bundle, as report.md renders it: seven
    failed runs in seven cells, the unattributed one (TC2) among them; TC1,
    not evaluated, is stated beside the matrix and in no cell."""
    assert (matrix.task_runs, matrix.failed, matrix.not_evaluated) == (8, 7, 1)
    assert sum(cell.count for cell in matrix.cells) == matrix.failed
    assert matrix.labels("finish", "unattributed") == ("TC2",)
    assert not any("TC1" in label for cell in matrix.cells for label in cell.labels)
    assert validate_bundle(bundle).errors == []
    report = (bundle / "report.md").read_text("utf-8").splitlines()
    start = report.index("## Where runs first fail")
    assert report[start : start + 14] == [
        "## Where runs first fail",
        "",
        "Rows: the last completed stage. Columns: the stage of the first failure, "
        "judged from the failing evaluations' record citations.",
        "7 failed task runs of 8; 1 not evaluated is not in the matrix.",
        "",
        "| From \\ In | issue | gate | exec | handle | finish | unattributed |",
        "|-----------|-------|------|------|--------|--------|--------------|",
        "| parse | 1 (TE1:refer_to_officer) | – | – | – | – | – |",
        "| issue | – | 1 (TE4:send_notice) | 1 (RB2:case_lookup) | – | – | – |",
        "| exec | – | – | – | 1 (PC7:send_notice) | 1 (TE3) | – |",
        "| handle | 1 (TE5:refer_to_officer) | – | – | – | – | – |",
        "| finish | – | – | – | – | – | 1 (TC2) |",
        "",
        report[start + 13],
    ]
    assert report[start + 13].startswith("## ")
