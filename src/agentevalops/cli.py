"""CLI entry point for AgentEvalOps."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import typer

if TYPE_CHECKING:
    from agentevalops.bundles.validator import BundleValidationResult
    from agentevalops.config.loader import LoadedRunConfig
    from agentevalops.core.schemas import RunSummary, TraceEvent

from agentevalops import __version__

app = typer.Typer(
    name="agentevalops",
    help="AgentEvalOps — local-first agent evaluation platform.",
    no_args_is_help=True,
)


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(f"agentevalops {__version__}")


@app.command()
def doctor() -> None:
    """Run a basic environment sanity check."""
    typer.echo(f"Python:        {sys.version}")
    typer.echo(f"agentevalops: {__version__}")
    typer.echo("Status:        OK")


@app.command()
def run(
    config: Path = typer.Option(
        Path("configs/toy_smoke.yaml"),
        "--config",
        "-c",
        help="Path to run config YAML.",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Write result bundle to this directory.",
    ),
) -> None:
    """Run an evaluation from a YAML config file."""
    import asyncio

    from agentevalops.bundles.constants import REQUIRED_BUNDLE_FILES
    from agentevalops.config.loader import load_run_config
    from agentevalops.core.errors import ConfigurationError, InfrastructureError
    from agentevalops.core.schemas import RunSummary
    from agentevalops.orchestration.local import LocalOrchestrator
    from agentevalops.registry import (
        KIND_BENCHMARK,
        KIND_EVALUATOR,
        KIND_POLICY,
        default_registry,
        discover_all,
    )
    from agentevalops.stores.local import LocalTraceStore
    from agentevalops.stores.memory import InMemoryTraceStore

    # docs/adr/0006 D24: plugins first, so a config can name a benchmark or
    # domain pack an installed package provides.
    discover_all()
    try:
        loaded = load_run_config(config)
    except ConfigurationError as exc:
        typer.echo(f"Configuration error: {exc}", err=True)
        raise typer.Exit(code=1)

    benchmark_cls = default_registry.get(KIND_BENCHMARK, loaded.benchmark_id)
    # docs/adr/0006 D25: the benchmark validates its own options.
    try:
        benchmark = benchmark_cls(**loaded.benchmark_options)
    except TypeError as exc:
        typer.echo(
            f"Configuration error: benchmark '{loaded.benchmark_id}' does not "
            f"accept these benchmark_options: {exc}",
            err=True,
        )
        raise typer.Exit(code=1)
    except ConfigurationError as exc:
        typer.echo(f"Configuration error: {exc}", err=True)
        raise typer.Exit(code=1)
    # docs/adr/0006 D13: every evaluator the run configures, in order.
    evaluators = [
        default_registry.get(KIND_EVALUATOR, name)() for name in loaded.evaluators
    ]
    policy_cls = default_registry.get(KIND_POLICY, "basic")

    try:
        runner = _build_runner(loaded, benchmark)
    except ConfigurationError as exc:
        typer.echo(f"Configuration error: {exc}", err=True)
        raise typer.Exit(code=1)

    if output is not None:
        existing = [f for f in REQUIRED_BUNDLE_FILES if (output / f).exists()]
        if existing:
            typer.echo(
                f"Output directory '{output}' already contains bundle "
                f"files: {', '.join(existing)}.",
                err=True,
            )
            raise typer.Exit(code=1)
        # Streams traces to <output>/traces.jsonl as they happen, so a hard
        # kill mid-run still leaves a readable (unsealed) partial trace even
        # if the best-effort write below can't run at all (e.g. SIGKILL).
        store: InMemoryTraceStore | LocalTraceStore = LocalTraceStore(output)
    else:
        store = InMemoryTraceStore()

    # ROADMAP R25, R26: the tasks' categories, outcome groups and variants,
    # from the benchmark's task metadata, for the CLI's summary and the
    # report's outcomes section; nothing new in summary.json.
    scenario_info = _task_info(benchmark)

    orchestrator = LocalOrchestrator(
        run_config=loaded.run_config,
        benchmark=benchmark,
        runner=runner,
        trace_store=store,
        evaluator=evaluators,
        policy_checker=policy_cls(),
        policy_spec=loaded.policy_spec,
    )

    def _write_bundle(
        summary: RunSummary,
        status: Literal["sealed", "interrupted", "partial"],
        all_events: list[TraceEvent],
    ) -> None:
        """Best-effort bundle write, used on every exit path.

        Before docs/adr/0002 (2026-08-05) only the clean-completion path
        ever reached ``BundleWriter`` -- ``KeyboardInterrupt`` and
        ``InfrastructureError`` both exited without writing anything beyond
        whatever ``LocalTraceStore`` had already flushed to
        ``traces.jsonl`` on its own. ``status`` records *why* this bundle
        isn't necessarily a normal one, so a reader (and, eventually,
        W12c's replay tooling) can tell a finalized-under-failure bundle
        apart from a complete one without guessing from the summary's
        shape alone.
        """
        if output is None:
            return
        from agentevalops.bundles.writer import BundleWriter

        # overwrite=True: `output` was already validated above to be free of
        # bundle files before the run started; the only file that may exist
        # now is traces.jsonl, written live by LocalTraceStore during the run.
        BundleWriter(output, overwrite=True).write(
            run_config=loaded.run_config,
            summary=summary,
            all_events=all_events,
            policy_spec=loaded.policy_spec,
            config_name=str(config),
            benchmark_id=loaded.benchmark_id,
            benchmark_scenario=loaded.benchmark_scenario,
            status=status,
            benchmark_options=loaded.benchmark_options,
            tool_calls=orchestrator.tool_call_records,
            end_states=orchestrator.end_states,
            scenario_info=scenario_info,
        )
        suffix = "" if status == "sealed" else f" (status: {status})"
        typer.echo(f"Bundle written to: {output.resolve()}{suffix}")

    def _degraded_summary() -> tuple[RunSummary, list[TraceEvent]]:
        """Best-effort RunSummary for a run that didn't return one.

        Only reached when ``orchestrator.run()`` itself couldn't complete
        normally -- an interrupt, or a failure outside any single task's
        scope (``LocalOrchestrator._run_task`` already catches a runner's
        own ``InfrastructureError``/``ConfigurationError`` per task and
        returns a real, fully-populated summary with the affected tasks
        marked not-evaluated instead of raising -- see docs/adr/0002).
        Counts are honestly left at zero/unknown rather than guessed;
        ``trace_event_count`` is the one thing actually knowable here, from
        whatever the trace store captured before things went wrong.
        """
        events = store.events(loaded.run_config.run_id)
        return (
            RunSummary(
                run_id=loaded.run_config.run_id,
                trace_event_count=len(events),
            ),
            events,
        )

    try:
        summary: RunSummary = asyncio.run(orchestrator.run())
    except (KeyboardInterrupt, asyncio.CancelledError):
        degraded, events = _degraded_summary()
        typer.echo("Run interrupted.", err=True)
        _write_bundle(degraded, "interrupted", events)
        raise typer.Exit(code=130)
    except ConfigurationError as exc:
        # Reachable only for a ConfigurationError raised outside a single
        # task's scope (e.g. from the scorer or policy checker) -- a
        # model-backed runner's own missing-credential ConfigurationError is
        # now caught per task by the orchestrator and comes back as a
        # normal summary with the affected task(s) not-evaluated, not a
        # raise reaching here.
        degraded, events = _degraded_summary()
        typer.echo(f"Configuration error: {exc}", err=True)
        _write_bundle(degraded, "partial", events)
        raise typer.Exit(code=1)
    except InfrastructureError as exc:
        # Same reasoning as ConfigurationError above -- a runner's own
        # infrastructure failure no longer reaches here; this remains a
        # defensive backstop for an InfrastructureError raised elsewhere in
        # the orchestration loop.
        degraded, events = _degraded_summary()
        typer.echo(f"Infrastructure error: {exc}", err=True)
        _write_bundle(degraded, "partial", events)
        raise typer.Exit(code=1)
    except Exception as exc:  # noqa: BLE001 - last-resort finalize, see docs/adr/0002
        degraded, events = _degraded_summary()
        typer.echo(f"Unexpected error: {exc}", err=True)
        _write_bundle(degraded, "partial", events)
        raise typer.Exit(code=1)

    _print_summary(summary, _categories(scenario_info))
    _write_bundle(summary, "sealed", store.events(loaded.run_config.run_id))


def _build_runner(loaded: LoadedRunConfig, benchmark: Any = None) -> Any:
    """Construct the agent runner selected by the config's ``runner_id``.

    ``"mock"`` (the default) takes the demo/test knobs, and with
    ``mock_trajectory`` set plays back every task's scripted trajectory of
    that kind from *benchmark* (docs/adr/0006 D11). Model-backed runners
    such as ``"langgraph"`` take a ``model`` and read their provider
    credentials from the environment.

    Raises
    ------
    ConfigurationError
        If the selected runner is not registered — e.g. ``langgraph`` before
        its optional dependencies are installed and it registers itself.
        ``UnknownComponentError`` is a ``ConfigurationError`` subclass, so the
        caller's existing config-error handling surfaces a clear message.
    """
    from agentevalops.core.errors import ConfigurationError
    from agentevalops.registry import KIND_RUNNER, default_registry

    runner_cls = default_registry.get(KIND_RUNNER, loaded.runner_id)
    if loaded.runner_id == "mock":
        scripts = None
        if loaded.mock_trajectory is not None:
            if not hasattr(benchmark, "scripted_trajectories"):
                raise ConfigurationError(
                    f"benchmark '{loaded.benchmark_id}' has no scripted "
                    "trajectories to play back; remove mock_trajectory"
                )
            scripts = benchmark.scripted_trajectories(loaded.mock_trajectory)
        return runner_cls(
            should_fail=loaded.mock_fail,
            cost_per_task_usd=loaded.mock_cost_per_task_usd,
            scripts=scripts,
        )
    if loaded.runner_id == "azure-openai":
        return runner_cls(
            model=loaded.model,
            deployment=loaded.deployment,
            api_version=loaded.api_version,
        )
    return runner_cls(model=loaded.model)


def _bundle_run_id(bundle: Path) -> str | None:
    """Return the run_id recorded in a bundle's metadata.json, or None."""
    import json

    try:
        data = json.loads((bundle / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    run_id = data.get("run_id") if isinstance(data, dict) else None
    return str(run_id) if run_id is not None else None


def _candidate_bundles(runs_dir: Path) -> list[Path]:
    """Return sub-directories of *runs_dir* that look like result bundles."""
    return [
        child
        for child in sorted(runs_dir.iterdir())
        if child.is_dir() and (child / "metadata.json").exists()
    ]


def _resolve_bundle(runs_dir: Path, run_id: str) -> Path | None:
    """Find the bundle for *run_id* under *runs_dir*.

    ``run_id="latest"`` resolves to the most-recently-modified bundle.
    Otherwise a bundle matches when its recorded run_id (or, as a fallback,
    its directory name) equals *run_id*.
    """
    candidates = _candidate_bundles(runs_dir)
    if not candidates:
        return None
    if run_id == "latest":
        return max(candidates, key=lambda p: p.stat().st_mtime)
    for bundle in candidates:
        if _bundle_run_id(bundle) == run_id or bundle.name == run_id:
            return bundle
    return None


@app.command()
def report(
    run_id: str = typer.Option(
        ...,
        "--run-id",
        "-r",
        help="Run ID to report on, or 'latest' for the newest bundle.",
    ),
    runs_dir: Path = typer.Option(
        Path("runs"),
        "--runs-dir",
        help="Directory that holds result bundles.",
    ),
) -> None:
    """Print the markdown report for a previously-saved run bundle."""
    if not runs_dir.is_dir():
        typer.echo(f"Runs directory not found: {runs_dir}", err=True)
        raise typer.Exit(code=1)

    bundle = _resolve_bundle(runs_dir, run_id)
    if bundle is None:
        typer.echo(f"No bundle found for run-id '{run_id}' in {runs_dir}", err=True)
        raise typer.Exit(code=1)

    report_path = bundle / "report.md"
    if not report_path.exists():
        typer.echo(f"Bundle '{bundle}' has no report.md", err=True)
        raise typer.Exit(code=1)

    typer.echo(report_path.read_text(encoding="utf-8"))


@app.command()
def replay(
    bundle: Path = typer.Option(
        ...,
        "--bundle",
        "-b",
        help="Path to a result bundle directory.",
    ),
) -> None:
    """[Deprecated] Alias for validate-bundle -- structural checks only.

    ``replay`` historically meant bundle *verification* (checksums,
    cross-file consistency), never behavioral re-execution -- see
    docs/adr/0003-replay-semantics.md, Q1. ``validate-bundle`` is now the
    single, honestly-named structural entry point; this command delegates
    to the exact same check and produces identical output and exit codes.
    The ``replay`` name is reserved for behavioral replay once it ships;
    using it today for a structural check it doesn't perform is the
    confusion ADR-0003 exists to stop.
    """
    typer.echo(
        "Warning: `agentevalops replay` is deprecated and now an alias "
        "for `validate-bundle` (structural checks only -- no behavioral "
        "re-execution). The `replay` name is reserved for behavioral "
        "replay once it ships; see docs/adr/0003-replay-semantics.md.",
        err=True,
    )
    _run_validate_bundle(bundle)


@app.command("validate-bundle")
def validate_bundle(
    bundle: Path = typer.Option(
        ...,
        "--bundle",
        "-b",
        help="Path to a result bundle directory.",
    ),
) -> None:
    """Validate the structural integrity of a saved result bundle."""
    _run_validate_bundle(bundle)


def _run_validate_bundle(bundle: Path) -> None:
    """Shared body for ``validate-bundle`` and its ``replay`` alias.

    Kept as one function (not two commands each calling ``validate_bundle``
    independently) so the two can never drift into two different output
    formats or exit-code rules under maintenance -- ``replay``'s whole
    purpose here is to behave identically, not similarly.
    """
    from agentevalops.bundles.validator import validate_bundle as _validate

    if not bundle.exists():
        typer.echo(f"Bundle path not found: {bundle}", err=True)
        raise typer.Exit(code=1)

    result = _validate(bundle)
    _print_validation_result(bundle, result)
    if not result.valid:
        raise typer.Exit(code=1)


def _task_info(benchmark: Any) -> dict[str, dict[str, Any]]:
    """Each task's ``category``, ``outcome_group`` and ``variant`` from the
    benchmark's task metadata, for tasks that carry any of them (a domain
    pack's scenarios do; the toy's do not). Computed at run time for the
    CLI's summary and the report's outcomes section only: the bundle gains
    them with format 0.6 (R28)."""
    info: dict[str, dict[str, Any]] = {}
    try:
        tasks = list(benchmark.list_tasks())
    except Exception:  # noqa: BLE001 - a summary line must never fail a run
        return info
    for task in tasks:
        entry: dict[str, Any] = {}
        for key in ("category", "outcome_group"):
            value = task.metadata.get(key)
            if isinstance(value, str) and value:
                entry[key] = value
        variant = task.metadata.get("variant")
        if isinstance(variant, dict) and variant:
            entry["variant"] = dict(variant)
        if entry:
            info[str(task.task_id)] = entry
    return info


def _categories(scenario_info: dict[str, dict[str, Any]]) -> dict[str, str]:
    return {
        task_id: str(entry["category"])
        for task_id, entry in scenario_info.items()
        if "category" in entry
    }


def category_lines(summary: RunSummary, categories: dict[str, str]) -> list[str]:
    """One line per category -- passed over evaluated, and the not-evaluated
    count -- in first-seen order (ROADMAP R25: verdicts in three categories).
    Empty when no task carries a category."""
    if not categories:
        return []
    order: list[str] = []
    counts: dict[str, list[int]] = {}
    for result in summary.task_results:
        category = categories.get(str(result.task_id))
        if category is None:
            continue
        if category not in counts:
            order.append(category)
            counts[category] = [0, 0, 0]  # passed, evaluated, not evaluated
        if not result.evaluation.evaluated:
            counts[category][2] += 1
        else:
            counts[category][1] += 1
            if result.evaluation.passed:
                counts[category][0] += 1
    width = max(len(c) for c in order) if order else 0
    return [
        f"  {category:<{width}}  {passed}/{evaluated} evaluated passed "
        f"({not_evaluated} not evaluated)"
        for category in order
        for passed, evaluated, not_evaluated in [counts[category]]
    ]


def _print_summary(summary: object, categories: dict[str, str] | None = None) -> None:
    """Print a concise one-block run summary to stdout."""
    from agentevalops.core.schemas import RunSummary

    if not isinstance(summary, RunSummary):
        typer.echo(str(summary))
        return

    from agentevalops.reports.markdown import format_pass_rate

    typer.echo(f"Run ID  : {summary.run_id}")
    typer.echo(
        f"Tasks   : {format_pass_rate(summary)}, {summary.total_tasks} attempted"
    )
    typer.echo(f"Failed  : {summary.failed_tasks}")
    typer.echo(f"Cost    : ${summary.total_cost_usd:.4f}")
    typer.echo(f"Tokens  : {summary.total_tokens}")
    if summary.policy_verdict is not None:
        verdict = summary.policy_verdict.verdict.value.upper()
        checker = summary.policy_verdict.checker_id
        typer.echo(f"Policy  : {verdict}  ({checker})")
    lines = category_lines(summary, categories or {})
    if lines:
        typer.echo("By category:")
        for line in lines:
            typer.echo(line)


def _print_validation_result(bundle: Path, result: BundleValidationResult) -> None:
    """Print a bundle validation result to stdout (errors to stderr).

    Shared by ``validate-bundle`` and its ``replay`` alias -- both commands
    call this with the exact same ``BundleValidationResult``, so their
    stdout is identical by construction, not by coincidentally-matching
    formatting code maintained twice.
    """
    typer.echo(f"Bundle:        {bundle.resolve()}")
    if result.run_id is not None:
        typer.echo(f"Run ID:        {result.run_id}")
    if result.bundle_format_version is not None:
        typer.echo(f"Format:        {result.bundle_format_version}")
    if result.manifest_valid is not None:
        mstatus = "PASS" if result.manifest_valid else "FAIL"
        typer.echo(f"Manifest:      {mstatus}")
    typer.echo(f"Trace events:  {result.trace_event_count}")
    typer.echo(f"Evaluations:   {result.evaluation_count}")
    if result.policy_verdict is not None:
        typer.echo(f"Policy:        {result.policy_verdict.upper()}")
    typer.echo(f"Files checked: {result.checked_files}")
    status = "PASS" if result.valid else "FAIL"
    typer.echo(f"Status:        {status}")
    for warning in result.warnings:
        typer.echo(f"  W {warning}")
    for error in result.errors:
        typer.echo(f"  ! {error}", err=True)


def main() -> None:
    """Package-level entry point wired up by pyproject.toml."""
    app()


if __name__ == "__main__":
    main()
