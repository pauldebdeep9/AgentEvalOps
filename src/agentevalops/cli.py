"""CLI entry point for AgentEvalOps."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from agentevalops.replay.local import ReplaySummary

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

    from agentevalops.agents.mock_runner import MockAgentRunner
    from agentevalops.benchmarks.toy.adapter import ToyBenchmarkAdapter
    from agentevalops.config.loader import load_run_config
    from agentevalops.core.errors import ConfigurationError
    from agentevalops.evaluators.deterministic import DeterministicEvaluator
    from agentevalops.orchestration.local import LocalOrchestrator, RunSummary
    from agentevalops.policy.basic_checker import BasicPolicyChecker
    from agentevalops.stores.memory import InMemoryTraceStore

    try:
        loaded = load_run_config(config)
    except ConfigurationError as exc:
        typer.echo(f"Configuration error: {exc}", err=True)
        raise typer.Exit(code=1)

    store = InMemoryTraceStore()
    orchestrator = LocalOrchestrator(
        run_config=loaded.run_config,
        benchmark=ToyBenchmarkAdapter(scenario=loaded.benchmark_scenario),
        runner=MockAgentRunner(
            should_fail=loaded.mock_fail,
            cost_per_task_usd=loaded.mock_cost_per_task_usd,
        ),
        trace_store=store,
        evaluator=DeterministicEvaluator(),
        policy_checker=BasicPolicyChecker(),
        policy_spec=loaded.policy_spec,
    )

    summary: RunSummary = asyncio.run(orchestrator.run())
    _print_summary(summary)

    if output is not None:
        from agentevalops.bundles.writer import BundleWriter

        all_events = store.events(loaded.run_config.run_id)
        BundleWriter(output).write(
            run_config=loaded.run_config,
            summary=summary,
            all_events=all_events,
            policy_spec=loaded.policy_spec,
            config_name=str(config),
        )
        typer.echo(f"Bundle written to: {output.resolve()}")


@app.command()
def replay(
    bundle: Path = typer.Option(
        ...,
        "--bundle",
        "-b",
        help="Path to a result bundle directory.",
    ),
) -> None:
    """Replay and verify a saved result bundle."""
    from agentevalops.bundles.reader import BundleReader
    from agentevalops.core.errors import BundleError
    from agentevalops.replay.local import LocalReplayVerifier

    if not bundle.exists():
        typer.echo(f"Bundle path not found: {bundle}", err=True)
        raise typer.Exit(code=1)

    try:
        loaded = BundleReader(bundle).read()
    except BundleError as exc:
        typer.echo(f"Bundle read error: {exc}", err=True)
        raise typer.Exit(code=1)

    replay_summary = LocalReplayVerifier(loaded).verify()
    _print_replay_summary(replay_summary)

    if not replay_summary.checks_passed:
        raise typer.Exit(code=1)


@app.command("validate-bundle")
def validate_bundle(
    bundle: Path = typer.Option(
        ...,
        "--bundle",
        "-b",
        help="Path to a result bundle directory.",
    ),
) -> None:
    """Validate the integrity of a saved result bundle."""
    from agentevalops.bundles.validator import validate_bundle as _validate

    if not bundle.exists():
        typer.echo(f"Bundle path not found: {bundle}", err=True)
        raise typer.Exit(code=1)

    result = _validate(bundle)
    typer.echo(f"Bundle:        {bundle.resolve()}")
    typer.echo(f"Files checked: {result.checked_files}")
    status = "PASS" if result.valid else "FAIL"
    typer.echo(f"Status:        {status}")
    for warning in result.warnings:
        typer.echo(f"  W {warning}")
    for error in result.errors:
        typer.echo(f"  ! {error}", err=True)
    if not result.valid:
        raise typer.Exit(code=1)


def _print_summary(summary: object) -> None:
    """Print a concise one-block run summary to stdout."""
    from agentevalops.orchestration.local import RunSummary

    if not isinstance(summary, RunSummary):
        typer.echo(str(summary))
        return

    total = summary.total_tasks
    passed = summary.passed_tasks
    failed = summary.failed_tasks
    pass_rate = (passed / total * 100) if total > 0 else 0.0
    typer.echo(f"Run ID  : {summary.run_id}")
    typer.echo(f"Tasks   : {passed} / {total} passed")
    typer.echo(f"Failed  : {failed}")
    typer.echo(f"Rate    : {pass_rate:.0f}%")
    typer.echo(f"Cost    : ${summary.total_cost_usd:.4f}")
    typer.echo(f"Tokens  : {summary.total_tokens}")
    if summary.policy_verdict is not None:
        verdict = summary.policy_verdict.verdict.value.upper()
        checker = summary.policy_verdict.checker_id
        typer.echo(f"Policy  : {verdict}  ({checker})")


def _print_replay_summary(rs: ReplaySummary) -> None:
    """Print a concise replay verification summary to stdout."""
    typer.echo("Replay summary")
    typer.echo(f"Bundle:        {rs.bundle_path}")
    typer.echo(f"Run ID:        {rs.run_id}")
    if rs.bundle_format_version is not None:
        typer.echo(f"Format:        {rs.bundle_format_version}")
    if rs.manifest_valid is not None:
        mstatus = "PASS" if rs.manifest_valid else "FAIL"
        typer.echo(f"Manifest:      {mstatus}")
    typer.echo(f"Trace events:  {rs.trace_event_count}")
    typer.echo(f"Evaluations:   {rs.evaluation_count}")
    if rs.policy_verdict is not None:
        typer.echo(f"Policy:        {rs.policy_verdict.upper()}")
    status = "PASS" if rs.checks_passed else "FAIL"
    typer.echo(f"Replay status: {status}")
    for failure in rs.failures:
        typer.echo(f"  ! {failure}", err=True)


def main() -> None:
    """Package-level entry point wired up by pyproject.toml."""
    app()
