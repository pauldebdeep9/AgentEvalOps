"""report.md is dated by the bundle, not the clock (ADR-0008 decision 9;
R29 (a) slice 2).

Its Generated line is the run's created_at, as metadata.json records it,
like the evidence report's header; the renderer reads no clock, so the
same bundle renders to the same bytes at any time, and rescore can compare
report.md byte for byte.
"""

from __future__ import annotations

import ast
import json
import time
from pathlib import Path

from typer.testing import CliRunner

from agentevalops.cli import app
from agentevalops.core.schemas import ResourceLimits, RunConfig, RunSummary
from agentevalops.core.types import BACKEND_LOCAL, AgentId, RunId
from agentevalops.reports import markdown
from agentevalops.reports.markdown import render_report

ROOT = Path(__file__).resolve().parents[1]
CREATED_AT = "2026-11-02T01:02:03.456789+00:00"


def _config() -> RunConfig:
    return RunConfig(
        run_id=RunId("dated"),
        agent_id=AgentId("agent"),
        backend_id=BACKEND_LOCAL,
        resource_limits=ResourceLimits(),
    )


def test_the_same_bundle_renders_the_same_bytes_at_two_clock_times() -> None:
    """Fail-first: with the clock, the second render a second later had a
    different Generated line."""
    summary = RunSummary(run_id=RunId("dated"))
    first = render_report(_config(), summary, [], "c.yaml", created_at=CREATED_AT)
    time.sleep(1.1)
    second = render_report(_config(), summary, [], "c.yaml", created_at=CREATED_AT)
    assert first.encode("utf-8") == second.encode("utf-8")
    assert f"Generated: {CREATED_AT}" in first


def test_the_renderer_reads_no_clock() -> None:
    tree = ast.parse(Path(markdown.__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert imported.isdisjoint({"datetime", "time"})


def test_without_a_timestamp_the_line_says_so() -> None:
    report = render_report(_config(), RunSummary(run_id=RunId("dated")), [])
    assert "Generated: not recorded" in report


def test_a_written_report_carries_the_bundles_created_at(tmp_path: Path) -> None:
    out = tmp_path / "toy"
    result = CliRunner().invoke(
        app,
        ["run", "--config", str(ROOT / "configs/toy_smoke.yaml"), "--output", str(out)],
    )
    assert result.exit_code == 0, result.output
    created_at = json.loads((out / "metadata.json").read_text("utf-8"))["created_at"]
    report = (out / "report.md").read_text("utf-8")
    assert f"Generated: {created_at} (the run's created_at" in report
    evidence = (out / "evidence_report.md").read_text("utf-8")
    assert f"- Created: {created_at}" in evidence
