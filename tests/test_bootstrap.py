"""Bootstrap confidence intervals on pass^k (ROADMAP R36, slice 1).

``scorers/bootstrap.py`` is the only home of the method: a cluster
bootstrap over the decided scenarios -- each resample draws n scenarios
with replacement from the n whose pass^k verdict is decided, every one
carrying its verdict whole, so a scenario's k repetitions can never
tighten the interval -- and the nearest-rank percentiles of the B
resampled proportions. The seed of each scope is derived from the run's
seed the way ``derive_seed`` derives a task run's. A library the writer
does not call yet.
"""

from __future__ import annotations

import hashlib
import math
import random

import pytest

from agentevalops.bundles.serializers import (
    from_jsonable,
    load_pass_rate_interval,
    to_jsonable,
)
from agentevalops.core.schemas import PassKResult, PassRateInterval
from agentevalops.scorers.bootstrap import (
    ci_seed,
    pass_k_interval,
    percentile_bounds,
)


def _verdicts(outcomes: list[bool | None], k: int = 1) -> list[PassKResult]:
    """One scenario per outcome, each run *k* times: a passing scenario's k
    runs all passed; a failing one's last run failed; a not-evaluated one's
    last run was not evaluated."""
    out = []
    for n, outcome in enumerate(outcomes):
        if outcome is None:
            passed, failed, not_evaluated = k - 1, 0, 1
        elif outcome:
            passed, failed, not_evaluated = k, 0, 0
        else:
            passed, failed, not_evaluated = k - 1, 1, 0
        out.append(
            PassKResult(
                task_id=f"S{n:02d}",
                k=k,
                passed=passed,
                failed=failed,
                not_evaluated=not_evaluated,
                pass_k=outcome,
                not_evaluated_cause=None if outcome is not None else "infrastructure",
            )
        )
    return out


def _oracle(outcomes: list[bool], b: int, seed: int) -> tuple[float, float]:
    """The method as the R36 brief states it, written out independently of
    the module: B resamples of n scenarios drawn with replacement from the
    verdicts in their canonical order (fails, then passes), each the
    proportion of passes; nearest rank at floor(0.025(B-1)) and
    ceil(0.975(B-1)) of the sorted proportions."""
    rng = random.Random(seed)
    n = len(outcomes)
    population = sorted(outcomes)
    props = sorted(sum(rng.choices(population, k=n)) / n for _ in range(b))
    return props[math.floor(0.025 * (b - 1))], props[math.ceil(0.975 * (b - 1))]


SIXTY = [True] * 12 + [False] * 8


# ---------------------------------------------------------------------------
# The percentile rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("b", "low", "high"),
    [
        (1000, 24, 975),  # floor(24.975), ceil(974.025)
        (101, 2, 98),  # floor(2.5), ceil(97.5)
        (10_000, 249, 9750),  # floor(249.975), ceil(9749.025)
        (41, 1, 39),  # floor(1.0), ceil(39.0): no rounding either way
        (100, 2, 97),  # floor(2.475), ceil(96.525)
    ],
)
def test_the_bounds_are_the_nearest_ranks_without_interpolation(
    b: int, low: int, high: int
) -> None:
    """Fail-first: no module. The proportions are their own indexes,
    shuffled, so each bound names the index it was taken from."""
    values = [float(i) for i in range(b)]
    random.Random(0).shuffle(values)
    assert percentile_bounds(values) == (float(low), float(high))


def test_a_single_resample_is_both_bounds() -> None:
    assert percentile_bounds([0.4]) == (0.4, 0.4)


# ---------------------------------------------------------------------------
# The seed
# ---------------------------------------------------------------------------


def test_each_scope_has_its_own_seed_derived_like_a_task_runs() -> None:
    for scope in ("overall", "task_execution"):
        expected = int(hashlib.sha256(f"42:ci:{scope}".encode()).hexdigest(), 16)
        assert ci_seed(42, scope) == expected
    assert ci_seed(42, "overall") != ci_seed(42, "task_execution")
    assert ci_seed(42, "overall") != ci_seed(43, "overall")


# ---------------------------------------------------------------------------
# The interval
# ---------------------------------------------------------------------------


def test_the_interval_is_the_method_as_stated() -> None:
    """The module's interval is the oracle's, to the last bit, for a seeded
    sixty-percent set, and it records what reproduces it."""
    seed = ci_seed(7, "overall")
    interval = pass_k_interval(_verdicts(list(SIXTY)), 2000, seed)
    assert interval is not None
    assert (interval.low, interval.high) == _oracle(SIXTY, 2000, seed)
    assert interval == PassRateInterval(
        low=interval.low,
        high=interval.high,
        b=2000,
        seed=seed,
        n_scenarios=20,
        method="cluster-bootstrap-percentile",
    )


@pytest.mark.parametrize("b", [1, 2, 3, 5, 8, 13])
def test_exactly_b_resamples_are_drawn(b: int) -> None:
    """At small B every resample shows in the bounds, so one too many or
    too few moves them (self-mutation pass: at B = 2000 over 20 scenarios
    an extra draw changed nothing the other tests read)."""
    outcomes = [True, False, True, True, False, True, False]
    seed = ci_seed(11, "overall")
    interval = pass_k_interval(_verdicts(outcomes), b, seed)
    assert interval is not None
    assert (interval.low, interval.high) == _oracle(outcomes, b, seed)


def test_the_same_inputs_and_seed_give_the_same_interval() -> None:
    seed = ci_seed(7, "overall")
    first = pass_k_interval(_verdicts(list(SIXTY)), 1000, seed)
    again = pass_k_interval(_verdicts(list(SIXTY)), 1000, seed)
    assert first is not None and first == again


def test_a_sixty_percent_set_has_an_interval_around_sixty_percent() -> None:
    interval = pass_k_interval(_verdicts(list(SIXTY)), 10_000, ci_seed(7, "overall"))
    assert interval is not None
    assert interval.low < 0.60 < interval.high
    assert 0.30 <= interval.low and interval.high <= 0.85


def test_a_scenarios_repetitions_ride_inside_its_verdict() -> None:
    """The cluster property: the same scenario verdicts, run once or three
    times each, give the same interval -- the repetitions are not resampled
    and cannot tighten it."""
    seed = ci_seed(7, "overall")
    once = pass_k_interval(_verdicts(list(SIXTY), k=1), 2000, seed)
    thrice = pass_k_interval(_verdicts(list(SIXTY), k=3), 2000, seed)
    assert once is not None and once == thrice
    assert thrice is not None and thrice.n_scenarios == 20


def test_the_scenarios_order_does_not_move_the_interval() -> None:
    """Slice 2: the decided verdicts are resampled in a canonical order, so
    the interval is a function of how many of the n passed, B and the seed.
    Fail-first: interleaving the passes and fails moved it."""
    seed = ci_seed(7, "overall")
    interleaved = [n % 5 < 3 for n in range(20)]
    assert sorted(interleaved) == sorted(SIXTY)
    blocked = pass_k_interval(_verdicts(list(SIXTY)), 2000, seed)
    mixed = pass_k_interval(_verdicts(interleaved), 2000, seed)
    reverse = pass_k_interval(_verdicts(list(reversed(SIXTY))), 2000, seed)
    assert blocked == mixed == reverse


def test_not_evaluated_scenarios_are_left_out_of_the_resampling() -> None:
    """Three scenarios not evaluated beside the twenty decided: the interval
    is the twenty's, and n counts only them."""
    seed = ci_seed(7, "overall")
    decided = pass_k_interval(_verdicts(list(SIXTY)), 2000, seed)
    outcomes = [None, *SIXTY[:10], None, *SIXTY[10:], None]
    mixed = pass_k_interval(_verdicts(outcomes), 2000, seed)
    assert mixed is not None
    assert mixed == decided
    assert mixed.n_scenarios == 20


def test_an_all_pass_set_has_an_upper_bound_of_one_and_a_stated_lower_bound() -> None:
    interval = pass_k_interval(_verdicts([True] * 8), 1000, ci_seed(7, "overall"))
    assert interval is not None
    assert interval.high == 1.0
    assert interval.low == 1.0
    assert interval.n_scenarios == 8


@pytest.mark.parametrize(
    "outcomes", [[], [None], [None, None], [True], [False, None]]
)
def test_fewer_than_two_decided_scenarios_have_no_interval(
    outcomes: list[bool | None],
) -> None:
    assert pass_k_interval(_verdicts(outcomes), 1000, ci_seed(7, "overall")) is None


def test_two_decided_scenarios_have_one() -> None:
    interval = pass_k_interval(_verdicts([True, False]), 1000, ci_seed(7, "overall"))
    assert interval is not None
    assert interval.n_scenarios == 2
    assert (interval.low, interval.high) == _oracle([True, False], 1000, interval.seed)


@pytest.mark.parametrize("b", [0, -1])
def test_a_resample_count_below_one_is_refused(b: int) -> None:
    with pytest.raises(ValueError, match="at least one resample"):
        pass_k_interval(_verdicts(list(SIXTY)), b, ci_seed(7, "overall"))


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------


def test_the_interval_round_trips_through_its_loader() -> None:
    interval = pass_k_interval(_verdicts(list(SIXTY)), 1000, ci_seed(7, "overall"))
    assert interval is not None
    data = to_jsonable(interval)
    assert data == {
        "low": interval.low,
        "high": interval.high,
        "b": 1000,
        "seed": ci_seed(7, "overall"),
        "n_scenarios": 20,
        "method": "cluster-bootstrap-percentile",
    }
    assert load_pass_rate_interval(data) == interval
    assert from_jsonable(PassRateInterval, data) == interval


def test_a_record_with_another_method_does_not_load() -> None:
    interval = pass_k_interval(_verdicts(list(SIXTY)), 1000, ci_seed(7, "overall"))
    data = {**to_jsonable(interval), "method": "wilson"}
    with pytest.raises(ValueError, match="cluster-bootstrap-percentile"):
        load_pass_rate_interval(data)
