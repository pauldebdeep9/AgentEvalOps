"""A run whose runner yielded its agent.terminal event is finished
(R29 close-out; ADR-0008's stopped-run rule, from the producer's side).

validate-bundle reads stopped or finished from the records: a run with an
agent.terminal event finished, and a stopped run has none. The
orchestrator guarantees it. When a runner's stream raises after it yielded
the terminal event, the run is still harvested and evaluated like any
finished run; the late error is logged as a warning and changes no result.
A run with no terminal event is stopped, as before.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from reference_bundles import _carried_config, _registry, _run
from typer.testing import CliRunner

from agentevalops import registry as registry_module
from agentevalops.agents.mock_runner import MockAgentRunner
from agentevalops.bundles.validator import validate_bundle
from agentevalops.cli import app
from agentevalops.core.errors import InfrastructureError
from agentevalops.core.schemas import AgentInput, ResourceLimits
from agentevalops.registry import KIND_RUNNER

#: The task run whose runner raises after its terminal event.
LATE = "TC1"


class _FailsAfterFinishing(MockAgentRunner):
    """The mock, except that for one task the stream raises after the
    agent's whole trajectory, terminal event included."""

    async def run(self, input: AgentInput, limits: ResourceLimits) -> Any:
        stream = await super().run(input, limits)
        late = str(input.task.task_id) == LATE

        async def events() -> Any:
            async for event in stream:
                yield event
            if late:
                raise InfrastructureError(
                    "the sandbox went away after the agent finished"
                )

        return events()


@pytest.fixture
def bundle(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> Path:
    fresh = registry_module.build_default_registry()
    fresh.register(KIND_RUNNER, "mock", _FailsAfterFinishing, replace=True)
    with caplog.at_level(logging.WARNING), _registry(fresh):
        return _run(_carried_config(tmp_path, "late", "seed"), tmp_path / "late").path


def _result(bundle: Path, task_id: str) -> dict[str, Any]:
    summary = json.loads((bundle / "summary.json").read_text("utf-8"))
    (run,) = [r for r in summary["task_results"] if r["task_id"] == task_id]
    return run


def test_a_run_that_raises_after_its_terminal_event_is_finished(
    bundle: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Fail-first: the run was recorded as stopped by its infrastructure
    while its agent.terminal event was in the trace, and validate-bundle
    failed the bundle under the stopped-run rule."""
    assert validate_bundle(bundle).errors == []
    run = _result(bundle, LATE)
    assert run["evaluation"]["evaluated"] is True
    assert run["evaluation"]["not_evaluated_cause"] is None
    assert run["output"]["termination_reason"] != "infra_failure"
    # The bundle is written in the fixture: its log is the setup phase's.
    late = [r for r in caplog.get_records("setup") if r.levelno == logging.WARNING]
    assert any(
        f"{LATE}/0" in r.getMessage()
        and "after its agent.terminal event" in r.getMessage()
        and "the sandbox went away" in r.getMessage()
        for r in late
    ), [r.getMessage() for r in late]


def test_the_late_error_changes_no_result_and_rescores_as_a_match(bundle: Path) -> None:
    """Judged like any finished run: the same verdict as a run whose stream
    ended cleanly, and a MATCH on rescore."""
    assert _result(bundle, LATE)["evaluation"]["passed"] is True
    result = CliRunner().invoke(app, ["rescore", "--bundle", str(bundle)])
    assert result.exit_code == 0, result.output
    assert result.output.rstrip().endswith("RESCORE: MATCH")
