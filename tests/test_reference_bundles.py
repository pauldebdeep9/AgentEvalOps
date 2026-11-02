"""The shared reference bundles (R29 (b2) Part 1): written once per session,
never changed in place, and written without leaking the failing runner.

The session fixture (``conftest.py``) checks at the end of the session that
no test changed a shared bundle; ``changes`` is what it calls, tested here
on a copy. The carried-infra bundle is written with a test runner
registered as ``mock``: once the bundles exist, the package's own registry,
mock runner and orchestrator are back.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from reference_bundles import (
    DEFAULT_REGISTRY,
    INFRA,
    KINDS,
    ORCHESTRATOR_RUN,
    InfraFailingRunner,
    Reference,
    ReferenceBundles,
    _registry,
    changes,
    snapshot,
)

from agentevalops import registry as registry_module
from agentevalops.agents.mock_runner import MockAgentRunner
from agentevalops.orchestration.local import LocalOrchestrator
from agentevalops.registry import KIND_RUNNER


def _summary(ref: Reference) -> dict:  # type: ignore[type-arg]
    return json.loads((ref.path / "summary.json").read_text("utf-8"))


def test_the_failing_runner_does_not_outlive_its_run(
    reference_bundles: ReferenceBundles,
) -> None:
    """Fail-first: with the builder's registry patch left in place, the
    session's default registry still held the failing runner here."""
    assert registry_module.default_registry is DEFAULT_REGISTRY
    assert registry_module.default_registry.get(KIND_RUNNER, "mock") is MockAgentRunner
    assert LocalOrchestrator.run is ORCHESTRATOR_RUN
    # ... and it did run where it was meant to, and only there.
    infra = _summary(reference_bundles["carried-infra"])
    causes = {
        r["task_id"]: r["evaluation"]["not_evaluated_cause"]
        for r in infra["task_results"]
    }
    assert causes[INFRA] == "infrastructure"
    seed = _summary(reference_bundles["seed"])
    assert (seed["passed_tasks"], seed["total_tasks"]) == (26, 26)


def test_the_builders_registry_is_its_own_whatever_is_patched() -> None:
    """A module fixture's patch -- a failing runner, a spied run -- holds
    neither while the bundles are written, and is back afterwards."""
    patched = registry_module.build_default_registry()
    patched.register(KIND_RUNNER, "mock", InfraFailingRunner, replace=True)

    async def spy(self: LocalOrchestrator) -> None:
        raise AssertionError("spied")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(registry_module, "default_registry", patched)
        mp.setattr(LocalOrchestrator, "run", spy)
        with _registry(registry_module.build_default_registry()):
            assert registry_module.default_registry.get(KIND_RUNNER, "mock") is (
                MockAgentRunner
            )
            assert LocalOrchestrator.run is ORCHESTRATOR_RUN
        assert registry_module.default_registry is patched
        assert LocalOrchestrator.run is spy


def test_every_kind_is_written(reference_bundles: ReferenceBundles) -> None:
    assert tuple(reference_bundles.bundles) == KINDS
    for kind in KINDS:
        ref = reference_bundles[kind]
        assert (ref.path / "manifest.json").is_file(), kind
        assert "Bundle written to:" in ref.output, kind


def test_a_change_to_a_shared_bundle_is_named(
    reference_bundles: ReferenceBundles, tmp_path: Path
) -> None:
    """What the session fixture checks at the end, on a copy: a changed, an
    added and a removed file are each named; an untouched copy has none."""
    copy = {"toy": Reference(reference_bundles["toy"].copy(tmp_path / "toy"), "")}
    before = snapshot(copy)
    assert changes(before, copy) == []
    (tmp_path / "toy" / "report.md").write_text("edited", encoding="utf-8")
    (tmp_path / "toy" / "extra.txt").write_text("new", encoding="utf-8")
    (tmp_path / "toy" / "policy.json").unlink()
    assert changes(before, copy) == [
        "toy/extra.txt: added",
        "toy/policy.json: removed",
        "toy/report.md: changed",
    ]
