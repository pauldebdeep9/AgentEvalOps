"""CLI best-effort finalize: every exit path must write a bundle.

Before docs/adr/0002 (2026-08-05 session, Task 1), only the clean-completion
path in ``cli.py``'s ``run`` command ever reached ``BundleWriter`` --
``KeyboardInterrupt`` and ``InfrastructureError`` both exited with only
whatever ``LocalTraceStore`` had already flushed to ``traces.jsonl`` on its
own (see ``tests/fixtures/bundles/partial-infra-failure-2026-08-03/``, a
real, preserved example of exactly this). Reproduced fault-injected (no
network) at the start of this session; confirmed against the pre-fix code
that ``orchestrator.run()`` raising left no ``metadata.json`` behind.

``LocalOrchestrator.run`` is monkeypatched directly here rather than
fault-injecting through a runner, because as of this same session's Task 3
the orchestrator now catches a runner's own ``InfrastructureError``/
``ConfigurationError`` *per task* and returns a normal summary instead of
raising -- these tests are specifically about the CLI's remaining,
defensive backstop for failures that still escape that per-task catch
(``KeyboardInterrupt``, or any exception outside a single task's scope).
"""

from __future__ import annotations

import json
import pathlib

import pytest
from typer.testing import CliRunner

from agentevalops.cli import app
from agentevalops.core.errors import InfrastructureError
from agentevalops.orchestration.local import LocalOrchestrator

runner = CliRunner()
_CONFIG = pathlib.Path(__file__).parent.parent / "configs" / "toy_smoke.yaml"


def _read_metadata(output_dir: pathlib.Path) -> dict:
    return json.loads((output_dir / "metadata.json").read_text(encoding="utf-8"))


def test_run_writes_bundle_on_infrastructure_error(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _raising_run(self: LocalOrchestrator) -> None:
        raise InfrastructureError("simulated: fault-injected, no network")

    monkeypatch.setattr(LocalOrchestrator, "run", _raising_run)
    output_dir = tmp_path / "infra-failure-bundle"

    result = runner.invoke(
        app, ["run", "--config", str(_CONFIG), "--output", str(output_dir)]
    )

    assert result.exit_code == 1
    assert "Infrastructure error" in result.output
    assert (output_dir / "metadata.json").exists()
    metadata = _read_metadata(output_dir)
    assert metadata["status"] == "partial"
    assert metadata["sealed"] is False
    # Every required bundle file is present, not just traces.jsonl.
    for filename in (
        "config.json", "evaluations.json", "policy.json",
        "summary.json", "report.md",
    ):
        assert (output_dir / filename).exists(), filename


def test_run_writes_bundle_on_configuration_error_backstop(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defensive backstop: a ConfigurationError raised from somewhere in
    orchestrator.run() other than a single task's scope (unusual today --
    no current Evaluator/PolicyChecker raises one -- but not structurally
    prevented) must still finalize a bundle, same as InfrastructureError.
    """
    from agentevalops.core.errors import ConfigurationError

    async def _raising_run(self: LocalOrchestrator) -> None:
        raise ConfigurationError("simulated: outside any single task's scope")

    monkeypatch.setattr(LocalOrchestrator, "run", _raising_run)
    output_dir = tmp_path / "config-error-bundle"

    result = runner.invoke(
        app, ["run", "--config", str(_CONFIG), "--output", str(output_dir)]
    )

    assert result.exit_code == 1
    assert "Configuration error" in result.output
    metadata = _read_metadata(output_dir)
    assert metadata["status"] == "partial"


def test_run_writes_bundle_on_unexpected_exception(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _raising_run(self: LocalOrchestrator) -> None:
        raise RuntimeError("a genuine bug, not a modeled failure mode")

    monkeypatch.setattr(LocalOrchestrator, "run", _raising_run)
    output_dir = tmp_path / "unexpected-failure-bundle"

    result = runner.invoke(
        app, ["run", "--config", str(_CONFIG), "--output", str(output_dir)]
    )

    assert result.exit_code == 1
    assert "Unexpected error" in result.output
    metadata = _read_metadata(output_dir)
    assert metadata["status"] == "partial"


def test_finalized_under_failure_bundle_distinguishable_from_complete(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The core Task-1 claim: a reader must be able to tell a
    finalized-under-failure bundle apart from a normal one without
    guessing -- that's what `status` is for (docs/adr/0002, Axis 4).
    """
    clean_dir = tmp_path / "clean"
    failed_dir = tmp_path / "failed"

    clean_result = runner.invoke(
        app, ["run", "--config", str(_CONFIG), "--output", str(clean_dir)]
    )
    assert clean_result.exit_code == 0
    clean_meta = _read_metadata(clean_dir)

    async def _raising_run(self: LocalOrchestrator) -> None:
        raise InfrastructureError("simulated outage")

    monkeypatch.setattr(LocalOrchestrator, "run", _raising_run)
    failed_result = runner.invoke(
        app, ["run", "--config", str(_CONFIG), "--output", str(failed_dir)]
    )
    assert failed_result.exit_code == 1
    failed_meta = _read_metadata(failed_dir)

    assert clean_meta["status"] == "sealed"
    assert clean_meta["sealed"] is True
    assert failed_meta["status"] == "partial"
    assert failed_meta["sealed"] is False
    assert clean_meta["status"] != failed_meta["status"]
