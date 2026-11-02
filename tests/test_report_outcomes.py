"""The report grouped by MGF-Agentic §1.2.2's kinds of harm (R26 slice 5):
five groups in the section's order, per-scenario rows, no composite, an
explicit line for a group with nothing executed, counterfactual pairs
and the scripted-trajectory note on perturbation rows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import fast_yaml
from reference_bundles import ReferenceBundles
from typer.testing import CliRunner

from agentevalops.cli import app
from agentevalops.core.schemas import (
    NEGATIVE_OUTCOMES,
    AgentOutput,
    EvaluationResult,
    ResourceLimits,
    RunConfig,
    RunSummary,
    TaskRunResult,
    TerminationReason,
)
from agentevalops.core.types import BACKEND_LOCAL, AgentId, RunId, TaskId
from agentevalops.domain_packs.seed import SeedDomainPack
from agentevalops.reports.markdown import OUTCOME_LABELS, outcome_section, render_report

CONFIG = Path("configs/agentic_baseline_mock.yaml")
SYSTEM = Path("configs/seed_system.yaml")


def _config(agent: str = "mock-agent-v1") -> RunConfig:
    return RunConfig(
        run_id=RunId("r"),
        agent_id=AgentId(agent),
        backend_id=BACKEND_LOCAL,
        resource_limits=ResourceLimits(),
    )


def _result(
    task_id: str, *, passed: bool, evaluated: bool = True, failed: tuple[str, ...] = ()
) -> TaskRunResult:
    evaluations = [EvaluationResult(kind, kind, 0.0, False) for kind in failed] or [
        EvaluationResult(
            "e", "deterministic", 1.0 if passed else 0.0, passed, evaluated=evaluated
        )
    ]
    return TaskRunResult(
        task_id=TaskId(task_id),
        output=AgentOutput(
            success=True, termination_reason=TerminationReason.COMPLETED
        ),
        evaluation=EvaluationResult(
            "v", "verdict", 1.0 if passed else 0.0, passed, evaluated=evaluated
        ),
        event_count=0,
        evaluations=evaluations,
    )


INFO: dict[str, dict[str, Any]] = {
    "TE1": {"category": "task_execution", "outcome_group": "erroneous_action"},
    "PC2": {"category": "policy_compliance", "outcome_group": "unauthorised_action"},
    "RB6": {
        "category": "robustness",
        "outcome_group": "biased_action",
        "variant": {
            "kind": "counterfactual",
            "base": "TE1",
            "attribute": "name",
            "from": "Tan Wei Ling",
            "to": "Priya Krishnan",
            "unchanged": {"gender": "female", "age": 34},
        },
    },
    "RB8": {
        "category": "robustness",
        "outcome_group": "erroneous_action",
        "variant": {
            "kind": "perturbation",
            "base": "TE1",
            "family": "typos_punctuation",
        },
    },
}


def test_the_five_groups_render_in_order_with_rows_and_an_explicit_empty_line() -> None:
    summary = RunSummary(
        run_id=RunId("r"),
        task_results=[
            _result("TE1", passed=True),
            _result("PC2", passed=False, failed=("policy", "state_based")),
            _result("RB6", passed=True),
            _result("RB8", passed=True),
        ],
    )
    text = "\n".join(outcome_section(_config(), summary, INFO))
    headings = [line for line in text.splitlines() if line.startswith("### ")]
    assert headings == [f"### {OUTCOME_LABELS[g]}" for g in NEGATIVE_OUTCOMES]
    assert "| TE1 | 0 | passed | — |" in text
    assert "| PC2 | 0 | failed | policy, state_based |" in text
    # Two groups ran nothing: the report says so instead of hiding them.
    assert text.count("No scenario executed in this group.") == 2
    breaches = text.split("### Data breaches")[1].split("### ")[0]
    assert "No scenario executed in this group." in breaches
    # No composite: nothing is added up across groups.
    assert "nothing is added up across groups" in text
    for word in ("Total", "overall", "score"):
        assert word not in text.replace("Failed evaluators", "")


def test_a_counterfactual_row_reports_its_pair_and_whether_it_differs() -> None:
    same = RunSummary(
        run_id=RunId("r"),
        task_results=[_result("TE1", passed=True), _result("RB6", passed=True)],
    )
    text = "\n".join(outcome_section(_config(), same, INFO))
    assert (
        "pair with TE1: name Tan Wei Ling → Priya Krishnan (gender female, age 34 "
        "unchanged); base passed, variant passed: no difference"
    ) in text
    differs = RunSummary(
        run_id=RunId("r"),
        task_results=[
            _result("TE1", passed=True),
            _result("RB6", passed=False, failed=("policy",)),
        ],
    )
    text = "\n".join(outcome_section(_config(), differs, INFO))
    assert "base passed, variant failed: differs" in text
    alone = RunSummary(run_id=RunId("r"), task_results=[_result("RB6", passed=True)])
    assert "base not run, variant passed: differs" in "\n".join(
        outcome_section(_config(), alone, INFO)
    )
    unjudged = RunSummary(
        run_id=RunId("r"), task_results=[_result("RB6", passed=False, evaluated=False)]
    )
    assert "not evaluated" in "\n".join(outcome_section(_config(), unjudged, INFO))


def test_a_perturbation_row_on_a_scripted_run_says_it_is_not_agent_evidence() -> None:
    """Scripted is the run's runner_id (docs/adr/0007 D4), never the agent's
    name: an agent called "mock-..." on a model-backed runner is not
    scripted, and a scripted run under any agent name is."""
    summary = RunSummary(run_id=RunId("r"), task_results=[_result("RB8", passed=True)])
    named_mock = "\n".join(outcome_section(_config("mock-agent-v1"), summary, INFO))
    assert "scripted trajectory" not in named_mock
    scripted = "\n".join(
        outcome_section(_config("any-agent"), summary, INFO, scripted=True)
    )
    assert (
        "TE1's text under typos_punctuation; run on a scripted trajectory" in scripted
    )
    assert "not evidence that an agent withstood the noise (R30)" in scripted
    real = "\n".join(outcome_section(_config("openai-gpt"), summary, INFO))
    assert "TE1's text under typos_punctuation" in real
    assert "scripted" not in real


def test_the_report_has_the_section_only_when_tasks_carry_groups() -> None:
    summary = RunSummary(run_id=RunId("r"), task_results=[_result("TE1", passed=True)])
    with_groups = render_report(_config(), summary, [], "c.yaml", INFO)
    assert "## Outcomes by kind of harm" in with_groups
    assert with_groups.index("## Task Results") < with_groups.index(
        "## Outcomes by kind of harm"
    )
    assert with_groups.index("## Outcomes by kind of harm") < with_groups.index(
        "## Policy Checks"
    )
    without = render_report(_config(), summary, [], "c.yaml")
    assert "## Outcomes by kind of harm" not in without
    assert "## Policy Checks" in without


def test_the_shipped_run_fills_all_five_groups(
    reference_bundles: ReferenceBundles,
) -> None:
    """R26's exit criterion: the report shows all five groups, each with at
    least one executed scenario."""
    # The shared seed run (its exit code 0 is checked where it is written).
    out = reference_bundles["seed"].path
    report = (out / "report.md").read_text(encoding="utf-8")
    section = report.split("## Outcomes by kind of harm")[1].split("## Policy Checks")[
        0
    ]
    assert "No scenario executed in this group." not in section
    pack = SeedDomainPack()
    for group in NEGATIVE_OUTCOMES:
        block = section.split(f"### {OUTCOME_LABELS[group]}")[1].split("### ")[0]
        expected = [s.scenario_id for s in pack.scenarios() if s.outcome_group == group]
        assert expected, group
        for sid in expected:
            assert f"| {sid} | 0 | passed |" in block, (group, sid)
    assert "pair with TE1: name Tan Wei Ling → Priya Krishnan" in section
    assert "run on a scripted trajectory" in section
    # summary.json is untouched by the section (format 0.5, content only).
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert "outcome_group" not in summary["task_results"][0]
    assert "scenario_info" not in summary


def test_a_run_of_one_scenario_reports_the_empty_groups(tmp_path: Path) -> None:
    doc = fast_yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    doc["system"] = str(SYSTEM.resolve())  # the copy lives in tmp_path (R27)
    doc["benchmark_options"]["scenarios"] = ["RB1"]
    doc["run_id"] = "one"
    config = tmp_path / "one.yaml"
    config.write_text(fast_yaml.safe_dump(doc), encoding="utf-8")
    out = tmp_path / "bundle"
    result = CliRunner().invoke(
        app, ["run", "--config", str(config), "--output", str(out)]
    )
    assert result.exit_code == 0, result.output
    section = (
        (out / "report.md")
        .read_text(encoding="utf-8")
        .split("## Outcomes by kind of harm")[1]
    )
    assert section.count("No scenario executed in this group.") == 4
    assert (
        "| RB1 | 0 | passed |"
        in section.split("### Disruption to connected systems")[1]
    )
