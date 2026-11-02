"""The writer's runner fields are required, and a bundle without a runner
says so (R28 follow-up, 2026-11-02; docs/adr/0007 D4).

Like ``evidence``, ``runner_id`` and ``mock_trajectory`` have no default:
a caller passes ``None`` when a value is legitimately absent, never by
leaving it out. A 0.6 bundle whose ``runner_id`` is null cannot say whether
its trajectories were scripted; both reports say so in the header, and
``validate-bundle`` warns without failing.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from bundle_inputs import minimal_evidence

from agentevalops.agents.mock_runner import MockAgentRunner
from agentevalops.benchmarks.toy.adapter import ToyBenchmarkAdapter
from agentevalops.bundles.manifest import write_manifest
from agentevalops.bundles.validator import validate_bundle
from agentevalops.bundles.writer import BundleWriter
from agentevalops.core.schemas import ResourceLimits, RunConfig
from agentevalops.core.types import BACKEND_LOCAL, AgentId, RunId
from agentevalops.evaluators.deterministic import DeterministicEvaluator
from agentevalops.orchestration.local import LocalOrchestrator
from agentevalops.policy.basic_checker import BasicPolicyChecker
from agentevalops.reports.markdown import RUNNER_NOT_RECORDED
from agentevalops.stores.memory import InMemoryTraceStore

LEGACY_0_5 = Path(__file__).resolve().parent / "fixtures/bundles/seed-narrow-0.5"


def _write(tmp_path: Path, **runner: Any) -> Path:
    store = InMemoryTraceStore()
    orch = LocalOrchestrator(
        run_config=RunConfig(
            run_id=RunId("runner-fields"),
            agent_id=AgentId("agent"),
            backend_id=BACKEND_LOCAL,
            resource_limits=ResourceLimits(),
        ),
        benchmark=ToyBenchmarkAdapter("smoke"),
        runner=MockAgentRunner(),
        trace_store=store,
        evaluator=DeterministicEvaluator(),
        policy_checker=BasicPolicyChecker(),
    )
    summary = asyncio.run(orch.run())
    return BundleWriter(tmp_path / "bundle").write(
        run_config=orch._config,
        summary=summary,
        all_events=store.events(orch._config.run_id),
        tool_calls=orch.tool_call_records,
        end_states=orch.end_states,
        evidence=minimal_evidence(summary),
        **runner,
    )


@pytest.mark.parametrize(
    "runner",
    [{"mock_trajectory": None}, {"runner_id": "mock"}, {}],
)
def test_leaving_a_runner_field_out_is_a_type_error(
    tmp_path: Path, runner: dict[str, Any]
) -> None:
    """Fail-first: with defaults, each of these wrote a bundle."""
    with pytest.raises(TypeError, match="runner_id|mock_trajectory"):
        _write(tmp_path, **runner)


def test_none_is_passed_explicitly_and_recorded(tmp_path: Path) -> None:
    bundle = _write(tmp_path, runner_id="openai", mock_trajectory=None)
    config = json.loads((bundle / "config.json").read_text("utf-8"))
    assert (config["runner_id"], config["mock_trajectory"]) == ("openai", None)


@pytest.mark.parametrize("report", ["report.md", "evidence_report.md"])
def test_a_bundle_with_no_runner_says_so_in_both_headers(
    tmp_path: Path, report: str
) -> None:
    """Fail-first: before, report.md said nothing and the evidence report
    printed a made-up runner name."""
    text = (_write(tmp_path, runner_id=None, mock_trajectory=None) / report).read_text(
        "utf-8"
    )
    header = text.split("\n## ", 1)[0]
    assert RUNNER_NOT_RECORDED in header
    assert text.count(RUNNER_NOT_RECORDED) == 1
    assert "not recorded" in header


@pytest.mark.parametrize("report", ["report.md", "evidence_report.md"])
def test_a_bundle_with_a_runner_names_it_and_says_nothing_more(
    tmp_path: Path, report: str
) -> None:
    text = (
        _write(tmp_path, runner_id="openai", mock_trajectory=None) / report
    ).read_text("utf-8")
    assert RUNNER_NOT_RECORDED not in text
    assert "`openai`" in text.split("\n## Testing", 1)[0]


def test_validate_bundle_warns_on_a_null_runner_and_still_passes(
    tmp_path: Path,
) -> None:
    """Fail-first: before, a 0.6 bundle with no runner validated silently."""
    result = validate_bundle(_write(tmp_path, runner_id=None, mock_trajectory=None))
    assert result.valid, result.errors
    assert any("records no runner_id" in w for w in result.warnings)


def test_validate_bundle_warns_on_a_missing_runner_key(tmp_path: Path) -> None:
    bundle = _write(tmp_path, runner_id="mock", mock_trajectory=None)
    config_path = bundle / "config.json"
    config = json.loads(config_path.read_text("utf-8"))
    del config["runner_id"]
    config_path.write_text(json.dumps(config), encoding="utf-8")
    write_manifest(bundle)
    result = validate_bundle(bundle)
    assert result.valid, result.errors
    assert any("records no runner_id" in w for w in result.warnings)


def test_no_warning_when_the_runner_is_recorded(tmp_path: Path) -> None:
    result = validate_bundle(_write(tmp_path, runner_id="mock", mock_trajectory=None))
    assert not any("runner_id" in w for w in result.warnings)


def test_a_0_5_bundle_is_not_warned_about_a_field_it_predates() -> None:
    result = validate_bundle(LEGACY_0_5)
    assert not any("runner_id" in w for w in result.warnings)
