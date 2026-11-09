"""The triage walker (ROADMAP R38, slice 2).

One task run per screen -- the task, its calls with faults and injections
by name, its end state -- and the reviewer's verdict before the harness's
findings, unless asked otherwise. Single-letter keys: p, f, d for the
verdict, v for the full records, b to step back, q to quit. A scripted
``Prompter`` drives every session here, without a terminal.

The bundle is read, never written: every byte of it, and its signature,
are the same after a session as before, and validate-bundle, the gate and
rescore print the same with the sidecar beside it as without.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest
from reference_bundles import ReferenceBundles
from test_signing import _allowed, _key, home  # noqa: F401  (home: a fixture)
from typer.testing import CliRunner

from agentevalops.bundles.signing import sign_manifest, verify_manifest
from agentevalops.cli import app
from agentevalops.triage.store import load_annotations, sidecar_path
from agentevalops.triage.walker import (
    VERDICT_PROMPT,
    TaskInfo,
    agreement,
    load_view,
    render_screen,
    walk,
)

T0 = datetime(2026, 11, 9, 8, 30, tzinfo=timezone.utc)


def _now() -> datetime:
    return T0


class Script:
    """A Prompter that answers from a script and logs everything."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.log: list[tuple[str, str]] = []

    def show(self, text: str) -> None:
        self.log.append(("show", text))

    def ask(self, prompt: str) -> str:
        self.log.append(("ask", prompt))
        if not self.answers:
            raise AssertionError(f"the script ran out at {prompt!r}")
        return self.answers.pop(0)

    def shown(self) -> str:
        return "\n".join(text for kind, text in self.log if kind == "show")

    def first(self, kind: str, needle: str) -> int:
        return next(n for n, (k, t) in enumerate(self.log) if k == kind and needle in t)


@pytest.fixture
def bundle(reference_bundles: ReferenceBundles, tmp_path: Path) -> Path:
    """A copy of the known-bad bundle: 26 failed task runs."""
    return reference_bundles["known-bad"].copy(tmp_path / "known-bad")


def _run(bundle: Path) -> tuple[str, int]:
    """The bundle's run_id and seed, as its own records give them."""
    summary = json.loads((bundle / "summary.json").read_text("utf-8"))
    config = json.loads((bundle / "config.json").read_text("utf-8"))
    return summary["run_id"], config["run_config"]["seed"]


def _hashes(bundle: Path) -> dict[str, str]:
    return {
        str(p.relative_to(bundle)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(bundle.rglob("*"))
        if p.is_file()
    }


def _walk_all(bundle: Path, verdict: str = "f") -> Script:
    view = load_view(bundle)
    answers = [
        a for n in range(len(view.frame.entries)) for a in (verdict, f"note {n}", "")
    ]
    script = Script(*answers)
    walk(view, script, now=_now)
    return script


# ---------------------------------------------------------------------------
# The screen
# ---------------------------------------------------------------------------


def test_a_screen_shows_the_task_the_calls_and_the_end_state(bundle: Path) -> None:
    """Fail-first: no module. RB2's scheduled fault and RB5's injected
    result are named; the arguments are shortened; nothing of the
    harness's findings."""
    view = load_view(bundle)
    entries = [e.task_id for e in view.frame.entries]
    rb2 = render_screen(
        view, entries.index("RB2"), TaskInfo("Decide C2.", "robustness")
    )
    lines = rb2.splitlines()
    assert (
        lines[0]
        == f"[{entries.index('RB2') + 1}/26] RB2 rep 0 · robustness · scripted run"
    )
    assert lines[1] == "Task: Decide C2."
    assert lines[2] == "Calls:"
    assert "1. case_lookup(case_id=C2)" in lines[3] and lines[3].endswith(
        "FAULT malformed_result"
    )
    assert lines[-1].startswith("Injections: none · End state: ")
    assert "decisions=1" in lines[-1]
    rb5 = render_screen(view, entries.index("RB5"))
    assert "Injections: inj-record-export (call 1)" in rb5
    assert "Task: (not recorded in this view)" in rb5
    assert " · - · scripted run" in rb5.splitlines()[0]
    for screen in (rb2, rb5):
        assert "Harness" not in screen and "rule " not in screen


# ---------------------------------------------------------------------------
# Verdict before findings
# ---------------------------------------------------------------------------


def test_findings_stay_hidden_until_the_verdict(bundle: Path) -> None:
    script = Script("f", "looks wrong", "q")
    walk(load_view(bundle), script, now=_now)
    verdict = script.first("ask", VERDICT_PROMPT)
    findings = script.first("show", "Harness: FAILED")
    assert findings > verdict
    assert not any("Harness" in t for k, t in script.log[:verdict] if k == "show")
    assert script.log[findings + 1] == (
        "ask",
        "You and the harness agree. [enter] next",
    )


def test_show_findings_first_flips_the_order(bundle: Path) -> None:
    script = Script("f", "", "q")
    walk(load_view(bundle), script, show_findings_first=True, now=_now)
    assert script.first("show", "Harness: FAILED") < script.first("ask", VERDICT_PROMPT)
    assert sum("Harness:" in t for k, t in script.log if k == "show") == 1


def test_v_shows_every_argument_and_result_in_full(bundle: Path) -> None:
    script = Script("v", "p", "", "q")
    view = load_view(bundle)
    walk(view, script, now=_now)
    full = script.log[script.first("show", "Full records")][1]
    first = view.frame.entries[0]
    calls = view.records[(first.task_id, first.repetition)].calls
    for call in calls:
        assert f"result: {call.result}" in full
    assert "End state:" in full


def test_an_unknown_key_is_named_and_asked_again(bundle: Path) -> None:
    script = Script("x", "d", "", "q")
    walk(load_view(bundle), script, now=_now)
    assert (
        "Keys: p pass, f fail, d defer, v full records, b back, q quit."
        in script.shown()
    )
    assert [
        a.verdict for a in load_annotations(bundle, load_view(bundle).run_id).values()
    ] == ["defer"]


@pytest.mark.parametrize(
    ("verdict", "harness", "line"),
    [
        ("pass", "passed", "You and the harness agree."),
        ("fail", "failed", "You and the harness agree."),
        ("pass", "failed", "You and the harness disagree: you passed a run it failed."),
        ("fail", "passed", "You and the harness disagree: you failed a run it passed."),
        ("defer", "failed", "Deferred."),
        ("fail", "not_evaluated", "The harness did not evaluate this run."),
    ],
)
def test_the_agreement_line(verdict: str, harness: str, line: str) -> None:
    assert agreement(verdict, harness) == line  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# b, q, resume
# ---------------------------------------------------------------------------


def test_b_steps_back_and_the_new_verdict_supersedes(bundle: Path) -> None:
    script = Script("f", "first look", "", "b", "p", "on reflection", "", "q")
    view = load_view(bundle)
    walk(view, script, now=_now)
    first, second = view.frame.entries[:2]
    lines = sidecar_path(bundle).read_text("utf-8").splitlines()
    assert len(lines) == 2
    annotations = load_annotations(bundle, view.run_id)
    assert list(annotations) == [(first.task_id, first.repetition)]
    assert annotations[(first.task_id, first.repetition)].note == "on reflection"
    screens = [
        t.splitlines()[0] for k, t in script.log if k == "show" and t.startswith("[")
    ]
    assert screens == [
        f"[1/26] {first.task_id} rep 0 · - · scripted run",
        f"[2/26] {second.task_id} rep 0 · - · scripted run",
        f"[1/26] {first.task_id} rep 0 · - · scripted run",
        f"[2/26] {second.task_id} rep 0 · - · scripted run",
    ]


def test_b_on_the_first_run_says_there_is_nothing_before(bundle: Path) -> None:
    script = Script("b", "q")
    walk(load_view(bundle), script, now=_now)
    assert "Nothing before this run in this session." in script.shown()


def test_q_quits_and_a_resumed_session_starts_where_it_stopped(bundle: Path) -> None:
    first = Script("f", "one", "", "q")
    result = walk(load_view(bundle), first, now=_now)
    assert (result.annotated, result.quit) == (1, True)
    second = Script("q")
    walk(load_view(bundle), second, now=_now)
    run_id, seed = _run(bundle)
    assert second.log[0][1] == (
        f"Triage of {run_id}: 26 task runs to walk -- 26 failed, 0 of 0 passing "
        f"sampled; 0 not evaluated, not walked. Seed {seed}. 1 already annotated."
    )
    assert second.log[1][1].startswith("[2/26] ")


def test_the_session_header_counts_what_is_not_walked(
    reference_bundles: ReferenceBundles, tmp_path: Path
) -> None:
    seed = reference_bundles["seed"].copy(tmp_path / "seed")
    script = Script("q")
    walk(load_view(seed), script, now=_now)
    run_id, run_seed = _run(seed)
    assert script.log[0][1] == (
        f"Triage of {run_id}: 6 task runs to walk -- 0 failed, 6 of 26 passing "
        f"sampled; 0 not evaluated, not walked. Seed {run_seed}."
    )


# ---------------------------------------------------------------------------
# A whole session, and what it leaves alone
# ---------------------------------------------------------------------------


def test_a_walkthrough_of_the_known_bad_bundle_writes_countable_notes(
    bundle: Path,
) -> None:
    script = _walk_all(bundle)
    annotations = load_annotations(bundle, _run(bundle)[0])
    assert len(annotations) == 26
    assert Counter(a.verdict for a in annotations.values()) == {"fail": 26}
    assert sorted(a.note for a in annotations.values()) == sorted(
        f"note {n}" for n in range(26)
    )
    assert all(
        a.annotated_at == T0.isoformat() and a.annotator is None
        for a in annotations.values()
    )
    assert script.log[-1] == ("show", "End of the frame: 26 annotated this session.")


def test_a_session_changes_no_byte_of_the_bundle(bundle: Path) -> None:
    before = _hashes(bundle)
    _walk_all(bundle)
    assert _hashes(bundle) == before
    assert sidecar_path(bundle).exists()
    assert not sidecar_path(bundle).is_relative_to(bundle)


def test_a_signed_bundle_still_verifies_after_a_session(
    home: Path,  # noqa: F811
    reference_bundles: ReferenceBundles,
) -> None:
    """The same, with a signature: a throwaway key in the test's own HOME."""
    bundle = reference_bundles["known-bad"].copy(home / "known-bad")
    key = _key(home, "reviewer")
    sign_manifest(bundle, key)
    allowed = _allowed(home, reviewer=key)
    signer = verify_manifest(bundle, allowed)
    before = _hashes(bundle)
    _walk_all(bundle)
    assert _hashes(bundle) == before
    assert verify_manifest(bundle, allowed) == signer


def test_validate_gate_and_rescore_ignore_the_sidecar(bundle: Path) -> None:
    def outputs() -> list[tuple[int, str]]:
        runs = [
            ["validate-bundle", "--bundle", str(bundle)],
            ["gate", "--bundle", str(bundle)],
            ["rescore", "--bundle", str(bundle)],
        ]
        return [
            (r.exit_code, r.output) for r in (CliRunner().invoke(app, a) for a in runs)
        ]

    without = outputs()
    _walk_all(bundle)
    assert sidecar_path(bundle).exists()
    assert outputs() == without
    shutil.copy(sidecar_path(bundle), bundle.parent / "elsewhere.jsonl")
    assert outputs() == without
