"""Contract tests for the human-facing Markdown report.

The report is where MiniEval's internal distinctions become useful to a
reader. These tests therefore construct every outcome directly instead of
running the benchmark: stable failure, flakiness, and an attempt that was
never evaluated must remain visibly different in the rendered document.
"""

from __future__ import annotations

from minieval.report import render_report
from minieval.schemas import (
    AgentOutput,
    Attempt,
    EvaluationResult,
    PolicyVerdict,
    RunConfig,
    RunId,
    RunSummary,
    TaskId,
    TaskResult,
    TaskSpec,
    TerminationReason,
    Verdict,
)

_RUN_ID = RunId("report-contract")


def _attempt(
    *,
    passed: bool,
    evaluated: bool = True,
    evaluation_notes: str = "",
    evaluation_citations: list[int] | None = None,
    policy_verdict: Verdict = Verdict.PASS,
    policy_notes: str = "",
    policy_citations: list[int] | None = None,
    cost_usd: float = 0.125,
) -> Attempt:
    return Attempt(
        index=0,
        output=AgentOutput(
            success=passed,
            termination_reason=(
                TerminationReason.COMPLETED
                if evaluated
                else TerminationReason.INFRA_FAILURE
            ),
            final_answer="answer" if evaluated else None,
            steps=1,
            cost_usd=cost_usd,
            error=None if evaluated else evaluation_notes,
        ),
        evaluation=EvaluationResult(
            evaluator_id="deterministic",
            passed=passed,
            evaluated=evaluated,
            citations=evaluation_citations or [],
            notes=evaluation_notes,
        ),
        policy=PolicyVerdict(
            policy_id="default",
            verdict=policy_verdict,
            citations=policy_citations or [],
            notes=policy_notes,
        ),
    )


def _task(task_id: str, description: str, attempts: list[Attempt]) -> TaskResult:
    for index, attempt in enumerate(attempts):
        attempt.index = index
    return TaskResult(
        task=TaskSpec(
            task_id=TaskId(task_id),
            description=description,
            criteria={"expected_substring": "answer"},
        ),
        attempts=attempts,
    )


def _summary() -> RunSummary:
    return RunSummary(
        run_id=_RUN_ID,
        config=RunConfig(
            run_id=_RUN_ID,
            runner="mock",
            model="mock-v1",
            repeats=2,
        ),
        task_results=[
            _task(
                "PASS",
                "always passes",
                [
                    _attempt(passed=True),
                    _attempt(
                        passed=True,
                        policy_verdict=Verdict.FAIL,
                        policy_notes="denied tool call",
                        policy_citations=[4],
                    ),
                ],
            ),
            _task(
                "FLAKY",
                "passes inconsistently",
                [
                    _attempt(passed=True),
                    _attempt(
                        passed=False,
                        evaluation_notes="answer mismatch",
                        evaluation_citations=[2],
                    ),
                ],
            ),
            _task(
                "FAIL",
                "always fails",
                [
                    _attempt(
                        passed=False,
                        evaluation_notes="required tool missing",
                    ),
                    _attempt(passed=False, evaluation_notes="answer mismatch"),
                ],
            ),
            _task(
                "NOTEVAL",
                "infrastructure failed",
                [
                    _attempt(
                        passed=False,
                        evaluated=False,
                        evaluation_notes="model API unavailable",
                        policy_verdict=Verdict.WARN,
                        policy_notes="policy not assessed",
                    ),
                    _attempt(
                        passed=False,
                        evaluated=False,
                        evaluation_notes="model API unavailable",
                        policy_verdict=Verdict.WARN,
                        policy_notes="policy not assessed",
                    ),
                ],
            ),
        ],
    )


def test_header_reports_run_metadata_partition_and_unverified_cost() -> None:
    report = render_report(_summary())

    assert report.startswith("# MiniEval Report\n")
    assert "- Run ID: `report-contract`" in report
    assert "- Runner: `mock`" in report
    assert "- Model: `mock-v1`" in report
    assert "- Repeats: 2" in report
    assert "- Total attempts: 8" in report
    assert "- Passed attempts: 3" in report
    assert "- Failed attempts: 3" in report
    assert "- Not evaluated attempts: 2" in report
    assert "- Reported cost (not invoice-verified): $1.000000" in report


def test_task_table_reports_k_over_n_evaluated_count_stability_and_policy() -> None:
    report = render_report(_summary())

    assert (
        "| `PASS` | always passes | 2/2 | 2/2 | Stable pass | fail: 1, pass: 1 |"
    ) in report
    assert (
        "| `FLAKY` | passes inconsistently | 1/2 | 2/2 | Flaky | pass: 2 |"
    ) in report
    assert "| `FAIL` | always fails | 0/2 | 2/2 | Stable fail | pass: 2 |" in report
    assert (
        "| `NOTEVAL` | infrastructure failed | 0/2 | 0/2 | Not evaluated | warn: 2 |"
    ) in report


def test_every_stability_group_names_its_tasks() -> None:
    report = render_report(_summary())

    assert "- Stable pass: `PASS`" in report
    assert "- Flaky: `FLAKY`" in report
    assert "- Stable fail: `FAIL`" in report
    assert "- Not evaluated: `NOTEVAL`" in report


def test_not_evaluated_is_not_collapsed_into_failed() -> None:
    """Absent evidence is its own category, not a failed judgement."""
    report = render_report(_summary())

    assert "- Failed attempts: 3" in report
    assert "- Not evaluated attempts: 2" in report
    assert "### `FAIL` — always fails" in report
    assert "### `NOTEVAL` — infrastructure failed" in report
    assert "Attempt 1 not evaluated" in report


def test_findings_include_evaluation_and_policy_notes_with_trace_steps() -> None:
    report = render_report(_summary())

    assert "Attempt 2 evaluation failed: answer mismatch (trace steps: 2)" in report
    assert "Attempt 2 policy fail: denied tool call (trace steps: 4)" in report
    assert "Attempt 1 evaluation failed: required tool missing" in report
    assert "Attempt 1 policy warn: policy not assessed" in report


def test_report_contains_no_percentages() -> None:
    assert "%" not in render_report(_summary())


def test_empty_summary_renders_an_explicitly_empty_report() -> None:
    summary = RunSummary(
        run_id=RunId("empty"),
        config=RunConfig(run_id=RunId("empty"), model=None, repeats=1),
    )

    report = render_report(summary)

    assert "- Model: `none`" in report
    assert "- Total attempts: 0" in report
    assert "| _No tasks_ | | | | | |" in report
    assert "- Stable pass: _none_" in report
    assert "- Flaky: _none_" in report
    assert "- Stable fail: _none_" in report
    assert "- Not evaluated: _none_" in report
    assert "No attempt-level findings." in report


def test_table_escapes_markdown_pipes_and_flattens_newlines() -> None:
    summary = RunSummary(
        run_id=RunId("markdown"),
        config=RunConfig(run_id=RunId("markdown")),
        task_results=[
            _task("PIPE", "left | right\ncontinued", [_attempt(passed=True)])
        ],
    )

    report = render_report(summary)

    assert "| `PIPE` | left \\| right continued |" in report
    assert report.endswith("\n")
