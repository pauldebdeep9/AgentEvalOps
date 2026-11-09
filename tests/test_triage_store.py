"""The triage sidecar (ROADMAP R38, slice 1).

A reviewer's verdicts on a bundle's task runs go to ``<bundle>.triage.jsonl``
beside the bundle -- never inside it, so validation never sees it. The file
is a log: one JSON line per annotation, appended, never edited; the last
line for a (task_id, repetition) wins, and a resumed session skips the
runs already annotated.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentevalops.triage.store import (
    Annotation,
    TriageError,
    append_annotation,
    load_annotations,
    sidecar_path,
)

RUN = "seed-run"
T0 = datetime(2026, 11, 9, 8, 30, tzinfo=timezone.utc)


def _clock(*times: datetime):  # type: ignore[no-untyped-def]
    ticks = iter(times)
    return lambda: next(ticks)


def test_the_sidecar_sits_beside_the_bundle(tmp_path: Path) -> None:
    """Fail-first: no module."""
    bundle = tmp_path / "runs" / "seed-1"
    bundle.mkdir(parents=True)
    expected = (tmp_path / "runs" / "seed-1.triage.jsonl").resolve()
    assert sidecar_path(bundle) == expected
    assert sidecar_path(Path(f"{bundle}/")) == expected
    assert sidecar_path(bundle / ".." / "seed-1") == expected
    assert not sidecar_path(bundle).is_relative_to(bundle)


def test_an_annotation_is_one_json_line_with_the_annotator_only_when_given(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "seed-1"
    bundle.mkdir()
    first = append_annotation(
        bundle, RUN, "TE1", 0, "fail", "notice before decision", now=_clock(T0)
    )
    append_annotation(bundle, RUN, "TE2", 1, "pass", "", annotator="dp", now=_clock(T0))
    lines = sidecar_path(bundle).read_text("utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [
        {
            "task_id": "TE1",
            "repetition": 0,
            "verdict": "fail",
            "note": "notice before decision",
            "annotated_at": "2026-11-09T08:30:00+00:00",
            "run_id": RUN,
        },
        {
            "task_id": "TE2",
            "repetition": 1,
            "verdict": "pass",
            "note": "",
            "annotated_at": "2026-11-09T08:30:00+00:00",
            "run_id": RUN,
            "annotator": "dp",
        },
    ]
    assert first == Annotation(
        "TE1", 0, "fail", "notice before decision", T0.isoformat(), RUN
    )
    assert list(bundle.iterdir()) == []


@pytest.mark.parametrize("verdict", ["maybe", "", "PASS"])
def test_a_verdict_is_pass_fail_or_defer(tmp_path: Path, verdict: str) -> None:
    with pytest.raises(TriageError, match="verdict"):
        append_annotation(tmp_path / "b", RUN, "TE1", 0, verdict, "", now=_clock(T0))  # type: ignore[arg-type]
    assert not sidecar_path(tmp_path / "b").exists()


def test_the_last_line_per_run_wins_and_the_log_keeps_both(tmp_path: Path) -> None:
    bundle = tmp_path / "seed-1"
    later = datetime(2026, 11, 9, 9, 0, tzinfo=timezone.utc)
    append_annotation(bundle, RUN, "TE1", 0, "fail", "first look", now=_clock(T0))
    append_annotation(bundle, RUN, "TE3", 0, "defer", "", now=_clock(T0))
    append_annotation(bundle, RUN, "TE1", 0, "pass", "on reflection", now=_clock(later))
    annotations = load_annotations(bundle, RUN)
    assert list(annotations) == [("TE1", 0), ("TE3", 0)]
    assert annotations[("TE1", 0)].verdict == "pass"
    assert annotations[("TE1", 0)].note == "on reflection"
    assert len(sidecar_path(bundle).read_text("utf-8").splitlines()) == 3


def test_a_missing_sidecar_is_no_annotations(tmp_path: Path) -> None:
    assert load_annotations(tmp_path / "seed-1", RUN) == {}


def test_a_line_that_does_not_load_is_named(tmp_path: Path) -> None:
    bundle = tmp_path / "seed-1"
    append_annotation(bundle, RUN, "TE1", 0, "fail", "", now=_clock(T0))
    with sidecar_path(bundle).open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")
    with pytest.raises(TriageError, match="line 2"):
        load_annotations(bundle, RUN)


def test_a_line_from_another_run_is_refused(tmp_path: Path) -> None:
    """A sidecar copied beside another bundle does not silently annotate it."""
    bundle = tmp_path / "seed-1"
    append_annotation(bundle, "other-run", "TE1", 0, "fail", "", now=_clock(T0))
    with pytest.raises(TriageError, match="other-run"):
        load_annotations(bundle, RUN)
