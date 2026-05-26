"""Tests for SimpleScorer."""

from __future__ import annotations

from agentevalops.core.schemas import EvaluationResult
from agentevalops.core.types import RunId
from agentevalops.scorers.simple import SimpleScorer


def _make_result(passed: bool, score: float | None = None) -> EvaluationResult:
    return EvaluationResult(
        evaluator_id="ev",
        evaluator_kind="deterministic",
        score=score if score is not None else (1.0 if passed else 0.0),
        passed=passed,
    )


def test_scorer_id() -> None:
    assert SimpleScorer().scorer_id == "simple-v1"


def test_empty_results() -> None:
    scorer = SimpleScorer()
    result = scorer.score(RunId("r1"), [])
    assert result.total_tasks == 0
    assert result.passed_tasks == 0
    assert result.failed_tasks == 0
    assert result.pass_rate == 0.0
    assert result.aggregate_score == 0.0


def test_all_pass_results() -> None:
    scorer = SimpleScorer()
    results = [_make_result(True), _make_result(True), _make_result(True)]
    sr = scorer.score(RunId("r2"), results)
    assert sr.passed_tasks == 3
    assert sr.failed_tasks == 0
    assert sr.pass_rate == 1.0


def test_mixed_results() -> None:
    scorer = SimpleScorer()
    results = [_make_result(True), _make_result(False), _make_result(True)]
    sr = scorer.score(RunId("r3"), results)
    assert sr.passed_tasks == 2
    assert sr.failed_tasks == 1
    assert sr.total_tasks == 3


def test_pass_rate_correct() -> None:
    scorer = SimpleScorer()
    results = [_make_result(True), _make_result(False)]
    sr = scorer.score(RunId("r4"), results)
    assert sr.pass_rate == 0.5


def test_aggregate_score_is_average() -> None:
    scorer = SimpleScorer()
    results = [_make_result(True, score=1.0), _make_result(False, score=0.0)]
    sr = scorer.score(RunId("r5"), results)
    assert sr.aggregate_score == 0.5


def test_failed_count_correct() -> None:
    scorer = SimpleScorer()
    results = [_make_result(False)] * 4 + [_make_result(True)]
    sr = scorer.score(RunId("r6"), results)
    assert sr.failed_tasks == 4
    assert sr.passed_tasks == 1


def test_does_not_mutate_input() -> None:
    scorer = SimpleScorer()
    original = [_make_result(True), _make_result(False)]
    original_len = len(original)
    scorer.score(RunId("r7"), original)
    assert len(original) == original_len


def test_scorer_id_field_in_result() -> None:
    scorer = SimpleScorer()
    sr = scorer.score(RunId("r8"), [_make_result(True)])
    assert sr.scorer_id == "simple-v1"


def test_run_id_propagated() -> None:
    scorer = SimpleScorer()
    sr = scorer.score(RunId("my-run"), [_make_result(True)])
    assert sr.run_id == RunId("my-run")
