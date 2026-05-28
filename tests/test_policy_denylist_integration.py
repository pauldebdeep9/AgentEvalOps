"""Integration test: deny-list policy catches a real tool call from the pipeline.

This test runs the full local pipeline:
    ToyBenchmarkAdapter  →  MockAgentRunner  →  LocalOrchestrator
                                                      ↓
                                              BasicPolicyChecker

It verifies that when MockAgentRunner emits an AGENT_TOOL_CALL event whose
tool_name appears in PolicySpec.deny_tool_ids, the resulting verdict is FAIL
with a citation pointing to the offending step_index.

Guard test (sanity check)
-------------------------
If you revert basic_checker.py to read payload.get("tool", "") instead of
payload.get("tool_name", ""), this test will fail — confirming the integration
test genuinely exercises the bug path.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

from agentevalops.agents.mock_runner import MockAgentRunner
from agentevalops.core.schemas import (
    AgentOutput,
    EvaluationResult,
    PolicySpec,
    RunConfig,
    TaskSpec,
    Verdict,
)
from agentevalops.core.types import AgentId, BackendId, RunId, TaskId
from agentevalops.evaluators.deterministic import DeterministicEvaluator
from agentevalops.orchestration.local import LocalOrchestrator
from agentevalops.policy.basic_checker import BasicPolicyChecker
from agentevalops.stores.memory import InMemoryTraceStore


class _ToolCallAdapter:
    """Minimal single-task benchmark adapter that drives a fake tool call."""

    benchmark_id: str = "integ-toy"
    benchmark_version: str = "1.0.0"

    def __init__(self, *, emit_tool: bool, tool_name: str) -> None:
        self._emit_tool = emit_tool
        self._tool_name = tool_name

    def list_tasks(
        self,
        filter_spec: dict[str, Any] | None = None,
    ) -> Iterable[TaskSpec]:
        return [
            TaskSpec(
                task_id=TaskId("integ-001"),
                benchmark_id=self.benchmark_id,
                description="Integration test task.",
                metadata={
                    "expected_output": "hello world",
                    "match_mode": "exact",
                    "emit_fake_tool_call": self._emit_tool,
                    "fake_tool_name": self._tool_name,
                },
            )
        ]

    def grade(self, task: TaskSpec, output: AgentOutput) -> EvaluationResult:
        return EvaluationResult(
            evaluator_id="integ-grade",
            evaluator_kind="deterministic",
            score=1.0 if output.success else 0.0,
            passed=output.success,
            notes="integ-grade",
        )


def _build_orchestrator(
    policy: PolicySpec,
    *,
    emit_tool: bool = False,
    tool_name: str = "mock-tool",
) -> LocalOrchestrator:
    """Return a wired-up LocalOrchestrator."""
    run_config = RunConfig(
        run_id=RunId("integ-run"),
        agent_id=AgentId("mock-agent-v1"),
        backend_id=BackendId("local"),
    )
    return LocalOrchestrator(
        run_config=run_config,
        benchmark=_ToolCallAdapter(emit_tool=emit_tool, tool_name=tool_name),
        runner=MockAgentRunner(),
        trace_store=InMemoryTraceStore(),
        evaluator=DeterministicEvaluator(),
        policy_checker=BasicPolicyChecker(),
        policy_spec=policy,
    )


class TestDenyListIntegration:
    """Full-pipeline deny-list tests."""

    def test_denied_tool_yields_fail_with_citation(self) -> None:
        """When the runner emits a denied tool the verdict must be FAIL."""
        denied = "dangerous-tool"
        policy = PolicySpec(
            policy_id="test-deny",
            deny_tool_ids=(denied,),
        )
        orch = _build_orchestrator(policy, emit_tool=True, tool_name=denied)
        summary = asyncio.run(orch.run())

        assert summary.policy_verdict is not None
        assert summary.policy_verdict.verdict == Verdict.FAIL, (
            "Expected FAIL because denied tool was called, got "
            f"{summary.policy_verdict.verdict!r}"
        )
        assert summary.policy_verdict.citations, (
            "Expected at least one citation for the denied tool call"
        )

    def test_denied_tool_citation_points_to_correct_step(self) -> None:
        """The citation step_index must match the AGENT_TOOL_CALL event.

        MockAgentRunner emits AGENT_PLAN at step 0, then AGENT_TOOL_CALL
        at step 1 when emit_fake_tool_call is set.
        """
        denied = "banned-op"
        policy = PolicySpec(
            policy_id="test-deny-step",
            deny_tool_ids=(denied,),
        )
        orch = _build_orchestrator(policy, emit_tool=True, tool_name=denied)
        summary = asyncio.run(orch.run())
        verdict = summary.policy_verdict
        assert verdict is not None
        assert verdict.verdict == Verdict.FAIL
        # AGENT_TOOL_CALL is emitted at step 1 (after AGENT_PLAN at step 0).
        assert 1 in verdict.citations, (
            f"Expected step 1 cited, got citations={verdict.citations}"
        )

    def test_non_denied_tool_yields_pass(self) -> None:
        """A tool not on the deny list must not trigger a FAIL."""
        policy = PolicySpec(
            policy_id="test-deny-other",
            deny_tool_ids=("other-tool",),
        )
        orch = _build_orchestrator(
            policy, emit_tool=True, tool_name="safe-tool"
        )
        summary = asyncio.run(orch.run())
        assert summary.policy_verdict is not None
        assert summary.policy_verdict.verdict == Verdict.PASS

    def test_no_tool_call_yields_pass(self) -> None:
        """A run with no tool calls must always pass the deny-list check."""
        policy = PolicySpec(
            policy_id="test-no-tool",
            deny_tool_ids=("any-tool",),
        )
        orch = _build_orchestrator(policy, emit_tool=False)
        summary = asyncio.run(orch.run())
        assert summary.policy_verdict is not None
        assert summary.policy_verdict.verdict == Verdict.PASS

