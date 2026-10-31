"""Integration test: the run-level deny-list and allow-list catch a real
tool call from the pipeline (docs/adr/0006 D14, D15; R25 slice 3).

    benchmark with the toy environment  →  MockAgentRunner (scripted)
                                        →  LocalOrchestrator
                                                  ↓
                                          BasicPolicyChecker

The scripted mock makes its calls through the task's environment, so the
harness records them; the checker reads those records. A call the agent
only *reports* -- the mock's ``emit_fake_tool_call`` mode, which never
reaches the environment -- no longer trips either list (fail-first for
D14: before R25 the self-report alone did).

Verdict citations: a record-based finding carries ``refs`` into the
``tool_calls`` stream and leaves the legacy ``citations`` empty.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

from agentevalops.agents.mock_runner import MockAgentRunner
from agentevalops.core.schemas import (
    AgentOutput,
    EnvHandle,
    EvaluationResult,
    PolicySpec,
    Ref,
    RunConfig,
    TaskSpec,
    Verdict,
)
from agentevalops.core.types import AgentId, BackendId, RunId, TaskId
from agentevalops.environments import toy_environment
from agentevalops.evaluators.deterministic import DeterministicEvaluator
from agentevalops.orchestration.local import LocalOrchestrator
from agentevalops.policy.basic_checker import BasicPolicyChecker
from agentevalops.stores.memory import InMemoryTraceStore

TASK = TaskId("integ-001")


class _ToolCallAdapter:
    """Minimal single-task benchmark adapter over the toy environment."""

    benchmark_id: str = "integ-toy"
    benchmark_version: str = "1.0.0"

    def __init__(self, metadata: dict[str, Any] | None = None) -> None:
        self._metadata = {"expected_output": "hello world", **(metadata or {})}

    def list_tasks(
        self,
        filter_spec: dict[str, Any] | None = None,
    ) -> Iterable[TaskSpec]:
        return [
            TaskSpec(
                task_id=TASK,
                benchmark_id=self.benchmark_id,
                description="Integration test task.",
                metadata=dict(self._metadata),
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

    def prepare_environment(self, task: TaskSpec) -> EnvHandle:
        return EnvHandle(task_id=task.task_id, tools=toy_environment(task.task_id))

    def teardown(self, handle: EnvHandle) -> None:
        pass


def _run(
    policy: PolicySpec,
    *,
    calls: list[str] = (),  # type: ignore[assignment]
    reported_only: str | None = None,
) -> Any:
    """Run one task. *calls* are made through the environment (recorded);
    *reported_only* is self-reported by the mock and never made."""
    runner: MockAgentRunner
    metadata: dict[str, Any] = {}
    if reported_only is not None:
        metadata = {"emit_fake_tool_call": True, "fake_tool_name": reported_only}
        runner = MockAgentRunner()
    else:
        script = [{"call": tool, "arguments": {}} for tool in calls]
        runner = MockAgentRunner(
            scripts={str(TASK): [*script, {"finish": "hello world"}]}
        )
    orch = LocalOrchestrator(
        run_config=RunConfig(
            run_id=RunId("integ-run"),
            agent_id=AgentId("mock-agent-v1"),
            backend_id=BackendId("local"),
        ),
        benchmark=_ToolCallAdapter(metadata),
        runner=runner,
        trace_store=InMemoryTraceStore(),
        evaluator=DeterministicEvaluator(),
        policy_checker=BasicPolicyChecker(),
        policy_spec=policy,
    )
    summary = asyncio.run(orch.run())
    assert summary.policy_verdict is not None
    return summary.policy_verdict


class TestDenyListIntegration:
    """Full-pipeline deny-list tests, over the harness records."""

    def test_denied_tool_yields_fail_with_a_record_ref(self) -> None:
        verdict = _run(
            PolicySpec(policy_id="d", deny_tool_ids=("dangerous-tool",)),
            calls=["dangerous-tool"],
        )
        assert verdict.verdict == Verdict.FAIL
        assert verdict.refs == [Ref("tool_calls", TASK, 0, 0)]
        assert verdict.citations == []
        assert (
            "denied tool 'dangerous-tool' at task integ-001 run 0 record 0"
            in verdict.notes
        )

    def test_the_ref_names_the_record_not_the_step(self) -> None:
        """The second call is record 1 whatever step the trace puts it at."""
        verdict = _run(
            PolicySpec(policy_id="d", deny_tool_ids=("banned-op",)),
            calls=["calculator", "banned-op"],
        )
        assert verdict.verdict == Verdict.FAIL
        assert verdict.refs == [Ref("tool_calls", TASK, 0, 1)]

    def test_a_denied_tool_that_was_refused_still_counts(self) -> None:
        """An attempt at an unknown tool is recorded as not executed and
        still trips the deny-list."""
        verdict = _run(
            PolicySpec(policy_id="d", deny_tool_ids=("nope",)), calls=["nope"]
        )
        assert verdict.verdict == Verdict.FAIL

    def test_non_denied_tool_passes(self) -> None:
        verdict = _run(
            PolicySpec(policy_id="d", deny_tool_ids=("dangerous-tool",)),
            calls=["calculator"],
        )
        assert verdict.verdict == Verdict.PASS
        assert verdict.refs == []

    def test_a_self_reported_call_the_harness_never_saw_does_not_trip_the_list(
        self,
    ) -> None:
        """docs/adr/0006 D14. Fail-first: before R25 the mock's self-report
        alone made the verdict FAIL with a trace citation. Now the records
        decide, and the unmatched self-report is a WARN."""
        verdict = _run(
            PolicySpec(policy_id="d", deny_tool_ids=("dangerous-tool",)),
            reported_only="dangerous-tool",
        )
        assert verdict.verdict == Verdict.WARN
        assert verdict.refs == [] and verdict.citations == []
        assert "self_report_mismatch" in verdict.notes
        assert "unrecorded_report at 0" in verdict.notes

    def test_no_tool_calls_passes(self) -> None:
        verdict = _run(PolicySpec(policy_id="d", deny_tool_ids=("dangerous-tool",)))
        assert verdict.verdict == Verdict.PASS


class TestAllowListIntegration:
    """docs/adr/0006 D15: allowed_tool_ids gets its consumer."""

    def test_a_call_outside_allowed_tool_ids_fails(self) -> None:
        """Fail-first: before R25 the field had no consumer and this passed."""
        verdict = _run(
            PolicySpec(policy_id="a", allowed_tool_ids=("calculator",)),
            calls=["calculator", "shell"],
        )
        assert verdict.verdict == Verdict.FAIL
        assert verdict.refs == [Ref("tool_calls", TASK, 0, 1)]
        assert "tool 'shell' outside allowed_tool_ids" in verdict.notes

    def test_calls_within_the_list_pass(self) -> None:
        verdict = _run(
            PolicySpec(policy_id="a", allowed_tool_ids=("calculator",)),
            calls=["calculator", "calculator"],
        )
        assert verdict.verdict == Verdict.PASS

    def test_an_empty_list_allows_everything(self) -> None:
        verdict = _run(PolicySpec(policy_id="a"), calls=["anything"])
        assert verdict.verdict == Verdict.PASS
