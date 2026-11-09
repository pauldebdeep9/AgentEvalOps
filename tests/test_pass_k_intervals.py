"""Bootstrap intervals on pass^k, wired into every run (ROADMAP R36, slice 2).

``bootstrap_samples`` (B) sits beside ``repetitions`` and ``seed``: in the
system declaration when there is one, the run config's top level
otherwise; 10,000 unless set, at least 100, and 0 turns intervals off.
``aggregate()`` -- the one place the orchestrator and rescore both derive a
summary -- gives the overall rate and each category's a seeded 95% interval
(``scorers/bootstrap.py``); rescore reads B and the seed from the bundle's
own ``config.json``, so the intervals it derives are the recorded ones,
byte for byte. A bundle from before R36 records no B and gets none. The
gate still reads the rate; the interval is context, in ``report.md`` only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import fast_yaml
import pytest
from reference_bundles import CONFIGS, ReferenceBundles
from typer.testing import CliRunner

from agentevalops.bundles.validator import validate_bundle
from agentevalops.cli import app, pass_k_phrase
from agentevalops.config.loader import load_run_config
from agentevalops.core.errors import ConfigurationError
from agentevalops.core.schemas import (
    CategoryPassK,
    PassKResult,
    PassRateInterval,
    RunSummary,
)
from agentevalops.core.types import RunId
from agentevalops.plans.io import to_document
from agentevalops.reports.markdown import repeatability_section
from agentevalops.scorers.bootstrap import ci_seed

FIXTURES = Path(__file__).parent / "fixtures" / "bundles"
HOMES = ("run config", "system declaration")
PRICE = 0.25

#: Under the Repeatability heading once, verbatim (owner, R36 slice 3).
HONESTY = (
    "The interval is the 2.5th–97.5th percentile of B scenario-level bootstrap "
    "resamples of this run's decided scenarios, derived from the run seed. It "
    "expresses sampling uncertainty over these scenarios only; it says nothing "
    "about scenarios outside the registered plan, and the gate judges the "
    "observed rate, not the interval. At 0/n or n/n the interval is zero-width "
    "and carries no uncertainty information; the rule-of-three bound beside it "
    "is display-only."
)


def _hex(seed: int, scope: str) -> str:
    return f"{ci_seed(seed, scope):064x}"


def _toy(tmp_path: Path, **changes: Any) -> Path:
    doc = fast_yaml.safe_load((CONFIGS / "toy_smoke.yaml").read_text("utf-8"))
    doc.update(changes)
    path = tmp_path / "toy.yaml"
    path.write_text(fast_yaml.safe_dump(doc), encoding="utf-8")
    return path


def _seed(tmp_path: Path, run: dict[str, Any], system: dict[str, Any]) -> Path:
    declared = fast_yaml.safe_load((CONFIGS / "seed_system.yaml").read_text("utf-8"))
    declared.update(system)
    (tmp_path / "system.yaml").write_text(
        fast_yaml.safe_dump(declared), encoding="utf-8"
    )
    doc = fast_yaml.safe_load(
        (CONFIGS / "agentic_baseline_mock.yaml").read_text("utf-8")
    )
    doc["system"] = "system.yaml"
    doc.update(run)
    path = tmp_path / "seed.yaml"
    path.write_text(fast_yaml.safe_dump(doc), encoding="utf-8")
    return path


def _config(tmp_path: Path, home: str, value: Any) -> Path:
    if home == "run config":
        return _toy(tmp_path, bootstrap_samples=value)
    return _seed(tmp_path, {}, {"bootstrap_samples": value})


def _run(config: Path, out: Path) -> str:
    result = CliRunner().invoke(
        app, ["run", "--config", str(config), "--output", str(out)]
    )
    assert result.exit_code == 0, result.output
    return result.output


def _json(path: Path) -> Any:
    return json.loads(path.read_text("utf-8"))


# ---------------------------------------------------------------------------
# B in its two homes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("home", HOMES)
def test_b_is_ten_thousand_unless_set(tmp_path: Path, home: str) -> None:
    config = _toy(tmp_path) if home == "run config" else _seed(tmp_path, {}, {})
    assert load_run_config(config).run_config.bootstrap_samples == 10_000


@pytest.mark.parametrize("home", HOMES)
@pytest.mark.parametrize("value", [0, 100, 2500])
def test_b_is_read_from_either_home(tmp_path: Path, home: str, value: int) -> None:
    config = _config(tmp_path, home, value)
    assert load_run_config(config).run_config.bootstrap_samples == value


@pytest.mark.parametrize("home", HOMES)
@pytest.mark.parametrize("value", ["many", [100], {"b": 100}])
def test_a_non_number_b_names_the_field(
    tmp_path: Path, home: str, value: Any
) -> None:
    with pytest.raises(
        ConfigurationError, match="bootstrap_samples must be an integer"
    ):
        load_run_config(_config(tmp_path, home, value))


@pytest.mark.parametrize("home", HOMES)
@pytest.mark.parametrize("value", [1, 99, -5])
def test_b_below_one_hundred_is_refused_unless_zero(
    tmp_path: Path, home: str, value: int
) -> None:
    with pytest.raises(
        ConfigurationError,
        match=f"bootstrap_samples must be 0 \\(off\\) or at least 100, got {value}",
    ):
        load_run_config(_config(tmp_path, home, value))


def test_b_in_both_homes_is_refused(tmp_path: Path) -> None:
    config = _seed(tmp_path, {"bootstrap_samples": 500}, {"bootstrap_samples": 500})
    with pytest.raises(
        ConfigurationError, match="bootstrap_samples is set in the system"
    ):
        load_run_config(config)


def test_a_declared_b_rides_the_plan_and_an_undeclared_one_does_not(
    tmp_path: Path, reference_bundles: ReferenceBundles
) -> None:
    """A declaration that sets B records it in the plan's system block; one
    that does not leaves the plan as it was, so its hash does not move."""
    from agentevalops.config.system import load_system_declaration

    declared = _seed(tmp_path, {}, {"bootstrap_samples": 500})
    system = load_system_declaration(declared.parent / "system.yaml")
    assert system.bootstrap_samples == 500
    shared = reference_bundles["seed"].path
    assert "bootstrap_samples" not in (shared / "test_plan.json").read_text("utf-8")
    plan = _json(shared / "test_plan.json")
    assert "bootstrap_samples" not in plan["system"]
    unset = load_system_declaration(CONFIGS / "seed_system.yaml")
    assert unset.bootstrap_samples is None
    from dataclasses import replace

    from agentevalops.plans.io import plan_from_document

    doc = plan_from_document(plan)
    assert doc.system is not None
    with_b = replace(doc, system=replace(doc.system, bootstrap_samples=500))
    assert to_document(with_b)["system"]["bootstrap_samples"] == 500
    assert plan_from_document(to_document(with_b)) == with_b


# ---------------------------------------------------------------------------
# The intervals a run records
# ---------------------------------------------------------------------------


def test_a_run_records_an_interval_overall_and_per_category(
    reference_bundles: ReferenceBundles,
) -> None:
    """Fail-first: summary.json held no interval. Each records its b, its
    scope's seed, derived from the run's, and its n decided scenarios."""
    bundle = reference_bundles["seed"].path
    config = _json(bundle / "config.json")["run_config"]
    assert config["bootstrap_samples"] == 10_000
    seed = config["seed"]
    summary = _json(bundle / "summary.json")
    assert summary["overall_pass_k_interval"] == {
        "low": 1.0,
        "high": 1.0,
        "b": 10_000,
        "seed": _hex(seed, "overall"),
        "n_scenarios": 26,
        "method": "cluster-bootstrap-percentile",
    }
    for roll_up in summary["pass_k_by_category"]:
        assert roll_up["interval"] == {
            "low": 1.0,
            "high": 1.0,
            "b": 10_000,
            "seed": _hex(seed, roll_up["category"]),
            "n_scenarios": roll_up["scenarios"],
            "method": "cluster-bootstrap-percentile",
        }
    bad = _json(reference_bundles["known-bad"].path / "summary.json")
    overall = bad["overall_pass_k_interval"]
    assert (overall["low"], overall["high"]) == (0.0, 0.0)


def test_b_zero_leaves_every_interval_out(tmp_path: Path) -> None:
    out = tmp_path / "off"
    output = _run(_toy(tmp_path, bootstrap_samples=0), out)
    assert _json(out / "config.json")["run_config"]["bootstrap_samples"] == 0
    summary = (out / "summary.json").read_text("utf-8")
    assert "interval" not in summary
    report = (out / "report.md").read_text("utf-8")
    assert "95% interval" not in report
    assert "- Overall:" not in report
    assert "Raw counts; no confidence intervals." in report
    assert "interval" not in output
    assert validate_bundle(out).errors == []


def test_rescoring_twice_gives_byte_identical_intervals(
    reference_bundles: ReferenceBundles, tmp_path: Path
) -> None:
    """Rescore reads B and the seed from the bundle's own config.json: two
    rescores write the summary the run wrote, byte for byte."""
    bundle = reference_bundles["seed"].path
    written = []
    for n in (1, 2):
        out = tmp_path / f"rescore-{n}"
        result = CliRunner().invoke(
            app, ["rescore", "--bundle", str(bundle), "--output", str(out)]
        )
        assert result.exit_code == 0, result.output
        assert result.output.rstrip().endswith("RESCORE: MATCH")
        written.append((out / "summary.json").read_bytes())
    assert written[0] == written[1] == (bundle / "summary.json").read_bytes()
    assert b"overall_pass_k_interval" in written[0]
    seed = _json(bundle / "config.json")["run_config"]["seed"]
    assert f'"seed": "{_hex(seed, "overall")}"'.encode() in written[0]


@pytest.mark.parametrize(
    "name", ["seed-narrow-0.5", "seed-narrow-0.6", "seed-narrow-known-bad-0.6"]
)
def test_a_bundle_from_before_r36_validates_and_gets_no_interval(name: str) -> None:
    bundle = FIXTURES / name
    assert validate_bundle(bundle).errors == []
    assert "bootstrap_samples" not in (bundle / "config.json").read_text("utf-8")


@pytest.mark.parametrize("name", ["seed-narrow-0.6", "seed-narrow-known-bad-0.6"])
def test_a_golden_bundle_rescores_with_no_interval_and_no_verdict_moved(
    name: str, tmp_path: Path
) -> None:
    out = tmp_path / "rescored"
    result = CliRunner().invoke(
        app, ["rescore", "--bundle", str(FIXTURES / name), "--output", str(out)]
    )
    assert result.exit_code == 3, result.output
    lines = result.output.splitlines()
    assert any(line.startswith("Verdicts: reproduce -- ") for line in lines), lines
    assert result.output.rstrip().endswith(
        "RESCORE: CODE_CHANGED -- the verdicts reproduce under the running code"
    )
    assert "interval" not in (out / "summary.json").read_text("utf-8")


# ---------------------------------------------------------------------------
# report.md
# ---------------------------------------------------------------------------


def _capped(tmp_path: Path) -> tuple[Path, str]:
    """Five scenarios at $0.25 with a $0.50 cap: TE1 and TE3 run, TC1, PC5
    and RB5 do not -- two decided of five."""
    out = tmp_path / "capped"
    config = _seed(
        tmp_path,
        {
            "mock_cost_per_task_usd": PRICE,
            "benchmark_options": {
                "pack": "seed",
                "scenarios": ["TE1", "TE3", "TC1", "PC5", "RB5"],
            },
        },
        {"run_limits": {"max_run_cost_usd": 2 * PRICE}},
    )
    return out, _run(config, out)


def test_each_interval_names_its_denominator_in_report_md_and_the_cli(
    tmp_path: Path,
) -> None:
    """Owner decision 1 (2026-11-09): the rate keeps every scenario as its
    denominator; each interval names its own, and the overall line shows
    passed-of-decided beside passed-of-all. Fail-first: slice 2's wording."""
    out, output = _capped(tmp_path)
    report = (out / "report.md").read_text("utf-8")
    overall = (
        "pass^1 2/5 scenarios (2/2 decided), 95% CI [1.00, 1.00] over 2 decided"
    )
    # Two decided: below three, no rule-of-three bound (R37 step 0).
    assert f"- Overall: {overall} (B=10000)\n" in report
    assert f"Pass^k  : {overall}\n" in output
    assert (
        "- task_execution: 2/2 scenarios passed all 1 repetitions; "
        "95% CI [1.00, 1.00] over 2 decided (B=10000)\n"
    ) in report
    assert (
        "- tool_calling: 0/1 scenarios passed all 1 repetitions; 1 not evaluated "
        "(run_budget_exhausted); 0 decided scenarios — no interval"
    ) in report
    assert (
        "| task_execution | 0.9 | pack | hard | pack | "
        "1.0 (95% CI [1.00, 1.00] over 2 decided) | met |"
    ) in report
    assert "| tool_calling | 1.0 | pack | hard | pack | 0.0 |" in report
    assert report.count(HONESTY) == 1
    assert "Raw counts; no confidence intervals." not in report
    assert "interval" not in (out / "evidence_report.md").read_text("utf-8")


def test_the_cli_shows_each_interval_with_its_denominator(
    reference_bundles: ReferenceBundles,
) -> None:
    """The seed run: every scenario passes, so every interval is [1.00, 1.00]
    and the rule-of-three bound stands beside it (decision 2)."""
    output = reference_bundles["seed"].output
    assert (
        "task_execution     6/6 evaluated passed (0 not evaluated); pass^1 6/6 "
        "scenarios, 95% CI [1.00, 1.00] over 6 decided, "
        "95% one-sided lower bound ~ 0.50 (rule of three)"
    ) in output
    assert (
        "Pass^k  : pass^1 26/26 scenarios (26/26 decided), 95% CI [1.00, 1.00] "
        "over 26 decided, 95% one-sided lower bound ~ 0.88 (rule of three)"
    ) in output


def test_an_all_fail_set_shows_the_upper_rule_of_three_bound(
    reference_bundles: ReferenceBundles,
) -> None:
    bundle = reference_bundles["known-bad"].path
    report = (bundle / "report.md").read_text("utf-8")
    assert (
        "- Overall: pass^1 0/26 scenarios (0/26 decided), 95% CI [0.00, 0.00] "
        "over 26 decided (B=10000); 95% one-sided upper bound ~ 0.12 (rule of three)"
    ) in report
    assert "rule of three" not in (bundle / "summary.json").read_text("utf-8")


def _roll_up(passed: int, failed: int, low: float, high: float) -> CategoryPassK:
    n = passed + failed
    return CategoryPassK(
        category="task_execution",
        k=1,
        scenarios=n,
        passed=passed,
        failed=failed,
        not_evaluated=0,
        pass_k_rate=passed / n,
        interval=PassRateInterval(
            low=low, high=high, b=1000, seed="0" * 64, n_scenarios=n
        ),
    )


def test_no_rule_of_three_bound_between_the_extremes() -> None:
    """3 of 5 passed: the interval alone, on both surfaces."""
    roll_up = _roll_up(3, 2, 0.2, 1.0)
    assert pass_k_phrase(roll_up) == (
        "; pass^1 3/5 scenarios, 95% CI [0.20, 1.00] over 5 decided"
    )
    summary = RunSummary(
        run_id=RunId("mixed"),
        pass_k=[
            PassKResult(f"S{n}", 1, int(n < 3), int(n >= 3), 0, n < 3)
            for n in range(5)
        ],
        pass_k_by_category=[roll_up],
        overall_pass_k_interval=roll_up.interval,
    )
    lines = repeatability_section(summary, {}, 1000)
    assert (
        "- task_execution: 3/5 scenarios passed all 1 repetitions; "
        "95% CI [0.20, 1.00] over 5 decided (B=1000)"
    ) in lines
    assert (
        "- Overall: pass^1 3/5 scenarios (3/5 decided), 95% CI [0.20, 1.00] "
        "over 5 decided (B=1000)"
    ) in lines
    assert not any("rule of three" in line for line in lines)


def test_the_rule_of_three_bound_is_left_out_up_to_three_decided() -> None:
    """Up to three decided scenarios the bound would read 0.00 or 1.00 and
    say nothing, so it is left out; the interval stays. From four it first
    says something (all-pass: 1 - 3/4 = 0.25). Fail-first (R37 step 0 of
    2026-11-09; the threshold moved from three to four): three of three
    passed printed "lower bound ~ 0.00"."""
    assert pass_k_phrase(_roll_up(2, 0, 1.0, 1.0)) == (
        "; pass^1 2/2 scenarios, 95% CI [1.00, 1.00] over 2 decided"
    )
    assert pass_k_phrase(_roll_up(0, 2, 0.0, 0.0)) == (
        "; pass^1 0/2 scenarios, 95% CI [0.00, 0.00] over 2 decided"
    )
    assert pass_k_phrase(_roll_up(3, 0, 1.0, 1.0)) == (
        "; pass^1 3/3 scenarios, 95% CI [1.00, 1.00] over 3 decided"
    )
    assert pass_k_phrase(_roll_up(0, 3, 0.0, 0.0)) == (
        "; pass^1 0/3 scenarios, 95% CI [0.00, 0.00] over 3 decided"
    )
    assert pass_k_phrase(_roll_up(4, 0, 1.0, 1.0)) == (
        "; pass^1 4/4 scenarios, 95% CI [1.00, 1.00] over 4 decided, "
        "95% one-sided lower bound ~ 0.25 (rule of three)"
    )
    assert pass_k_phrase(_roll_up(0, 40, 0.0, 0.0)) == (
        "; pass^1 0/40 scenarios, 95% CI [0.00, 0.00] over 40 decided, "
        "95% one-sided upper bound ~ 0.07 (rule of three)"
    )


def test_report_md_leaves_the_bound_out_below_three_decided() -> None:
    """The same rule in report.md's roll-up and overall lines."""
    roll_up = _roll_up(2, 0, 1.0, 1.0)
    summary = RunSummary(
        run_id=RunId("two"),
        pass_k=[PassKResult(f"S{n}", 1, 1, 0, 0, True) for n in range(2)],
        pass_k_by_category=[roll_up],
        overall_pass_k_interval=roll_up.interval,
    )
    lines = repeatability_section(summary, {}, 1000)
    assert (
        "- task_execution: 2/2 scenarios passed all 1 repetitions; "
        "95% CI [1.00, 1.00] over 2 decided (B=1000)"
    ) in lines
    assert (
        "- Overall: pass^1 2/2 scenarios (2/2 decided), 95% CI [1.00, 1.00] "
        "over 2 decided (B=1000)"
    ) in lines


def test_report_md_omits_the_interval_of_a_single_scenario(tmp_path: Path) -> None:
    out = tmp_path / "three"
    config = _seed(
        tmp_path,
        {"benchmark_options": {"pack": "seed", "scenarios": ["TE1", "TE3", "TC1"]}},
        {},
    )
    _run(config, out)
    report = (out / "report.md").read_text("utf-8")
    assert (
        "- tool_calling: 1/1 scenarios passed all 1 repetitions; "
        "1 scenario — interval omitted"
    ) in report
