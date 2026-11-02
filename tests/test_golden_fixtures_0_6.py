"""Golden format 0.6 bundles (R29 (b1) slice 4).

``tests/fixtures/bundles/seed-narrow-0.6`` and ``seed-narrow-known-bad-0.6``
were written by ``agentevalops run`` at `40da4b7` for the seed pack's mock
run narrowed to TE3, TC1, PC5 and RB5 -- the smallest scenario of each
category -- under the shipped system declaration, the first with the
known-good trajectories and the second with the known-bad ones. Like the
0.5 fixture they were run from a directory outside any checkout with
relative paths, so they record no path from the machine that wrote them.
Their bytes are what their manifests checksum: ``.gitattributes`` and the
pre-commit config leave ``tests/fixtures/`` alone.

They are what rescore will be tested against. Nothing here asserts on the
code-drift warning, which depends on the checkout running the test.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agentevalops.bundles.constants import peek_declared_format_version
from agentevalops.bundles.serializers import (
    from_jsonable,
    load_agent_output,
    load_end_state,
    load_evaluation,
    load_run_config,
    load_tool_call,
    load_trace_event,
    to_jsonable,
)
from agentevalops.bundles.validator import validate_bundle
from agentevalops.cli import app
from agentevalops.core.schemas import PolicyVerdict, ResultBundleMetadata, RunSummary
from agentevalops.replay.rebuild import rebuild_run

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/bundles"
GOOD = FIXTURES / "seed-narrow-0.6"
BAD = FIXTURES / "seed-narrow-known-bad-0.6"
SCENARIOS = {"TE3", "TC1", "PC5", "RB5"}


def _json(path: Path) -> Any:
    return json.loads(path.read_text("utf-8"))


def _lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line]


@pytest.mark.parametrize("fixture", [GOOD, BAD], ids=["good", "known-bad"])
def test_the_fixture_is_0_6_and_validates(fixture: Path) -> None:
    assert peek_declared_format_version(fixture) == "0.6"
    result = validate_bundle(fixture)
    assert result.errors == []
    assert result.checked_files == 13
    assert sum(f.stat().st_size for f in fixture.iterdir()) < 210_000


@pytest.mark.parametrize("fixture", [GOOD, BAD], ids=["good", "known-bad"])
def test_every_record_loads_with_the_typed_loaders(fixture: Path) -> None:
    for name, load in (
        ("tool_calls.jsonl", load_tool_call),
        ("end_states.jsonl", load_end_state),
        ("traces.jsonl", load_trace_event),
    ):
        lines = _lines(fixture / name)
        assert lines, name
        assert [to_jsonable(load(line)) for line in lines] == lines, name
    for entry in _json(fixture / "evaluations.json"):
        task_id, repetition, result = load_evaluation(entry)
        assert task_id in SCENARIOS
        run = {"task_id": task_id, "repetition": repetition}
        assert {**to_jsonable(result), **run} == entry
    summary = _json(fixture / "summary.json")
    assert to_jsonable(from_jsonable(RunSummary, summary)) == summary
    for result in summary["task_results"]:
        assert to_jsonable(load_agent_output(result["output"])) == result["output"]
    run_config = _json(fixture / "config.json")["run_config"]
    assert to_jsonable(load_run_config(run_config)) == run_config
    policy = _json(fixture / "policy.json")
    assert to_jsonable(from_jsonable(PolicyVerdict, policy)) == policy
    metadata = _json(fixture / "metadata.json")
    assert to_jsonable(from_jsonable(ResultBundleMetadata, metadata)) == metadata


def test_the_good_fixture_passes_every_scenario_and_the_gate() -> None:
    summary = from_jsonable(RunSummary, _json(GOOD / "summary.json"))
    assert (summary.passed_tasks, summary.failed_tasks) == (4, 0)
    assert {str(r.task_id) for r in summary.task_results} == SCENARIOS
    result = CliRunner().invoke(app, ["gate", "--bundle", str(GOOD)])
    assert result.exit_code == 0, result.output
    assert "GATE: PASS" in result.output


def test_the_known_bad_fixture_fails_every_scenario_and_three_hard_gates() -> None:
    summary = from_jsonable(RunSummary, _json(BAD / "summary.json"))
    assert (summary.passed_tasks, summary.failed_tasks) == (0, 4)
    result = CliRunner().invoke(app, ["gate", "--bundle", str(BAD)])
    assert result.exit_code == 1, result.output
    assert "GATE: FAIL" in result.output
    for category in ("task_execution", "tool_calling", "policy_compliance"):
        below = rf"! {category}: pass\^k 0\.0 is below .* \(hard gate\)"
        assert re.search(below, result.output)


@pytest.mark.parametrize("fixture", [GOOD, BAD], ids=["good", "known-bad"])
def test_the_fixture_rebuilds_the_tasks_it_ran(fixture: Path) -> None:
    config, plan = _json(fixture / "config.json"), _json(fixture / "test_plan.json")
    rebuilt = rebuild_run(config, plan)
    ran = [r["task_id"] for r in _json(fixture / "summary.json")["task_results"]]
    assert [str(t.task_id) for t in rebuilt.tasks] == ran
    assert len(rebuilt.descriptors) == 26


#: What no committed bundle may hold: a path from the machine that wrote
#: it, or anything shaped like a credential.
_FORBIDDEN = re.compile(
    r"/Users/|/home/|/private/|/tmp/|[A-Z]:\\\\|sk-(?:ant|proj)-|AKIA[0-9A-Z]{12}"
    r"|ghp_|_API_KEY|BEGIN [A-Z ]*PRIVATE KEY"
)


def test_no_fixture_bundle_holds_a_path_or_a_credential() -> None:
    bundles = [d for d in FIXTURES.iterdir() if (d / "manifest.json").exists()]
    assert {GOOD, BAD} <= set(bundles)
    for bundle in bundles:
        for file in bundle.iterdir():
            found = _FORBIDDEN.findall(file.read_text("utf-8"))
            assert found == [], f"{file.relative_to(FIXTURES)}: {found}"


def test_hooks_and_git_leave_the_fixture_bytes_alone() -> None:
    assert "tests/fixtures/** -text" in (ROOT / ".gitattributes").read_text("utf-8")
    hooks = (ROOT / ".pre-commit-config.yaml").read_text("utf-8")
    assert re.search(r"^exclude: \^tests/fixtures/", hooks, re.MULTILINE)
