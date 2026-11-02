"""The reference bundles every test session shares (R29 (b2) Part 1).

Five bundles, each written once per session by ``agentevalops run`` and
handed out by the session fixture ``reference_bundles`` (``conftest.py``):

- ``toy``: ``configs/toy_smoke.yaml``;
- ``seed``: ``configs/agentic_baseline_mock.yaml`` as shipped;
- ``known-bad``: the same with ``mock_trajectory: known_bad``;
- ``carried-infra``: the seed pack narrowed to one scenario per category,
  with one task run's infrastructure failing (a test runner registered as
  ``mock`` raises ``InfrastructureError`` for PC5);
- ``carried-preflight``: the same narrowing with an edited pack, registered
  as ``seed-te3-broken``, in which TE3's known-good trajectory fails its
  preflight.

They are shared, so a test never edits one in place: it copies it first
(``Reference.copy``). The session fixture snapshots every file's hash when
it writes them and checks the snapshot again at the end of the session.

Each bundle is written under a registry the builder makes itself and with
the orchestrator's own ``run``, whatever patch a module fixture holds when
the session fixture is first asked for, and every patch is undone before
the bundles are handed out: the failing runner never outlives its run.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import fast_yaml
import pytest
from typer.testing import CliRunner

from agentevalops import registry as registry_module
from agentevalops.agents.mock_runner import MockAgentRunner
from agentevalops.cli import app
from agentevalops.core.errors import InfrastructureError
from agentevalops.core.schemas import AgentInput, ResourceLimits
from agentevalops.domain_packs.seed import SeedDomainPack
from agentevalops.orchestration.local import LocalOrchestrator
from agentevalops.registry import KIND_DOMAIN_PACK, KIND_RUNNER

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = ROOT / "configs"
SEED_DATA = Path(str(resources.files("agentevalops.domain_packs.seed") / "data"))

#: The registry and the orchestrator's ``run`` as the package defines them,
#: taken when this module is first imported -- by ``conftest.py``, before
#: any test module can patch either.
DEFAULT_REGISTRY = registry_module.default_registry
ORCHESTRATOR_RUN = LocalOrchestrator.run

KINDS = ("toy", "seed", "known-bad", "carried-infra", "carried-preflight")

#: The carried-result runs: one scenario per category; the task run whose
#: infrastructure fails; the scenario whose known-good trajectory is broken
#: in the edited pack, and that pack's registered name.
CARRIED_SCENARIOS = ["TE3", "TC1", "PC5", "RB5"]
INFRA, DEFECT = "PC5", "TE3"
BROKEN_PACK = "seed-te3-broken"


@dataclass(frozen=True)
class Reference:
    """A shared bundle and what ``agentevalops run`` printed writing it."""

    path: Path
    output: str

    def copy(self, to: Path) -> Path:
        """A copy at *to*, for a test that changes the bundle."""
        shutil.copytree(self.path, to)
        return to


@dataclass(frozen=True)
class ReferenceBundles:
    """The five reference bundles by kind (``KINDS``), and the edited
    pack's data directory the preflight run used."""

    bundles: dict[str, Reference]
    broken_pack_data: Path

    def __getitem__(self, kind: str) -> Reference:
        return self.bundles[kind]


def edited_seed_data(
    tmp_path: Path, file: str, mutate: Callable[[dict[str, Any]], None]
) -> Path:
    """A copy of the seed pack's data directory with *file* changed by
    *mutate*."""
    copy = Path(tempfile.mkdtemp(prefix="data", dir=tmp_path))
    shutil.rmtree(copy)
    shutil.copytree(SEED_DATA, copy)
    doc = fast_yaml.safe_load((copy / file).read_text(encoding="utf-8"))
    mutate(doc)
    (copy / file).write_text(
        fast_yaml.safe_dump(doc, sort_keys=False), encoding="utf-8"
    )
    return copy


class InfraFailingRunner(MockAgentRunner):
    """The mock, except that one task's sandbox goes away mid-run."""

    async def run(self, input: AgentInput, limits: ResourceLimits) -> Any:
        if str(input.task.task_id) == INFRA:
            raise InfrastructureError("the sandbox went away")
        return await super().run(input, limits)


def break_te3(doc: dict[str, Any]) -> None:
    for scenario in doc["scenarios"]:
        if scenario["scenario_id"] == DEFECT:
            scenario["known_good"], scenario["known_bad"] = (
                scenario["known_bad"],
                scenario["known_good"],
            )


def registry_with_broken_pack(data_dir: Path) -> registry_module.Registry:
    """The built-in registry plus the edited pack: what a test patches in
    to rebuild the preflight run from its bundle."""
    fresh = registry_module.build_default_registry()
    fresh.register(KIND_DOMAIN_PACK, BROKEN_PACK, lambda: SeedDomainPack(data_dir))
    return fresh


@contextmanager
def _registry(registry: registry_module.Registry) -> Iterator[None]:
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(registry_module, "default_registry", registry)
        mp.setattr(LocalOrchestrator, "run", ORCHESTRATOR_RUN)
        yield


def _run(config: Path, out: Path) -> Reference:
    result = CliRunner().invoke(
        app, ["run", "--config", str(config), "--output", str(out)]
    )
    assert result.exit_code == 0, result.output
    return Reference(out, result.output)


def _write(path: Path, doc: dict[str, Any]) -> Path:
    path.write_text(fast_yaml.safe_dump(doc), encoding="utf-8")
    return path


def _seed_doc() -> dict[str, Any]:
    doc: dict[str, Any] = fast_yaml.safe_load(
        (CONFIGS / "agentic_baseline_mock.yaml").read_text("utf-8")
    )
    doc["system"] = str(CONFIGS / "seed_system.yaml")
    return doc


def _carried_config(tmp: Path, name: str, pack: str) -> Path:
    system = fast_yaml.safe_load((CONFIGS / "seed_system.yaml").read_text("utf-8"))
    system["pack_id"] = pack
    _write(tmp / f"{name}-system.yaml", system)
    doc = _seed_doc()
    doc["run_id"] = name
    doc["system"] = f"{name}-system.yaml"
    doc["benchmark_options"] = {"pack": pack, "scenarios": CARRIED_SCENARIOS}
    return _write(tmp / f"{name}.yaml", doc)


def build(tmp: Path) -> tuple[dict[str, Reference], Path]:
    """Write the five reference bundles under *tmp*; returns them and the
    edited pack's data directory."""
    bad = {**_seed_doc(), "mock_trajectory": "known_bad"}
    with _registry(registry_module.build_default_registry()):
        out = {
            "toy": _run(CONFIGS / "toy_smoke.yaml", tmp / "toy"),
            "seed": _run(CONFIGS / "agentic_baseline_mock.yaml", tmp / "seed"),
            "known-bad": _run(_write(tmp / "known-bad.yaml", bad), tmp / "known-bad"),
        }
    data_dir = edited_seed_data(tmp, "scenarios.yaml", break_te3)
    carried = registry_with_broken_pack(data_dir)
    carried.register(KIND_RUNNER, "mock", InfraFailingRunner, replace=True)
    with _registry(carried):
        for name, pack in (("infra", "seed"), ("preflight", BROKEN_PACK)):
            config = _carried_config(tmp, name, pack)
            out[f"carried-{name}"] = _run(config, tmp / f"carried-{name}")
    return out, data_dir


def snapshot(bundles: dict[str, Reference]) -> dict[str, dict[str, str]]:
    """Every file of every bundle, by name, with its sha256."""
    return {
        kind: {
            str(f.relative_to(ref.path)): hashlib.sha256(f.read_bytes()).hexdigest()
            for f in sorted(ref.path.rglob("*"))
            if f.is_file()
        }
        for kind, ref in bundles.items()
    }


def changes(
    before: dict[str, dict[str, str]], bundles: dict[str, Reference]
) -> list[str]:
    """What differs from *before*: each file changed, added or removed."""
    after = snapshot(bundles)
    found = []
    for kind in before:
        old, new = before[kind], after.get(kind, {})
        for name in sorted(set(old) | set(new)):
            if name not in new:
                found.append(f"{kind}/{name}: removed")
            elif name not in old:
                found.append(f"{kind}/{name}: added")
            elif old[name] != new[name]:
                found.append(f"{kind}/{name}: changed")
    return found
