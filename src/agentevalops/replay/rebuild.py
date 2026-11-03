"""Rebuilding the benchmark a bundle's run used (docs/adr/0008 R1; R29
(b1) slice 3).

``rebuild_run`` takes a bundle's ``config.json`` and ``test_plan.json`` and
returns the run's tasks as the run built them: the benchmark from the
registry with the recorded options (``registry.build_benchmark``, the CLI's
own function), its scenario descriptors before narrowing
(``plans.benchmark_descriptors``), narrowed to the plan's selection as the
CLI narrows it, and the scenario info the report reads
(``reports.markdown.scenario_info``). It checks that the tasks are the
plan's and that the plan's seeds are the ones the run's seed derives, so a
rescore runs over the same tasks under the same seeds. Nothing is
re-implemented: a second copy of any of these would let the rebuild drift
from the run.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agentevalops.bundles.serializers import LoadError, load_run_config
from agentevalops.core.errors import ConfigurationError
from agentevalops.core.perturbation import derive_seed
from agentevalops.core.schemas import RunConfig, RunPlan, TaskSpec
from agentevalops.plans import (
    benchmark_descriptors,
    benchmark_pack,
    plan_from_document,
)
from agentevalops.registry import UnknownComponentError, build_benchmark, discover_all
from agentevalops.reports.markdown import scenario_info


class RebuildError(ConfigurationError):
    """The run cannot be rebuilt here: rescore's "cannot rescore" (exit 2)."""


@dataclass(frozen=True)
class RebuiltRun:
    """A bundle's run as rebuilt: the benchmark narrowed as the run was, its
    tasks in the run's order, every scenario's descriptor, the report's
    scenario info, the plan and the run config, and the benchmark's pack
    as the run hashed it, before narrowing (None for a benchmark without
    one)."""

    benchmark: Any
    tasks: tuple[TaskSpec, ...]
    descriptors: dict[str, dict[str, Any]]
    scenario_info: dict[str, dict[str, Any]]
    plan: RunPlan
    run_config: RunConfig
    pack: Any = None


def rebuild_run(
    config: Mapping[str, Any], plan_document: Mapping[str, Any]
) -> RebuiltRun:
    """Rebuild a run from its ``config.json`` and ``test_plan.json``.

    Raises ``RebuildError`` when the benchmark or its domain pack is not
    installed, refuses the recorded options, yields tasks the plan did not
    select, or the plan's seeds are not the ones the run's seed derives.
    """
    discover_all()
    benchmark_id = str(config.get("benchmark_id") or "")
    options = dict(config.get("benchmark_options") or {})
    try:
        run_config = load_run_config(config.get("run_config"))
        plan = plan_from_document(dict(plan_document))
    except (LoadError, KeyError, TypeError, ValueError) as exc:
        raise RebuildError(
            f"the bundle's config or plan cannot be read: {exc}"
        ) from None
    try:
        benchmark = build_benchmark(benchmark_id, options)
    except UnknownComponentError as exc:
        raise RebuildError(
            f"benchmark '{benchmark_id}' with options {options} is not installed "
            f"here: {exc}"
        ) from None
    except ConfigurationError as exc:
        raise RebuildError(str(exc)) from None
    descriptors = benchmark_descriptors(benchmark)
    pack = benchmark_pack(benchmark)
    if plan.declared:
        narrowed = getattr(benchmark, "narrowed", None)
        if narrowed is None:
            raise RebuildError(
                f"benchmark '{benchmark_id}' cannot be narrowed to a declared plan"
            )
        try:
            benchmark = narrowed(plan.selected)
        except ConfigurationError as exc:
            raise RebuildError(
                f"the installed benchmark lacks a selected scenario: {exc}"
            ) from None
    tasks = tuple(benchmark.list_tasks())
    ids = [str(t.task_id) for t in tasks]
    if ids != list(plan.selected):
        raise RebuildError(
            f"the installed benchmark's tasks {ids} are not the plan's "
            f"{list(plan.selected)}"
        )
    for task_id in ids:
        derived = tuple(
            derive_seed(plan.seed, task_id, r) for r in range(plan.repetitions)
        )
        if tuple(plan.seeds.get(task_id, ())) != derived:
            raise RebuildError(
                f"the plan's seeds for '{task_id}' are not the ones its seed "
                f"{plan.seed} derives"
            )
    if run_config.seed != plan.seed:
        raise RebuildError(
            f"config.json's seed {run_config.seed} is not the plan's {plan.seed}"
        )
    return RebuiltRun(
        benchmark=benchmark,
        tasks=tasks,
        descriptors=descriptors,
        scenario_info=scenario_info(tasks),
        plan=plan,
        run_config=run_config,
        pack=pack,
    )
