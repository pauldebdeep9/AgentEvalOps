"""Building the test plan from a system declaration and a domain pack
(docs/adr/0005 D6; ROADMAP R27; testing-framework 3.6.1, 3.7.1).

Selection, three rules in order, each exclusion recorded with its reason:

1. **Tool coverage.** Every tool a scenario's known-good and known-bad
   scripts call must be declared; else ``undeclared_tool:<names>``.
2. **Involvement.** A scenario whose environment scripts approvals needs a
   person who answers them: only ``human_approves`` keeps it; else
   ``no_approver``.
3. **Reachability.** An outcome group the declared tools cannot reach is
   out: ``data_breach`` needs a tool that is ``restricted`` or
   ``external``; ``unauthorised_action`` needs a ``write`` or
   ``irreversible`` tool; else ``unreachable:<group>``.

The declaration is also held against the pack: a declared tool the pack
models must carry the pack's permission and data class (a contradiction
is an error, not an exclusion); a declared tool the pack does not model is
recorded under ``uncovered_tools``.

Thresholds come from the pack (``Thresholded``), and the owner may set one
where the pack has none, or tighten the pack's -- a higher ``min_pass_k``,
or ``hard`` for ``comparative`` -- never loosen it. Each value's source is
recorded.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
from collections.abc import Sequence
from datetime import datetime, timezone

from agentevalops import __version__
from agentevalops.core.errors import ConfigurationError
from agentevalops.core.perturbation import derive_seed
from agentevalops.core.protocols import DomainPack, Thresholded
from agentevalops.core.schemas import (
    MGF_CATEGORIES,
    PlanExclusion,
    PlanThreshold,
    RunConfig,
    RunPlan,
    Scenario,
    SystemDeclaration,
    ToolSpec,
)
from agentevalops.core.scripts import parse_script
from agentevalops.core.types import TaskId

__all__ = ["PLAN_VERSION", "build_plan", "select"]

#: The plan document's own version, independent of the bundle format.
PLAN_VERSION: str = "1"


def _called_tools(scenario: Scenario) -> list[str]:
    steps = (*parse_script(scenario.known_good), *parse_script(scenario.known_bad))
    return sorted({step.tool for step in steps if step.kind == "call"})


def select(
    declaration: SystemDeclaration, candidates: Sequence[Scenario]
) -> tuple[list[str], list[PlanExclusion]]:
    """The three rules over *candidates*, in the candidates' order."""
    declared = {tool.name for tool in declaration.tools}
    reachable = {
        "data_breach": any(
            tool.data_class == "restricted" or tool.external
            for tool in declaration.tools
        ),
        "unauthorised_action": any(
            tool.permission in ("write", "irreversible") for tool in declaration.tools
        ),
    }
    selected: list[str] = []
    excluded: list[PlanExclusion] = []
    for scenario in candidates:
        missing = [tool for tool in _called_tools(scenario) if tool not in declared]
        if missing:
            reason = "undeclared_tool:" + ",".join(missing)
        elif (
            scenario.environment.get("approvals")
            and declaration.involvement != "human_approves"
        ):
            reason = "no_approver"
        elif (
            scenario.outcome_group in reachable
            and not reachable[scenario.outcome_group]
        ):
            reason = f"unreachable:{scenario.outcome_group}"
        else:
            selected.append(scenario.scenario_id)
            continue
        excluded.append(PlanExclusion(scenario_id=scenario.scenario_id, reason=reason))
    return selected, excluded


async def _tool_specs(
    pack: DomainPack, candidates: Sequence[Scenario]
) -> dict[str, ToolSpec]:
    """Every tool the candidates' environments expose, by name."""
    specs: dict[str, ToolSpec] = {}
    for scenario in candidates:
        environment = pack.build_environment(scenario, TaskId(scenario.scenario_id))
        for spec in await environment.list_tools():
            specs.setdefault(spec.name, spec)
    return specs


def _check_tools(
    declaration: SystemDeclaration, pack: DomainPack, specs: dict[str, ToolSpec]
) -> tuple[str, ...]:
    uncovered: list[str] = []
    for tool in declaration.tools:
        spec = specs.get(tool.name)
        if spec is None:
            uncovered.append(tool.name)
            continue
        if spec.permission != tool.permission:
            raise ConfigurationError(
                f"tools: '{tool.name}' is declared {tool.permission}, but pack "
                f"'{pack.pack_id}' runs it as {spec.permission}; the declaration "
                "contradicts the pack"
            )
        if spec.data_class != tool.data_class:
            raise ConfigurationError(
                f"tools: '{tool.name}' is declared {tool.data_class}, but pack "
                f"'{pack.pack_id}' classes its data as {spec.data_class}; the "
                "declaration contradicts the pack"
            )
    return tuple(uncovered)


def _thresholds(
    declaration: SystemDeclaration, pack: DomainPack
) -> tuple[PlanThreshold, ...]:
    pack_by = (
        {t.category: t for t in pack.thresholds()}
        if isinstance(pack, Thresholded)
        else {}
    )
    owner_by = {o.category: o for o in declaration.thresholds}
    out: list[PlanThreshold] = []
    for category in MGF_CATEGORIES:
        from_pack = pack_by.get(category)
        from_owner = owner_by.get(category)
        if from_pack is None and from_owner is None:
            continue
        if from_pack is None:
            assert from_owner is not None
            if from_owner.min_pass_k is None or from_owner.gate is None:
                raise ConfigurationError(
                    f"thresholds.{category}: pack '{pack.pack_id}' sets no "
                    "threshold for this category, so system.yaml must set both "
                    "min_pass_k and gate"
                )
            out.append(
                PlanThreshold(
                    category=category,
                    min_pass_k=from_owner.min_pass_k,
                    min_pass_k_source="owner",
                    gate=from_owner.gate,
                    gate_source="owner",
                )
            )
            continue
        threshold = PlanThreshold(
            category=category,
            min_pass_k=from_pack.min_pass_k,
            min_pass_k_source="pack",
            gate=from_pack.gate,
            gate_source="pack",
        )
        if from_owner is not None:
            if from_owner.min_pass_k is not None:
                if from_owner.min_pass_k < from_pack.min_pass_k:
                    raise ConfigurationError(
                        f"thresholds.{category}.min_pass_k {from_owner.min_pass_k} "
                        f"loosens the pack's {from_pack.min_pass_k}: the owner may "
                        "set or tighten a threshold, never loosen it "
                        "(testing-framework 3.6.1)"
                    )
                threshold = dataclasses.replace(
                    threshold,
                    min_pass_k=from_owner.min_pass_k,
                    min_pass_k_source="owner",
                )
            if from_owner.gate is not None:
                if from_pack.gate == "hard" and from_owner.gate == "comparative":
                    raise ConfigurationError(
                        f"thresholds.{category}.gate comparative loosens the pack's "
                        "hard gate: the owner may make a comparative threshold a hard "
                        "gate, never the reverse (testing-framework 3.6.1)"
                    )
                threshold = dataclasses.replace(
                    threshold, gate=from_owner.gate, gate_source="owner"
                )
        out.append(threshold)
    return tuple(out)


def _scenarios_sha256(pack: DomainPack) -> str:
    """The pack's scenarios as loaded, hashed canonically, so an edit to the
    pack after the plan shows (ROADMAP R28 records source versions in full)."""
    canonical = json.dumps(
        [dataclasses.asdict(s) for s in pack.scenarios()],
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_plan(
    declaration: SystemDeclaration,
    pack: DomainPack,
    candidates: Sequence[Scenario],
    *,
    run_config: RunConfig,
    config_name: str = "",
    evaluators: Sequence[str] = (),
    requested: Sequence[str] | None = None,
    system_path: str = "",
) -> RunPlan:
    """The plan for running *candidates* of *pack* under *declaration*.

    Raises ``ConfigurationError`` when the declaration contradicts the
    pack's tool specs or loosens a pack threshold. Selecting nothing is not
    an error here: the caller decides whether an empty run may start.
    """
    specs = asyncio.run(_tool_specs(pack, candidates))
    uncovered = _check_tools(declaration, pack, specs)
    selected, excluded = select(declaration, candidates)
    repetitions = max(1, run_config.repetitions)
    seeds: dict[str, tuple[int, ...]] = {
        scenario_id: tuple(
            derive_seed(run_config.seed, scenario_id, repetition)
            for repetition in range(repetitions)
        )
        for scenario_id in selected
    }
    return RunPlan(
        plan_version=PLAN_VERSION,
        run_id=str(run_config.run_id),
        config_name=config_name,
        created_at=datetime.now(timezone.utc).isoformat(),
        platform_version=__version__,
        system=declaration,
        system_path=system_path,
        pack_id=pack.pack_id,
        pack_version=pack.pack_version,
        scenarios_sha256=_scenarios_sha256(pack),
        repetitions=repetitions,
        seed=run_config.seed,
        evaluators=tuple(evaluators),
        requested=None if requested is None else tuple(requested),
        selected=tuple(selected),
        excluded=tuple(excluded),
        seeds=seeds,
        thresholds=_thresholds(declaration, pack),
        uncovered_tools=uncovered,
    )
