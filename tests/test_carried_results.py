"""Carried results fit the records they are carried with (ADR-0008 R6;
R29 (b1) slice 4).

Rescore takes three things as recorded instead of recomputing them: each
scenario's preflight, the cause of a task run never evaluated, and the
agent's output. Two bundles that carry such results are made at test time,
once per session (``reference_bundles.py``), and not committed: a seed run
in which one task run's infrastructure failed, and a seed run in which one
scenario failed its preflight -- an edited pack, registered under its own
name so that the run can be rebuilt from the bundle as it was run.
validate-bundle holds what they carry to the rest of their records; each
rule has an edit it catches, on a copy.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from reference_bundles import (
    CARRIED_SCENARIOS,
    DEFECT,
    INFRA,
    ReferenceBundles,
    registry_with_broken_pack,
)

from agentevalops import registry as registry_module
from agentevalops.bundles.manifest import write_manifest
from agentevalops.bundles.serializers import (
    load_end_state,
    load_evaluation,
    load_tool_call,
    load_trace_event,
)
from agentevalops.bundles.validator import validate_bundle
from agentevalops.core.schemas import TerminationReason
from agentevalops.replay.rebuild import rebuild_run

SCENARIOS = CARRIED_SCENARIOS


@pytest.fixture
def bundles(reference_bundles: ReferenceBundles) -> dict[str, Path]:
    """The two shared carried-result bundles; read only."""
    return {
        "infra": reference_bundles["carried-infra"].path,
        "preflight": reference_bundles["carried-preflight"].path,
    }


def _json(path: Path) -> Any:
    return json.loads(path.read_text("utf-8"))


def _lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line]


def _run(summary: dict[str, Any], task_id: str) -> dict[str, Any]:
    (run,) = [r for r in summary["task_results"] if r["task_id"] == task_id]
    return run


# ---------------------------------------------------------------------------
# What the two runs carry, read back with the typed loaders
# ---------------------------------------------------------------------------


def test_both_bundles_validate(bundles: dict[str, Path]) -> None:
    for bundle in bundles.values():
        assert validate_bundle(bundle).errors == []


def test_an_infrastructure_failure_is_carried_as_recorded(
    bundles: dict[str, Path],
) -> None:
    bundle = bundles["infra"]
    runs = {}
    for entry in _json(bundle / "evaluations.json"):
        task_id, _, result = load_evaluation(entry)
        runs.setdefault(task_id, []).append(result)
    assert {e.not_evaluated_cause for e in runs[INFRA]} == {"infrastructure"}
    assert not any(e.evaluated for e in runs[INFRA])
    assert all(e.evaluated for t in SCENARIOS if t != INFRA for e in runs[t])
    run = _run(_json(bundle / "summary.json"), INFRA)
    assert run["output"]["termination_reason"] == TerminationReason.INFRA_FAILURE.value
    assert "the sandbox went away" in run["output"]["error"]
    # The runner raised after its environment was built: the end state is
    # still taken, and the agent called no tool.
    states = [load_end_state(s) for s in _lines(bundle / "end_states.jsonl")]
    assert (INFRA, 0) in {(str(s.task_id), s.repetition) for s in states}
    calls = [load_tool_call(c) for c in _lines(bundle / "tool_calls.jsonl")]
    assert INFRA not in {str(c.task_id) for c in calls}


def test_a_failed_preflight_is_carried_and_the_agent_never_ran(
    bundles: dict[str, Path],
) -> None:
    bundle = bundles["preflight"]
    summary = _json(bundle / "summary.json")
    (check,) = [p for p in summary["preflight"] if p["task_id"] == DEFECT]
    assert check["passed"] is False
    assert {e["not_evaluated_cause"] for e in _run(summary, DEFECT)["evaluations"]} == {
        "environment_defect"
    }
    events = [load_trace_event(e) for e in _lines(bundle / "traces.jsonl")]
    assert {e.kind.value for e in events if str(e.task_id) == DEFECT} == {
        "evaluator.score"
    }
    for stream in ("tool_calls.jsonl", "end_states.jsonl"):
        assert DEFECT not in {r["task_id"] for r in _lines(bundle / stream)}


def test_both_runs_rebuild_from_their_bundles_with_the_same_pack(
    bundles: dict[str, Path],
    reference_bundles: ReferenceBundles,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The edited pack is registered under its own name, so the rebuild
    finds the same data the run used, not the shipped pack's."""
    edited_pack = registry_with_broken_pack(reference_bundles.broken_pack_data)
    monkeypatch.setattr(registry_module, "default_registry", edited_pack)
    for bundle in bundles.values():
        plan = _json(bundle / "test_plan.json")
        rebuilt = rebuild_run(_json(bundle / "config.json"), plan)
        assert [str(t.task_id) for t in rebuilt.tasks] == plan["selected"]
        assert rebuilt.plan.data_sha256 == plan["pack"]["data_sha256"]
        assert rebuilt.benchmark.pack.data_sha256 == plan["pack"]["data_sha256"]
    shipped = _json(bundles["infra"] / "test_plan.json")["pack"]["data_sha256"]
    edited = _json(bundles["preflight"] / "test_plan.json")["pack"]["data_sha256"]
    assert edited != shipped


# ---------------------------------------------------------------------------
# Guards, fail-first: each edit, with the manifest re-sealed, validated
# before the check existed.
# ---------------------------------------------------------------------------


def _edit(
    source: Path, tmp_path: Path, file: str, change: Callable[[Any], Any]
) -> list[str]:
    bundle = tmp_path / "edited"
    shutil.copytree(source, bundle)
    path = bundle / file
    if file.endswith(".jsonl"):
        lines = change(_lines(path))
        path.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    else:
        doc = _json(path)
        change(doc)
        path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    write_manifest(bundle)
    return validate_bundle(bundle).errors


def _fits(errors: list[str], message: str) -> bool:
    return any("carried result does not fit" in e and message in e for e in errors)


def test_a_preflight_stopped_run_with_an_end_state(
    bundles: dict[str, Path], tmp_path: Path
) -> None:
    def add(states: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [*states, {"task_id": DEFECT, "repetition": 0, "state": {}}]

    errors = _edit(bundles["preflight"], tmp_path, "end_states.jsonl", add)
    assert _fits(errors, f"at {DEFECT}/0: stopped by its scenario's preflight")
    assert _fits(errors, "has an end state for it")


def test_a_preflight_stopped_run_with_a_tool_call(
    bundles: dict[str, Path], tmp_path: Path
) -> None:
    def add(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [*calls, {**calls[0], "task_id": DEFECT, "repetition": 0}]

    errors = _edit(bundles["preflight"], tmp_path, "tool_calls.jsonl", add)
    assert _fits(errors, "has tool calls for it")


def test_a_preflight_stopped_run_whose_preflight_passed(
    bundles: dict[str, Path], tmp_path: Path
) -> None:
    def passed(summary: dict[str, Any]) -> None:
        for check in summary["preflight"]:
            if check["task_id"] == DEFECT:
                check["passed"], check["cause"] = True, None

    errors = _edit(bundles["preflight"], tmp_path, "summary.json", passed)
    assert _fits(errors, f"records no failed preflight for {DEFECT}")


def test_an_infrastructure_failure_whose_output_says_otherwise(
    bundles: dict[str, Path], tmp_path: Path
) -> None:
    def completed(summary: dict[str, Any]) -> None:
        _run(summary, INFRA)["output"]["termination_reason"] = "completed"

    errors = _edit(bundles["infra"], tmp_path, "summary.json", completed)
    assert _fits(errors, "termination_reason is 'completed', not 'infra_failure'")


def test_an_infrastructure_failure_that_one_evaluator_evaluated(
    bundles: dict[str, Path], tmp_path: Path
) -> None:
    def one(summary: dict[str, Any]) -> None:
        entry = _run(summary, INFRA)["evaluations"][0]
        entry["evaluated"], entry["not_evaluated_cause"] = True, None

    errors = _edit(bundles["infra"], tmp_path, "summary.json", one)
    assert _fits(errors, f"at {INFRA}/0: not evaluated (infrastructure) by some")


def test_an_evaluated_run_without_its_end_state(
    bundles: dict[str, Path], tmp_path: Path
) -> None:
    def drop(states: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [s for s in states if s["task_id"] != "TC1"]

    errors = _edit(bundles["infra"], tmp_path, "end_states.jsonl", drop)
    assert _fits(errors, "at TC1/0: evaluated, but end_states.jsonl has no end state")


def test_an_output_that_is_not_what_the_terminal_event_records(
    bundles: dict[str, Path], tmp_path: Path
) -> None:
    def answer(summary: dict[str, Any]) -> None:
        _run(summary, "TC1")["output"]["final_answer"] = "something else"

    errors = _edit(bundles["infra"], tmp_path, "summary.json", answer)
    assert _fits(errors, "at TC1/0: its output is not what its agent.terminal")


def test_a_terminal_event_that_does_not_load(
    bundles: dict[str, Path], tmp_path: Path
) -> None:
    def corrupt(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for event in events:
            if event["kind"] == "agent.terminal" and event["task_id"] == "TC1":
                event["surprise"] = 1
        return events

    errors = _edit(bundles["infra"], tmp_path, "traces.jsonl", corrupt)
    assert _fits(errors, "at TC1/0: ") and _fits(errors, "surprise")
