"""Control coverage: which catalogue controls a run's executed, evaluated
scenarios evidence (docs/adr/0007 D5, D6; ROADMAP R28).

``control_coverage`` is a join, not a score: the plan's selection, each
scenario's pass^k from the summary, the scenarios' descriptors and the
catalogue. Every control gets one of five states --

- ``covered``: every selected scenario that cites it has a pass^k verdict;
- ``partly_covered``: some do, and some were not evaluated;
- ``uncovered``: selected citing scenarios exist and none has a verdict;
- ``not_in_plan``: citing scenarios exist and the plan selected none;
- ``undeclared``: no scenario in the pack cites it, or nothing was
  declared --

and beside it each citing scenario's own verdict. A failing scenario still
covers: coverage says the control was tested, the verdict says how it went,
and the two are never folded into one word. Nothing is aggregated per
control; that would be the composite ADR-0005 D2 forbids.

The result is ``controls.json``'s content. It has no timestamp of its own,
so two computations from one run are identical. R28 (b) has the writer
call this; until then it is library code.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agentevalops.catalogue import Catalogue, Control
from agentevalops.core.protocols import DomainPack
from agentevalops.core.schemas import (
    EvaluationResult,
    PassKResult,
    Ref,
    RunPlan,
    RunSummary,
)

CONTROLS_VERSION = "1"

#: The five states, in the order a reader meets them.
COVERAGE_STATES: tuple[str, ...] = (
    "covered",
    "partly_covered",
    "uncovered",
    "not_in_plan",
    "undeclared",
)

#: Why a selected scenario has no pass^k when the summary has no entry for
#: it at all: the run stopped before it.
NOT_RUN = "not run"
#: Why a citing scenario outside the plan's selection was left out when the
#: plan records no exclusion for it: the run config's own subset.
NOT_REQUESTED = "not requested by the run config"

#: The descriptor fields copied for each selected scenario.
_DESCRIPTOR_FIELDS = ("category", "outcome_group", "variant")


def scenario_descriptors(pack: DomainPack) -> dict[str, dict[str, Any]]:
    """Every scenario of *pack*, selected or not, as coverage reads it:
    ``category``, ``outcome_group``, ``variant`` and ``control_ids``, keyed
    by scenario id in the pack's order (docs/adr/0007 D5, decision 5 of
    2026-11-02). Built from the pack itself, so a benchmark narrowed to the
    plan's selection still yields the whole pack."""
    return {
        s.scenario_id: {
            "category": s.category,
            "outcome_group": s.outcome_group,
            "variant": dict(s.variant),
            "control_ids": list(s.control_ids),
        }
        for s in pack.scenarios()
    }


def _locator_key(locator: str) -> tuple[tuple[int, int | str], ...]:
    """Natural order: 1.2.1 before 1.10.1; names after numbers."""
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part) for part in locator.split(".")
    )


def _ordered_controls(catalogue: Catalogue) -> list[Control]:
    source_order = {source_id: n for n, source_id in enumerate(catalogue.sources)}
    return sorted(
        catalogue.controls.values(),
        key=lambda c: (
            source_order.get(c.source, len(source_order)),
            _locator_key(c.locator),
        ),
    )


def _ref(ref: Ref) -> dict[str, Any]:
    return {
        "stream": ref.stream,
        "task_id": None if ref.task_id is None else str(ref.task_id),
        "repetition": ref.repetition,
        "index": ref.index,
    }


def _evidence_of(
    summary: RunSummary, scenario_id: str
) -> tuple[list[dict[str, Any]], list[int]]:
    """The refs a scenario's evaluations cite across its repetitions, each
    once, in stream then repetition then index order; and the repetitions
    whose end state a state-based evaluation judged."""
    refs: dict[Ref, None] = {}
    end_states: set[int] = set()
    for result in summary.task_results:
        if str(result.task_id) != scenario_id:
            continue
        evaluations: Sequence[EvaluationResult] = result.evaluations or [
            result.evaluation
        ]
        for evaluation in evaluations:
            refs.update(dict.fromkeys(evaluation.refs))
            if evaluation.evaluator_kind == "state_based" and evaluation.evaluated:
                end_states.add(result.repetition)
    ordered = sorted(refs, key=lambda r: (r.stream, r.repetition, r.index))
    return [_ref(r) for r in ordered], sorted(end_states)


def _scenario_entry(
    descriptor: Mapping[str, Any], result: PassKResult | None
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        field: descriptor.get(field) for field in _DESCRIPTOR_FIELDS
    }
    entry["control_ids"] = list(descriptor.get("control_ids") or ())
    if result is None:
        entry.update(
            k=0,
            passed=0,
            failed=0,
            not_evaluated=0,
            pass_k=None,
            not_evaluated_cause=NOT_RUN,
        )
    else:
        entry.update(
            k=result.k,
            passed=result.passed,
            failed=result.failed,
            not_evaluated=result.not_evaluated,
            pass_k=result.pass_k,
            not_evaluated_cause=result.not_evaluated_cause,
        )
    return entry


def _state(citing: list[str], verdicts: list[dict[str, Any]]) -> str:
    if not citing:
        return "undeclared"
    if not verdicts:
        return "not_in_plan"
    judged = sum(v["pass_k"] is not None for v in verdicts)
    if judged == len(verdicts):
        return "covered"
    return "partly_covered" if judged else "uncovered"


def _sources(catalogue: Catalogue) -> list[dict[str, Any]]:
    return [
        {
            "id": s.id,
            "title": s.title,
            "publisher": s.publisher,
            "version": s.version,
            "published": s.published.isoformat(),
            "url": s.url,
            "sha256": s.sha256,
            "retrieved_on": s.retrieved_on.isoformat(),
        }
        for s in catalogue.sources.values()
    ]


def control_coverage(
    catalogue: Catalogue,
    plan: RunPlan | None,
    scenarios: Mapping[str, Mapping[str, Any]],
    summary: RunSummary,
    *,
    plan_sha256: str | None = None,
) -> dict[str, Any]:
    """``controls.json`` for one run (docs/adr/0007 D5, D6).

    *scenarios* maps every scenario id in the pack -- selected or not -- to
    its descriptors: ``control_ids``, ``category``, ``outcome_group`` and
    ``variant``, as the benchmark's task metadata carries them. *plan* is
    ``None`` for a run that declared nothing; every control is then
    ``undeclared``. *plan_sha256* is the hash registered before the run, so
    the file is tied to the plan it was computed from.
    """
    declared = plan is not None
    selected: tuple[str, ...] = plan.selected if plan is not None else ()
    exclusions = (
        {e.scenario_id: e.reason for e in plan.excluded} if plan is not None else {}
    )
    by_task = {r.task_id: r for r in summary.pass_k}

    scenario_entries = {
        scenario_id: _scenario_entry(
            scenarios.get(scenario_id, {}), by_task.get(scenario_id)
        )
        for scenario_id in selected
    }
    evidence = {
        scenario_id: _evidence_of(summary, scenario_id) for scenario_id in selected
    }

    controls: list[dict[str, Any]] = []
    for control in _ordered_controls(catalogue):
        citing = (
            [
                scenario_id
                for scenario_id, descriptor in scenarios.items()
                if control.id in (descriptor.get("control_ids") or ())
            ]
            if declared
            else []
        )
        verdicts: list[dict[str, Any]] = []
        excluded: dict[str, str] = {}
        not_evaluated: dict[str, str] = {}
        for scenario_id in citing:
            if scenario_id not in scenario_entries:
                excluded[scenario_id] = exclusions.get(scenario_id, NOT_REQUESTED)
                continue
            entry = scenario_entries[scenario_id]
            if entry["pass_k"] is None:
                not_evaluated[scenario_id] = entry["not_evaluated_cause"]
            refs, end_states = evidence[scenario_id]
            verdicts.append(
                {
                    "scenario_id": scenario_id,
                    "pass_k": entry["pass_k"],
                    "k": entry["k"],
                    "passed": entry["passed"],
                    "failed": entry["failed"],
                    "not_evaluated": entry["not_evaluated"],
                    "cause": entry["not_evaluated_cause"],
                    "refs": refs,
                    "end_states": end_states,
                }
            )
        item: dict[str, Any] = {
            "id": control.id,
            "source": control.source,
            "kind": control.kind,
            "label": control.label,
            "state": _state(citing, verdicts),
            "declared_by": citing,
            "excluded": excluded,
            "not_evaluated": not_evaluated,
            "verdicts": verdicts,
        }
        if control.kind == "process":
            item.update(
                classification=control.classification,
                principle=control.principle,
                evidence=list(control.evidence),
            )
        controls.append(item)

    return {
        "controls_version": CONTROLS_VERSION,
        "run_id": str(summary.run_id),
        "plan_sha256": plan_sha256,
        "declared": declared,
        "sources": _sources(catalogue),
        "catalogue": {
            "files": [{"name": name, "sha256": sha} for name, sha in catalogue.files]
        },
        "scenarios": scenario_entries,
        "controls": controls,
    }
