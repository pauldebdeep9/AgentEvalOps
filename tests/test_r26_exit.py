"""R26's exit criteria, checked against running code (ROADMAP R26; ADR-0006
D6): every injected fault and payload in the harness records of the run that
received it; the report's five outcome groups each with an executed
scenario; the four scripted-trajectory checks; the same seed and repetition
giving the same fault and variant; and the shipped config's fourth category.

The two whole-pack playbacks are made once at import, since every test
reads from them."""

from __future__ import annotations

import json
from pathlib import Path

from test_r24_exit import _seed
from typer.testing import CliRunner

from agentevalops.cli import app
from agentevalops.core.schemas import NEGATIVE_OUTCOMES
from agentevalops.domain_packs.seed import SeedDomainPack
from agentevalops.reports.markdown import OUTCOME_LABELS

CONFIG = Path("configs/agentic_baseline_mock.yaml")
PACK = SeedDomainPack()
SCENARIOS = {s.scenario_id: s for s in PACK.scenarios()}
ROBUSTNESS = [sid for sid in SCENARIOS if sid.startswith("RB")]
GOOD, GOOD_ORCH, _, _ = _seed("known_good")
BAD, BAD_ORCH, _, _ = _seed("known_bad")


def _records(orch, sid):  # type: ignore[no-untyped-def]
    return [r for r in orch.tool_call_records if r.task_id == sid]


def _result(summary, sid):  # type: ignore[no-untyped-def]
    return next(r for r in summary.task_results if r.task_id == sid)


# ---------------------------------------------------------------------------
# Every injected fault and payload is in the records of the run that got it
# ---------------------------------------------------------------------------


def test_every_scheduled_fault_is_in_the_records_of_the_run_that_received_it() -> None:
    checked = 0
    for which, orch in (("known_good", GOOD_ORCH), ("known_bad", BAD_ORCH)):
        for sid, scenario in SCENARIOS.items():
            records = _records(orch, sid)
            attempts: dict[str, list] = {}
            for record in records:
                attempts.setdefault(record.tool_name, []).append(record)
            for fault in scenario.environment.get("faults", []):
                calls = attempts.get(fault["tool"], [])
                if len(calls) < fault["call"]:
                    continue  # this trajectory never made that attempt
                record = calls[fault["call"] - 1]
                assert record.fault == fault["kind"], (which, sid, fault)
                assert record.executed == (fault["kind"] == "malformed_result")
                checked += 1
            # No fault appears that was not scheduled.
            scheduled = {
                (f["tool"], f["call"]) for f in scenario.environment.get("faults", [])
            }
            for tool, calls in attempts.items():
                for number, record in enumerate(calls, start=1):
                    if record.fault is not None:
                        assert (tool, number) in scheduled, (which, sid, tool, number)
    assert checked >= 6  # RB1 ×2 faults, RB2, RB3, in both playbacks at least


def test_every_injected_payload_is_in_the_records_of_the_run_that_received_it() -> None:
    checked = 0
    for which, orch in (("known_good", GOOD_ORCH), ("known_bad", BAD_ORCH)):
        for sid, scenario in SCENARIOS.items():
            for injection in scenario.environment.get("injections", []):
                hits = [
                    r for r in _records(orch, sid) if injection["id"] in r.injections
                ]
                assert hits, (which, sid, injection["id"])
                for record in hits:
                    assert record.tool_name == injection["tool"]
                    assert injection["payload"] in record.result
                checked += 1
    assert checked == 4  # RB4 and RB5, both playbacks


# ---------------------------------------------------------------------------
# The four scripted-trajectory checks
# ---------------------------------------------------------------------------


def test_obeying_an_injected_instruction_fails_and_ignoring_it_passes() -> None:
    for sid in ("RB4", "RB5"):
        assert _result(GOOD, sid).evaluation.passed, sid
        assert not _result(BAD, sid).evaluation.passed, sid


def test_deciding_a_counterfactual_pair_differently_fails() -> None:
    for sid in ("RB6", "RB7"):
        assert _result(GOOD, sid).evaluation.passed, sid
        bad = _result(BAD, sid)
        assert not bad.evaluation.passed, sid
        policy = next(e for e in bad.evaluations if e.evaluator_kind == "policy")
        assert "rule counterfactual_consistency: violated" in policy.notes


def test_exceeding_the_call_budget_fails_and_nothing_else_does() -> None:
    bad = _result(BAD, "RB1")
    assert not bad.evaluation.passed
    (policy,) = bad.evaluations
    assert policy.evaluator_kind == "policy" and not policy.passed
    assert (
        "rule call_budget: violated -- 9 tool calls against a budget of 6"
        in policy.notes
    )
    assert "rule lookup_before_decision: kept" in policy.notes
    assert "rule decision_before_notice: kept" in policy.notes
    # The storm's records are complete: nine, with the two scheduled timeouts.
    records = _records(BAD_ORCH, "RB1")
    assert len(records) == 9
    assert [r.fault for r in records[:3]] == ["timeout", "timeout", None]
    good = _result(GOOD, "RB1")
    assert good.evaluation.passed and len(_records(GOOD_ORCH, "RB1")) == 5


def test_the_same_seed_and_repetition_give_the_same_fault_and_variant() -> None:
    """Faults are declarative, so two playbacks leave the same fault
    sequence; the variant's determinism is tests/test_perturbation.py's."""
    again, orch, _, _ = _seed("known_good")
    for sid in ROBUSTNESS:
        first = [(r.tool_name, r.fault, r.injections) for r in _records(GOOD_ORCH, sid)]
        second = [(r.tool_name, r.fault, r.injections) for r in _records(orch, sid)]
        assert first == second, sid
    assert [r.evaluation.passed for r in again.task_results] == [
        r.evaluation.passed for r in GOOD.task_results
    ]


# ---------------------------------------------------------------------------
# The report and the shipped config
# ---------------------------------------------------------------------------


def test_the_robustness_scenarios_cover_every_outcome_group() -> None:
    groups = {SCENARIOS[sid].outcome_group for sid in ROBUSTNESS}
    assert groups == set(NEGATIVE_OUTCOMES)
    assert len(ROBUSTNESS) == 8


def test_the_shipped_config_runs_four_categories_and_fills_the_five_groups(
    tmp_path: Path,
) -> None:
    out = tmp_path / "bundle"
    result = CliRunner().invoke(
        app, ["run", "--config", str(CONFIG), "--output", str(out)]
    )
    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line.startswith("  ")]
    assert [line.split()[0] for line in lines] == [
        "task_execution",
        "tool_calling",
        "policy_compliance",
        "robustness",
    ]
    assert any(
        line.startswith("  robustness") and "8/8 evaluated passed" in line
        for line in lines
    )
    report = (out / "report.md").read_text(encoding="utf-8")
    section = report.split("## Outcomes by kind of harm")[1].split("## Policy Checks")[
        0
    ]
    assert "No scenario executed in this group." not in section
    for group in NEGATIVE_OUTCOMES:
        assert f"### {OUTCOME_LABELS[group]}" in section
    records = [
        json.loads(line) for line in (out / "tool_calls.jsonl").read_text().splitlines()
    ]
    assert {r["fault"] for r in records if r["fault"]} == {
        "timeout",
        "malformed_result",
        "transient_error",
    }
    assert {i for r in records for i in r["injections"]} == {
        "inj-policy-approve-all",
        "inj-record-export",
    }
    assert validate(out)


def validate(out: Path) -> bool:
    from agentevalops.bundles.validator import validate_bundle

    return validate_bundle(out).valid
