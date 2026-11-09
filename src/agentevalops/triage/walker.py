"""The triage walker: one task run per screen, the reviewer's verdict
before the harness's findings (ROADMAP R38).

A screen shows what a reviewer judges from -- the task, the calls with
their faults and injections by name, the end state -- and not what the
evaluators found, until a verdict is in: ``p`` pass, ``f`` fail, ``d``
defer, then a free-text note. ``v`` shows every argument and result in
full, ``b`` steps back to the run before (a new verdict supersedes the
old one in the sidecar's log), ``q`` quits; a later session resumes at the
first run not yet annotated. The findings, and whether the reviewer and
the harness agree, follow the verdict -- or come first, when asked.

Everything goes through a two-method ``Prompter`` (``show``, ``ask``), so
a session runs from a script in the tests and from the terminal in the
command. The bundle is read through ``BundleReader`` and the typed
loaders, never written; the verdicts go to the sidecar beside it.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from agentevalops.bundles.reader import BundleReader
from agentevalops.bundles.serializers import (
    from_jsonable,
    load_end_state,
    load_run_config,
    load_tool_call,
)
from agentevalops.core.schemas import EndStateRecord, TaskRunResult, ToolCallRecord
from agentevalops.triage.frame import (
    DEFAULT_PASSING_SHARE,
    Frame,
    FrameEntry,
    Harness,
    sampling_frame,
)
from agentevalops.triage.store import (
    Verdict,
    _utcnow,
    append_annotation,
    load_annotations,
)

__all__ = [
    "KEYS_HELP",
    "VERDICT_PROMPT",
    "Prompter",
    "RunRecords",
    "TaskInfo",
    "TriageView",
    "WalkResult",
    "agreement",
    "load_view",
    "render_screen",
    "walk",
]

VERDICT_PROMPT = (
    "Your verdict [p]ass [f]ail [d]efer, [v] full records, [b] back, [q] quit: "
)
KEYS_HELP = "Keys: p pass, f fail, d defer, v full records, b back, q quit."
_VERDICTS: dict[str, Verdict] = {"p": "pass", "f": "fail", "d": "defer"}
#: Arguments longer than this are shortened on the screen; ``v`` shows them.
_SHORT = 24


class Prompter(Protocol):
    """Where a session shows its screens and reads its answers."""

    def show(self, text: str) -> None: ...

    def ask(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class TaskInfo:
    """What the bundle's records do not carry about a task: its text and
    its category (the command rebuilds them from the run's benchmark)."""

    description: str = ""
    category: str = ""


@dataclass(frozen=True)
class RunRecords:
    """One task run's records, read from the bundle."""

    result: TaskRunResult
    calls: tuple[ToolCallRecord, ...]
    end_state: EndStateRecord | None


@dataclass(frozen=True)
class TriageView:
    """A bundle as a session sees it: the frame to walk and each run's
    records -- loaded once, read-only."""

    bundle: Path
    run_id: str
    frame: Frame
    records: Mapping[tuple[str, int], RunRecords]
    runner_label: str


@dataclass(frozen=True)
class WalkResult:
    annotated: int
    quit: bool


def _runner_label(config: Mapping[str, Any]) -> str:
    runner = config.get("runner_id")
    if runner == "mock":
        return "scripted run"
    return f"{runner} run" if runner else "run"


def load_view(
    bundle: Path,
    *,
    seed: int | None = None,
    passing_share: float = DEFAULT_PASSING_SHARE,
    include_not_evaluated: bool = False,
) -> TriageView:
    """The bundle at *bundle*, read through ``BundleReader`` and the typed
    loaders; *seed* defaults to the run's own."""
    loaded = BundleReader(Path(bundle)).read()
    run_config = load_run_config(loaded.config.get("run_config"))
    results = [
        from_jsonable(TaskRunResult, item, where="summary.json task_results")
        for item in loaded.summary.get("task_results") or []
    ]
    calls: dict[tuple[str, int], list[ToolCallRecord]] = {}
    for item in loaded.tool_calls:
        call = load_tool_call(item)
        calls.setdefault((str(call.task_id), call.repetition), []).append(call)
    states = {
        (str(s.task_id), s.repetition): s
        for s in (load_end_state(item) for item in loaded.end_states)
    }
    run_id = str(loaded.summary.get("run_id", run_config.run_id))
    records = {
        (str(r.task_id), r.repetition): RunRecords(
            result=r,
            calls=tuple(
                sorted(
                    calls.get((str(r.task_id), r.repetition), []), key=lambda c: c.index
                )
            ),
            end_state=states.get((str(r.task_id), r.repetition)),
        )
        for r in results
    }
    frame = sampling_frame(
        results,
        seed=run_config.seed if seed is None else seed,
        passing_share=passing_share,
        include_not_evaluated=include_not_evaluated,
        run_id=run_id,
    )
    return TriageView(
        Path(bundle), run_id, frame, records, _runner_label(loaded.config)
    )


# ---------------------------------------------------------------------------
# What a screen shows
# ---------------------------------------------------------------------------


def _value(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _arguments(arguments: Mapping[str, Any]) -> str:
    parts = []
    for key, value in arguments.items():
        text = _value(value)
        if len(text) > _SHORT:
            text = text[: _SHORT - 1] + "…"
        parts.append(f"{key}={text}")
    return ", ".join(parts)


def _status(call: ToolCallRecord) -> str:
    if call.approval in ("denied", "timed_out") and not call.executed:
        return f"NOT RUN ({call.approval})"
    if call.fault is not None:
        status = f"FAULT {call.fault}"
    elif call.error:
        status = "error"
    elif not call.executed:
        status = "not run"
    else:
        status = "ok"
    return f"approved, {status}" if call.approval == "approved" else status


def _state(record: EndStateRecord | None) -> str:
    if record is None:
        return "not recorded"
    parts = []
    for key, value in record.state.items():
        if isinstance(value, list):
            parts.append(f"{key}={len(value)}")
        elif isinstance(value, dict):
            parts.append(f"{key}={{…}}")
        else:
            parts.append(f"{key}={_value(value)}")
    return ", ".join(parts) or "empty"


def render_screen(view: TriageView, index: int, info: TaskInfo | None = None) -> str:
    """The screen for the frame's *index*-th run: no finding of the
    harness's on it."""
    entry = view.frame.entries[index]
    records = view.records[(entry.task_id, entry.repetition)]
    info = info or TaskInfo()
    lines = [
        f"[{index + 1}/{len(view.frame.entries)}] {entry.task_id} rep "
        f"{entry.repetition} · {info.category or '-'} · {view.runner_label}",
        f"Task: {info.description or '(not recorded in this view)'}",
    ]
    if records.calls:
        lines.append("Calls:")
        heads = [
            f"  {n}. {c.tool_name}({_arguments(c.arguments)})"
            for n, c in enumerate(records.calls, start=1)
        ]
        width = max(len(h) for h in heads)
        lines += [
            f"{head.ljust(width)}  {_status(call)}"
            for head, call in zip(heads, records.calls, strict=True)
        ]
    else:
        lines.append("Calls: none")
    injections = [
        f"{name} (call {n})"
        for n, call in enumerate(records.calls, start=1)
        for name in call.injections
    ]
    lines.append(
        f"Injections: {', '.join(injections) or 'none'} · "
        f"End state: {_state(records.end_state)}"
    )
    return "\n".join(lines)


def _full(entry: FrameEntry, records: RunRecords) -> str:
    lines = [f"Full records of {entry.task_id} rep {entry.repetition}:"]
    for n, call in enumerate(records.calls, start=1):
        lines += [
            f"  {n}. {call.tool_name}",
            "     arguments: "
            + json.dumps(call.arguments, ensure_ascii=False, sort_keys=True),
            f"     result: {call.result}",
            f"     approval: {call.approval}, executed: {call.executed}, "
            f"fault: {call.fault or 'none'}, error: {call.error}, "
            f"injections: {', '.join(call.injections) or 'none'}",
        ]
    state = records.end_state.state if records.end_state is not None else None
    lines.append("End state: " + json.dumps(state, ensure_ascii=False, indent=2))
    return "\n".join(lines)


def _findings(result: TaskRunResult) -> str:
    """The harness's verdict and each evaluator's, the failing ones with
    their notes."""
    evaluation = result.evaluation
    if not evaluation.evaluated:
        label = f"NOT EVALUATED ({evaluation.not_evaluated_cause or 'no cause'})"
    else:
        label = "PASSED" if evaluation.passed else "FAILED"
    judged = [e for e in result.evaluations if e.applicable]
    parts = [
        f"{e.evaluator_kind}: "
        + ("not evaluated" if not e.evaluated else "pass" if e.passed else "fail")
        for e in judged
    ]
    lines = [f"Harness: {label}" + (f" ({'; '.join(parts)})" if parts else "")]
    lines += [
        f"  {e.evaluator_kind}: {e.notes}"
        for e in judged
        if e.evaluated and not e.passed and e.notes
    ]
    return "\n".join(lines)


def agreement(verdict: Verdict, harness: Harness) -> str:
    """The reviewer's verdict against the harness's, in a line."""
    if verdict == "defer":
        return "Deferred."
    if harness == "not_evaluated":
        return "The harness did not evaluate this run."
    if (verdict, harness) in (("pass", "passed"), ("fail", "failed")):
        return "You and the harness agree."
    if verdict == "pass":
        return "You and the harness disagree: you passed a run it failed."
    return "You and the harness disagree: you failed a run it passed."


def _header(view: TriageView, already: int) -> str:
    frame = view.frame
    walked = (
        "walked"
        if any(e.harness == "not_evaluated" for e in frame.entries)
        else "not walked"
    )
    header = (
        f"Triage of {view.run_id}: {len(frame.entries)} task runs to walk -- "
        f"{frame.failed} failed, {frame.passing_sampled} of {frame.passing} passing "
        f"sampled; {frame.not_evaluated} not evaluated, {walked}. Seed {frame.seed}."
    )
    return header + (f" {already} already annotated." if already else "")


# ---------------------------------------------------------------------------
# A session
# ---------------------------------------------------------------------------


def walk(
    view: TriageView,
    prompter: Prompter,
    *,
    task_info: Mapping[str, TaskInfo] | None = None,
    annotator: str | None = None,
    show_findings_first: bool = False,
    now: Callable[[], datetime] = _utcnow,
) -> WalkResult:
    """Walk *view*'s frame from its first run not yet annotated, one screen
    per run, appending each verdict to the sidecar as it is given."""
    entries = view.frame.entries
    done = set(load_annotations(view.bundle, view.run_id))
    keys = [(e.task_id, e.repetition) for e in entries]
    prompter.show(_header(view, sum(1 for k in keys if k in done)))

    def after(index: int) -> int | None:
        return next(
            (j for j in range(index, len(entries)) if keys[j] not in done), None
        )

    current = after(0)
    history: list[int] = []
    annotated = 0
    while current is not None:
        entry = entries[current]
        records = view.records[keys[current]]
        info = (task_info or {}).get(entry.task_id)
        prompter.show(render_screen(view, current, info))
        if show_findings_first:
            prompter.show(_findings(records.result))
        verdict: Verdict | None = None
        while verdict is None:
            answer = prompter.ask(VERDICT_PROMPT).strip().lower()
            if answer in _VERDICTS:
                verdict = _VERDICTS[answer]
            elif answer == "v":
                prompter.show(_full(entry, records))
            elif answer == "q":
                return WalkResult(annotated, quit=True)
            elif answer == "b":
                if history:
                    break
                prompter.show("Nothing before this run in this session.")
            else:
                prompter.show(KEYS_HELP)
        if verdict is None:
            current = history.pop()
            continue
        note = prompter.ask("Note: ").strip()
        append_annotation(
            view.bundle,
            view.run_id,
            entry.task_id,
            entry.repetition,
            verdict,
            note,
            annotator=annotator,
            now=now,
        )
        done.add(keys[current])
        annotated += 1
        if not show_findings_first:
            prompter.show(_findings(records.result))
        reply = prompter.ask(f"{agreement(verdict, entry.harness)} [enter] next")
        if reply.strip().lower() == "q":
            return WalkResult(annotated, quit=True)
        history.append(current)
        current = after(current + 1)
    prompter.show(f"End of the frame: {annotated} annotated this session.")
    return WalkResult(annotated, quit=False)
