"""Loading a bundle's records back into their dataclasses
(bundles/serializers.py; ADR-0008 R1; R29 (b1) slice 2).

``from_jsonable`` is ``to_jsonable``'s inverse, driven by each dataclass's
own fields and type hints: a field added to a schema is loaded as soon as
the writer writes it, and a key the dataclass does not know is an error.
Every record of a fresh toy, seed and known-bad bundle loads and dumps back
to the same JSON.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import pytest
from reference_bundles import ReferenceBundles

from agentevalops.bundles.serializers import (
    LoadError,
    from_jsonable,
    load_agent_output,
    load_end_state,
    load_evaluation,
    load_run_config,
    load_tool_call,
    load_trace_event,
    to_jsonable,
)
from agentevalops.core.schemas import (
    PolicyVerdict,
    ResultBundleMetadata,
    RunSummary,
    ToolCallRecord,
    TraceEvent,
)

KINDS = ["toy", "seed", "known-bad"]


@pytest.fixture(scope="module")
def bundles(reference_bundles: ReferenceBundles) -> dict[str, Path]:
    """The shared toy, seed and known-bad bundles; read only."""
    return {kind: reference_bundles[kind].path for kind in KINDS}


def _lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line]


def _json(path: Path) -> Any:
    return json.loads(path.read_text("utf-8"))


@pytest.mark.parametrize("kind", KINDS)
def test_every_stream_line_round_trips(bundles: dict[str, Path], kind: str) -> None:
    bundle = bundles[kind]
    for name, load in (
        ("tool_calls.jsonl", load_tool_call),
        ("end_states.jsonl", load_end_state),
        ("traces.jsonl", load_trace_event),
    ):
        lines = _lines(bundle / name)
        if name != "tool_calls.jsonl" or kind != "toy":
            assert lines, name
        for line in lines:
            assert to_jsonable(load(line)) == line, name


@pytest.mark.parametrize("kind", KINDS)
def test_every_evaluation_round_trips_with_its_task_run(
    bundles: dict[str, Path], kind: str
) -> None:
    for entry in _json(bundles[kind] / "evaluations.json"):
        task_id, repetition, result = load_evaluation(entry)
        assert (task_id, repetition) == (entry["task_id"], entry["repetition"])
        assert {
            **to_jsonable(result),
            "task_id": task_id,
            "repetition": repetition,
        } == entry


@pytest.mark.parametrize("kind", KINDS)
def test_the_summary_its_outputs_the_config_policy_and_metadata_round_trip(
    bundles: dict[str, Path], kind: str
) -> None:
    bundle = bundles[kind]
    summary = _json(bundle / "summary.json")
    assert to_jsonable(from_jsonable(RunSummary, summary)) == summary
    for result in summary["task_results"]:
        assert to_jsonable(load_agent_output(result["output"])) == result["output"]
    config = _json(bundle / "config.json")["run_config"]
    assert to_jsonable(load_run_config(config)) == config
    policy = _json(bundle / "policy.json")
    assert to_jsonable(from_jsonable(PolicyVerdict, policy)) == policy
    metadata = _json(bundle / "metadata.json")
    assert to_jsonable(from_jsonable(ResultBundleMetadata, metadata)) == metadata


def test_loaded_values_have_their_types(bundles: dict[str, Path]) -> None:
    event = load_trace_event(_lines(bundles["seed"] / "traces.jsonl")[0])
    assert isinstance(event, TraceEvent) and isinstance(event.timestamp, datetime)
    assert event.kind.value == "agent.plan"
    record = load_tool_call(_lines(bundles["seed"] / "tool_calls.jsonl")[0])
    assert isinstance(record, ToolCallRecord) and isinstance(record.injections, tuple)
    summary = from_jsonable(RunSummary, _json(bundles["seed"] / "summary.json"))
    assert summary.policy_verdict is not None
    assert summary.task_results[0].evaluations[0].refs[0].stream == "trace"


# Guards, fail-first: before the loader checked, each of these loaded.


def test_a_key_the_dataclass_does_not_know_is_an_error(
    bundles: dict[str, Path],
) -> None:
    line = _lines(bundles["seed"] / "tool_calls.jsonl")[0]
    with pytest.raises(LoadError, match=r"no field for \['surprise'\]"):
        load_tool_call({**line, "surprise": 1})


def test_a_required_field_that_is_missing_is_an_error(bundles: dict[str, Path]) -> None:
    line = _lines(bundles["seed"] / "tool_calls.jsonl")[0]
    del line["tool_name"]
    with pytest.raises(LoadError, match="tool_name"):
        load_tool_call(line)


def test_a_missing_field_with_a_default_takes_it(bundles: dict[str, Path]) -> None:
    """An older bundle that predates a defaulted field still loads."""
    line = _lines(bundles["seed"] / "traces.jsonl")[0]
    del line["repetition"]
    assert load_trace_event(line).repetition == 0


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"approval": "maybe"}, "is not one of"),
        ({"index": "three"}, "expected int"),
        ({"injections": "not-a-list"}, "expected an array"),
        ({"arguments": ["a"]}, "expected an object"),
    ],
)
def test_a_value_of_the_wrong_type_is_an_error(
    bundles: dict[str, Path], change: dict[str, Any], message: str
) -> None:
    line = _lines(bundles["seed"] / "tool_calls.jsonl")[0]
    with pytest.raises(LoadError, match=message):
        load_tool_call({**line, **change})


def test_a_field_added_to_a_dataclass_needs_no_loader_change() -> None:
    """What the generic loader is for: a new field round-trips by itself."""

    @dataclasses.dataclass
    class Record:
        name: str
        when: datetime
        mode: Literal["a", "b"] = "a"
        tags: tuple[str, ...] = ()
        later: int | None = None

    record = Record("x", datetime(2026, 11, 2, 1, 2, 3), "b", ("t",), 7)
    assert from_jsonable(Record, to_jsonable(record)) == record


def test_a_union_of_two_types_is_refused() -> None:
    @dataclasses.dataclass
    class Ambiguous:
        value: int | str

    with pytest.raises(LoadError, match="cannot choose"):
        from_jsonable(Ambiguous, {"value": 1})
