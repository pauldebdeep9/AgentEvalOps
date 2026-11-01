"""R25's exit criteria, checked against running code (ROADMAP R25;
docs/adr/0006 D5, D14, D15, D26): the policy-compliance category on the seed
pack, the run-level allow-list, the detector, and the shipped config's
verdicts in three categories."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import fast_yaml
from test_r24_exit import EVALUATIONS, GOOD_CALLS, SCENARIO_IDS, N, _seed
from typer.testing import CliRunner

from agentevalops.cli import app, category_lines
from agentevalops.config.loader import load_run_config
from agentevalops.core.schemas import (
    AgentOutput,
    EvaluationResult,
    RunSummary,
    TaskRunResult,
    TerminationReason,
)
from agentevalops.core.types import RunId, TaskId
from agentevalops.domain_packs.seed import SeedDomainPack
from agentevalops.policy import basic_checker

CONFIG = Path("configs/agentic_baseline_mock.yaml")
SYSTEM = Path("configs/seed_system.yaml")
PACK = SeedDomainPack()
SCENARIOS = {s.scenario_id: s for s in PACK.scenarios()}
POLICY_SCENARIOS = [sid for sid in SCENARIO_IDS if sid.startswith("PC")]

#: The rule each policy scenario's known-bad breaks, by design.
BROKEN_RULE = {
    "PC1": "decision_before_notice",
    "PC2": "no_retry_after_denial",
    "PC3": "restricted_export_destination",
    "PC4": "least_privilege",
    "PC5": "restricted_export_destination",
    "PC6": "notice_transparency",
    "PC7": "no_pii_leak",
    "PC8": "no_pii_leak",
}


def test_eight_policy_scenarios_with_both_trajectories_and_the_evaluator() -> None:
    assert set(POLICY_SCENARIOS) == set(BROKEN_RULE)
    for sid in POLICY_SCENARIOS:
        scenario = SCENARIOS[sid]
        assert scenario.category == "policy_compliance"
        assert "policy" in scenario.applicable_evaluators
        assert scenario.known_good and scenario.known_bad
        assert BROKEN_RULE[sid] in scenario.expected["policy"]["rules"]
        assert scenario.control_ids


def test_each_policy_known_bad_breaks_its_rule_and_each_known_good_keeps_it() -> None:
    """Each known-bad breaks the rule it was written to break, and the
    policy evaluator says which; the known-good keeps every rule."""
    good, _, _, _ = _seed("known_good")
    bad, _, _, _ = _seed("known_bad")
    by_id_good = {str(r.task_id): r for r in good.task_results}
    by_id_bad = {str(r.task_id): r for r in bad.task_results}
    for sid, rule in BROKEN_RULE.items():
        good_policy = next(
            e for e in by_id_good[sid].evaluations if e.evaluator_kind == "policy"
        )
        assert good_policy.passed, (sid, good_policy.notes)
        assert f"rule {rule}: kept" in good_policy.notes
        bad_policy = next(
            e for e in by_id_bad[sid].evaluations if e.evaluator_kind == "policy"
        )
        assert not bad_policy.passed, (sid, bad_policy.notes)
        assert f"rule {rule}: violated" in bad_policy.notes, (sid, bad_policy.notes)
        assert bad_policy.citations == []
        assert all(r.stream == "tool_calls" for r in bad_policy.refs)


def test_the_approval_gate_shows_in_the_records_of_the_policy_scenarios() -> None:
    good, orch, _, _ = _seed("known_good")
    records = {
        sid: [r for r in orch.tool_call_records if r.task_id == sid]
        for sid in POLICY_SCENARIOS
    }
    denied = next(r for r in records["PC2"] if r.tool_name == "send_notice")
    assert (denied.approval, denied.executed) == ("denied", False)
    timed_out = next(r for r in records["PC3"] if r.tool_name == "send_notice")
    assert (timed_out.approval, timed_out.executed) == ("timed_out", False)
    export = next(r for r in records["PC5"] if r.tool_name == "records_export")
    assert (export.approval, export.executed, export.data_class) == (
        "approved",
        True,
        "restricted",
    )
    assert all(
        r.approval == "not_required"
        for sid in POLICY_SCENARIOS
        for r in records[sid]
        if r.permission != "irreversible"
    )


def test_allowed_tool_ids_has_a_consumer_outside_the_schema() -> None:
    """The row's grep, as a test: a non-schema consumer of allowed_tool_ids."""
    source = inspect.getsource(basic_checker)
    assert "policy.allowed_tool_ids" in source
    loaded = load_run_config(CONFIG)
    assert set(loaded.policy_spec.allowed_tool_ids) == {t.name for t in PACK.tools}


# ---------------------------------------------------------------------------
# Verdicts in three categories, through the CLI
# ---------------------------------------------------------------------------


def _result(task_id: str, *, passed: bool, evaluated: bool = True) -> TaskRunResult:
    return TaskRunResult(
        task_id=TaskId(task_id),
        output=AgentOutput(
            success=True, termination_reason=TerminationReason.COMPLETED
        ),
        evaluation=EvaluationResult(
            "e", "k", 1.0 if passed else 0.0, passed, evaluated=evaluated
        ),
        event_count=0,
    )


def test_category_lines_count_each_category_in_first_seen_order() -> None:
    summary = RunSummary(
        run_id=RunId("r"),
        task_results=[
            _result("a", passed=True),
            _result("b", passed=False),
            _result("c", passed=True, evaluated=False),
            _result("d", passed=True),
            _result("e", passed=True),  # no category: not counted
        ],
    )
    categories = {
        "a": "task_execution",
        "b": "task_execution",
        "c": "tool_calling",
        "d": "policy_compliance",
    }
    assert category_lines(summary, categories) == [
        "  task_execution     1/2 evaluated passed (0 not evaluated)",
        "  tool_calling       0/0 evaluated passed (1 not evaluated)",
        "  policy_compliance  1/1 evaluated passed (0 not evaluated)",
    ]
    assert category_lines(summary, {}) == []


def test_the_shipped_config_produces_verdicts_in_three_categories(
    tmp_path: Path,
) -> None:
    out = tmp_path / "bundle"
    result = CliRunner().invoke(
        app, ["run", "--config", str(CONFIG), "--output", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert "Policy  : PASS" in result.output
    assert "By category:" in result.output
    lines = [line for line in result.output.splitlines() if line.startswith("  ")]
    counts = {s.category: 0 for s in PACK.scenarios()}
    for s in PACK.scenarios():
        counts[s.category] += 1
    assert lines == [
        f"  {category:<17}  {n}/{n} evaluated passed (0 not evaluated)"
        f"; pass^1 {n}/{n} scenarios"  # R27: pass^k on every category line
        for category, n in counts.items()
    ]
    assert list(counts)[:3] == ["task_execution", "tool_calling", "policy_compliance"]
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert (summary["passed_tasks"], summary["failed_tasks"]) == (N, 0)
    assert "category" not in summary["task_results"][0]  # CLI-only until 0.6
    policy = json.loads((out / "policy.json").read_text(encoding="utf-8"))
    assert policy["verdict"] == "pass" and policy["refs"] == []
    evaluations = json.loads((out / "evaluations.json").read_text(encoding="utf-8"))
    assert len(evaluations) == EVALUATIONS
    assert len((out / "tool_calls.jsonl").read_text().splitlines()) == GOOD_CALLS
    for command in ("validate-bundle", "replay"):
        assert CliRunner().invoke(app, [command, "--bundle", str(out)]).exit_code == 0


def test_the_known_bad_playback_fails_every_category_and_keeps_the_run_level_pass(
    tmp_path: Path,
) -> None:
    doc = fast_yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    doc["system"] = str(SYSTEM.resolve())  # the copy lives in tmp_path (R27)
    doc["mock_trajectory"] = "known_bad"
    config = tmp_path / "bad.yaml"
    config.write_text(fast_yaml.safe_dump(doc), encoding="utf-8")
    out = tmp_path / "bundle"
    result = CliRunner().invoke(
        app, ["run", "--config", str(config), "--output", str(out)]
    )
    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line.startswith("  ")]
    assert all(
        "0/" in line and "evaluated passed (0 not evaluated)" in line for line in lines
    )
    assert len(lines) == len({s.category for s in PACK.scenarios()})
    # Every known-bad stays within the pack's six tools, so the run-level
    # allow-list passes; a stray tool would fail it.
    assert "Policy  : PASS" in result.output


def test_a_run_that_strays_outside_allowed_tool_ids_fails_the_policy_verdict(
    tmp_path: Path,
) -> None:
    """Fail-first for the shipped config's allow-list: narrow it and the
    known-good exports in PC5 and the policy search elsewhere fail the run."""
    doc = fast_yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    doc["system"] = str(SYSTEM.resolve())  # the copy lives in tmp_path (R27)
    doc["policy"]["allowed_tool_ids"] = [
        "case_lookup",
        "status_update",
        "send_notice",
        "refer_to_officer",
    ]
    config = tmp_path / "narrow.yaml"
    config.write_text(fast_yaml.safe_dump(doc), encoding="utf-8")
    out = tmp_path / "bundle"
    result = CliRunner().invoke(
        app, ["run", "--config", str(config), "--output", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert "Policy  : FAIL" in result.output
    policy = json.loads((out / "policy.json").read_text(encoding="utf-8"))
    assert "outside allowed_tool_ids" in policy["notes"]
    assert policy["citations"] == [] and policy["refs"]
    assert {r["stream"] for r in policy["refs"]} == {"tool_calls"}


def _case_identifiers() -> list[tuple[str, str]]:
    return [
        (kind, value)
        for case in PACK.cases.values()
        for kind, value in (
            ("nric", case.applicant.nric),
            ("phone", case.applicant.phone),
            ("email", case.applicant.email),
        )
    ]


def test_the_detector_flags_every_seeded_identifier_and_none_of_the_look_alikes() -> (
    None
):
    from agentevalops.core.pii import check_letter, detect

    text = " ".join(value for _, value in _case_identifiers())
    found = {(d.kind, d.value) for d in detect(text, synthetic=True)}
    assert found == set(_case_identifiers())
    look_alikes = " ".join(
        f"{prefix}{digits}{'A' if check_letter(prefix, digits) != 'A' else 'B'}"
        for prefix in "STFG"
        for digits in ("1234567", "7654321", "0000001")
    )
    assert detect(look_alikes) == []
    assert detect(text) == [
        d for d in detect(text) if d.kind == "email"
    ]  # default: no X, no 5
