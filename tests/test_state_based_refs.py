"""state_based cites the terminal on a judged failure; refs leave rescore's
deciding set (ROADMAP R42, slice 1).

A state_based failure points at what it judged: the run's ``agent.terminal``
trace event, after which the end state stood. Passes and not-applicable
results cite nothing -- the evaluator norm from R42 on (ADR-0006 D17). The
mismatches stay in the notes.

With evaluators citing more on new bundles, a refs difference no longer
decides a rescore: it is shown as evidence ("refs (evidence)"), as notes
are, and the deciding fields are applicable, evaluated, passed, score and
not_evaluated_cause (ADR-0008 R3, amended). A bundle from before R42
therefore rescores CODE_CHANGED with its verdicts reproducing. Refs decide
by the bundle's era (ADR-0008 R3, as amended): a bundle that records
``refs_version`` (written from R42 on), or that the running code wrote,
holds its refs among the deciding fields, so a forged or dropped ref is a
DIFFER naming the run and the evaluator; an older bundle's refs are
evidence. Both commands print the era, so a bundle demoted to pre-R42 trust
-- its marker deleted and its recorded code edited -- reads as one.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from reference_bundles import ReferenceBundles
from typer.testing import CliRunner

from agentevalops.bundles.constants import REFS_VERSION
from agentevalops.bundles.manifest import write_manifest
from agentevalops.bundles.serializers import from_jsonable
from agentevalops.bundles.validator import validate_bundle
from agentevalops.cli import app
from agentevalops.core.schemas import (
    AgentOutput,
    EndStateRecord,
    EvaluationResult,
    EvaluatorContext,
    Ref,
    ResultBundleMetadata,
    TaskSpec,
    TerminationReason,
    TraceEvent,
    TraceEventKind,
)
from agentevalops.core.types import RunId, TaskId
from agentevalops.evaluators.state_based import StateBasedEvaluator
from agentevalops.replay.rescore import ProvenanceItem, refs_decide

FIXTURES = Path(__file__).parent / "fixtures" / "bundles"
T = TaskId("TE1")
EXPECTED = {"decisions": [{"case_id": "C1", "status": "approved"}]}
WRONG = {"schema": "s", "decisions": [{"case_id": "C1", "status": "rejected"}]}
RIGHT = {"schema": "s", "decisions": [{"case_id": "C1", "status": "approved"}]}


def _trace(*, terminal: bool = True) -> list[TraceEvent]:
    kinds = [TraceEventKind.AGENT_PLAN, TraceEventKind.AGENT_FINAL_ANSWER]
    if terminal:
        kinds.append(TraceEventKind.AGENT_TERMINAL)
    return [
        TraceEvent(run_id=RunId("r"), step_index=n, kind=k, task_id=T, repetition=2)
        for n, k in enumerate(kinds)
    ]


def _judge(
    expected: Any, state: dict[str, Any] | None, trace: list[TraceEvent]
) -> EvaluationResult:
    task = TaskSpec(
        task_id=T,
        benchmark_id="b",
        description="d",
        metadata={} if expected is None else {"expected_end_state": expected},
    )
    states = () if state is None else (EndStateRecord(T, 2, state),)
    context = EvaluatorContext(end_states=states, task_id=T, repetition=2)
    output = AgentOutput(success=True, termination_reason=TerminationReason.COMPLETED)
    return asyncio.run(StateBasedEvaluator().evaluate(task, output, trace, context))


def test_state_based_cites_the_terminal_on_a_judged_failure() -> None:
    """Fail-first: a state_based result carried no refs. The ref is the
    terminal's own address -- not the first event, not a tool call."""
    terminal = Ref("trace", T, 2, 2)
    mismatch = _judge(EXPECTED, WRONG, _trace())
    assert not mismatch.passed and mismatch.refs == [terminal]
    assert "decisions" in mismatch.notes
    missing = _judge(EXPECTED, None, _trace())
    assert not missing.passed and missing.refs == [terminal]
    assert missing.notes == "no end state was recorded for this task run"


@pytest.mark.parametrize(
    ("expected", "state", "terminal"),
    [
        (EXPECTED, RIGHT, True),  # a pass
        (None, RIGHT, True),  # not applicable
        (EXPECTED, None, False),  # no end state, and no terminal to cite
    ],
    ids=["pass", "not-applicable", "no-terminal"],
)
def test_state_based_cites_nothing_otherwise(
    expected: Any, state: dict[str, Any] | None, terminal: bool
) -> None:
    assert _judge(expected, state, _trace(terminal=terminal)).refs == []


def test_rescore_on_a_pre_r42_bundle_reports_code_changed_with_zero_verdicts_moved(
    tmp_path: Path,
) -> None:
    """The 0.6 known-bad golden bundle, written before state_based cited:
    its refs differ from the running code's, as evidence; no verdict
    moves. Fail-first: no refs differed before state_based cited; with it
    citing and refs still deciding, the verdicts did not reproduce."""
    result = CliRunner().invoke(
        app, ["rescore", "--bundle", str(FIXTURES / "seed-narrow-known-bad-0.6")]
    )
    assert result.exit_code == 3, result.output
    lines = result.output.splitlines()
    assert any(line.startswith("Verdicts: reproduce -- ") for line in lines), lines
    assert not any(line.startswith("  ! ") for line in lines), lines
    evidence = [line for line in lines if "refs (evidence)" in line]
    assert any("state-based-v1: refs (evidence)" in line for line in evidence), lines
    # A control's verdicts repeat their evaluations' refs: evidence too.
    assert any(line.startswith("  note: control ") for line in evidence), lines
    assert all(line.endswith("(shown, does not decide)") for line in evidence)
    assert result.output.rstrip().endswith(
        "RESCORE: CODE_CHANGED -- the verdicts reproduce under the running code"
    )


# ---------------------------------------------------------------------------
# The terminal: the last one
# ---------------------------------------------------------------------------


def test_state_based_cites_the_last_terminal() -> None:
    """A trace with two terminal events (none does in practice): the run's
    last word is the one cited."""
    trace = _trace() + [
        TraceEvent(
            run_id=RunId("r"),
            step_index=3,
            kind=TraceEventKind.AGENT_TERMINAL,
            task_id=T,
            repetition=2,
        )
    ]
    assert _judge(EXPECTED, WRONG, trace).refs == [Ref("trace", T, 2, 3)]


def test_the_ref_is_the_terminals_step_index_not_its_position() -> None:
    """A trace whose steps do not start at 0 (a resumed run's, say): the
    ref names the terminal's own step_index."""
    trace = [
        TraceEvent(run_id=RunId("r"), step_index=n, kind=k, task_id=T, repetition=2)
        for n, k in (
            (5, TraceEventKind.AGENT_PLAN),
            (9, TraceEventKind.AGENT_TERMINAL),
        )
    ]
    assert _judge(EXPECTED, WRONG, trace).refs == [Ref("trace", T, 2, 9)]


# ---------------------------------------------------------------------------
# The era: the marker, and who decides
# ---------------------------------------------------------------------------


def _metadata(**changes: Any) -> ResultBundleMetadata:
    base = json.loads(
        (FIXTURES / "seed-narrow-known-bad-0.6" / "metadata.json").read_text("utf-8")
    )
    return from_jsonable(ResultBundleMetadata, {**base, **changes})


def _provenance(differs: bool) -> tuple[ProvenanceItem, ...]:
    running = "b" if differs else "a"
    return (ProvenanceItem("package", "a", running, "differs" if differs else "same"),)


@pytest.mark.parametrize(
    ("marker", "code_differs", "decides"),
    [
        (1, True, True),  # written from R42 on: refs decide under any code
        (2, True, True),
        (None, True, False),  # written before R42, rescored by later code
        (0, True, False),
        (None, False, True),  # the running code wrote it: refs reproduce
    ],
)
def test_refs_decide_by_the_bundles_era(
    marker: int | None, code_differs: bool, decides: bool
) -> None:
    metadata = _metadata(**({} if marker is None else {"refs_version": marker}))
    assert refs_decide(metadata, _provenance(code_differs)) is decides


def test_a_new_bundle_records_its_refs_version_and_an_old_one_does_not(
    reference_bundles: ReferenceBundles,
) -> None:
    fresh = json.loads(
        (reference_bundles["known-bad"].path / "metadata.json").read_text("utf-8")
    )
    assert fresh["refs_version"] == REFS_VERSION == 1
    for name in ("seed-narrow-0.5", "seed-narrow-0.6", "seed-narrow-known-bad-0.6"):
        old = (FIXTURES / name / "metadata.json").read_text("utf-8")
        assert "refs_version" not in old


def test_a_pre_r42_bundles_control_refs_all_read_as_evidence(tmp_path: Path) -> None:
    """The old known-bad golden bundle: its 14 control differences and its 3
    state_based ones are refs, shown as evidence; nothing decides."""
    result = CliRunner().invoke(
        app, ["rescore", "--bundle", str(FIXTURES / "seed-narrow-known-bad-0.6")]
    )
    lines = result.output.splitlines()
    controls = [
        line
        for line in lines
        if line.startswith("  note: control ") and "refs (evidence)" in line
    ]
    evaluations = [line for line in lines if "state-based-v1: refs (evidence)" in line]
    assert (len(controls), len(evaluations)) == (14, 3), lines
    assert not any(line.startswith("  ! ") for line in lines), lines


# ---------------------------------------------------------------------------
# Forgeries of a bundle written from R42 on: each a DIFFER
# ---------------------------------------------------------------------------


def _copy(reference_bundles: ReferenceBundles, tmp_path: Path) -> Path:
    return Path(shutil.copytree(reference_bundles["known-bad"].path, tmp_path / "kb"))


def _edit_refs(bundle: Path, task: str, kind: str, change: Any) -> None:
    """Apply *change* to *task*'s *kind* evaluation refs in all three
    records -- evaluations.json, the summary's evaluations and verdict,
    and the evaluator.score event -- so the three still agree (C1)."""
    evaluations = json.loads((bundle / "evaluations.json").read_text("utf-8"))
    for entry in evaluations:
        if entry["task_id"] == task and entry["evaluator_kind"] == kind:
            entry["refs"] = change(entry["refs"])
    (bundle / "evaluations.json").write_text(json.dumps(evaluations, indent=2), "utf-8")
    summary = json.loads((bundle / "summary.json").read_text("utf-8"))
    for run in summary["task_results"]:
        if run["task_id"] == task:
            for entry in run["evaluations"]:
                if entry["evaluator_kind"] == kind:
                    entry["refs"] = change(entry["refs"])
    (bundle / "summary.json").write_text(json.dumps(summary, indent=2), "utf-8")
    lines = []
    for line in (bundle / "traces.jsonl").read_text("utf-8").splitlines():
        event = json.loads(line)
        payload = event["payload"]
        if (
            event["kind"] == "evaluator.score"
            and event["task_id"] == task
            and payload.get("evaluator_kind") == kind
        ):
            payload["refs"] = change(payload["refs"])
        lines.append(json.dumps(event))
    (bundle / "traces.jsonl").write_text("\n".join(lines) + "\n", "utf-8")


def _moved(index: int) -> Any:
    def change(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {**r, "index": index} if r["stream"] == "tool_calls" else r for r in refs
        ]

    return change


def _rescore(bundle: Path) -> Any:
    write_manifest(bundle)
    assert validate_bundle(bundle).errors == []
    return CliRunner().invoke(app, ["rescore", "--bundle", str(bundle)])


def test_a_forged_ref_in_a_post_r42_bundle_is_a_differ(
    reference_bundles: ReferenceBundles, tmp_path: Path
) -> None:
    """TE1's tool_use ref moved from call 0 to call 1 in all three records,
    report.md left as it was, the manifest re-sealed: the bundle validates,
    and rescore names the run, the evaluator and the ref. Fail-first: with
    refs as evidence everywhere it read MATCH (R42's first version)."""
    bundle = _copy(reference_bundles, tmp_path)
    _edit_refs(bundle, "TE1", "tool_use", _moved(1))
    result = _rescore(bundle)
    assert result.exit_code == 1, result.output
    assert "  ! TE1/0 tool-use-v1: refs recorded " in result.output
    assert result.output.rstrip().endswith("RESCORE: DIFFER")


def test_refs_deleted_from_a_post_r42_bundle_are_a_differ(
    reference_bundles: ReferenceBundles, tmp_path: Path
) -> None:
    """TE3's state_based refs emptied in all three records."""
    bundle = _copy(reference_bundles, tmp_path)
    _edit_refs(bundle, "TE3", "state_based", lambda refs: [])
    result = _rescore(bundle)
    assert result.exit_code == 1, result.output
    assert "  ! TE3/0 state-based-v1: refs recorded [], rescored " in result.output


def _edit_first_control(bundle: Path, change: Any) -> str:
    path = bundle / "controls.json"
    doc = json.loads(path.read_text("utf-8"))
    control = next(c for c in doc["controls"] if c.get("verdicts"))
    change(control["verdicts"][0])
    path.write_text(json.dumps(doc, indent=2), "utf-8")
    return str(control["id"])


def _control_lines(result: Any, control: str) -> list[str]:
    """The fields of the deciding lines that name *control*."""
    prefix = f"  ! control {control}: "
    return [
        line[len(prefix) :].split(" ", 1)[0]
        for line in result.output.splitlines()
        if line.startswith(prefix)
    ]


def test_a_control_refs_forgery_in_a_post_r42_bundle_is_a_differ(
    reference_bundles: ReferenceBundles, tmp_path: Path
) -> None:
    """controls.json alone -- no three-record check covers it -- one
    verdict's refs emptied."""
    bundle = _copy(reference_bundles, tmp_path)
    control = _edit_first_control(bundle, lambda v: v.update(refs=[]))
    result = _rescore(bundle)
    assert result.exit_code == 1, result.output
    assert _control_lines(result, control) == ["verdicts"], result.output
    # Deciding, so not also shown as evidence (the gate review, E4).
    assert "refs (evidence)" not in result.output


def test_a_controls_verdict_counts_still_decide(
    reference_bundles: ReferenceBundles, tmp_path: Path
) -> None:
    bundle = _copy(reference_bundles, tmp_path)
    control = _edit_first_control(bundle, lambda v: v.update(failed=v["failed"] + 1))
    result = _rescore(bundle)
    assert result.exit_code == 1, result.output
    # Counted once, not again as the old era's split (the gate review, E4).
    assert _control_lines(result, control) == ["verdicts"], result.output


def test_stripping_the_marker_does_not_make_a_forged_ref_evidence(
    reference_bundles: ReferenceBundles, tmp_path: Path
) -> None:
    """The forger also deletes refs_version from metadata.json: the running
    code wrote the bundle, so its refs still decide."""
    bundle = _copy(reference_bundles, tmp_path)
    metadata = json.loads((bundle / "metadata.json").read_text("utf-8"))
    del metadata["refs_version"]
    (bundle / "metadata.json").write_text(json.dumps(metadata, indent=2), "utf-8")
    _edit_refs(bundle, "TE1", "tool_use", _moved(1))
    result = _rescore(bundle)
    assert result.exit_code == 1, result.output
    assert "  ! TE1/0 tool-use-v1: refs recorded " in result.output


def test_a_forged_policy_ref_decides(
    reference_bundles: ReferenceBundles, tmp_path: Path
) -> None:
    """POLICY_FIELDS is unchanged: the policy verdict's refs decide."""
    bundle = _copy(reference_bundles, tmp_path)
    path = bundle / "policy.json"
    policy = json.loads(path.read_text("utf-8"))
    policy["refs"] = [
        *policy["refs"],
        {"stream": "trace", "task_id": "TE1", "repetition": 0, "index": 0},
    ]
    path.write_text(json.dumps(policy, indent=2), "utf-8")
    result = _rescore(bundle)
    assert result.exit_code == 1, result.output
    assert "  ! policy: refs recorded " in result.output


def test_an_old_bundles_control_shows_its_refs_beside_a_moved_count(
    tmp_path: Path,
) -> None:
    """A pre-R42 bundle: a control verdict's count edited decides, and the
    refs difference in the same verdict is still shown as evidence."""
    bundle = Path(
        shutil.copytree(FIXTURES / "seed-narrow-known-bad-0.6", tmp_path / "old")
    )
    path = bundle / "controls.json"
    doc = json.loads(path.read_text("utf-8"))
    control = next(
        c
        for c in doc["controls"]
        if c.get("verdicts") and c["id"] == "mgf-agentic:1.2.2"
    )
    control["verdicts"][0]["failed"] += 1
    path.write_text(json.dumps(doc, indent=2), "utf-8")
    result = _rescore(bundle)
    assert "  ! control mgf-agentic:1.2.2: verdicts recorded " in result.output
    assert (
        "  note: control mgf-agentic:1.2.2: refs (evidence) recorded " in result.output
    )


def _set_metadata(bundle: Path, **changes: Any) -> None:
    """Set (or, with ``None``, delete) keys of metadata.json."""
    path = bundle / "metadata.json"
    metadata = json.loads(path.read_text("utf-8"))
    for key, value in changes.items():
        if value is None:
            metadata.pop(key, None)
        else:
            metadata[key] = value
    path.write_text(json.dumps(metadata, indent=2), "utf-8")


OTHER_CODE = "0" * 64


def test_a_marked_bundle_rescored_by_other_code_still_decides_its_refs(
    reference_bundles: ReferenceBundles, tmp_path: Path
) -> None:
    """The marker alone decides under changed code: a forged ref in a bundle
    that records refs_version, whose package_sha256 is not the running
    code's, still moves a verdict. Every other forgery here is rescored by
    the code that wrote it, where the same-code rule decides on its own (the
    gate's mutation review, 2b)."""
    bundle = _copy(reference_bundles, tmp_path)
    _set_metadata(bundle, package_sha256=OTHER_CODE)
    _edit_refs(bundle, "TE1", "tool_use", _moved(1))
    result = _rescore(bundle)
    assert result.exit_code == 3, result.output
    lines = result.output.splitlines()
    assert "Refs:    deciding (refs_version 1)" in lines, lines
    assert any(line.startswith("Verdicts: do not reproduce -- ") for line in lines)
    assert "  ! TE1/0 tool-use-v1: refs recorded " in result.output
    assert result.output.rstrip().endswith(
        "RESCORE: CODE_CHANGED -- the verdicts do not reproduce under the running code"
    )


def test_a_toy_bundle_without_its_marker_still_decides_its_refs(
    reference_bundles: ReferenceBundles, tmp_path: Path
) -> None:
    """A toy run records no pack, so two provenance items read "not
    compared": that is not a difference in the code, so the running code
    still wrote the bundle, and a ref forged after deleting the marker
    decides (the gate's mutation review, E3: counting "not compared"
    against the rule let this read MATCH)."""
    bundle = Path(shutil.copytree(reference_bundles["toy"].path, tmp_path / "toy"))
    _set_metadata(bundle, refs_version=None)
    first = json.loads((bundle / "evaluations.json").read_text("utf-8"))[0]
    task = first["task_id"]
    added = {"stream": "trace", "task_id": task, "repetition": 0, "index": 0}
    _edit_refs(bundle, task, first["evaluator_kind"], lambda refs: [*refs, added])
    result = _rescore(bundle)
    assert result.exit_code == 1, result.output
    lines = result.output.splitlines()
    assert any(line.endswith("not compared: no pack recorded") for line in lines)
    assert (
        "Refs:    deciding (no refs_version, but the running code wrote this bundle)"
        in lines
    ), lines
    assert any(
        line.startswith(f"  ! {task}/0 ") and ": refs recorded " in line
        for line in lines
    ), lines


# ---------------------------------------------------------------------------
# The era line: rescore says what decided, validate-bundle what is recorded
# ---------------------------------------------------------------------------


def _strip_state_based(bundle: Path) -> None:
    """The era downgrade's second half: every state_based ref gone from the
    three records, and from the control verdicts that repeat it where no
    other evaluator cites the same record."""
    evaluations = json.loads((bundle / "evaluations.json").read_text("utf-8"))
    tasks = {
        e["task_id"]
        for e in evaluations
        if e["evaluator_kind"] == "state_based" and e["refs"]
    }
    assert tasks, "the known-bad bundle has state_based failures to strip"
    stripped: set[tuple[Any, ...]] = set()

    def key(ref: dict[str, Any]) -> tuple[Any, ...]:
        return (ref["stream"], ref["task_id"], ref["repetition"], ref["index"])

    def strip(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        stripped.update(key(r) for r in refs)
        return []

    for task in sorted(tasks):
        _edit_refs(bundle, task, "state_based", strip)
    evaluations = json.loads((bundle / "evaluations.json").read_text("utf-8"))
    gone = stripped - {key(r) for e in evaluations for r in e["refs"]}
    path = bundle / "controls.json"
    doc = json.loads(path.read_text("utf-8"))
    for control in doc["controls"]:
        for verdict in control.get("verdicts") or ():
            verdict["refs"] = [r for r in verdict["refs"] if key(r) not in gone]
    path.write_text(json.dumps(doc, indent=2), "utf-8")


def test_the_era_downgrade_under_the_same_code_is_a_differ(
    reference_bundles: ReferenceBundles, tmp_path: Path
) -> None:
    """The owner's forgery: refs_version deleted AND the state_based refs
    stripped together, re-sealed. The running code wrote the bundle, so the
    stripped refs decide, and the era line says why."""
    bundle = _copy(reference_bundles, tmp_path)
    _set_metadata(bundle, refs_version=None)
    _strip_state_based(bundle)
    result = _rescore(bundle)
    assert result.exit_code == 1, result.output
    lines = result.output.splitlines()
    assert (
        "Refs:    deciding (no refs_version, but the running code wrote this bundle)"
        in lines
    ), lines
    assert "  ! TE3/0 state-based-v1: refs recorded [], rescored " in result.output


def test_the_era_downgrade_under_other_code_is_a_visible_demotion(
    reference_bundles: ReferenceBundles, tmp_path: Path
) -> None:
    """The same forgery with package_sha256 also edited: the bundle now
    reads as one written before R42 by other code, and is trusted as one --
    its refs are evidence, the verdicts reproduce, and the outcome is
    CODE_CHANGED, never MATCH. Accepted (ADR-0008 R3, as amended) only
    because the demotion is visible: both commands print the era, and the
    signature is the defence against tampering at pre-R42 trust."""
    bundle = _copy(reference_bundles, tmp_path)
    _set_metadata(bundle, refs_version=None, package_sha256=OTHER_CODE)
    _strip_state_based(bundle)
    result = _rescore(bundle)
    assert result.exit_code == 3, result.output
    lines = result.output.splitlines()
    assert "Refs:    evidence (pre-R42 bundle)" in lines, lines
    assert any(line.startswith("Verdicts: reproduce -- ") for line in lines)
    assert not any(line.startswith("  ! ") for line in lines), lines
    assert any(
        line.startswith("  note: TE3/0 state-based-v1: refs (evidence) recorded []")
        for line in lines
    ), lines
    validated = CliRunner().invoke(app, ["validate-bundle", "--bundle", str(bundle)])
    assert "Refs:          evidence (pre-R42 bundle)" in validated.output.splitlines()


@pytest.mark.parametrize(
    ("bundle", "rescored", "recorded"),
    [
        (
            "fresh",
            "deciding (refs_version 1)",
            "deciding (refs_version 1)",
        ),
        (
            "golden",
            "evidence (pre-R42 bundle)",
            "evidence (pre-R42 bundle)",
        ),
        (
            "unmarked",
            "deciding (no refs_version, but the running code wrote this bundle)",
            "evidence (pre-R42 bundle)",
        ),
        (
            "zero",
            "deciding (unrecognised refs_version 0, but the running code wrote "
            "this bundle)",
            "evidence (unrecognised refs_version 0)",
        ),
        (
            "true",
            "deciding (unrecognised refs_version True, but the running code "
            "wrote this bundle)",
            "evidence (unrecognised refs_version True)",
        ),
        (
            "zero-other-code",
            "evidence (unrecognised refs_version 0)",
            "evidence (unrecognised refs_version 0)",
        ),
        (
            "two-other-code",
            "deciding (refs_version 2)",
            "deciding (refs_version 2)",
        ),
    ],
)
def test_both_commands_print_the_refs_era(
    reference_bundles: ReferenceBundles,
    tmp_path: Path,
    bundle: str,
    rescored: str,
    recorded: str,
) -> None:
    """One era line in each command: rescore's says what decided, and why;
    validate-bundle's what the bundle records -- so a supposedly new bundle
    claiming to be old reads as such. --json carries rescore's."""
    if bundle == "golden":
        path = Path(
            shutil.copytree(FIXTURES / "seed-narrow-known-bad-0.6", tmp_path / "old")
        )
    else:
        path = _copy(reference_bundles, tmp_path)
        _set_metadata(
            path,
            **{
                "fresh": {},
                "unmarked": {"refs_version": None},
                "zero": {"refs_version": 0},
                "true": {"refs_version": True},
                "zero-other-code": {"refs_version": 0, "package_sha256": OTHER_CODE},
                "two-other-code": {"refs_version": 2, "package_sha256": OTHER_CODE},
            }[bundle],
        )
        write_manifest(path)
    out = tmp_path / "rescore.json"
    result = CliRunner().invoke(
        app, ["rescore", "--bundle", str(path), "--json", str(out)]
    )
    assert f"Refs:    {rescored}" in result.output.splitlines(), result.output
    doc = json.loads(out.read_text("utf-8"))
    assert doc["refs"] == {"deciding": rescored.startswith("deciding"), "era": rescored}
    validated = CliRunner().invoke(app, ["validate-bundle", "--bundle", str(path)])
    assert validated.exit_code == 0, validated.output
    assert f"Refs:          {recorded}" in validated.output.splitlines()
