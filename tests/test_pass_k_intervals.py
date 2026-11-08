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
from agentevalops.cli import app
from agentevalops.config.loader import load_run_config
from agentevalops.core.errors import ConfigurationError
from agentevalops.plans.io import to_document
from agentevalops.scorers.bootstrap import ci_seed

FIXTURES = Path(__file__).parent / "fixtures" / "bundles"
HOMES = ("run config", "system declaration")
PRICE = 0.25


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
        "seed": ci_seed(seed, "overall"),
        "n_scenarios": 26,
        "method": "cluster-bootstrap-percentile",
    }
    for roll_up in summary["pass_k_by_category"]:
        assert roll_up["interval"] == {
            "low": 1.0,
            "high": 1.0,
            "b": 10_000,
            "seed": ci_seed(seed, roll_up["category"]),
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
    assert result.exit_code in (0, 3), result.output
    assert "Verdicts: reproduce" in result.output
    assert "interval" not in (out / "summary.json").read_text("utf-8")


# ---------------------------------------------------------------------------
# report.md
# ---------------------------------------------------------------------------


def test_report_md_states_each_interval_or_why_there_is_none(tmp_path: Path) -> None:
    """Five scenarios at $0.25 with a $0.50 cap: TE1 and TE3 run, TC1, PC5
    and RB5 do not. task_execution has two decided scenarios and an
    interval; the other categories none decided. Overall, two decided of
    five. The thresholds table shows the interval beside the rate the gate
    reads; the evidence report is untouched."""
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
    _run(config, out)
    report = (out / "report.md").read_text("utf-8")
    assert (
        "- task_execution: 2/2 scenarios passed all 1 repetitions; "
        "95% interval 1.00–1.00 over 2 decided scenarios"
    ) in report
    assert (
        "- tool_calling: 0/1 scenarios passed all 1 repetitions; 1 not evaluated "
        "(run_budget_exhausted); 0 decided scenarios — no interval"
    ) in report
    assert (
        "- Overall: 2/5 scenarios passed all 1 repetitions; 3 not evaluated; "
        "95% interval 1.00–1.00 over 2 decided scenarios"
    ) in report
    assert "10000 resamples" in report
    assert (
        "| task_execution | 0.9 | pack | hard | pack | "
        "1.0 (95% interval 1.00–1.00) | met |"
    ) in report
    assert "| tool_calling | 1.0 | pack | hard | pack | 0.0 |" in report
    evidence = (out / "evidence_report.md").read_text("utf-8")
    assert "interval" not in evidence


def test_report_md_omits_the_interval_of_a_single_scenario(tmp_path: Path) -> None:
    out = tmp_path / "five"
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
