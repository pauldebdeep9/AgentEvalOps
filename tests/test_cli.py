"""CLI tests: help, version, doctor, and run commands."""

from __future__ import annotations

import pathlib

from typer.testing import CliRunner

from agentevalops import __version__
from agentevalops.cli import app

runner = CliRunner()

_CONFIG = (
    pathlib.Path(__file__).parent.parent / "configs" / "toy_smoke.yaml"
)


def test_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "agentevalops" in result.output.lower()


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_doctor_command() -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "OK" in result.output


def test_doctor_shows_python_version() -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "Python" in result.output


def test_run_command_exits_zero() -> None:
    result = runner.invoke(app, ["run", "--config", str(_CONFIG)])
    assert result.exit_code == 0, result.output


def test_run_command_prints_run_id() -> None:
    result = runner.invoke(app, ["run", "--config", str(_CONFIG)])
    assert "toy-smoke-001" in result.output


def test_run_command_prints_tasks_passed() -> None:
    result = runner.invoke(app, ["run", "--config", str(_CONFIG)])
    assert "2 / 2 passed" in result.output


def test_run_command_prints_policy() -> None:
    result = runner.invoke(app, ["run", "--config", str(_CONFIG)])
    assert "PASS" in result.output


def test_run_command_missing_config_exits_nonzero(tmp_path: pathlib.Path) -> None:
    result = runner.invoke(
        app, ["run", "--config", str(tmp_path / "no_such.yaml")]
    )
    assert result.exit_code != 0


def test_run_command_with_output_creates_bundle(tmp_path: pathlib.Path) -> None:
    output_dir = tmp_path / "my-bundle"
    result = runner.invoke(
        app,
        ["run", "--config", str(_CONFIG), "--output", str(output_dir)],
    )
    assert result.exit_code == 0, result.output
    assert output_dir.exists()
    for fname in (
        "metadata.json",
        "config.json",
        "traces.jsonl",
        "evaluations.json",
        "policy.json",
        "summary.json",
        "report.md",
    ):
        assert (output_dir / fname).exists(), f"Missing bundle file: {fname}"


def test_run_command_with_output_prints_bundle_path(
    tmp_path: pathlib.Path,
) -> None:
    output_dir = tmp_path / "bundle-out"
    result = runner.invoke(
        app,
        ["run", "--config", str(_CONFIG), "--output", str(output_dir)],
    )
    assert result.exit_code == 0, result.output
    assert "Bundle written to:" in result.output


def test_run_command_without_output_no_bundle_message(
    tmp_path: pathlib.Path,
) -> None:
    result = runner.invoke(app, ["run", "--config", str(_CONFIG)])
    assert result.exit_code == 0, result.output
    assert "Bundle written to:" not in result.output


# ---------------------------------------------------------------------------
# WBS 5: failed count + pass rate in CLI output
# ---------------------------------------------------------------------------


def test_run_command_prints_failed_count() -> None:
    result = runner.invoke(app, ["run", "--config", str(_CONFIG)])
    assert "Failed  : 0" in result.output


def test_run_command_prints_pass_rate() -> None:
    result = runner.invoke(app, ["run", "--config", str(_CONFIG)])
    assert "Rate    :" in result.output
    assert "%" in result.output
