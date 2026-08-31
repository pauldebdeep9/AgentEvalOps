"""Tests for minieval.bundle: git_provenance, and the round trip through
write_bundle.

The round trip is the substantive part of this module. W6 produced four
bugs at a serialisation boundary (the payload/AgentOutput conversion),
all invisible to tests that were passing at the time -- this is the same
boundary again, one hop further out, through actual JSON on disk. Every
RunSummary here is built by hand, never produced by running the
benchmark: constructed inputs isolate the failure to bundle.py, per
CLAUDE.md's rule, and the four hand-built attempts (passed, failed on
merit, never evaluated, limit-terminated) exercise every branch
write_bundle's per-attempt serialisation can take.
"""

from __future__ import annotations

import json
import random
import re
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

import pytest

from minieval import bundle
from minieval.schemas import (
    TOOL_NAME_PAYLOAD_KEY,
    AgentOutput,
    Attempt,
    EvaluationResult,
    PolicyVerdict,
    RunConfig,
    RunId,
    RunSummary,
    TaskId,
    TaskResult,
    TaskSpec,
    TerminationReason,
    TraceEvent,
    TraceEventKind,
    Verdict,
)

_THIS_REPO = Path(__file__).resolve().parent


def test_inside_this_repo_sha_is_plausible_hex_and_branch_is_non_empty() -> None:
    result = bundle.git_provenance(cwd=_THIS_REPO)

    assert re.fullmatch(r"[0-9a-f]{7,40}", result["sha"])
    assert result["branch"] != ""
    assert result["branch"] != "unavailable"


def test_outside_a_git_checkout_every_field_is_exactly_unavailable() -> None:
    """A subdirectory of this repo would still be inside the checkout and
    would pass this test while checking nothing -- tempfile's directory
    lives under the OS temp root, genuinely outside this repo's tree."""
    with tempfile.TemporaryDirectory() as tmp:
        result = bundle.git_provenance(cwd=Path(tmp))

    assert result == {
        "sha": "unavailable",
        "branch": "unavailable",
        "dirty": "unavailable",
    }


def test_simulated_git_failure_yields_unavailable_not_a_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*args: object, **kwargs: object) -> NoReturn:
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(subprocess, "run", _raise)

    result = bundle.git_provenance(cwd=_THIS_REPO)

    assert result == {
        "sha": "unavailable",
        "branch": "unavailable",
        "dirty": "unavailable",
    }


def test_simulated_timeout_yields_unavailable_not_a_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*args: object, **kwargs: object) -> NoReturn:
        raise subprocess.TimeoutExpired(cmd="git", timeout=bundle._GIT_TIMEOUT_SECONDS)

    monkeypatch.setattr(subprocess, "run", _raise)

    result = bundle.git_provenance(cwd=_THIS_REPO)

    assert result == {
        "sha": "unavailable",
        "branch": "unavailable",
        "dirty": "unavailable",
    }


def test_return_value_is_entirely_str_valued() -> None:
    """A later field addition that quietly returns None instead of a str
    must be caught here, not by whichever caller first tries to
    json.dumps it."""
    result = bundle.git_provenance(cwd=_THIS_REPO)

    assert isinstance(result, dict)
    assert result
    assert all(isinstance(value, str) for value in result.values())


# ---------------------------------------------------------------------------
# write_bundle: a hand-built RunSummary covering one attempt from each of
# the four outcomes an attempt can have, so every branch of write_bundle's
# per-attempt serialisation is exercised.
# ---------------------------------------------------------------------------

_RUN_ID = RunId("bundle-round-trip")
_PASS_TASK = TaskId("PASS")
_FAIL_TASK = TaskId("FAIL")
_NOTEVAL_TASK = TaskId("NOTEVAL")
_LIMIT_TASK = TaskId("LIMIT")
_TS = datetime(2026, 1, 1, tzinfo=UTC)
_WEIRD_COST = 0.10999999999999999
"""A cost value carrying real accumulation error, the kind repeated 0.01
additions actually produce (see W6's own cost sums). The bundle must
record this exactly -- not round it, not approximate it on the way back.
"""


def _event(
    task_id: TaskId,
    attempt: int,
    step_index: int,
    kind: TraceEventKind,
    payload: dict[str, Any],
    cost_usd: float = 0.01,
) -> TraceEvent:
    return TraceEvent(
        run_id=_RUN_ID,
        task_id=task_id,
        attempt=attempt,
        step_index=step_index,
        kind=kind,
        payload=payload,
        timestamp=_TS,
        cost_usd=cost_usd,
    )


def _build_summary() -> RunSummary:
    # PASS: completes, correct answer, judged and passing.
    pass_output = AgentOutput(
        success=True,
        termination_reason=TerminationReason.COMPLETED,
        final_answer="42",
        steps=1,
        cost_usd=0.02,
    )
    pass_result = TaskResult(
        task=TaskSpec(
            task_id=_PASS_TASK,
            description="passes",
            criteria={"expected_substring": "42"},
        ),
        attempts=[
            Attempt(
                index=0,
                output=pass_output,
                evaluation=EvaluationResult(
                    evaluator_id="deterministic",
                    passed=True,
                    evaluated=True,
                    citations=[0],
                ),
                policy=PolicyVerdict(policy_id="default", verdict=Verdict.PASS),
                events=[
                    _event(
                        _PASS_TASK,
                        0,
                        0,
                        TraceEventKind.AGENT_FINAL_ANSWER,
                        {"final_answer": "42"},
                    ),
                    _event(
                        _PASS_TASK,
                        0,
                        1,
                        TraceEventKind.AGENT_TERMINAL,
                        pass_output.to_payload(),
                    ),
                ],
            )
        ],
    )

    # FAIL: completes, wrong answer, judged and failing on merit.
    fail_output = AgentOutput(
        success=True,
        termination_reason=TerminationReason.COMPLETED,
        final_answer="wrong",
        steps=1,
        cost_usd=0.02,
    )
    fail_result = TaskResult(
        task=TaskSpec(
            task_id=_FAIL_TASK,
            description="fails on merit",
            criteria={"expected_substring": "42"},
        ),
        attempts=[
            Attempt(
                index=0,
                output=fail_output,
                evaluation=EvaluationResult(
                    evaluator_id="deterministic",
                    passed=False,
                    evaluated=True,
                    citations=[0],
                    notes="expected_substring '42' not found in final_answer='wrong'",
                ),
                policy=PolicyVerdict(policy_id="default", verdict=Verdict.PASS),
                events=[
                    _event(
                        _FAIL_TASK,
                        0,
                        0,
                        TraceEventKind.AGENT_FINAL_ANSWER,
                        {"final_answer": "wrong"},
                    ),
                    _event(
                        _FAIL_TASK,
                        0,
                        1,
                        TraceEventKind.AGENT_TERMINAL,
                        fail_output.to_payload(),
                    ),
                ],
            )
        ],
    )

    # NOTEVAL: malformed trace, never reaches the evaluator. Carries the
    # accumulation-error cost, on both the lone event and the output.
    noteval_output = AgentOutput(
        success=False,
        termination_reason=TerminationReason.INFRA_FAILURE,
        final_answer=None,
        steps=0,
        cost_usd=_WEIRD_COST,
        error="no AGENT_TERMINAL emitted",
    )
    noteval_result = TaskResult(
        task=TaskSpec(
            task_id=_NOTEVAL_TASK, description="never evaluated", criteria={}
        ),
        attempts=[
            Attempt(
                index=0,
                output=noteval_output,
                evaluation=EvaluationResult(
                    evaluator_id="deterministic",
                    passed=False,
                    evaluated=False,
                    notes="no AGENT_TERMINAL emitted",
                ),
                policy=PolicyVerdict(
                    policy_id="default",
                    verdict=Verdict.WARN,
                    notes=(
                        "policy could not be assessed because the trace was "
                        "non-conforming"
                    ),
                ),
                events=[
                    _event(
                        _NOTEVAL_TASK,
                        0,
                        0,
                        TraceEventKind.AGENT_PLAN,
                        {},
                        cost_usd=_WEIRD_COST,
                    ),
                ],
            )
        ],
    )

    # LIMIT: stopped mid-stream by a limit, judged, failing on the reason.
    limit_output = AgentOutput(
        success=False,
        termination_reason=TerminationReason.LIMIT_STEPS,
        final_answer=None,
        steps=1,
        cost_usd=0.03,
    )
    limit_result = TaskResult(
        task=TaskSpec(
            task_id=_LIMIT_TASK,
            description="limit terminated",
            criteria={"expected_substring": "x"},
        ),
        attempts=[
            Attempt(
                index=0,
                output=limit_output,
                evaluation=EvaluationResult(
                    evaluator_id="deterministic",
                    passed=False,
                    evaluated=True,
                    notes="did not complete: termination_reason=limit_steps",
                ),
                policy=PolicyVerdict(policy_id="default", verdict=Verdict.PASS),
                events=[
                    _event(_LIMIT_TASK, 0, 0, TraceEventKind.AGENT_PLAN, {}),
                    _event(
                        _LIMIT_TASK,
                        0,
                        1,
                        TraceEventKind.AGENT_TOOL_CALL,
                        {TOOL_NAME_PAYLOAD_KEY: "search"},
                    ),
                    _event(
                        _LIMIT_TASK,
                        0,
                        2,
                        TraceEventKind.AGENT_TOOL_RESULT,
                        {TOOL_NAME_PAYLOAD_KEY: "search"},
                    ),
                ],
            )
        ],
    )

    return RunSummary(
        run_id=_RUN_ID,
        config=RunConfig(run_id=_RUN_ID, runner="mock", repeats=1),
        task_results=[pass_result, fail_result, noteval_result, limit_result],
    )


def _all_events(summary: RunSummary) -> list[TraceEvent]:
    return [e for tr in summary.task_results for a in tr.attempts for e in a.events]


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


def test_traces_round_trip_as_equal_objects() -> None:
    """Object equality, not field-by-field spot-checking: a dropped field
    is invisible to a test that only checks the fields it remembered to
    name."""
    summary = _build_summary()
    original = _all_events(summary)

    with tempfile.TemporaryDirectory() as tmp:
        bundle_dir = bundle.write_bundle(summary, Path(tmp))
        lines = (bundle_dir / "traces.jsonl").read_text().splitlines()

    assert len(lines) == len(original)
    parsed = [TraceEvent.from_json(line) for line in lines]
    assert parsed == original


def test_results_output_reconstructs_via_from_payload_as_enum_member() -> None:
    """The exact W6 bug, now crossing JSON as well: every attempt's
    output, read back from results.json and reconstructed via
    from_payload, must hold a real TerminationReason member -- not a
    plain str that merely equals the right value. isinstance is the
    check that tells the two apart; == does not."""
    summary = _build_summary()

    with tempfile.TemporaryDirectory() as tmp:
        bundle_dir = bundle.write_bundle(summary, Path(tmp))
        results = json.loads((bundle_dir / "results.json").read_text())

    reconstructed = [
        AgentOutput.from_payload(attempt["output"])
        for task in results["tasks"]
        for attempt in task["attempts"]
    ]
    assert len(reconstructed) == 4
    for output in reconstructed:
        assert isinstance(output.termination_reason, TerminationReason)


# ---------------------------------------------------------------------------
# Self-locating lines
# ---------------------------------------------------------------------------


def test_traces_are_groupable_by_task_and_attempt_ignoring_file_order() -> None:
    """A shuffled file must still be groupable: each line carries enough
    of its own identity (task_id, attempt, step_index) that a reader
    never needs to trust where in the file it landed."""
    summary = _build_summary()

    with tempfile.TemporaryDirectory() as tmp:
        bundle_dir = bundle.write_bundle(summary, Path(tmp))
        lines = (bundle_dir / "traces.jsonl").read_text().splitlines()

    random.Random(0).shuffle(lines)
    parsed = [TraceEvent.from_json(line) for line in lines]

    grouped: dict[tuple[str, int], list[TraceEvent]] = {}
    for event in parsed:
        grouped.setdefault((str(event.task_id), event.attempt), []).append(event)
    for events in grouped.values():
        events.sort(key=lambda e: e.step_index)

    for task_result in summary.task_results:
        for attempt in task_result.attempts:
            key = (str(task_result.task.task_id), attempt.index)
            assert grouped[key] == attempt.events


# ---------------------------------------------------------------------------
# Enum and float fidelity
# ---------------------------------------------------------------------------


def test_no_enum_repr_leaks_into_results_or_traces() -> None:
    summary = _build_summary()

    with tempfile.TemporaryDirectory() as tmp:
        bundle_dir = bundle.write_bundle(summary, Path(tmp))
        results_text = (bundle_dir / "results.json").read_text()
        traces_text = (bundle_dir / "traces.jsonl").read_text()

    for banned in ("TerminationReason", "Stability", "Verdict"):
        assert banned not in results_text
        assert banned not in traces_text


def test_cost_with_accumulation_error_round_trips_exactly() -> None:
    """Exactness is the contract, not pytest.approx: the bundle must
    record what the run actually produced, error and all."""
    summary = _build_summary()

    with tempfile.TemporaryDirectory() as tmp:
        bundle_dir = bundle.write_bundle(summary, Path(tmp))
        results = json.loads((bundle_dir / "results.json").read_text())
        trace_events = [
            json.loads(line)
            for line in (bundle_dir / "traces.jsonl").read_text().splitlines()
        ]

    noteval_task = next(
        t for t in results["tasks"] if t["task_id"] == str(_NOTEVAL_TASK)
    )
    assert noteval_task["attempts"][0]["output"]["cost_usd"] == _WEIRD_COST

    noteval_event = next(e for e in trace_events if e["task_id"] == str(_NOTEVAL_TASK))
    assert noteval_event["cost_usd"] == _WEIRD_COST


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------

_EXPECTED_FILES = {
    "metadata.json",
    "config.json",
    "traces.jsonl",
    "results.json",
    "report.md",
    "replay_command.txt",
}


def test_all_six_files_exist_and_no_seventh() -> None:
    summary = _build_summary()

    with tempfile.TemporaryDirectory() as tmp:
        bundle_dir = bundle.write_bundle(summary, Path(tmp))
        names = {p.name for p in bundle_dir.iterdir()}

    assert names == _EXPECTED_FILES


def test_metadata_git_fields_are_all_strings() -> None:
    summary = _build_summary()

    with tempfile.TemporaryDirectory() as tmp:
        bundle_dir = bundle.write_bundle(summary, Path(tmp))
        metadata = json.loads((bundle_dir / "metadata.json").read_text())

    assert all(isinstance(v, str) for v in metadata["git"].values())


def test_writing_twice_leaves_six_files_with_the_second_writes_content() -> None:
    first = _build_summary()
    second = _build_summary()
    second.task_results = second.task_results[:1]  # PASS only, distinguishable

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle.write_bundle(first, root)
        bundle_dir = bundle.write_bundle(second, root)

        names = {p.name for p in bundle_dir.iterdir()}
        results = json.loads((bundle_dir / "results.json").read_text())
        report = (bundle_dir / "report.md").read_text()

    assert names == _EXPECTED_FILES
    assert [t["task_id"] for t in results["tasks"]] == [str(_PASS_TASK)]
    assert "| `PASS` | passes |" in report
    assert "`FAIL`" not in report


def test_report_file_is_rendered_from_the_same_summary() -> None:
    summary = _build_summary()

    with tempfile.TemporaryDirectory() as tmp:
        bundle_dir = bundle.write_bundle(summary, Path(tmp))
        report = (bundle_dir / "report.md").read_text()

    assert "- Total attempts: 4" in report
    assert "- Passed attempts: 1" in report
    assert "- Failed attempts: 2" in report
    assert "- Not evaluated attempts: 1" in report
    assert "| `NOTEVAL` | never evaluated | 0/1 | 0/1 | Not evaluated |" in report


def test_returned_path_is_the_bundle_directory_and_exists() -> None:
    summary = _build_summary()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle_dir = bundle.write_bundle(summary, root)

        assert bundle_dir == root / str(_RUN_ID)
        assert bundle_dir.is_dir()
