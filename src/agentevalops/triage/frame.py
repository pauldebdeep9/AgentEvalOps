"""The triage sampling frame: which task runs a reviewer walks, and in
what order (ROADMAP R38).

- **Every failed task run** -- evaluated, not passed.
- **A share of the passing ones**, ``ceil(share x passing)`` sampled
  (0.2 unless set; the ceiling is taken in exact arithmetic). A random
  passing slice is what finds an evaluator's blind spots, so it is never
  skipped: one passing run is sampled even at a small share.
- **Not-evaluated runs** are counted for the session's header and walked
  only when asked for.
- **One shuffle of the whole frame**, so failures and passes interleave
  and a reviewer cannot read the harness's verdict off a run's position.

The seed has the shape of ``derive_seed`` (docs/adr/0006 D6):
``int(sha256(f"{seed}:triage"))`` seeds one ``random.Random``, which draws
the passing sample and then shuffles. ``seed`` is the run's own, from the
bundle's ``config.json``, so two reviewers of one bundle see one order; an
explicit seed overrides it. The runs are sorted by (task_id, repetition)
before any draw, so the frame does not depend on the order the records
list them in.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Literal

from agentevalops.bundles.reader import BundleReader
from agentevalops.bundles.serializers import from_jsonable, load_run_config
from agentevalops.core.schemas import TaskRunResult
from agentevalops.triage.store import Annotation

__all__ = [
    "DEFAULT_PASSING_SHARE",
    "Frame",
    "FrameEntry",
    "load_frame",
    "remaining",
    "sampling_frame",
    "triage_seed",
]

DEFAULT_PASSING_SHARE = 0.2

#: The harness's verdict on a task run in the frame.
Harness = Literal["failed", "passed", "not_evaluated"]


@dataclass(frozen=True)
class FrameEntry:
    task_id: str
    repetition: int
    harness: Harness


@dataclass(frozen=True)
class Frame:
    """The task runs to walk, in walking order, and the counts behind them:
    ``failed`` (all in the frame), ``passing`` (of which
    ``passing_sampled`` are in it), ``not_evaluated`` (in it only when
    asked for), and the run ``seed`` the order derives from."""

    entries: tuple[FrameEntry, ...]
    seed: int
    failed: int
    passing: int
    passing_sampled: int
    not_evaluated: int
    run_id: str = ""


def triage_seed(seed: int) -> int:
    """The shuffle's seed: ``int(sha256(f"{seed}:triage"))``."""
    return int(hashlib.sha256(f"{seed}:triage".encode()).hexdigest(), 16)


def sampling_frame(
    task_results: Sequence[TaskRunResult],
    *,
    seed: int,
    passing_share: float = DEFAULT_PASSING_SHARE,
    include_not_evaluated: bool = False,
    run_id: str = "",
) -> Frame:
    """Every failed run of *task_results*, ``ceil(passing_share x passing)``
    sampled passing ones, and with *include_not_evaluated* the
    not-evaluated ones, shuffled together once."""
    if not 0 <= passing_share <= 1:
        raise ValueError(f"passing_share must be between 0 and 1, got {passing_share}")
    failed: list[tuple[str, int]] = []
    passing: list[tuple[str, int]] = []
    not_evaluated: list[tuple[str, int]] = []
    for result in task_results:
        key = (str(result.task_id), result.repetition)
        if not result.evaluation.evaluated:
            not_evaluated.append(key)
        elif result.evaluation.passed:
            passing.append(key)
        else:
            failed.append(key)
    failed.sort()
    passing.sort()
    not_evaluated.sort()
    rng = random.Random(triage_seed(seed))
    share = math.ceil(Fraction(str(passing_share)) * len(passing))
    sampled = rng.sample(passing, share)
    order: list[tuple[tuple[str, int], Harness]] = [(k, "failed") for k in failed]
    order += [(k, "passed") for k in sampled]
    if include_not_evaluated:
        order += [(k, "not_evaluated") for k in not_evaluated]
    rng.shuffle(order)
    return Frame(
        entries=tuple(FrameEntry(task, rep, harness) for (task, rep), harness in order),
        seed=seed,
        failed=len(failed),
        passing=len(passing),
        passing_sampled=share,
        not_evaluated=len(not_evaluated),
        run_id=run_id,
    )


def remaining(
    frame: Frame, annotations: Mapping[tuple[str, int], Annotation]
) -> tuple[FrameEntry, ...]:
    """The frame's entries not yet annotated, in walking order: a resumed
    session starts where the last one stopped."""
    return tuple(
        e for e in frame.entries if (e.task_id, e.repetition) not in annotations
    )


def load_frame(
    bundle: Path,
    *,
    seed: int | None = None,
    passing_share: float = DEFAULT_PASSING_SHARE,
    include_not_evaluated: bool = False,
) -> Frame:
    """The sampling frame of the bundle at *bundle*, read through
    ``BundleReader`` and the typed loaders -- never written. *seed*
    defaults to the run's own, from ``config.json``."""
    loaded = BundleReader(Path(bundle)).read()
    run_config = load_run_config(loaded.config.get("run_config"))
    results = [
        from_jsonable(TaskRunResult, item, where="summary.json task_results")
        for item in loaded.summary.get("task_results") or []
    ]
    return sampling_frame(
        results,
        seed=run_config.seed if seed is None else seed,
        passing_share=passing_share,
        include_not_evaluated=include_not_evaluated,
        run_id=str(loaded.summary.get("run_id", run_config.run_id)),
    )
