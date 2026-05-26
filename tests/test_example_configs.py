"""Integration tests for the four example config scenarios.

Each test drives the full local pipeline end-to-end using one of the configs
in configs/.  Replay is verified on bundles written to tmp_path.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from agentevalops.agents.mock_runner import MockAgentRunner
from agentevalops.benchmarks.toy.adapter import ToyBenchmarkAdapter
from agentevalops.bundles.reader import BundleReader
from agentevalops.bundles.writer import BundleWriter
from agentevalops.config.loader import load_run_config
from agentevalops.core.schemas import Verdict
from agentevalops.evaluators.deterministic import DeterministicEvaluator
from agentevalops.orchestration.local import LocalOrchestrator, RunSummary
from agentevalops.policy.basic_checker import BasicPolicyChecker
from agentevalops.replay.local import LocalReplayVerifier
from agentevalops.stores.memory import InMemoryTraceStore

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _run(config_path: Path) -> tuple[RunSummary, InMemoryTraceStore]:
    """Load config, run the pipeline, and return (summary, store)."""
    loaded = load_run_config(config_path)
    store = InMemoryTraceStore()
    orch = LocalOrchestrator(
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
    summary = asyncio.run(orch.run())
    return summary, store


def _run_and_write(
    config_path: Path, tmp_path: Path
) -> tuple[RunSummary, Path]:
    """Run pipeline, write bundle, return (summary, bundle_dir)."""
    loaded = load_run_config(config_path)
    store = InMemoryTraceStore()
    orch = LocalOrchestrator(
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
    summary = asyncio.run(orch.run())
    bundle_dir = tmp_path / "bundle"
    BundleWriter(bundle_dir).write(
        run_config=loaded.run_config,
        summary=summary,
        all_events=store.events(loaded.run_config.run_id),
        policy_spec=loaded.policy_spec,
        config_name=str(config_path),
    )
    return summary, bundle_dir


# ---------------------------------------------------------------------------
# toy_smoke.yaml — happy path
# ---------------------------------------------------------------------------


def test_smoke_all_tasks_pass() -> None:
    summary, _ = _run(Path("configs/toy_smoke.yaml"))
    assert summary.passed_tasks == 2
    assert summary.failed_tasks == 0


def test_smoke_policy_passes() -> None:
    summary, _ = _run(Path("configs/toy_smoke.yaml"))
    assert summary.policy_verdict is not None
    assert summary.policy_verdict.verdict == Verdict.PASS


def test_smoke_score_result_pass_rate() -> None:
    summary, _ = _run(Path("configs/toy_smoke.yaml"))
    assert summary.score_result is not None
    assert summary.score_result.pass_rate == 1.0


def test_smoke_bundle_written(tmp_path: Path) -> None:
    _, bundle_dir = _run_and_write(Path("configs/toy_smoke.yaml"), tmp_path)
    assert bundle_dir.exists()
    for fname in (
        "metadata.json",
        "config.json",
        "traces.jsonl",
        "evaluations.json",
        "policy.json",
        "summary.json",
        "report.md",
    ):
        assert (bundle_dir / fname).exists(), f"Missing: {fname}"


def test_smoke_replay_passes(tmp_path: Path) -> None:
    _, bundle_dir = _run_and_write(Path("configs/toy_smoke.yaml"), tmp_path)
    loaded_bundle = BundleReader(bundle_dir).read()
    replay = LocalReplayVerifier(loaded_bundle).verify()
    assert replay.checks_passed, replay.failures


# ---------------------------------------------------------------------------
# toy_failure.yaml — evaluation failure
# ---------------------------------------------------------------------------


def test_failure_all_tasks_fail() -> None:
    summary, _ = _run(Path("configs/toy_failure.yaml"))
    assert summary.passed_tasks == 0
    assert summary.failed_tasks == 2


def test_failure_policy_still_passes() -> None:
    """Policy should pass — cost is $0.00, well within limit."""
    summary, _ = _run(Path("configs/toy_failure.yaml"))
    assert summary.policy_verdict is not None
    assert summary.policy_verdict.verdict == Verdict.PASS


def test_failure_score_result_pass_rate_zero() -> None:
    summary, _ = _run(Path("configs/toy_failure.yaml"))
    assert summary.score_result is not None
    assert summary.score_result.pass_rate == 0.0


def test_failure_bundle_written(tmp_path: Path) -> None:
    _, bundle_dir = _run_and_write(Path("configs/toy_failure.yaml"), tmp_path)
    assert (bundle_dir / "summary.json").exists()
    assert (bundle_dir / "evaluations.json").exists()


def test_failure_replay_passes(tmp_path: Path) -> None:
    _, bundle_dir = _run_and_write(Path("configs/toy_failure.yaml"), tmp_path)
    loaded_bundle = BundleReader(bundle_dir).read()
    replay = LocalReplayVerifier(loaded_bundle).verify()
    assert replay.checks_passed, replay.failures


# ---------------------------------------------------------------------------
# toy_policy_violation.yaml — cost ceiling
# ---------------------------------------------------------------------------


def test_policy_violation_tasks_pass() -> None:
    summary, _ = _run(Path("configs/toy_policy_violation.yaml"))
    assert summary.passed_tasks == 2


def test_policy_violation_policy_fails() -> None:
    summary, _ = _run(Path("configs/toy_policy_violation.yaml"))
    assert summary.policy_verdict is not None
    assert summary.policy_verdict.verdict == Verdict.FAIL


def test_policy_violation_notes_mention_cost() -> None:
    summary, _ = _run(Path("configs/toy_policy_violation.yaml"))
    assert summary.policy_verdict is not None
    assert "cost" in summary.policy_verdict.notes.lower()


def test_policy_violation_bundle_written(tmp_path: Path) -> None:
    _, bundle_dir = _run_and_write(
        Path("configs/toy_policy_violation.yaml"), tmp_path
    )
    assert (bundle_dir / "policy.json").exists()


def test_policy_violation_replay_passes(tmp_path: Path) -> None:
    _, bundle_dir = _run_and_write(
        Path("configs/toy_policy_violation.yaml"), tmp_path
    )
    loaded_bundle = BundleReader(bundle_dir).read()
    replay = LocalReplayVerifier(loaded_bundle).verify()
    assert replay.checks_passed, replay.failures


# ---------------------------------------------------------------------------
# toy_trace_limit.yaml — trace event ceiling
# ---------------------------------------------------------------------------


def test_trace_limit_tasks_pass() -> None:
    summary, _ = _run(Path("configs/toy_trace_limit.yaml"))
    assert summary.passed_tasks == 2


def test_trace_limit_policy_fails() -> None:
    summary, _ = _run(Path("configs/toy_trace_limit.yaml"))
    assert summary.policy_verdict is not None
    assert summary.policy_verdict.verdict == Verdict.FAIL


def test_trace_limit_notes_mention_events() -> None:
    summary, _ = _run(Path("configs/toy_trace_limit.yaml"))
    assert summary.policy_verdict is not None
    notes = summary.policy_verdict.notes.lower()
    assert "events" in notes or "trace" in notes


def test_trace_limit_trace_event_count() -> None:
    """Default runner: 3 events/task × 2 tasks = 6."""
    summary, _ = _run(Path("configs/toy_trace_limit.yaml"))
    assert summary.trace_event_count == 6


def test_trace_limit_bundle_written(tmp_path: Path) -> None:
    _, bundle_dir = _run_and_write(
        Path("configs/toy_trace_limit.yaml"), tmp_path
    )
    assert (bundle_dir / "report.md").exists()


def test_trace_limit_replay_passes(tmp_path: Path) -> None:
    _, bundle_dir = _run_and_write(
        Path("configs/toy_trace_limit.yaml"), tmp_path
    )
    loaded_bundle = BundleReader(bundle_dir).read()
    replay = LocalReplayVerifier(loaded_bundle).verify()
    assert replay.checks_passed, replay.failures


# ---------------------------------------------------------------------------
# toy_mixed.yaml — 2 pass / 1 fail; no policy violation
# ---------------------------------------------------------------------------


def test_mixed_two_tasks_pass() -> None:
    summary, _ = _run(Path("configs/toy_mixed.yaml"))
    assert summary.passed_tasks == 2


def test_mixed_one_task_fails() -> None:
    summary, _ = _run(Path("configs/toy_mixed.yaml"))
    assert summary.failed_tasks == 1


def test_mixed_total_tasks() -> None:
    summary, _ = _run(Path("configs/toy_mixed.yaml"))
    assert summary.total_tasks == 3


def test_mixed_pass_rate() -> None:
    summary, _ = _run(Path("configs/toy_mixed.yaml"))
    assert summary.score_result is not None
    assert abs(summary.score_result.pass_rate - 2 / 3) < 1e-9


def test_mixed_policy_passes() -> None:
    summary, _ = _run(Path("configs/toy_mixed.yaml"))
    assert summary.policy_verdict is not None
    assert summary.policy_verdict.verdict == Verdict.PASS


def test_mixed_bundle_written(tmp_path: Path) -> None:
    _, bundle_dir = _run_and_write(Path("configs/toy_mixed.yaml"), tmp_path)
    assert (bundle_dir / "report.md").exists()


def test_mixed_replay_passes(tmp_path: Path) -> None:
    _, bundle_dir = _run_and_write(Path("configs/toy_mixed.yaml"), tmp_path)
    loaded_bundle = BundleReader(bundle_dir).read()
    replay = LocalReplayVerifier(loaded_bundle).verify()
    assert replay.checks_passed, replay.failures
