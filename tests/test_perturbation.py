"""Task-text perturbation with the derived seed (R26 slice 4; docs/adr/0006
D6; testing-framework 6.1.1): the same seed and repetition give the same
variant, the perturbed text reaches the agent, and ids survive."""

from __future__ import annotations

import asyncio
import random
import shutil
import tempfile
from importlib import resources
from pathlib import Path
from typing import Any

import fast_yaml
import pytest

from agentevalops.agents.mock_runner import MockAgentRunner
from agentevalops.benchmarks.agentic_baseline import AgenticBaselineBenchmark
from agentevalops.benchmarks.toy.adapter import ToyBenchmarkAdapter
from agentevalops.core.perturbation import (
    PERTURBATION_FAMILIES,
    derive_seed,
    perturb_text,
    typos_punctuation,
)
from agentevalops.core.protocols import Perturbing
from agentevalops.core.schemas import (
    ResourceLimits,
    RunConfig,
    TraceEventKind,
)
from agentevalops.core.types import BACKEND_LOCAL, AgentId, RunId
from agentevalops.domain_packs import DomainPackError
from agentevalops.domain_packs.seed import SeedDomainPack
from agentevalops.evaluators.deterministic import DeterministicEvaluator
from agentevalops.evaluators.policy_compliance import PolicyComplianceEvaluator
from agentevalops.evaluators.state_based import StateBasedEvaluator
from agentevalops.evaluators.tool_use import ToolUseEvaluator
from agentevalops.orchestration.local import LocalOrchestrator
from agentevalops.policy.basic_checker import BasicPolicyChecker
from agentevalops.stores.memory import InMemoryTraceStore

DATA_DIR = Path(str(resources.files("agentevalops.domain_packs.seed") / "data"))
PACK = SeedDomainPack()
SCENARIOS = {s.scenario_id: s for s in PACK.scenarios()}
TEXT = (
    "Application C1 under the Community Support Grant is waiting for a decision. "
    "Look the case up, decide it under the policy, and notify the applicant of "
    "3,000 reasons; the limit is 3000."
)


# ---------------------------------------------------------------------------
# The seed and the family
# ---------------------------------------------------------------------------


def test_the_seed_is_derived_as_d6_writes_it() -> None:
    import hashlib

    expected = int(hashlib.sha256(b"7:TE1:2").hexdigest(), 16)
    assert derive_seed(7, "TE1", 2) == expected
    assert derive_seed(7, "TE1", 2) == derive_seed(7, "TE1", 2)
    assert len({derive_seed(0, "TE1", r) for r in range(5)}) == 5
    assert (
        derive_seed(0, "TE1", 0) != derive_seed(0, "TE2", 0) != derive_seed(1, "TE1", 0)
    )


def test_the_family_is_deterministic_changes_the_text_and_protects_ids() -> None:
    first = typos_punctuation(TEXT, random.Random(42), 0.2)
    again = typos_punctuation(TEXT, random.Random(42), 0.2)
    other = typos_punctuation(TEXT, random.Random(43), 0.2)
    assert first == again
    assert first != TEXT and other != TEXT
    assert first != other
    for variant in (first, other):
        assert "C1" in variant.split(" ")
        assert "3,000" in variant and "3000" in variant.rstrip(".;,")
        assert len(variant.split(" ")) == len(TEXT.split(" "))
        # Every word keeps its first and last letter.
        for before, after in zip(TEXT.split(" "), variant.split(" ")):
            core_b = before.rstrip(".,;:")
            core_a = after.rstrip(".,;:")
            if core_b.isalpha() and len(core_b) >= 4:
                assert (core_a[0], core_a[-1]) == (core_b[0], core_b[-1]), (
                    before,
                    after,
                )


def test_at_least_one_change_is_made_even_at_a_tiny_intensity() -> None:
    for seed in range(20):
        assert typos_punctuation(TEXT, random.Random(seed), 0.0001) != TEXT
    # Nothing eligible: nothing changes.
    assert typos_punctuation("C1 C2 3000", random.Random(1), 1.0) == "C1 C2 3000"


def test_perturb_text_names_its_families_and_refuses_strangers() -> None:
    assert PERTURBATION_FAMILIES == ("typos_punctuation",)
    assert perturb_text(TEXT, "typos_punctuation", 5) == perturb_text(
        TEXT, "typos_punctuation", 5
    )
    with pytest.raises(ValueError, match="unknown perturbation family"):
        perturb_text(TEXT, "homoglyphs", 5)


# ---------------------------------------------------------------------------
# The hook
# ---------------------------------------------------------------------------


def test_the_benchmark_is_perturbing_and_varies_only_perturbation_scenarios() -> None:
    benchmark = AgenticBaselineBenchmark(PACK)
    assert isinstance(benchmark, Perturbing)
    assert not isinstance(ToyBenchmarkAdapter(), Perturbing)
    tasks = {str(t.task_id): t for t in benchmark.list_tasks()}
    te1 = benchmark.perturb(tasks["TE1"], derive_seed(0, "TE1", 0))
    assert te1 is tasks["TE1"]
    seed = derive_seed(0, "RB8", 0)
    rb8 = benchmark.perturb(tasks["RB8"], seed)
    assert rb8.task_id == tasks["RB8"].task_id and rb8.metadata == tasks["RB8"].metadata
    assert rb8.description != tasks["RB8"].description
    assert "C1" in rb8.description.split(" ")
    assert benchmark.perturb(tasks["RB8"], seed).description == rb8.description
    assert (
        benchmark.perturb(tasks["RB8"], derive_seed(0, "RB8", 1)).description
        != rb8.description
    )
    assert tasks["RB8"].metadata["variant"] == {
        "kind": "perturbation",
        "base": "TE1",
        "family": "typos_punctuation",
        "intensity": 0.2,
    }


def _plan_texts(seed_base: int, repetitions: int) -> list[str]:
    """The task text the agent saw for each repetition of RB8, from the
    mock's plan event in the trace."""
    benchmark = AgenticBaselineBenchmark(PACK, scenarios=["RB8"])
    store = InMemoryTraceStore()
    orch = LocalOrchestrator(
        run_config=RunConfig(
            run_id=RunId("p"),
            agent_id=AgentId("mock-agent-v1"),
            backend_id=BACKEND_LOCAL,
            resource_limits=ResourceLimits(),
            repetitions=repetitions,
            seed=seed_base,
        ),
        benchmark=benchmark,
        runner=MockAgentRunner(scripts=benchmark.scripted_trajectories("known_good")),
        trace_store=store,
        evaluator=[
            DeterministicEvaluator(),
            ToolUseEvaluator(),
            StateBasedEvaluator(),
            PolicyComplianceEvaluator(),
        ],
        policy_checker=BasicPolicyChecker(),
    )
    summary = asyncio.run(orch.run())
    assert summary.passed_tasks == repetitions, summary.task_results[0].evaluation.notes
    plans = [
        e
        for e in store.events(RunId("p"))
        if e.kind == TraceEventKind.AGENT_PLAN
        and e.payload["plan"].startswith("Solve: ")
    ]
    return [
        p.payload["plan"][len("Solve: ") :]
        for p in sorted(plans, key=lambda e: e.repetition)
    ]


def test_perturbed_text_reaches_the_agent_and_the_seed_fixes_the_variant() -> None:
    """R26's exit criteria for the variant: the agent saw perturbed text, and
    the same seed and repetition give the same variant while another
    repetition differs."""
    original = SCENARIOS["RB8"].description
    first = _plan_texts(seed_base=0, repetitions=2)
    again = _plan_texts(seed_base=0, repetitions=2)
    assert first == again
    assert len(first) == 2 and first[0] != first[1]
    for text in first:
        assert text != original and "C1" in text.split(" ")
    assert _plan_texts(seed_base=1, repetitions=1)[0] != first[0]
    # The base is not varied: TE1's agent sees TE1's text.
    benchmark = AgenticBaselineBenchmark(PACK, scenarios=["TE1"])
    task = next(iter(benchmark.list_tasks()))
    assert benchmark.perturb(task, derive_seed(0, "TE1", 0)).description == original


def test_the_evaluators_judge_against_the_task_as_declared() -> None:
    """The perturbation varies what the agent sees; the expectations are
    the base's, so the known-good passes and the known-bad fails as for
    TE1 (checked for every scenario by the exit tests too)."""
    from test_r24_exit import _seed

    good, _, _, _ = _seed("known_good")
    bad, _, _, _ = _seed("known_bad")
    g = next(r for r in good.task_results if r.task_id == "RB8")
    b = next(r for r in bad.task_results if r.task_id == "RB8")
    assert g.evaluation.passed and all(e.passed for e in g.evaluations)
    assert not b.evaluation.passed and not any(e.passed for e in b.evaluations)


# ---------------------------------------------------------------------------
# The loader's guards
# ---------------------------------------------------------------------------


def _edited(tmp_path: Path, mutate: Any) -> Path:
    copy = Path(tempfile.mkdtemp(prefix="data", dir=tmp_path))
    shutil.rmtree(copy)
    shutil.copytree(DATA_DIR, copy)
    doc = fast_yaml.safe_load((copy / "scenarios.yaml").read_text(encoding="utf-8"))
    mutate(doc)
    (copy / "scenarios.yaml").write_text(
        fast_yaml.safe_dump(doc, sort_keys=False), encoding="utf-8"
    )
    return copy


def _variant(**perturbation: Any) -> dict[str, Any]:
    block = {"base": "TE1", "family": "typos_punctuation", "intensity": 0.2}
    block.update(perturbation)
    return {
        "scenario_id": "RBX",
        "perturbation": block,
        "outcome_group": "erroneous_action",
        "category": "robustness",
        "applicable_evaluators": ["deterministic"],
        "control_ids": ["mgf-agentic:2.3.2"],
        "known_good": [
            {"call": "case_lookup", "arguments": {"case_id": "C1"}},
            {
                "call": "status_update",
                "arguments": {
                    "case_id": "C1",
                    "decision": "approved",
                    "reason_codes": ["ELIG-OK"],
                },
            },
            {"finish": "C1: approved"},
        ],
        "known_bad": [{"finish": "x"}],
    }


@pytest.mark.parametrize(
    ("change", "match"),
    [
        (
            {"perturbation": {"base": "nope", "family": "typos_punctuation"}},
            "not a scenario declared before",
        ),
        (
            {"perturbation": {"base": "RB6", "family": "typos_punctuation"}},
            "is itself a variant",
        ),
        (
            {"perturbation": {"base": "TE1", "family": "homoglyphs"}},
            "family must be one of",
        ),
        (
            {
                "perturbation": {
                    "base": "TE1",
                    "family": "typos_punctuation",
                    "intensity": 0,
                }
            },
            "in \\(0, 1\\]",
        ),
        (
            {
                "perturbation": {
                    "base": "TE1",
                    "family": "typos_punctuation",
                    "intensity": 2,
                }
            },
            "in \\(0, 1\\]",
        ),
        (
            {
                "perturbation": {
                    "base": "TE1",
                    "family": "typos_punctuation",
                    "extra": 1,
                }
            },
            "unknown key",
        ),
        ({"description": "my own"}, "takes its description from the base"),
        ({"expected": {"expected_substring": "x"}}, "takes its expected from the base"),
        (
            {"applicable_evaluators": ["deterministic", "llm_judge"]},
            "not the base's evaluators",
        ),
        (
            {"counterfactual": {"base": "TE1", "swap": "name", "to": "Priya Krishnan"}},
            "one variant, not two",
        ),
    ],
)
def test_a_malformed_perturbation_fails_at_load(
    tmp_path: Path, change: dict[str, Any], match: str
) -> None:
    def mutate(doc: dict[str, Any]) -> None:
        doc["scenarios"].append({**_variant(), **change})

    with pytest.raises(DomainPackError, match=match):
        SeedDomainPack(_edited(tmp_path, mutate))


def test_a_perturbation_inherits_its_base() -> None:
    rb8, te1 = SCENARIOS["RB8"], SCENARIOS["TE1"]
    assert rb8.description == te1.description
    assert rb8.expected == te1.expected
    assert rb8.environment == te1.environment
    assert rb8.applicable_evaluators == te1.applicable_evaluators
    assert "policy" not in rb8.applicable_evaluators  # no extra rule for a perturbation
