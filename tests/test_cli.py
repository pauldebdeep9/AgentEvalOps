"""CLI tests: help, version, doctor, and run commands."""

from __future__ import annotations

import pathlib

import pytest
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


def test_run_command_rejects_output_dir_with_existing_bundle(
    tmp_path: pathlib.Path,
) -> None:
    output_dir = tmp_path / "already-a-bundle"
    output_dir.mkdir()
    (output_dir / "metadata.json").write_text("{}", encoding="utf-8")
    result = runner.invoke(
        app,
        ["run", "--config", str(_CONFIG), "--output", str(output_dir)],
    )
    assert result.exit_code != 0
    assert "already contains bundle files" in result.output


def test_run_command_interrupt_persists_partial_trace(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A KeyboardInterrupt mid-run must not lose already-streamed events.

    LocalTraceStore flushes+fsyncs each event to <output>/traces.jsonl as it
    happens, so even though no bundle is produced (no metadata.json etc.),
    the partial trace file survives on disk.
    """
    from agentevalops.core.schemas import TraceEvent
    from agentevalops.core.types import RunId
    from agentevalops.stores import local as local_store_module

    output_dir = tmp_path / "interrupted-bundle"
    original_append = local_store_module.LocalTraceStore.append
    call_count = {"n": 0}

    async def _flaky_append(
        self: local_store_module.LocalTraceStore,
        run_id: RunId,
        event: TraceEvent,
    ) -> None:
        await original_append(self, run_id, event)
        call_count["n"] += 1
        # Only the first call raises: toy_smoke.yaml has 2 tasks, and raising
        # from every sibling's append would cause asyncio to report each
        # cancelled sibling's own KeyboardInterrupt as "never retrieved" --
        # noise unrelated to what this test is verifying (durability).
        if call_count["n"] == 1:
            raise KeyboardInterrupt("simulated Ctrl-C")

    monkeypatch.setattr(
        local_store_module.LocalTraceStore, "append", _flaky_append
    )

    result = runner.invoke(
        app,
        ["run", "--config", str(_CONFIG), "--output", str(output_dir)],
    )

    assert result.exit_code == 130
    assert "Run interrupted" in result.output
    traces_path = output_dir / "traces.jsonl"
    assert traces_path.exists()
    assert traces_path.read_text(encoding="utf-8").strip() != ""
    assert not (output_dir / "metadata.json").exists()


def test_run_command_interrupt_without_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Interrupt without --output: still exits 130 with a plain message
    (no LocalTraceStore/output path is involved -- uses InMemoryTraceStore).
    """
    from agentevalops.stores import memory as memory_store_module

    call_count = {"n": 0}

    async def _flaky_append(self: object, run_id: object, event: object) -> None:
        call_count["n"] += 1
        # Only the first call raises; toy_smoke.yaml has 2 tasks, and every
        # sibling raising independently would print asyncio's own "never
        # retrieved" noise into the captured output, breaking the exact
        # match below on unrelated grounds.
        if call_count["n"] == 1:
            raise KeyboardInterrupt("simulated Ctrl-C")

    monkeypatch.setattr(
        memory_store_module.InMemoryTraceStore, "append", _flaky_append
    )

    result = runner.invoke(app, ["run", "--config", str(_CONFIG)])

    assert result.exit_code == 130
    assert result.output.strip() == "Run interrupted."


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


# ---------------------------------------------------------------------------
# W6: report --run-id command
# ---------------------------------------------------------------------------


def _make_bundle(runs_dir: pathlib.Path, name: str) -> pathlib.Path:
    """Run the toy config and write a bundle under runs_dir/name."""
    out = runs_dir / name
    result = runner.invoke(
        app, ["run", "--config", str(_CONFIG), "--output", str(out)]
    )
    assert result.exit_code == 0, result.output
    return out


def test_report_by_run_id(tmp_path: pathlib.Path) -> None:
    _make_bundle(tmp_path, "toy-smoke-001")
    result = runner.invoke(
        app,
        ["report", "--run-id", "toy-smoke-001", "--runs-dir", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    # report.md content is echoed; it references the run id.
    assert "toy-smoke-001" in result.output


def test_report_latest(tmp_path: pathlib.Path) -> None:
    _make_bundle(tmp_path, "bundle-a")
    result = runner.invoke(
        app, ["report", "--run-id", "latest", "--runs-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert "toy-smoke-001" in result.output


def test_report_by_directory_name_fallback(tmp_path: pathlib.Path) -> None:
    # Resolve by the bundle's directory name even if it differs from run_id.
    _make_bundle(tmp_path, "custom-dir-name")
    result = runner.invoke(
        app,
        ["report", "--run-id", "custom-dir-name", "--runs-dir", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output


def test_report_unknown_run_id_exits_nonzero(tmp_path: pathlib.Path) -> None:
    _make_bundle(tmp_path, "toy-smoke-001")
    result = runner.invoke(
        app, ["report", "--run-id", "nope", "--runs-dir", str(tmp_path)]
    )
    assert result.exit_code != 0
    assert "No bundle found" in result.output


def test_report_missing_runs_dir_exits_nonzero(
    tmp_path: pathlib.Path,
) -> None:
    result = runner.invoke(
        app,
        ["report", "--run-id", "latest", "--runs-dir", str(tmp_path / "nope")],
    )
    assert result.exit_code != 0
    assert "Runs directory not found" in result.output


def test_report_empty_runs_dir_exits_nonzero(tmp_path: pathlib.Path) -> None:
    result = runner.invoke(
        app, ["report", "--run-id", "latest", "--runs-dir", str(tmp_path)]
    )
    assert result.exit_code != 0
    assert "No bundle found" in result.output


def test_report_bundle_without_report_md_exits_nonzero(
    tmp_path: pathlib.Path,
) -> None:
    bundle = _make_bundle(tmp_path, "toy-smoke-001")
    (bundle / "report.md").unlink()
    result = runner.invoke(
        app,
        ["report", "--run-id", "toy-smoke-001", "--runs-dir", str(tmp_path)],
    )
    assert result.exit_code != 0
    assert "no report.md" in result.output


def test_run_langgraph_runner_missing_key_exits_cleanly(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The langgraph runner is registered but validates its API key lazily at
    # run time; with no key, `run` must exit cleanly (not crash / not call the
    # network).  Delete the key so the test is deterministic offline.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = tmp_path / "lg.yaml"
    cfg.write_text(
        "run_id: r1\nbenchmark_id: toy\nrunner_id: langgraph\n"
        'model: "claude-haiku-4-5"\n',
        encoding="utf-8",
    )
    result = runner.invoke(app, ["run", "--config", str(cfg)])
    assert result.exit_code != 0
    assert "ANTHROPIC_API_KEY" in result.output


def test_report_skips_dir_with_unreadable_metadata(
    tmp_path: pathlib.Path,
) -> None:
    # A directory whose metadata.json is unparseable is not matched by run_id
    # but is still skipped gracefully (no crash); a valid sibling resolves.
    bad = tmp_path / "bad-bundle"
    bad.mkdir()
    (bad / "metadata.json").write_text("{ not json", encoding="utf-8")
    _make_bundle(tmp_path, "toy-smoke-001")
    result = runner.invoke(
        app,
        ["report", "--run-id", "toy-smoke-001", "--runs-dir", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert "toy-smoke-001" in result.output
