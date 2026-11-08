"""Bootstrap confidence intervals on pass^k (ROADMAP R36; ADR-0006 D18).

The only home of the method; every interval a bundle records is made here,
so a bundle rescored anywhere gives byte-identical intervals.

- **Statistic.** The pass^k proportion over the *decided* scenarios, those
  whose ``PassKResult.pass_k`` is not None. A scenario not evaluated is
  left out of the resampling; the caller states how many there were.
- **Resampling unit: the scenario** (a cluster bootstrap). Each of the
  ``b`` resamples draws n scenarios with replacement from the n decided
  ones, each carrying its pass^k verdict whole -- its k repetitions ride
  inside the verdict, so they can never tighten the interval -- and yields
  one proportion. Draws are ``rng.choices(verdicts, k=n)``: an index of
  ``floor(rng.random() * n)``, the same algorithm in every supported
  Python.
- **Percentiles: nearest rank, no interpolation.** Sorted ascending, the
  low bound is the proportion at index floor(0.025 (b-1)) and the high at
  ceil(0.975 (b-1)), computed in integers so no float rounding moves them.
- **Seed.** Each scope -- ``"overall"`` or a category's name -- has its own,
  ``int(sha256(f"{seed}:ci:{scope}"))`` from the run's seed, the shape of
  ``derive_seed`` (``core/perturbation.py``), and its own
  ``random.Random``.
- **Small n.** Fewer than two decided scenarios: no interval.

Standard library only.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Sequence

from agentevalops.core.schemas import PassKResult, PassRateInterval

__all__ = ["ci_seed", "pass_k_interval", "percentile_bounds"]


def ci_seed(seed: int, scope: str) -> int:
    """The bootstrap seed of *scope* (``"overall"`` or a category) in a run
    whose seed is *seed*: ``int(sha256(f"{seed}:ci:{scope}"))``."""
    return int(hashlib.sha256(f"{seed}:ci:{scope}".encode()).hexdigest(), 16)


def percentile_bounds(proportions: Sequence[float]) -> tuple[float, float]:
    """The 2.5th and 97.5th percentiles of *proportions*, by nearest rank:
    sorted ascending, the values at floor(0.025 (b-1)) and ceil(0.975 (b-1)),
    in integer arithmetic."""
    ordered = sorted(proportions)
    last = len(ordered) - 1
    return ordered[(25 * last) // 1000], ordered[-((-975 * last) // 1000)]


def pass_k_interval(
    verdicts: Sequence[PassKResult], b: int, seed: int
) -> PassRateInterval | None:
    """The 95% interval on the pass^k proportion of *verdicts*' decided
    scenarios, from *b* cluster-bootstrap resamples drawn with
    ``random.Random(seed)``; None when fewer than two are decided. *seed* is
    the scope's own (``ci_seed``) and is recorded with the interval."""
    if b < 1:
        raise ValueError(f"a bootstrap needs at least one resample, not {b}")
    decided = [r.pass_k for r in verdicts if r.pass_k is not None]
    n = len(decided)
    if n < 2:
        return None
    rng = random.Random(seed)
    proportions = [sum(rng.choices(decided, k=n)) / n for _ in range(b)]
    low, high = percentile_bounds(proportions)
    return PassRateInterval(low=low, high=high, b=b, seed=seed, n_scenarios=n)
