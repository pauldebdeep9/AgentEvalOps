"""Synthetic inputs for the coverage computation and the evidence-report
renderer (docs/adr/0007 D5-D7; ROADMAP R28 (a) slice 4): plans, summaries
and catalogues built in code, so the library is tested before the writer
calls it."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import fast_yaml

from agentevalops.catalogue import Catalogue, load_catalogue
from agentevalops.core.schemas import (
    AgentOutput,
    BusinessMetric,
    CategoryPassK,
    DeclaredTool,
    EvaluationResult,
    PassKResult,
    PlanExclusion,
    PlanThreshold,
    Ref,
    RunPlan,
    RunSummary,
    SystemDeclaration,
    TaskRunResult,
    TerminationReason,
)
from agentevalops.core.types import RunId, TaskId

RUN_ID = "run-synthetic"
CREATED_AT = "2001-02-03T04:05:06+00:00"
INTENDED_USE = "Decide grant applications | and refer the unclear ones"
OUT_OF_SCOPE = ("Appeals against a decision", "Cases outside the grant scheme")
METRIC = BusinessMetric("referral_rate", "Share of cases referred to an officer")


def plan(
    selected: tuple[str, ...],
    excluded: tuple[tuple[str, str], ...] = (),
    *,
    requested: tuple[str, ...] | None = None,
) -> RunPlan:
    system = SystemDeclaration(
        system_id="grants-agent",
        pack_id="seed",
        involvement="human_approves",
        tools=(DeclaredTool("case_lookup", "read"),),
        intended_use=INTENDED_USE,
        out_of_scope=OUT_OF_SCOPE,
        business_metrics=(METRIC,),
    )
    return RunPlan(
        plan_version="1",
        run_id=RUN_ID,
        config_name="synthetic",
        created_at=CREATED_AT,
        platform_version="0",
        system=system,
        system_path="system.yaml",
        pack_id="seed",
        pack_version="0.1.0",
        scenarios_sha256="0" * 64,
        repetitions=2,
        seed=0,
        evaluators=("tool_use", "state_based"),
        requested=requested,
        selected=selected,
        excluded=tuple(PlanExclusion(s, reason) for s, reason in excluded),
        seeds={s: (1, 2) for s in selected},
        thresholds=(PlanThreshold("tool_calling", 1.0, "pack", "hard", "pack"),),
        uncovered_tools=(),
    )


def _evaluation(
    kind: str, passed: bool, evaluated: bool, refs: list[Ref], cause: Any = None
) -> EvaluationResult:
    return EvaluationResult(
        evaluator_id=f"{kind}-v1",
        evaluator_kind=kind,
        score=1.0 if passed else 0.0,
        passed=passed,
        evaluated=evaluated,
        not_evaluated_cause=cause,
        refs=refs,
    )


def summary(outcomes: dict[str, str], k: int = 2) -> RunSummary:
    """A summary where each scenario's every repetition ``passed``,
    ``failed`` or was ``not_evaluated`` (cause ``infrastructure``). Each
    evaluated run cites two tool-call records and a trace event, and its
    state-based evaluation judged the end state."""
    results: list[TaskRunResult] = []
    pass_k: list[PassKResult] = []
    for scenario_id, outcome in outcomes.items():
        for repetition in range(k):
            task = TaskId(scenario_id)
            evaluated = outcome != "not_evaluated"
            passed = outcome == "passed"
            refs = [
                Ref("tool_calls", task, repetition, 4),
                Ref("tool_calls", task, repetition, 2),
                Ref("trace", task, repetition, 7),
            ]
            evaluations = [
                _evaluation(
                    "tool_use",
                    passed,
                    evaluated,
                    refs if evaluated else [],
                    None if evaluated else "infrastructure",
                ),
                _evaluation(
                    "state_based",
                    passed,
                    evaluated,
                    [refs[0]] if evaluated else [],
                    None if evaluated else "infrastructure",
                ),
            ]
            results.append(
                TaskRunResult(
                    task_id=task,
                    output=AgentOutput(True, TerminationReason.COMPLETED, "done"),
                    evaluation=evaluations[0],
                    event_count=3,
                    evaluations=evaluations,
                    repetition=repetition,
                )
            )
        pass_k.append(
            PassKResult(
                task_id=scenario_id,
                k=k,
                passed=k if outcome == "passed" else 0,
                failed=k if outcome == "failed" else 0,
                not_evaluated=k if outcome == "not_evaluated" else 0,
                pass_k=None if outcome == "not_evaluated" else outcome == "passed",
                not_evaluated_cause=(
                    "infrastructure" if outcome == "not_evaluated" else None
                ),
            )
        )
    passed = sum(o == "passed" for o in outcomes.values())
    return RunSummary(
        run_id=RunId(RUN_ID),
        task_results=results,
        pass_k=pass_k,
        pass_k_by_category=[
            CategoryPassK(
                category="tool_calling",
                k=k,
                scenarios=len(outcomes),
                passed=passed,
                failed=sum(o == "failed" for o in outcomes.values()),
                not_evaluated=sum(o == "not_evaluated" for o in outcomes.values()),
                pass_k_rate=passed / len(outcomes) if outcomes else 0.0,
                not_evaluated_causes=(
                    {"infrastructure": skipped}
                    if (skipped := sum(o == "not_evaluated" for o in outcomes.values()))
                    else {}
                ),
            )
        ],
    )


def catalogue(tmp_path: Path, citations: int = 7) -> Catalogue:
    """A small catalogue: source ``doc`` with sections ``doc:1`` to
    ``doc:<citations>``, and source ``tf`` with one process entry of each
    class under two principles."""
    doc = {
        "source": _source("doc", "A Test Document"),
        "controls": [
            {
                "id": f"doc:{n}",
                "kind": "section",
                "locator": str(n),
                "label": f"Section {n} in our words",
            }
            for n in range(1, citations + 1)
        ],
    }
    entries = [
        ("1.1.1", 1, "direct", ["scenarios", "artefact:test_plan.json#thresholds"]),
        ("1.2.1", 1, "partial", ["scenarios"]),
        ("1.10.1", 1, "supporting", ["artefact:test_plan.json#system.intended_use"]),
        ("2.1.1", 2, "later", []),
        ("2.2.1", 2, "organisational", []),
        ("2.3.1", 2, "traditional-only", []),
    ]
    tf = {
        "source": _source("tf", "Another Test Framework"),
        "principles": {1: "First principle", 2: "Second principle"},
        "process_counts": {
            "total": 6,
            "direct": 1,
            "partial": 1,
            "supporting": 1,
            "later": 1,
            "organisational": 1,
            "traditional-only": 1,
        },
        "controls": [
            {
                "id": f"tf:{locator}",
                "kind": "process",
                "locator": locator,
                "principle": principle,
                "scope": "both",
                "label": f"Check {locator} in our words",
                "classification": klass,
                **({"available_from": "v0.7"} if klass == "later" else {}),
                **({"evidence": evidence} if evidence else {}),
            }
            for locator, principle, klass, evidence in entries
        ],
    }
    paths = []
    for name, content in (("a-doc.yaml", doc), ("b-tf.yaml", tf)):
        path = tmp_path / name
        path.write_text(fast_yaml.safe_dump(content, sort_keys=False), encoding="utf-8")
        paths.append(path)
    return load_catalogue(paths)


def _source(source_id: str, title: str) -> dict[str, Any]:
    return {
        "id": source_id,
        "title": title,
        "publisher": "Test Publisher",
        "version": "1.0",
        "published": "2026-01-15",
        "url": f"https://example.test/{source_id}.pdf",
        "sha256": "a" * 64,
        "retrieved_on": "2026-10-31",
        "ip_posture": "identifiers-and-own-labels",
    }


def descriptor(*control_ids: str, category: str = "tool_calling") -> dict[str, Any]:
    return {
        "category": category,
        "outcome_group": "erroneous_action",
        "variant": {},
        "control_ids": list(control_ids),
    }
