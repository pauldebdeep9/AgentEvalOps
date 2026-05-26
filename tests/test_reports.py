"""Tests for the Markdown report renderer."""

from __future__ import annotations

from agentevalops.core.schemas import (
    AgentOutput,
    EvaluationResult,
    PolicyVerdict,
    ResourceLimits,
    RunConfig,
    TerminationReason,
    TraceEvent,
    TraceEventKind,
    Verdict,
)
from agentevalops.core.types import BACKEND_LOCAL, AgentId, RunId, TaskId
from agentevalops.orchestration.local import RunSummary, TaskRunResult
from agentevalops.reports.markdown import render_report


def _make_summary() -> tuple[RunConfig, RunSummary, list[TraceEvent]]:
    run_id = RunId("report-test-001")
    run_config = RunConfig(
        run_id=run_id,
        agent_id=AgentId("mock"),
        backend_id=BACKEND_LOCAL,
        resource_limits=ResourceLimits(max_tokens=1000),
    )
    event = TraceEvent(
        run_id=run_id,
        step_index=0,
        kind=TraceEventKind.AGENT_PLAN,
        payload={},
    )
    output = AgentOutput(
        success=True,
        termination_reason=TerminationReason.COMPLETED,
        total_tokens=10,
    )
    evaluation = EvaluationResult(
        evaluator_id="ev",
        evaluator_kind="deterministic",
        score=1.0,
        passed=True,
        notes="ok",
    )
    policy_verdict = PolicyVerdict(
        checker_id="basic-policy-v1",
        policy_id="default",
        verdict=Verdict.PASS,
        citations=[],
        notes="all checks passed",
    )
    task_result = TaskRunResult(
        task_id=TaskId("toy-001"),
        output=output,
        evaluation=evaluation,
        event_count=1,
    )
    summary = RunSummary(
        run_id=run_id,
        task_results=[task_result],
        policy_verdict=policy_verdict,
        total_tasks=1,
        passed_tasks=1,
        total_cost_usd=0.0,
        total_tokens=10,
    )
    return run_config, summary, [event]


def test_render_report_returns_string() -> None:
    run_config, summary, events = _make_summary()
    result = render_report(run_config, summary, events)
    assert isinstance(result, str)


def test_report_contains_run_id() -> None:
    run_config, summary, events = _make_summary()
    report = render_report(run_config, summary, events, "config.yaml")
    assert "report-test-001" in report


def test_report_contains_config_name() -> None:
    run_config, summary, events = _make_summary()
    report = render_report(run_config, summary, events, "my_config.yaml")
    assert "my_config.yaml" in report


def test_report_contains_pass_fraction() -> None:
    run_config, summary, events = _make_summary()
    report = render_report(run_config, summary, events)
    assert "1 / 1 passed" in report


def test_report_contains_trace_event_count() -> None:
    run_config, summary, events = _make_summary()
    report = render_report(run_config, summary, events)
    assert "1" in report  # one event


def test_report_contains_policy_verdict() -> None:
    run_config, summary, events = _make_summary()
    report = render_report(run_config, summary, events)
    assert "PASS" in report
    assert "basic-policy-v1" in report


def test_report_contains_task_table_row() -> None:
    run_config, summary, events = _make_summary()
    report = render_report(run_config, summary, events)
    assert "toy-001" in report
    assert "yes" in report  # passed column


def test_report_no_policy_verdict() -> None:
    run_config, summary, events = _make_summary()
    summary.policy_verdict = None
    report = render_report(run_config, summary, events)
    assert "No policy check was run" in report


def test_report_starts_with_heading() -> None:
    run_config, summary, events = _make_summary()
    report = render_report(run_config, summary, events)
    assert report.startswith("# AgentEvalOps Run Report")


def test_report_contains_pass_rate() -> None:
    run_config, summary, events = _make_summary()
    report = render_report(run_config, summary, events)
    assert "100%" in report


def test_report_contains_failed_count() -> None:
    run_config, summary, events = _make_summary()
    report = render_report(run_config, summary, events)
    assert "0 failed" in report


def test_report_has_task_results_section() -> None:
    run_config, summary, events = _make_summary()
    report = render_report(run_config, summary, events)
    assert "## Task Results" in report


def test_report_has_trace_summary_section() -> None:
    run_config, summary, events = _make_summary()
    report = render_report(run_config, summary, events)
    assert "## Trace Summary" in report
