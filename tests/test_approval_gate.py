"""The simulated approver and the approval gate (R25 slice 1; docs/adr/0006
D5): every call to an irreversible tool is put to the approver, answered
instantly from the scenario's script, and nothing unapproved runs."""

from __future__ import annotations

import asyncio
import json
import shutil
from importlib import resources
from pathlib import Path
from typing import Any

import pytest
import yaml

from agentevalops.agents.mock_runner import MockAgentRunner
from agentevalops.core.errors import ConfigurationError
from agentevalops.core.schemas import AgentInput, ResourceLimits, TaskSpec, ToolSpec
from agentevalops.core.types import TaskId
from agentevalops.domain_packs import DomainPackError
from agentevalops.domain_packs.seed import SeedDomainPack
from agentevalops.environments import (
    Approver,
    InProcessToolEnvironment,
    ScriptedApprover,
    cross_check,
    toy_environment,
)

DATA_DIR = Path(str(resources.files("agentevalops.domain_packs.seed") / "data"))


async def _ok(arguments: dict[str, Any]) -> str:
    return "done"


def _env(
    approver: ScriptedApprover | None = None, permission: str = "irreversible"
) -> InProcessToolEnvironment:
    specs = (
        ToolSpec("send", "d", {}, permission=permission),  # type: ignore[arg-type]
        ToolSpec("look", "d", {}, permission="read"),
    )
    return InProcessToolEnvironment(
        TaskId("t"), specs, {"send": _ok, "look": _ok}, approver=approver
    )


def _call(env: InProcessToolEnvironment, tool: str) -> Any:
    return asyncio.run(env.call(tool, {"x": 1}))


# ---------------------------------------------------------------------------
# The approver
# ---------------------------------------------------------------------------


def test_the_scripted_approver_answers_per_tool_per_call_in_order() -> None:
    approver = ScriptedApprover({"send": ["deny", "approve", "timeout"]})
    assert isinstance(approver, Approver)
    answers = [approver.decide("send", {}) for _ in range(4)]
    assert answers == ["denied", "approved", "timed_out", "denied"]
    assert approver.schedule() == {"send": ("deny", "approve", "timeout")}


def test_anything_unscripted_is_denied() -> None:
    """Fail closed (D5: nothing unapproved runs)."""
    assert ScriptedApprover().decide("send", {}) == "denied"
    assert ScriptedApprover({"other": ["approve"]}).decide("send", {}) == "denied"


@pytest.mark.parametrize(
    ("schedule", "match"),
    [
        ({"send": "approve"}, "must be a list"),
        ({"send": ["maybe"]}, "unknown answer"),
        ({"send": [1]}, "unknown answer"),
    ],
)
def test_a_malformed_schedule_is_refused(schedule: Any, match: str) -> None:
    with pytest.raises(ConfigurationError, match=match):
        ScriptedApprover(schedule)


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def test_a_denied_call_never_executes_and_the_record_says_so() -> None:
    """Fail-first: before the gate the call ran and was recorded as
    executed with approval not_required."""
    env = _env(ScriptedApprover({"send": ["deny"]}))
    result = _call(env, "send")
    assert (
        result.error and result.content == "error: send was not taken: approval denied"
    )
    (record,) = env.records()
    assert (record.executed, record.approval, record.error) == (False, "denied", True)
    assert record.result == result.content


def test_a_timed_out_call_never_executes_either() -> None:
    env = _env(ScriptedApprover({"send": ["timeout"]}))
    result = _call(env, "send")
    assert result.content == "error: send was not taken: approval timed out"
    (record,) = env.records()
    assert (record.executed, record.approval) == (False, "timed_out")


def test_an_approved_call_executes_and_is_recorded_approved() -> None:
    env = _env(ScriptedApprover({"send": ["approve"]}))
    result = _call(env, "send")
    assert (result.content, result.error) == ("done", False)
    (record,) = env.records()
    assert (record.executed, record.approval, record.error) == (True, "approved", False)


def test_the_schedule_is_consumed_one_answer_per_call() -> None:
    env = _env(ScriptedApprover({"send": ["deny", "approve"]}))
    assert [_call(env, "send").error for _ in range(3)] == [True, False, True]
    assert [r.approval for r in env.records()] == ["denied", "approved", "denied"]
    assert [r.executed for r in env.records()] == [False, True, False]


def test_no_approver_means_nothing_irreversible_is_approved() -> None:
    env = _env()
    assert _call(env, "send").error
    assert env.records()[0].approval == "denied"


def test_read_and_write_tools_never_consult_the_approver() -> None:
    class _Counting:
        calls = 0

        def decide(self, tool_name: str, arguments: Any) -> Any:
            self.calls += 1
            return "approved"

    approver = _Counting()
    for permission in ("read", "write"):
        env = _env(approver, permission=permission)  # type: ignore[arg-type]
        assert not _call(env, "send").error
        assert env.records()[0].approval == "not_required"
    env = _env(approver, permission="irreversible")  # type: ignore[arg-type]
    _call(env, "look")
    assert env.records()[0].approval == "not_required"
    assert approver.calls == 0
    _call(env, "send")
    assert approver.calls == 1 and env.records()[1].approval == "approved"


def test_the_toy_environment_is_untouched() -> None:
    env = toy_environment(TaskId("t"))
    asyncio.run(env.call("calculator", {"expression": "2 + 2"}))
    (record,) = env.records()
    assert (record.executed, record.approval, record.result) == (
        True,
        "not_required",
        "4",
    )


def test_a_denied_call_still_matches_its_self_report() -> None:
    """The agent reported the attempt and the harness recorded it, so the
    cross-check is quiet; the record's own approval field says it was
    refused (D14)."""
    env = _env(ScriptedApprover({"send": ["deny"]}))
    task = TaskSpec(task_id=TaskId("t"), benchmark_id="b", description="d")
    runner = MockAgentRunner(
        scripts={"t": [{"call": "send", "arguments": {"x": 1}}, {"finish": "x"}]}
    )

    async def go() -> list[Any]:
        return [
            e
            async for e in await runner.run(
                AgentInput(task=task, context={"run_id": "r"}, environment=env),
                ResourceLimits(),
            )
        ]

    events = asyncio.run(go())
    assert cross_check(env.records(), events) == []
    results = [e.payload for e in events if e.kind.value == "agent.tool_result"]
    assert results[0]["result"] == "error: send was not taken: approval denied"


# ---------------------------------------------------------------------------
# The seed pack scripts its approvals
# ---------------------------------------------------------------------------


def _edited(tmp_path: Path, mutate: Any) -> Path:
    copy = tmp_path / "data"
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


def _run_known_good(pack: SeedDomainPack, scenario_id: str) -> InProcessToolEnvironment:
    scenario = {s.scenario_id: s for s in pack.scenarios()}[scenario_id]
    env = pack.build_environment(scenario, TaskId(scenario_id))
    runner = MockAgentRunner(scripts={scenario_id: scenario.known_good})
    task = TaskSpec(task_id=TaskId(scenario_id), benchmark_id="b", description="d")

    async def go() -> None:
        async for _ in await runner.run(
            AgentInput(task=task, context={"run_id": "r"}, environment=env),
            ResourceLimits(),
        ):
            pass

    asyncio.run(go())
    return env


def test_every_r24_scenario_scripts_its_irreversible_calls() -> None:
    pack = SeedDomainPack()
    for scenario in pack.scenarios():
        irreversible = {
            step["call"]
            for step in scenario.known_good + scenario.known_bad
            if "call" in step and step["call"] in ("send_notice", "records_export")
        }
        approvals = scenario.environment["approvals"]
        assert irreversible <= set(approvals), scenario.scenario_id
        if scenario.category != "policy_compliance":
            # Outside the policy category the scenarios test the agent, not
            # the approver: every answer is approve (RB3 scripts two, for a
            # retried notice). R25's policy scenarios script refusals too.
            assert all(
                answer == "approve"
                for answers in approvals.values()
                for answer in answers
            ), scenario.scenario_id


def test_a_notice_in_a_known_good_runs_approved_and_is_recorded_so() -> None:
    env = _run_known_good(SeedDomainPack(), "TE1")
    notice = next(r for r in env.records() if r.tool_name == "send_notice")
    assert (notice.executed, notice.approval) == (True, "approved")
    assert asyncio.run(env.snapshot())["notices"][0]["case_id"] == "C1"
    assert json.loads(_call_lookup(env))["status"] == "approved"


def _call_lookup(env: InProcessToolEnvironment) -> str:
    return asyncio.run(env.call("case_lookup", {"case_id": "C1"})).content


def test_without_a_scripted_answer_the_notice_is_denied(tmp_path: Path) -> None:
    """Fail closed through the pack: drop TE1's approvals and its known-good
    notice is refused, which the end state and the record both show."""

    def drop(doc: dict[str, Any]) -> None:
        del _scenario(doc, "TE1")["approvals"]

    env = _run_known_good(SeedDomainPack(_edited(tmp_path, drop)), "TE1")
    notice = next(r for r in env.records() if r.tool_name == "send_notice")
    assert (notice.executed, notice.approval) == (False, "denied")
    assert asyncio.run(env.snapshot())["notices"] == []


@pytest.mark.parametrize(
    ("approvals", "match"),
    [
        ({"case_lookup": ["approve"]}, "not an irreversible seed tool"),
        ({"send_notice": ["later"]}, "unknown answer"),
        ({"send_notice": []}, "non-empty list of strings"),
        ({"send_notice": "approve"}, "non-empty list of strings"),
        ("approve", "approvals must be a mapping"),
    ],
)
def test_malformed_approvals_fail_at_load(
    tmp_path: Path, approvals: Any, match: str
) -> None:
    def mutate(doc: dict[str, Any]) -> None:
        _scenario(doc, "TE1")["approvals"] = approvals

    with pytest.raises(DomainPackError, match=match):
        SeedDomainPack(_edited(tmp_path, mutate))


def test_approvals_are_optional_for_scenarios_without_irreversible_calls(
    tmp_path: Path,
) -> None:
    def drop(doc: dict[str, Any]) -> None:
        te5 = _scenario(doc, "TE5")
        del te5["approvals"]
        te5["known_bad"] = [
            {
                "call": "status_update",
                "arguments": {
                    "case_id": "C6",
                    "decision": "approved",
                    "reason_codes": ["ELIG-OK"],
                },
            },
            {"finish": "C6: approved"},
        ]

    pack = SeedDomainPack(_edited(tmp_path, drop))
    te5 = {s.scenario_id: s for s in pack.scenarios()}["TE5"]
    assert te5.environment == {"approvals": {}, "faults": [], "injections": []}
