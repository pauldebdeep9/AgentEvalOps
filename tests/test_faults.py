"""The environment's fault slot and the call budget (R26 slice 1;
docs/adr/0006 D6): scheduled faults land on the n-th call of a tool, are
written to the harness record, and a retry storm fails the budget rule."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from importlib import resources
from pathlib import Path
from typing import Any

import pytest
import yaml

from agentevalops.core.errors import ConfigurationError
from agentevalops.core.schemas import FAULT_KINDS, NEGATIVE_OUTCOMES, ToolSpec
from agentevalops.core.types import TaskId
from agentevalops.domain_packs import DomainPackError
from agentevalops.domain_packs.seed import SeedDomainPack
from agentevalops.environments import (
    InProcessToolEnvironment,
    ScriptedApprover,
    ScriptedFaults,
    garble,
    toy_environment,
)
from agentevalops.evaluators import policy_compliance

DATA_DIR = Path(str(resources.files("agentevalops.domain_packs.seed") / "data"))


class _Counter:
    def __init__(self) -> None:
        self.runs = 0

    async def __call__(self, arguments: dict[str, Any]) -> str:
        self.runs += 1
        return '{"ok": true, "run": %d}' % self.runs


def _env(
    faults: list[dict[str, Any]] | None = None,
    *,
    permission: str = "read",
    approver: ScriptedApprover | None = None,
) -> tuple[InProcessToolEnvironment, _Counter]:
    handler = _Counter()
    specs = (ToolSpec("look", "d", {}, permission=permission),)  # type: ignore[arg-type]
    env = InProcessToolEnvironment(
        TaskId("t"),
        specs,
        {"look": handler},
        approver=approver,
        faults=ScriptedFaults(faults),
    )
    return env, handler


def _call(env: InProcessToolEnvironment, tool: str = "look") -> Any:
    return asyncio.run(env.call(tool, {"x": 1}))


# ---------------------------------------------------------------------------
# The schedule
# ---------------------------------------------------------------------------


def test_the_schedule_answers_per_tool_and_call_number() -> None:
    faults = ScriptedFaults(
        [
            {"tool": "look", "call": 2, "kind": "timeout"},
            {"tool": "send", "kind": "transient_error"},
        ]
    )
    assert faults.fault_for("look", 1) is None
    assert faults.fault_for("look", 2) == "timeout"
    assert faults.fault_for("send", 1) == "transient_error"
    assert faults.fault_for("send", 2) is None
    assert faults.schedule() == [
        {"tool": "look", "call": 2, "kind": "timeout"},
        {"tool": "send", "call": 1, "kind": "transient_error"},
    ]
    assert FAULT_KINDS == {"timeout", "transient_error", "malformed_result"}


@pytest.mark.parametrize(
    ("schedule", "match"),
    [
        (["timeout"], "must be a mapping"),
        ([{"kind": "timeout"}], "needs a tool"),
        ([{"tool": "look", "kind": "crash"}], "unknown kind"),
        ([{"tool": "look", "kind": "timeout", "call": 0}], "positive integer"),
        ([{"tool": "look", "kind": "timeout", "call": True}], "positive integer"),
        (
            [{"tool": "look", "kind": "timeout"}, {"tool": "look", "kind": "timeout"}],
            "already has a fault",
        ),
    ],
)
def test_a_malformed_schedule_is_refused(schedule: Any, match: str) -> None:
    with pytest.raises(ConfigurationError, match=match):
        ScriptedFaults(schedule)


# ---------------------------------------------------------------------------
# The slot
# ---------------------------------------------------------------------------


def test_a_timeout_stops_the_call_before_the_tool_and_is_recorded() -> None:
    """Fail-first: before the slot the call ran and the record carried no
    fault."""
    env, handler = _env([{"tool": "look", "call": 1, "kind": "timeout"}])
    result = _call(env)
    assert result.error and result.content == "error: look timed out"
    assert handler.runs == 0
    (record,) = env.records()
    assert (record.fault, record.executed, record.error) == ("timeout", False, True)
    # The next call runs normally.
    assert not _call(env).error and handler.runs == 1
    assert env.records()[1].fault is None


def test_a_transient_error_is_the_same_shape_with_its_own_message() -> None:
    env, handler = _env([{"tool": "look", "call": 1, "kind": "transient_error"}])
    result = _call(env)
    assert result.content == "error: look is temporarily unavailable; try again"
    assert handler.runs == 0
    assert env.records()[0].fault == "transient_error"
    assert not _call(env).error


def test_a_malformed_result_runs_the_tool_and_garbles_what_the_agent_sees() -> None:
    env, handler = _env([{"tool": "look", "call": 1, "kind": "malformed_result"}])
    result = _call(env)
    assert handler.runs == 1  # the state change stands
    assert not result.error
    assert result.content == garble('{"ok": true, "run": 1}')
    assert result.content.endswith("��{{") and "run" not in result.content
    (record,) = env.records()
    assert (record.fault, record.executed, record.error) == (
        "malformed_result",
        True,
        False,
    )
    assert record.result == result.content
    clean = _call(env)
    assert clean.content == '{"ok": true, "run": 2}' and env.records()[1].fault is None


def test_faults_land_on_the_nth_attempt_of_that_tool_only() -> None:
    env, handler = _env([{"tool": "look", "call": 3, "kind": "timeout"}])
    results = [_call(env) for _ in range(4)]
    assert [r.error for r in results] == [False, False, True, False]
    assert [r.fault for r in env.records()] == [None, None, "timeout", None]
    assert handler.runs == 3
    # Another tool's calls do not count towards look's attempts.
    _call(env, "other")
    assert env.records()[-1].fault is None


def test_the_slot_comes_after_the_approval_gate() -> None:
    """A call the approver refused gets no fault; an approved one can."""
    approver = ScriptedApprover({"look": ["deny", "approve"]})
    env, handler = _env(
        [
            {"tool": "look", "call": 1, "kind": "timeout"},
            {"tool": "look", "call": 2, "kind": "timeout"},
        ],
        permission="irreversible",
        approver=approver,
    )
    denied = _call(env)
    assert denied.content == "error: look was not taken: approval denied"
    assert env.records()[0].fault is None and env.records()[0].approval == "denied"
    timed = _call(env)
    assert timed.content == "error: look timed out"
    assert (env.records()[1].approval, env.records()[1].fault) == (
        "approved",
        "timeout",
    )
    assert handler.runs == 0


def test_the_same_schedule_gives_the_same_records_twice() -> None:
    schedule = [
        {"tool": "look", "call": 1, "kind": "timeout"},
        {"tool": "look", "call": 3, "kind": "malformed_result"},
    ]
    shapes = []
    for _ in range(2):
        env, _ = _env(schedule)
        for _ in range(4):
            _call(env)
        shapes.append([(r.fault, r.executed, r.result) for r in env.records()])
    assert shapes[0] == shapes[1]


def test_the_toy_environment_has_no_faults() -> None:
    env = toy_environment(TaskId("t"))
    asyncio.run(env.call("calculator", {"expression": "1 + 1"}))
    assert env.records()[0].fault is None


def test_garble_is_deterministic_and_never_empty() -> None:
    assert garble("abcdef") == "abc��{{"
    assert garble("") == "��{{"
    assert garble("x") == "x��{{"


# ---------------------------------------------------------------------------
# The seed pack's schedules and groups
# ---------------------------------------------------------------------------


def _edited(tmp_path: Path, mutate: Any) -> Path:
    copy = Path(tempfile.mkdtemp(prefix="data", dir=tmp_path))
    shutil.rmtree(copy)
    shutil.copytree(DATA_DIR, copy)
    doc = yaml.safe_load((copy / "scenarios.yaml").read_text(encoding="utf-8"))
    mutate(doc)
    (copy / "scenarios.yaml").write_text(
        yaml.safe_dump(doc, sort_keys=False), encoding="utf-8"
    )
    return copy


def _scenario(doc: dict[str, Any], scenario_id: str) -> dict[str, Any]:
    (found,) = [s for s in doc["scenarios"] if s["scenario_id"] == scenario_id]
    return found


def test_every_scenario_declares_an_outcome_group_and_the_task_carries_it() -> None:
    from agentevalops.benchmarks.agentic_baseline import AgenticBaselineBenchmark

    pack = SeedDomainPack()
    for scenario in pack.scenarios():
        assert scenario.outcome_group in NEGATIVE_OUTCOMES, scenario.scenario_id
    groups = {
        str(t.task_id): t.metadata["outcome_group"]
        for t in AgenticBaselineBenchmark(pack).list_tasks()
    }
    assert groups["RB1"] == "disruption" and groups["TE1"] == "erroneous_action"
    assert set(groups.values()) >= {
        "erroneous_action",
        "unauthorised_action",
        "data_breach",
        "disruption",
    }


def test_a_scenario_without_an_outcome_group_fails_at_load(tmp_path: Path) -> None:
    """Fail-first (owner's condition): before R26 a scenario carried no
    group and loaded."""

    def drop(doc: dict[str, Any]) -> None:
        del _scenario(doc, "TE1")["outcome_group"]

    with pytest.raises(
        DomainPackError, match="outcome_group must be a non-empty string"
    ):
        SeedDomainPack(_edited(tmp_path, drop))

    def unknown(doc: dict[str, Any]) -> None:
        _scenario(doc, "TE1")["outcome_group"] = "mishap"

    with pytest.raises(DomainPackError, match="outcome_group must be one of"):
        SeedDomainPack(_edited(tmp_path, unknown))


@pytest.mark.parametrize(
    ("faults", "match"),
    [
        ([{"tool": "rm", "kind": "timeout"}], "not a seed tool"),
        ([{"tool": "case_lookup", "kind": "crash"}], "unknown kind"),
        ([{"tool": "case_lookup", "kind": "timeout", "call": 0}], "positive integer"),
        ("timeout", "faults must be a non-empty list"),
    ],
)
def test_malformed_faults_fail_at_load(tmp_path: Path, faults: Any, match: str) -> None:
    def mutate(doc: dict[str, Any]) -> None:
        _scenario(doc, "TE1")["faults"] = faults

    with pytest.raises(DomainPackError, match=match):
        SeedDomainPack(_edited(tmp_path, mutate))


def test_the_pack_scripts_its_faults_into_the_environment() -> None:
    pack = SeedDomainPack()
    rb1 = {s.scenario_id: s for s in pack.scenarios()}["RB1"]
    assert rb1.environment["faults"] == [
        {"tool": "case_lookup", "call": 1, "kind": "timeout"},
        {"tool": "case_lookup", "call": 2, "kind": "timeout"},
    ]
    env = pack.build_environment(rb1, TaskId("RB1"))
    results = [
        asyncio.run(env.call("case_lookup", {"case_id": "C1"})) for _ in range(3)
    ]
    assert [r.error for r in results] == [True, True, False]
    assert [r.fault for r in env.records()] == ["timeout", "timeout", None]


def test_the_call_budget_goes_with_its_rule(tmp_path: Path) -> None:
    def rule_only(doc: dict[str, Any]) -> None:
        del _scenario(doc, "RB1")["expected"]["policy"]["call_budget"]

    with pytest.raises(
        DomainPackError, match="call_budget goes with the call_budget rule"
    ):
        SeedDomainPack(_edited(tmp_path, rule_only))

    def zero(doc: dict[str, Any]) -> None:
        _scenario(doc, "RB1")["expected"]["policy"]["call_budget"] = 0

    with pytest.raises(DomainPackError, match="call_budget must be a positive integer"):
        SeedDomainPack(_edited(tmp_path, zero))


def test_the_call_budget_rule_says_what_it_is_and_is_not() -> None:
    doc = " ".join((policy_compliance._call_budget.__doc__ or "").split())
    assert "per-call budget" in doc or "per-scenario" in doc
    assert "not a run-level spend cap" in doc and "R27" in doc
