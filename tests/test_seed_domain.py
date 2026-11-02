"""The seed domain pack (R24 slice 4; docs/adr/0005 D9, docs/adr/0006 D11,
D23, D26): six tools, eight cases, ten scenarios, the synthetic names, and
the guards that keep the data honest."""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Callable
from importlib import import_module, resources
from pathlib import Path
from typing import Any

import pytest
import yaml
from reference_bundles import edited_seed_data as _edited

from agentevalops import registry as registry_module
from agentevalops.agents import mock_runner
from agentevalops.agents.mock_runner import MockAgentRunner
from agentevalops.benchmarks.agentic_baseline import AgenticBaselineBenchmark
from agentevalops.catalogue import load_catalogue
from agentevalops.core import identifiers, scripts
from agentevalops.core.identifiers import (
    check_letter,
    is_synthetic_email,
    is_synthetic_nric,
    is_synthetic_phone,
    synthetic_nric,
    unsafe_identifiers,
)
from agentevalops.core.protocols import DomainPack
from agentevalops.core.schemas import (
    AgentInput,
    ResourceLimits,
    Scenario,
    TaskSpec,
    TraceEvent,
    TraceEventKind,
)
from agentevalops.core.types import TaskId
from agentevalops.domain_packs import DomainPackError
from agentevalops.domain_packs.seed import SeedDomainPack
from agentevalops.domain_packs.seed.environment import (
    END_STATE_SCHEMA,
    TOOL_SPECS,
    build_environment,
)
from agentevalops.domain_packs.seed.pack import DATA_FILES, strings_in
from agentevalops.environments import InProcessToolEnvironment, cross_check
from agentevalops.registry import KIND_BENCHMARK, KIND_DOMAIN_PACK

DATA_DIR = Path(str(resources.files("agentevalops.domain_packs.seed") / "data"))
PACK = SeedDomainPack()
SCENARIOS = {s.scenario_id: s for s in PACK.scenarios()}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(
    scenario: Scenario, steps: tuple[dict[str, Any], ...]
) -> tuple[list[TraceEvent], InProcessToolEnvironment]:
    env = PACK.build_environment(scenario, TaskId(scenario.scenario_id))
    runner = MockAgentRunner(scripts={scenario.scenario_id: steps})
    task = TaskSpec(
        task_id=TaskId(scenario.scenario_id),
        benchmark_id="agentic-baseline",
        description=scenario.description,
    )
    inp = AgentInput(task=task, context={"run_id": "r"}, environment=env)

    async def go() -> list[TraceEvent]:
        return [e async for e in await runner.run(inp, ResourceLimits())]

    return asyncio.run(go()), env


def _contains(expected: Any, actual: Any) -> bool:
    """*expected* as a template over *actual*: every key it names must
    match, lists in full and in order, scalars exactly. The state_based
    evaluator (slice 5) owns this rule; here it checks the data."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            k in actual and _contains(v, actual[k]) for k, v in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(_contains(e, a) for e, a in zip(expected, actual))
        )
    return bool(expected == actual)


def _snapshot(env: InProcessToolEnvironment) -> dict[str, Any]:
    return asyncio.run(env.snapshot())


def _call(env: InProcessToolEnvironment, tool: str, **arguments: Any) -> str:
    return asyncio.run(env.call(tool, arguments)).content


def _env() -> InProcessToolEnvironment:
    """A case-handling environment whose approver says yes to every
    irreversible call: these tests are about the tools, not the gate
    (tests/test_approval_gate.py is)."""
    return build_environment(
        TaskId("t"),
        PACK.cases,
        PACK.policy,
        approvals={"send_notice": ["approve"] * 5, "records_export": ["approve"] * 5},
    )


# ---------------------------------------------------------------------------
# The pack, through the registry
# ---------------------------------------------------------------------------


def test_the_seed_pack_is_a_domain_pack() -> None:
    assert isinstance(PACK, DomainPack)
    assert (PACK.pack_id, PACK.pack_version) == ("seed", "0.1.0")


def test_the_registry_resolves_the_seed_pack_for_agentic_baseline() -> None:
    reg = registry_module.build_default_registry()
    assert reg.get(KIND_DOMAIN_PACK, "seed") is SeedDomainPack
    benchmark = reg.get(KIND_BENCHMARK, "agentic-baseline")(pack="seed")
    assert isinstance(benchmark, AgenticBaselineBenchmark)
    tasks = list(benchmark.list_tasks())
    assert [str(t.task_id) for t in tasks] == list(SCENARIOS)
    te1 = tasks[0]
    assert te1.metadata["case_id"] == "C1"
    assert te1.metadata["control_ids"] == list(SCENARIOS["TE1"].control_ids)
    assert te1.applicable_evaluators == ("deterministic", "tool_use", "state_based")


def test_pyproject_publishes_the_built_in_seed_pack_as_an_entry_point() -> None:
    """docs/adr/0006 D24: the seed domain registers through the same entry
    point a third-party pack would; discovery finds the very object the
    built-in registration holds and skips it quietly."""
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    group = '[project.entry-points."agentevalops.domain_packs"]'
    assert group in text
    target = 'seed = "agentevalops.domain_packs.seed:SeedDomainPack"'
    assert target in text.split(group, 1)[1].split("[", 1)[0]
    module, attr = target.split('"')[1].split(":")
    assert getattr(import_module(module), attr) is registry_module.get(
        KIND_DOMAIN_PACK, "seed"
    )


def test_the_script_grammar_is_shared_through_core() -> None:
    assert mock_runner.parse_script is scripts.parse_script
    assert mock_runner.ScriptStep is scripts.ScriptStep


# ---------------------------------------------------------------------------
# The tools
# ---------------------------------------------------------------------------


def test_six_tools_with_their_permission_and_data_classes() -> None:
    assert {t.name: (t.permission, t.data_class) for t in PACK.tools} == {
        "case_lookup": ("read", "normal"),
        "status_update": ("write", "normal"),
        "send_notice": ("irreversible", "normal"),
        "records_export": ("irreversible", "restricted"),
        "policy_search": ("read", "normal"),
        "refer_to_officer": ("write", "normal"),
    }
    env = _env()
    assert [t.name for t in asyncio.run(env.list_tools())] == [
        t.name for t in TOOL_SPECS
    ]


def test_case_lookup_returns_the_record_and_the_open_status() -> None:
    record = json.loads(_call(_env(), "case_lookup", case_id="C1"))
    applicant = PACK.cases["C1"].applicant
    assert record["status"] == "open"
    assert record["applicant"] == {
        "name": applicant.name,
        "gender": "female",
        "age": 34,
        "nric": applicant.nric,
        "phone": applicant.phone,
        "email": applicant.email,
    }
    assert record["facts"]["monthly_income_verified"] == 2400


def test_an_unknown_case_is_an_error_result_that_changes_nothing() -> None:
    env = _env()
    for tool in ("case_lookup", "status_update", "send_notice", "refer_to_officer"):
        result = asyncio.run(env.call(tool, {"case_id": "C99"}))
        assert result.error and "no application 'C99'" in result.content
    snapshot = _snapshot(env)
    assert all(
        snapshot[k] == [] for k in ("decisions", "notices", "referrals", "exports")
    )
    assert all(r.error for r in env.records())


def test_status_update_refuses_bad_decisions_and_codes() -> None:
    env = _env()
    assert "decision must be one of" in _call(
        env, "status_update", case_id="C1", decision="maybe", reason_codes=["ELIG-OK"]
    )
    assert "unknown reason code" in _call(
        env, "status_update", case_id="C1", decision="approved", reason_codes=["NOPE"]
    )
    assert "at least one reason code" in _call(
        env, "status_update", case_id="C1", decision="approved", reason_codes=[]
    )
    assert "list of strings" in _call(
        env, "status_update", case_id="C1", decision="approved", reason_codes="ELIG-OK"
    )
    assert _snapshot(env)["decisions"] == []
    assert (
        _call(
            env,
            "status_update",
            case_id="C1",
            decision="approved",
            reason_codes=["ELIG-OK"],
        )
        == "C1 approved: ELIG-OK"
    )
    assert _snapshot(env)["decisions"] == [
        {"case_id": "C1", "status": "approved", "reason_codes": ["ELIG-OK"]}
    ]
    assert json.loads(_call(env, "case_lookup", case_id="C1"))["status"] == "approved"


def test_send_notice_goes_to_the_applicant_and_records_its_fields() -> None:
    env = _env()
    assert "needs a body" in _call(
        env, "send_notice", case_id="C2", body=" ", ai_disclosure=True, reason_codes=[]
    )
    assert "true or false" in _call(
        env, "send_notice", case_id="C2", body="x", ai_disclosure="yes", reason_codes=[]
    )
    out = _call(
        env,
        "send_notice",
        case_id="C2",
        body="Rejected. Reason codes: INC-OVER. An AI assistant prepared this.",
        ai_disclosure=True,
        reason_codes=["INC-OVER"],
    )
    email = PACK.cases["C2"].applicant.email
    assert out == f"notice about C2 sent to {email}"
    (notice,) = _snapshot(env)["notices"]
    assert (notice["to"], notice["ai_disclosure"], notice["reason_codes"]) == (
        email,
        True,
        ["INC-OVER"],
    )


def test_records_export_is_recorded_as_restricted_and_irreversible() -> None:
    env = _env()
    assert "needs a destination" in _call(env, "records_export", case_id="C1")
    out = _call(
        env, "records_export", case_id="C1", destination="archive@agency.example"
    )
    assert out == "record of C1 exported to archive@agency.example"
    record = env.records()[-1]
    assert (record.permission, record.data_class, record.executed) == (
        "irreversible",
        "restricted",
        True,
    )
    assert _snapshot(env)["exports"] == [
        {
            "case_id": "C1",
            "destination": "archive@agency.example",
            "fields": ["applicant", "facts", "status"],
        }
    ]


def test_policy_search_matches_clauses_by_word() -> None:
    env = _env()
    hits = _call(env, "policy_search", query="income limit")
    assert hits.startswith("P2 Income limit:") and "3,000" in hits
    assert "no clause matches 'zebra'" == _call(env, "policy_search", query="zebra")
    assert "at least one word" in _call(env, "policy_search", query="  ")
    assert "P4 Missing or conflicting facts" in _call(
        env, "policy_search", query="CONFLICT"
    )


def test_refer_to_officer_records_the_referral() -> None:
    env = _env()
    out = _call(
        env,
        "refer_to_officer",
        case_id="C6",
        reason_codes=["REFER-MISSING"],
        note="no statement",
    )
    assert out == "C6 referred to an officer: REFER-MISSING"
    assert _snapshot(env)["referrals"] == [
        {"case_id": "C6", "reason_codes": ["REFER-MISSING"], "note": "no statement"}
    ]
    assert json.loads(_call(env, "case_lookup", case_id="C6"))["status"] == "referred"


def test_each_task_run_gets_a_fresh_environment_with_the_schema() -> None:
    first, second = _env(), _env()
    _call(
        first,
        "status_update",
        case_id="C1",
        decision="approved",
        reason_codes=["ELIG-OK"],
    )
    assert _snapshot(second) == {
        "schema": END_STATE_SCHEMA,
        "decisions": [],
        "notices": [],
        "referrals": [],
        "exports": [],
    }
    assert len(_snapshot(first)["decisions"]) == 1


def test_build_environment_refuses_a_foreign_scenario() -> None:
    foreign = Scenario("nope", "d", "task_execution", known_good=({"finish": "x"},))
    with pytest.raises(DomainPackError, match="not in the seed pack"):
        PACK.build_environment(foreign, TaskId("t"))


# ---------------------------------------------------------------------------
# The cases and the names
# ---------------------------------------------------------------------------


def test_eight_cases_carry_their_ground_truth_and_the_r26_attributes() -> None:
    cases = PACK.cases
    assert list(cases) == [f"C{i}" for i in range(1, 9)]
    for case in cases.values():
        assert case.outcome in ("approve", "reject", "refer")
        assert case.reason_codes and set(case.reason_codes) <= set(
            PACK.policy.reason_codes
        )
        assert case.applicant.gender in ("female", "male")
        assert isinstance(case.applicant.age, int) and case.applicant.age > 0
        assert is_synthetic_nric(case.applicant.nric)
        assert is_synthetic_phone(case.applicant.phone)
        assert is_synthetic_email(case.applicant.email)
    assert {c.applicant.gender for c in cases.values()} == {"female", "male"}


def test_c6_and_c7_are_the_ambiguous_cases_and_are_referred() -> None:
    ambiguous = {c.case_id: c.ambiguity for c in PACK.cases.values() if c.ambiguous}
    assert ambiguous == {"C6": "missing", "C7": "conflict"}
    for case in PACK.cases.values():
        assert (case.outcome == "refer") == case.ambiguous
    assert PACK.cases["C6"].facts["monthly_income_verified"] is None
    c7 = PACK.cases["C7"].facts
    assert c7["monthly_income_declared"] != c7["monthly_income_verified"]


def test_names_cover_every_group_for_each_gender_and_pair_across_groups() -> None:
    names = PACK.names
    assert len(names) == 8 and len({n.name for n in names}) == 8
    groups = {n.group for n in names}
    assert groups == {"chinese", "malay", "indian", "eurasian"}
    for gender in ("female", "male"):
        assert {n.group for n in names if n.gender == gender} == groups
    pairs = PACK.name_pairs()
    assert len(pairs) == 12 and len(set(pairs)) == 12
    by_name = {n.name: n for n in names}
    for first, second in pairs:
        assert by_name[first].gender == by_name[second].gender
        assert by_name[first].group != by_name[second].group
    for case in PACK.cases.values():
        assert by_name[case.applicant.name].gender == case.applicant.gender


# ---------------------------------------------------------------------------
# The scenarios
# ---------------------------------------------------------------------------


def test_scenarios_across_the_three_categories() -> None:
    assert list(SCENARIOS)[:10] == [
        "TE1",
        "TE2",
        "TE3",
        "TE4",
        "TE5",
        "TE6",
        "TC1",
        "TC2",
        "TC3",
        "TC4",
    ]
    assert all(sid[:2] in ("PC", "RB") for sid in list(SCENARIOS)[10:])
    by_prefix = {
        "TE": "task_execution",
        "TC": "tool_calling",
        "PC": "policy_compliance",
        "RB": "robustness",
    }
    for scenario in SCENARIOS.values():
        assert scenario.category == by_prefix[scenario.scenario_id[:2]], (
            scenario.scenario_id
        )
    assert {s.expected["case_id"] for s in SCENARIOS.values()} == set(PACK.cases)


def test_every_scenario_declares_evaluators_controls_and_both_trajectories() -> None:
    catalogue = load_catalogue()
    for s in SCENARIOS.values():
        assert s.applicable_evaluators
        assert set(s.applicable_evaluators) <= {
            "deterministic",
            "tool_use",
            "state_based",
            "policy",
        }
        assert s.control_ids
        for control_id in s.control_ids:
            catalogue.control(control_id)  # raises for an unknown id
        assert "finish" in s.known_good[-1]
        assert s.known_bad
        assert ("tool_use" in s.applicable_evaluators) == ("tool_use" in s.expected)
        assert ("state_based" in s.applicable_evaluators) == (
            "expected_end_state" in s.expected
        )
        assert ("deterministic" in s.applicable_evaluators) == bool(
            {"expected_output", "expected_substring"} & set(s.expected)
        )
        assert ("policy" in s.applicable_evaluators) == ("policy" in s.expected)


def test_every_known_good_runs_clean_and_reaches_its_expected_end_state() -> None:
    for s in SCENARIOS.values():
        events, env = _run(s, s.known_good)
        assert events[-1].kind == TraceEventKind.AGENT_TERMINAL
        assert events[-1].payload["success"] is True, s.scenario_id
        assert cross_check(env.records(), events) == [], s.scenario_id
        # A refusal by the approver, or a scheduled fault, is scripted, not
        # an error of the run.
        refused = ("denied", "timed_out")
        assert not any(
            r.error and r.approval not in refused and r.fault is None
            for r in env.records()
        ), s.scenario_id
        if "expected_end_state" in s.expected:
            assert _contains(s.expected["expected_end_state"], _snapshot(env)), (
                s.scenario_id
            )


def test_every_known_bad_misses_the_expected_end_state_where_it_applies() -> None:
    checked = []
    for s in SCENARIOS.values():
        if "state_based" not in s.applicable_evaluators:
            continue
        _, env = _run(s, s.known_bad)
        assert not _contains(s.expected["expected_end_state"], _snapshot(env)), (
            s.scenario_id
        )
        checked.append(s.scenario_id)
    assert len(checked) == sum(
        1 for s in SCENARIOS.values() if "state_based" in s.applicable_evaluators
    )


def test_the_known_good_refers_an_ambiguous_case_and_the_known_bad_decides_it() -> None:
    for scenario_id, case_id in (("TE5", "C6"), ("TE6", "C7")):
        s = SCENARIOS[scenario_id]
        assert s.expected["case_id"] == case_id and PACK.cases[case_id].ambiguous
        _, good = _run(s, s.known_good)
        good_tools = [r.tool_name for r in good.records()]
        assert "refer_to_officer" in good_tools and "status_update" not in good_tools
        assert _snapshot(good)["decisions"] == []
        _, bad = _run(s, s.known_bad)
        assert "status_update" in [r.tool_name for r in bad.records()]
        assert _snapshot(bad)["decisions"] != []


# ---------------------------------------------------------------------------
# Load-time guards, each fail-first
# ---------------------------------------------------------------------------


def _scenario(doc: dict[str, Any], scenario_id: str) -> dict[str, Any]:
    (found,) = [s for s in doc["scenarios"] if s["scenario_id"] == scenario_id]
    return found


def _case(doc: dict[str, Any], case_id: str) -> dict[str, Any]:
    (found,) = [c for c in doc["cases"] if c["case_id"] == case_id]
    return found


def test_an_unknown_control_id_fails_at_load(tmp_path: Path) -> None:
    """Fail-first: without the catalogue check the pack loaded."""

    def mutate(doc: dict[str, Any]) -> None:
        _scenario(doc, "TE1")["control_ids"] = ["mgf-agentic:9.9.9"]

    with pytest.raises(DomainPackError, match="unknown control id 'mgf-agentic:9.9.9'"):
        SeedDomainPack(_edited(tmp_path, "scenarios.yaml", mutate))


@pytest.mark.parametrize(
    ("field", "value", "flagged"),
    [
        ("nric", "S4471920E", "S4471920E"),
        ("nric", "T4471920E", "T4471920E"),
        ("nric", "F4471920E", "F4471920E"),
        ("nric", "G4471920E", "G4471920E"),
        ("nric", "M4471920E", "M4471920E"),
        ("nric", "X4471920A", "X4471920A"),  # the X prefix with a wrong letter
        ("phone", "91038472", "91038472"),
        ("phone", "61038472", "61038472"),
        ("email", "weiling.tan@mail.com", "weiling.tan@mail.com"),
        ("email", "weiling.tan@example.com", "weiling.tan@example.com"),
    ],
)
def test_a_seeded_identifier_that_could_be_real_fails_at_load(
    tmp_path: Path, field: str, value: str, flagged: str
) -> None:
    """Fail-first: without the D26 scan the pack loaded every one of these."""

    def mutate(doc: dict[str, Any]) -> None:
        _case(doc, "C1")["applicant"][field] = value

    with pytest.raises(DomainPackError, match="D26") as info:
        SeedDomainPack(_edited(tmp_path, "cases.yaml", mutate))
    assert flagged in str(info.value)


def test_an_identifier_hidden_in_free_text_is_caught_too(tmp_path: Path) -> None:
    def mutate(doc: dict[str, Any]) -> None:
        doc["clauses"][0]["text"] += " Call 81234567 with questions."

    with pytest.raises(DomainPackError, match="81234567"):
        SeedDomainPack(_edited(tmp_path, "policy.yaml", mutate))


def test_a_shape_that_passes_the_scan_still_needs_the_scheme(tmp_path: Path) -> None:
    """A phone with seven digits is no telephone number to the scan, but it
    is not a synthetic one either."""

    def mutate(doc: dict[str, Any]) -> None:
        _case(doc, "C1")["applicant"]["phone"] = "5103847"

    with pytest.raises(DomainPackError, match="not a synthetic number"):
        SeedDomainPack(_edited(tmp_path, "cases.yaml", mutate))


def test_no_string_anywhere_in_the_pack_data_seeds_a_real_identifier() -> None:
    """Scans every string in every data file and every tool description, so
    new data cannot slip a real-looking identifier past the guard."""
    texts = list(PACK.data_strings()) + [t.description for t in TOOL_SPECS]
    assert len(texts) > 300
    assert [(t, unsafe_identifiers(t)) for t in texts if unsafe_identifiers(t)] == []
    # Positive control: the seeded identifiers are in what was scanned.
    for case in PACK.cases.values():
        for value in (case.applicant.nric, case.applicant.phone, case.applicant.email):
            assert value in texts


def test_strings_in_walks_keys_and_leaves() -> None:
    assert list(strings_in({"a": ["b", {"c": "d"}], "e": 1, "f": ("g",)})) == [
        "a",
        "b",
        "c",
        "d",
        "e",
        "f",
        "g",
    ]
    assert set(DATA_FILES) == {
        "policy.yaml",
        "names.yaml",
        "cases.yaml",
        "scenarios.yaml",
        "thresholds.yaml",
    }


def test_the_ambiguous_case_rule_is_enforced_at_load(tmp_path: Path) -> None:
    """Fail-first: without the rule a known-good that decided C6 loaded."""

    def decides(doc: dict[str, Any]) -> None:
        _scenario(doc, "TE5")["known_good"] = [
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

    with pytest.raises(
        DomainPackError, match="ambiguous, so known_good must call refer_to_officer"
    ):
        SeedDomainPack(_edited(tmp_path, "scenarios.yaml", decides))

    def never_decides(doc: dict[str, Any]) -> None:
        _scenario(doc, "TE5")["known_bad"] = [{"finish": "C6: nothing"}]

    with pytest.raises(DomainPackError, match="known_bad must decide"):
        SeedDomainPack(_edited(tmp_path, "scenarios.yaml", never_decides))

    def refers_a_clear_case(doc: dict[str, Any]) -> None:
        _scenario(doc, "TE1")["known_good"] = [
            {
                "call": "refer_to_officer",
                "arguments": {"case_id": "C1", "reason_codes": ["REFER-MISSING"]},
            },
            {"finish": "C1: referred"},
        ]

    with pytest.raises(DomainPackError, match="clear outcome"):
        SeedDomainPack(_edited(tmp_path, "scenarios.yaml", refers_a_clear_case))


def test_expected_blocks_must_match_the_declared_evaluators(tmp_path: Path) -> None:
    def undeclared(doc: dict[str, Any]) -> None:
        del _scenario(doc, "TE1")["expected"]["expected_substring"]

    with pytest.raises(
        DomainPackError, match="'deterministic' applies but expected declares nothing"
    ):
        SeedDomainPack(_edited(tmp_path, "scenarios.yaml", undeclared))

    def inapplicable(doc: dict[str, Any]) -> None:
        _scenario(doc, "TC1")["expected"]["expected_end_state"] = {"exports": []}

    with pytest.raises(DomainPackError, match="'state_based', which does not apply"):
        SeedDomainPack(_edited(tmp_path, "scenarios.yaml", inapplicable))


def test_other_malformed_data_fails_at_load(tmp_path: Path) -> None:
    def foreign_tool(doc: dict[str, Any]) -> None:
        _scenario(doc, "TE1")["known_bad"].insert(
            0, {"call": "delete_case", "arguments": {}}
        )

    with pytest.raises(DomainPackError, match="not a seed tool"):
        SeedDomainPack(_edited(tmp_path, "scenarios.yaml", foreign_tool))

    def no_finish(doc: dict[str, Any]) -> None:
        _scenario(doc, "TE1")["known_good"].pop()

    with pytest.raises(DomainPackError, match="must end with 'finish'"):
        SeedDomainPack(_edited(tmp_path, "scenarios.yaml", no_finish))

    def bad_step(doc: dict[str, Any]) -> None:
        _scenario(doc, "TE1")["known_good"][0] = {"jump": "x"}

    with pytest.raises(DomainPackError, match="script step 0"):
        SeedDomainPack(_edited(tmp_path, "scenarios.yaml", bad_step))

    def unknown_evaluator(doc: dict[str, Any]) -> None:
        _scenario(doc, "TE1")["applicable_evaluators"].append("llm_judge")

    with pytest.raises(DomainPackError, match="unknown kind"):
        SeedDomainPack(_edited(tmp_path, "scenarios.yaml", unknown_evaluator))

    def unknown_case(doc: dict[str, Any]) -> None:
        _scenario(doc, "TE1")["case_id"] = "C9"

    with pytest.raises(DomainPackError, match="unknown case 'C9'"):
        SeedDomainPack(_edited(tmp_path, "scenarios.yaml", unknown_case))

    def tool_use_names_a_stranger(doc: dict[str, Any]) -> None:
        _scenario(doc, "TE1")["expected"]["tool_use"]["forbidden"].append("rm")

    with pytest.raises(DomainPackError, match="not seed tools"):
        SeedDomainPack(_edited(tmp_path, "scenarios.yaml", tool_use_names_a_stranger))

    def wrong_gender(doc: dict[str, Any]) -> None:
        _case(doc, "C1")["applicant"]["gender"] = "male"

    with pytest.raises(DomainPackError, match="is female in names.yaml"):
        SeedDomainPack(_edited(tmp_path, "cases.yaml", wrong_gender))

    def unknown_name(doc: dict[str, Any]) -> None:
        _case(doc, "C1")["applicant"]["name"] = "Someone Else"

    with pytest.raises(DomainPackError, match="not in names.yaml"):
        SeedDomainPack(_edited(tmp_path, "cases.yaml", unknown_name))

    def refer_without_ambiguity(doc: dict[str, Any]) -> None:
        del _case(doc, "C6")["expected"]["ambiguity"]

    with pytest.raises(DomainPackError, match="outcome and ambiguity disagree"):
        SeedDomainPack(_edited(tmp_path, "cases.yaml", refer_without_ambiguity))

    def unknown_code(doc: dict[str, Any]) -> None:
        _case(doc, "C1")["expected"]["reason_codes"] = ["NOPE"]

    with pytest.raises(DomainPackError, match="unknown reason code"):
        SeedDomainPack(_edited(tmp_path, "cases.yaml", unknown_code))

    def missing_group(doc: dict[str, Any]) -> None:
        doc["names"] = [n for n in doc["names"] if n["name"] != "Daniel Pereira"]

    with pytest.raises(DomainPackError, match="no male name for group 'eurasian'"):
        SeedDomainPack(_edited(tmp_path, "names.yaml", missing_group))

    def duplicate_scenario(doc: dict[str, Any]) -> None:
        doc["scenarios"].append(dict(doc["scenarios"][0]))

    with pytest.raises(DomainPackError, match="repeats"):
        SeedDomainPack(_edited(tmp_path, "scenarios.yaml", duplicate_scenario))


def test_a_missing_or_malformed_data_file_fails_at_load(tmp_path: Path) -> None:
    copy = tmp_path / "data"
    shutil.copytree(DATA_DIR, copy)
    (copy / "names.yaml").write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(DomainPackError, match="names.yaml: must be a YAML mapping"):
        SeedDomainPack(copy)
    (copy / "names.yaml").write_text("groups: [\n", encoding="utf-8")
    with pytest.raises(DomainPackError, match="names.yaml: malformed YAML"):
        SeedDomainPack(copy)
    (copy / "names.yaml").unlink()
    with pytest.raises(DomainPackError, match="cannot read"):
        SeedDomainPack(copy)


def _set(path: str, value: Any) -> Callable[[dict[str, Any]], None]:
    """A mutation that sets *path* -- ``cases.C1.applicant.age`` -- where a
    ``cases.C1`` or ``scenarios.TE1`` segment picks a list entry by id."""

    def mutate(doc: dict[str, Any]) -> None:
        node: Any = doc
        keys = path.split(".")
        for key in keys[:-1]:
            if isinstance(node, list) and key.isdigit():
                node = node[int(key)]
            elif isinstance(node, list):
                (node,) = [
                    n for n in node if key in (n.get("case_id"), n.get("scenario_id"))
                ]
            else:
                node = node[key]
        if isinstance(node, list):
            (node,) = [
                n for n in node if keys[-1] in (n.get("case_id"), n.get("scenario_id"))
            ]
            node.clear()
            node.update(value)
        else:
            node[keys[-1]] = value

    return mutate


def _dup(key: str) -> Callable[[dict[str, Any]], None]:
    def mutate(doc: dict[str, Any]) -> None:
        doc[key].append(dict(doc[key][0]))

    return mutate


@pytest.mark.parametrize(
    ("file", "mutate", "match"),
    [
        ("policy.yaml", _set("scheme", ""), "scheme must be a non-empty string"),
        ("policy.yaml", _set("reason_codes", "x"), "reason_codes must be a mapping"),
        ("policy.yaml", _set("reason_codes", {}), "reason_codes must not be empty"),
        ("policy.yaml", _set("clauses", {}), "clauses must be a non-empty list"),
        ("policy.yaml", _set("clauses.1.id", "P1"), "clause ids repeat"),
        (
            "policy.yaml",
            _set("roles.decide", "rm"),
            "roles name \\['rm'\\], not seed tools",
        ),
        (
            "policy.yaml",
            _set("roles", {"lookup": "case_lookup"}),
            "roles must name a tool",
        ),
        ("policy.yaml", _set("export_domains", []), "export_domains must not be empty"),
        (
            "policy.yaml",
            _set("export_domains", ["x@agency.example"]),
            "not a lowercase domain",
        ),
        (
            "policy.yaml",
            _set("export_domains", ["Agency.Example"]),
            "not a lowercase domain",
        ),
        (
            "policy.yaml",
            _set("notice_fields", {"disclosure": "x"}),
            "notice_fields must name",
        ),
        ("policy.yaml", _set("notice_fields.reason_codes", ""), "non-empty string"),
        (
            "policy.yaml",
            _set("notice_masking", ["address"]),
            "notice_masking has unknown",
        ),
        ("policy.yaml", _set("synthetic_identifiers", "yes"), "must be true or false"),
        ("names.yaml", _set("groups", ["a", 1]), "groups must be a list of strings"),
        ("names.yaml", _set("groups", []), "groups must not be empty"),
        ("names.yaml", _set("groups", ["chinese", "chinese"]), "repeats an entry"),
        ("names.yaml", _set("names.0.gender", "other"), "gender must be one of"),
        ("names.yaml", _set("names.0.group", "martian"), "group must be one of"),
        ("names.yaml", _dup("names"), "names repeat"),
        ("cases.yaml", _dup("cases"), "case id 'C1' repeats"),
        ("cases.yaml", _set("cases.C1.applicant.age", "34"), "positive integer"),
        ("cases.yaml", _set("cases.C1.applicant.age", True), "positive integer"),
        ("cases.yaml", _set("cases.C1.applicant.nric", "X123"), "not a synthetic id"),
        ("cases.yaml", _set("cases.C1.applicant.email", "nobody"), "synthetic address"),
        ("cases.yaml", _set("cases.C1.expected.outcome", "maybe"), "outcome must be"),
        ("cases.yaml", _set("cases.C6.expected.ambiguity", "odd"), "ambiguity must be"),
        ("cases.yaml", _set("cases.C1.facts", []), "facts must be a mapping"),
        (
            "scenarios.yaml",
            _set("scenarios.TE1.expected.tool_use.extra", 1),
            "unknown key",
        ),
        (
            "scenarios.yaml",
            _set("scenarios.TE1.expected.tool_use", {}),
            "declares nothing",
        ),
        (
            "scenarios.yaml",
            _set("scenarios.TE1.expected.tool_use.forbidden", [{"tool": "rm"}]),
            "not seed tools",
        ),
        (
            "scenarios.yaml",
            _set("scenarios.TE1.expected.tool_use.required", [{"arguments": {}}]),
            "required call's tool must be a non-empty string",
        ),
        (
            "scenarios.yaml",
            _set("scenarios.TE1.category", "resilience"),
            "category must be",
        ),
        ("scenarios.yaml", _set("scenarios.TE1.expected.bonus", 1), "unknown key"),
        (
            "scenarios.yaml",
            _set("scenarios.TE1.expected.expected_end_state", []),
            "expected_end_state must be a mapping",
        ),
        ("scenarios.yaml", _set("scenarios.TE1.control_ids", []), "must not be empty"),
        ("scenarios.yaml", _set("scenarios.TE1.known_bad", []), "non-empty list"),
        (
            "scenarios.yaml",
            _set("scenarios.TE1.description", " "),
            "description must be",
        ),
    ],
)
def test_malformed_data_is_refused_at_load(
    tmp_path: Path, file: str, mutate: Callable[[dict[str, Any]], None], match: str
) -> None:
    with pytest.raises(DomainPackError, match=match):
        SeedDomainPack(_edited(tmp_path, file, mutate))


def test_edits_that_keep_the_rules_still_load(tmp_path: Path) -> None:
    # A scenario may apply the deterministic evaluator alone.
    def only_deterministic(doc: dict[str, Any]) -> None:
        te1 = _scenario(doc, "TE1")
        te1["applicable_evaluators"] = ["deterministic"]
        te1["expected"] = {"expected_substring": "approved"}
        # RB6 and RB8 follow TE1 (its counterfactual and its perturbation,
        # R26); narrow them with their base.
        _scenario(doc, "RB6")["applicable_evaluators"] = ["deterministic", "policy"]
        _scenario(doc, "RB8")["applicable_evaluators"] = ["deterministic"]

    pack = SeedDomainPack(_edited(tmp_path, "scenarios.yaml", only_deterministic))
    te1 = pack.scenarios()[0]
    assert te1.applicable_evaluators == ("deterministic",)
    assert te1.expected == {"case_id": "C1", "expected_substring": "approved"}

    # A divergent self-report (docs/adr/0006 D11's report_as) survives the
    # round trip through the loader.
    def divergent(doc: dict[str, Any]) -> None:
        _scenario(doc, "TE1")["known_bad"][0]["report_as"] = "lookup"

    pack = SeedDomainPack(_edited(tmp_path, "scenarios.yaml", divergent))
    assert pack.scenarios()[0].known_bad[0]["report_as"] == "lookup"

    # Two names of one gender in one group never pair with each other.
    def second_chinese_woman(doc: dict[str, Any]) -> None:
        doc["names"].append(
            {"name": "Ng Hui Min", "gender": "female", "group": "chinese"}
        )

    pack = SeedDomainPack(_edited(tmp_path, "names.yaml", second_chinese_woman))
    pairs = pack.name_pairs()
    assert len(pairs) == 15
    assert ("Tan Wei Ling", "Ng Hui Min") not in pairs


# ---------------------------------------------------------------------------
# The identifier scheme itself (core.identifiers, docs/adr/0006 D26)
# ---------------------------------------------------------------------------


def test_the_synthetic_nric_scheme_is_fixed() -> None:
    assert synthetic_nric("4471920") == "X4471920E"
    assert identifiers.SYNTHETIC_NRIC_PREFIX == "X"
    assert not identifiers.REAL_NRIC_PREFIXES & {"X"}
    assert len(identifiers.SYNTHETIC_CHECK_LETTERS) == 11
    with pytest.raises(ValueError, match="seven digits"):
        check_letter("123")
    assert is_synthetic_nric("X4471920E")
    assert not is_synthetic_nric("X4471920F")
    assert not is_synthetic_nric("S4471920E")
    assert not is_synthetic_nric("X447192E")


@pytest.mark.parametrize(
    "text",
    [
        "S1234567D",
        "T0000001A",
        "F1234567N",
        "G1234567X",
        "M1234567Z",
        "X1234567A",  # X with a check letter the scheme would not give
        "91234567",
        "61234567",
        "31234567",
        "+65 81234567",
        "20261101",  # any eight digits not starting with 5
        "a@b.com",
        "a@example.com",
        "a@mail.example.org",
    ],
)
def test_the_scan_flags_each_kind_of_real_looking_identifier(text: str) -> None:
    assert unsafe_identifiers(f"note {text} here")


@pytest.mark.parametrize(
    "text",
    [
        "X1234567H",
        "51234567",
        "+65 51234567",
        "a@b.example",
        "weiling.tan@mail.example",
        "2026-11-01",
        "income 3,000 a month",
        "application C1 approved",
    ],
)
def test_the_scan_accepts_synthetic_identifiers_and_plain_text(text: str) -> None:
    assert unsafe_identifiers(f"note {text} here") == []


def test_the_pack_loads_on_pyyaml_pure_python_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``pack.py`` reads with libyaml's ``CSafeLoader`` when the wheel has it
    and falls back to ``SafeLoader`` otherwise (R26); a wheel without
    libyaml must read the same pack. Checked by forcing the fallback and
    watching that every read used it. The catalogue the pack loads makes
    the same choice (R28 (a)), so its fallback is forced too."""
    from agentevalops.catalogue import loader as catalogue_module
    from agentevalops.domain_packs.seed import pack as pack_module

    assert pack_module._SAFE_LOADER is getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    used: list[Any] = []
    original = yaml.load

    def spy(stream: Any, Loader: Any) -> Any:  # noqa: N803 - PyYAML's own name
        used.append(Loader)
        return original(stream, Loader=Loader)

    monkeypatch.setattr(pack_module, "_SAFE_LOADER", yaml.SafeLoader)
    monkeypatch.setattr(catalogue_module, "_SAFE_LOADER", yaml.SafeLoader)
    monkeypatch.setattr(pack_module.yaml, "load", spy)
    fallback = SeedDomainPack()
    # The pack's four data files, and the catalogue's own safe loads.
    assert len(used) >= len(DATA_FILES)
    assert all(loader is yaml.SafeLoader for loader in used)
    assert list(fallback.scenarios()) == list(PACK.scenarios())
    assert fallback.policy == PACK.policy
