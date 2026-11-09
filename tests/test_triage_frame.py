"""The triage sampling frame (ROADMAP R38, slice 1).

Every failed task run, plus ceil(share x passing) sampled passing runs --
the random passing slice is what finds an evaluator's blind spots, and is
never skipped -- shuffled together once, seeded from the run's own seed in
the D6 shape, so two reviewers of one bundle see the same order and
cannot read the harness's verdict off a run's position. Not-evaluated
runs are counted, not walked, unless asked for.
"""

from __future__ import annotations

import hashlib
import json
import math
import random

import pytest
from reference_bundles import ReferenceBundles

from agentevalops.core.schemas import (
    AgentOutput,
    EvaluationResult,
    TaskRunResult,
    TerminationReason,
)
from agentevalops.core.types import TaskId
from agentevalops.triage.frame import (
    Frame,
    FrameEntry,
    load_frame,
    remaining,
    sampling_frame,
    triage_seed,
)
from agentevalops.triage.store import Annotation


def _result(
    task: str, rep: int = 0, *, passed: bool = True, evaluated: bool = True
) -> TaskRunResult:
    return TaskRunResult(
        task_id=TaskId(task),
        output=AgentOutput(
            success=True, termination_reason=TerminationReason.COMPLETED
        ),
        evaluation=EvaluationResult(
            evaluator_id="v",
            evaluator_kind="verdict",
            score=float(passed),
            passed=evaluated and passed,
            evaluated=evaluated,
        ),
        event_count=0,
        repetition=rep,
    )


def _results(failed: int, passing: int, not_evaluated: int = 0) -> list[TaskRunResult]:
    return (
        [_result(f"F{n:02d}", passed=False) for n in range(failed)]
        + [_result(f"P{n:02d}") for n in range(passing)]
        + [_result(f"N{n:02d}", evaluated=False) for n in range(not_evaluated)]
    )


def test_the_triage_seed_has_the_d6_shape() -> None:
    """Fail-first: no module."""
    assert triage_seed(42) == int(hashlib.sha256(b"42:triage").hexdigest(), 16)
    assert triage_seed(42) != triage_seed(43)


def test_the_frame_is_every_failed_run_and_the_passing_share() -> None:
    frame = sampling_frame(_results(10, 25, 3), seed=7)
    harness = [e.harness for e in frame.entries]
    assert harness.count("failed") == 10
    assert harness.count("passed") == 5  # ceil(0.2 x 25)
    assert (
        frame.failed,
        frame.passing_sampled,
        frame.passing,
        frame.not_evaluated,
    ) == (
        10,
        5,
        25,
        3,
    )
    assert not any(e.task_id.startswith("N") for e in frame.entries)


@pytest.mark.parametrize(
    ("passing", "share", "sampled"),
    [
        (26, 0.2, 6),
        (25, 0.2, 5),
        (1, 0.2, 1),
        (0, 0.2, 0),
        (10, 0.0, 0),
        (7, 1.0, 7),
        (30, 0.1, 3),
    ],
)
def test_the_passing_slice_is_the_ceiling_of_its_share(
    passing: int, share: float, sampled: int
) -> None:
    """ceil in exact arithmetic: 0.2 x 25 is 5, not 5.000000000000001; one
    passing run is never sampled away."""
    frame = sampling_frame(_results(2, passing), seed=7, passing_share=share)
    assert frame.passing_sampled == sampled


@pytest.mark.parametrize("share", [-0.1, 1.5])
def test_a_share_outside_zero_to_one_is_refused(share: float) -> None:
    with pytest.raises(ValueError, match="passing_share"):
        sampling_frame(_results(1, 1), seed=7, passing_share=share)


def test_one_shuffle_of_the_whole_frame_interleaves_failures_and_passes() -> None:
    """The frame is the oracle's: the passing sample, then one shuffle of
    failed + sampled, from one random.Random(triage_seed(seed)); the
    failures are not all first."""
    results = _results(10, 25)
    frame = sampling_frame(results, seed=7)
    rng = random.Random(triage_seed(7))
    passing = sorted(
        (str(r.task_id), r.repetition) for r in results if r.evaluation.passed
    )
    failed = sorted(
        (str(r.task_id), r.repetition) for r in results if not r.evaluation.passed
    )
    sampled = rng.sample(passing, math.ceil(len(passing) / 5))
    order = [(k, "failed") for k in failed] + [(k, "passed") for k in sampled]
    rng.shuffle(order)
    assert [((e.task_id, e.repetition), e.harness) for e in frame.entries] == order
    harness = [e.harness for e in frame.entries]
    assert harness != sorted(harness)


def test_the_same_seed_gives_the_same_order_and_another_seed_another() -> None:
    results = _results(6, 20)
    assert sampling_frame(results, seed=7) == sampling_frame(results, seed=7)
    assert (
        sampling_frame(results, seed=7).entries
        != sampling_frame(results, seed=8).entries
    )


def test_the_frame_does_not_depend_on_the_order_of_the_results() -> None:
    results = _results(6, 20, 2)
    shuffled = list(results)
    random.Random(1).shuffle(shuffled)
    assert sampling_frame(shuffled, seed=7) == sampling_frame(results, seed=7)


def test_not_evaluated_runs_join_only_when_asked() -> None:
    frame = sampling_frame(_results(2, 0, 3), seed=7, include_not_evaluated=True)
    assert (
        sorted(e.harness for e in frame.entries)
        == ["failed"] * 2 + ["not_evaluated"] * 3
    )
    assert frame.not_evaluated == 3


def test_resume_skips_annotated_runs_and_never_duplicates() -> None:
    frame = sampling_frame(_results(4, 10), seed=7)
    done = {
        (e.task_id, e.repetition): Annotation(
            e.task_id, e.repetition, "fail", "", "t", "r"
        )
        for e in frame.entries[:2]
    }
    left = remaining(frame, done)
    assert left == frame.entries[2:]
    assert remaining(frame, {**done, **done}) == left
    assert remaining(frame, {}) == frame.entries


def test_the_default_seed_is_the_runs_own_and_an_explicit_one_overrides(
    reference_bundles: ReferenceBundles,
) -> None:
    bundle = reference_bundles["known-bad"].path
    seed = json.loads((bundle / "config.json").read_text("utf-8"))["run_config"]["seed"]
    frame = load_frame(bundle)
    assert frame.seed == seed
    assert frame.failed == 26 and frame.passing == 0
    assert (
        frame.run_id
        == json.loads((bundle / "summary.json").read_text("utf-8"))["run_id"]
    )
    assert load_frame(bundle, seed=seed + 1).entries != frame.entries
    assert load_frame(bundle, seed=seed + 1).seed == seed + 1


def test_a_bundles_passing_runs_are_sampled(
    reference_bundles: ReferenceBundles,
) -> None:
    frame = load_frame(reference_bundles["seed"].path)
    assert (frame.failed, frame.passing, frame.passing_sampled) == (0, 26, 6)
    assert all(
        isinstance(e, FrameEntry) and e.harness == "passed" for e in frame.entries
    )
    assert isinstance(frame, Frame)
