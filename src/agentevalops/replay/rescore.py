"""Rescore: the evaluation layer over a bundle's own records (docs/adr/0008
R1-R6; ADR-0005 D7's layer order; ADR-0003 Q6's vocabulary).

Read-only: it never runs the agent, a model or a tool, and never writes into
the bundle. The structural layer runs first -- a bundle that does not
validate, with every check, cannot be rescored. Then the run is rebuilt from
``config.json`` and the plan (``replay/rebuild.py``), and each task run is
derived again through the run's own code (``orchestration/verdicts.py``):

- a run with an ``agent.terminal`` event finished, and is judged again from
  its recorded events, tool calls and end state, whatever ``summary.json``
  says of it;
- a run without one was stopped before any evaluator could judge it. When
  the orchestrator's result for it follows from the records -- its
  infrastructure failed -- it is made again by the run's own ``stopped``,
  with the reason the bundle records, and compared. When it does not -- its
  scenario failed the preflight, or its configuration failed -- it is
  carried as recorded, and named (R6).

The summary, the policy verdict and the control coverage are derived again,
and every content file is rendered in memory by the writer's own renderer
(``bundles/writer.render_bundle``), with the bundle's own stamp, and
compared with the bundle's.

The outcome: ``CODE_CHANGED`` when the running code differs from the code
that wrote the bundle on any of five items (R2), reported with whether the
verdicts reproduced under it; otherwise ``MATCH`` when every result R3 names
is equal, and ``DIFFER`` when one is not. No clock value reaches a result:
every time comes from the bundle, and a record without one cannot be
rescored.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from agentevalops.bundles.constants import (
    CONTROLS_FILENAME,
    EVIDENCE_REPORT_FILENAME,
    PACKAGE_HASH_FROM,
    REPLAY_COMMAND_FILENAME,
    format_tuple,
)
from agentevalops.bundles.provenance import package_sha256
from agentevalops.bundles.reader import BundleReader, LoadedBundle
from agentevalops.bundles.serializers import (
    LoadError,
    from_jsonable,
    load_end_state,
    load_tool_call,
    load_trace_event,
)
from agentevalops.bundles.validator import validate_bundle
from agentevalops.bundles.writer import BundleStamp, render_bundle
from agentevalops.catalogue import load_catalogue
from agentevalops.core.errors import ConfigurationError
from agentevalops.core.protocols import Evaluator, PolicyChecker
from agentevalops.core.schemas import (
    HARNESS_EVENT_KINDS,
    EndStateRecord,
    PolicySpec,
    ResultBundleMetadata,
    RunSummary,
    TaskRunResult,
    ToolCallRecord,
    TraceEvent,
    TraceEventKind,
)
from agentevalops.orchestration.verdicts import (
    close_summary,
    is_carried,
    judge,
    new_summary,
    run_policy_spec,
    scored,
    stopped,
    tally,
    task_runs,
    terminal_of,
)
from agentevalops.plans import EvidenceInputs, pack_data_sha256, scenarios_sha256
from agentevalops.registry import (
    UnknownComponentError,
    build_evaluators,
    build_policy_checker,
)
from agentevalops.replay.rebuild import RebuiltRun, rebuild_run

Outcome = Literal["MATCH", "DIFFER", "CODE_CHANGED"]

#: docs/adr/0008 R4. 2 is "cannot rescore".
EXIT_CODES: dict[str, int] = {"MATCH": 0, "DIFFER": 1, "CODE_CHANGED": 3}
EXIT_CANNOT_RESCORE: int = 2

#: ADR-0005 D7: rescore is the evaluation layer, between the structural
#: layer (validate-bundle) and the behavioural one (not built).
LAYER = "evaluation"

#: R3: the fields of an evaluation result that decide a MATCH. Notes are
#: shown when they differ, and do not decide; the evaluator id names the
#: result, and a changed one is R2's fifth item.
EVALUATION_FIELDS = (
    "applicable",
    "evaluated",
    "passed",
    "score",
    "not_evaluated_cause",
    "refs",
)
#: R3: the summary's counts and pass^k.
SUMMARY_FIELDS = (
    "passed_tasks",
    "failed_tasks",
    "not_evaluated_tasks",
    "pass_k",
    "pass_k_by_category",
)
#: R3: the policy verdict and its refs; its citations decide with it.
POLICY_FIELDS = ("verdict", "citations", "refs")
#: R3 and decision 9: compared byte for byte.
REPORTS = ("report.md", EVIDENCE_REPORT_FILENAME)

NO_PACK = "not compared: no pack recorded"
NO_DATA_HASH = "not compared: neither pack has a data hash"

#: The causes of a stopped run whose result follows from its records alone:
#: the infrastructure failed, and the run's own ``stopped`` makes the result
#: again from the reason the bundle records. A failed preflight or a failed
#: configuration leaves nothing in the records to derive it from.
DERIVED_STOPS = frozenset({"infrastructure"})


class RescoreError(Exception):
    """The bundle cannot be rescored (exit 2); the message says why."""


@dataclass(frozen=True)
class ProvenanceItem:
    """One of the five things R2 compares between the code that wrote the
    bundle and the code now running."""

    name: str
    recorded: Any
    running: Any
    status: Literal["same", "differs", "not compared"]
    note: str = ""


@dataclass(frozen=True)
class Difference:
    """A result the bundle records and the rescore derives differently."""

    where: str
    field: str
    recorded: Any
    rescored: Any


@dataclass(frozen=True)
class Rescore:
    """What a rescore found. ``files`` are the recomputed content files."""

    bundle: Path
    provenance: tuple[ProvenanceItem, ...]
    verdicts: tuple[Difference, ...]
    reports: tuple[Difference, ...]
    notes: tuple[Difference, ...]
    differing_files: tuple[str, ...]
    carried: tuple[str, ...]
    task_runs: int
    evaluations: int
    files: dict[str, bytes] = field(repr=False)

    @property
    def code_changed(self) -> bool:
        return any(item.status == "differs" for item in self.provenance)

    @property
    def verdicts_reproduce(self) -> bool:
        return not self.verdicts

    @property
    def outcome(self) -> Outcome:
        if self.code_changed:
            return "CODE_CHANGED"
        return "MATCH" if not (self.verdicts or self.reports) else "DIFFER"

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.outcome]


def rescore(bundle: Path) -> Rescore:
    """Rescore the bundle at *bundle*. Raises ``RescoreError`` when it
    cannot be rescored, for whatever reason: nothing after validation ends
    in any other exception."""
    validation = validate_bundle(bundle)
    if not validation.valid:
        raise RescoreError(
            f"the bundle does not validate ({len(validation.errors)} error(s)); "
            f"first: {validation.errors[0]}"
        )
    declared = format_tuple(validation.bundle_format_version)
    if declared is None or declared < PACKAGE_HASH_FROM:
        raise RescoreError(
            f"format {validation.bundle_format_version} predates package_sha256; "
            "rescore needs format "
            + ".".join(str(n) for n in PACKAGE_HASH_FROM)
            + " or later"
        )
    try:
        return _rescore(bundle)
    except RescoreError:
        raise
    except UnknownComponentError as exc:
        raise RescoreError(f"a component the bundle names is not installed: {exc}")
    except (ConfigurationError, LoadError) as exc:
        raise RescoreError(str(exc)) from None
    except (KeyError, TypeError, ValueError, UnicodeError, OSError) as exc:
        raise RescoreError(
            f"the bundle's records cannot be read: {type(exc).__name__}: {exc}"
        ) from None


def _rescore(bundle: Path) -> Rescore:
    loaded = BundleReader(bundle).read()
    rebuilt = rebuild_run(loaded.config, loaded.test_plan or {})
    if rebuilt.plan.pack_id is not None and rebuilt.pack is None:
        raise RescoreError(
            f"the bundle records pack '{rebuilt.plan.pack_id}', but the installed "
            f"benchmark '{loaded.config.get('benchmark_id')}' has no pack"
        )
    metadata = from_jsonable(
        ResultBundleMetadata, loaded.metadata, where="metadata.json"
    )
    stamp = BundleStamp.recorded(
        metadata, (bundle / REPLAY_COMMAND_FILENAME).read_text("utf-8")
    )
    records = _Records.of(loaded)
    configured = [str(kind) for kind in loaded.config.get("evaluators") or ()]
    evaluators = build_evaluators(configured)
    derived = asyncio.run(
        _derive(rebuilt, records, evaluators, build_policy_checker(), stamp.created_at)
    )
    files = render_bundle(
        rebuilt.run_config,
        derived.summary,
        _trace_as_recorded(records.events, derived.events, stamp.created_at),
        plan=rebuilt.plan,
        test_plan_sha256=metadata.test_plan_sha256 or "",
        stamp=stamp,
        evidence=EvidenceInputs(
            catalogue=load_catalogue(), scenarios=rebuilt.descriptors
        ),
        runner_id=loaded.config.get("runner_id"),
        mock_trajectory=loaded.config.get("mock_trajectory"),
        evaluators=configured,
        policy_spec=records.policy_spec,
        config_name=str(loaded.config.get("config_name") or ""),
        benchmark_id=str(loaded.config.get("benchmark_id") or ""),
        benchmark_scenario=str(loaded.config.get("benchmark_scenario") or ""),
        status=metadata.status,
        benchmark_options=loaded.config.get("benchmark_options") or {},
        tool_calls=records.tool_calls,
        end_states=records.end_states,
        scenario_info=rebuilt.scenario_info,
    )
    verdicts, notes = _compare_results(loaded, files, derived.carried)
    reports = tuple(
        d
        for name in REPORTS
        if (d := _first_line_difference(name, bundle / name, files[name]))
    )
    differing = tuple(
        name for name, data in files.items() if (bundle / name).read_bytes() != data
    )
    judged = [
        r
        for r in derived.summary.task_results
        if (str(r.task_id), r.repetition) not in derived.carried
    ]
    return Rescore(
        bundle=bundle,
        provenance=_provenance(loaded, rebuilt, configured, evaluators, files),
        verdicts=verdicts,
        reports=reports,
        notes=notes,
        differing_files=differing,
        carried=_carried(records.summary, derived),
        task_runs=len(judged),
        evaluations=sum(len(r.evaluations) for r in judged),
        files=files,
    )


# ---------------------------------------------------------------------------
# Deriving the results again
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Records:
    """A bundle's records, loaded back into the run's own types."""

    events: list[TraceEvent]
    tool_calls: list[ToolCallRecord]
    end_states: list[EndStateRecord]
    summary: RunSummary
    policy_spec: PolicySpec | None

    @classmethod
    def of(cls, loaded: LoadedBundle) -> _Records:
        # A record without its time would take the clock's when loaded.
        for name, lines in (
            ("traces.jsonl", loaded.traces),
            ("tool_calls.jsonl", loaded.tool_calls),
        ):
            for number, line in enumerate(lines, start=1):
                if "timestamp" not in line:
                    raise RescoreError(f"{name} line {number} records no timestamp")
        spec = loaded.config.get("policy_spec")
        return cls(
            events=[load_trace_event(e) for e in loaded.traces],
            tool_calls=[load_tool_call(r) for r in loaded.tool_calls],
            end_states=[load_end_state(s) for s in loaded.end_states],
            summary=from_jsonable(RunSummary, loaded.summary, where="summary.json"),
            policy_spec=None
            if spec is None
            else from_jsonable(PolicySpec, spec, where="config.json policy_spec"),
        )

    def of_run(self, task_id: str, repetition: int) -> _RunRecords:
        def mine(item: Any) -> bool:
            return (str(item.task_id), item.repetition) == (task_id, repetition)

        return _RunRecords(
            events=[
                e for e in self.events if mine(e) and e.kind not in HARNESS_EVENT_KINDS
            ],
            tool_calls=tuple(r for r in self.tool_calls if mine(r)),
            end_states=tuple(s for s in self.end_states if mine(s)),
        )


@dataclass(frozen=True)
class _RunRecords:
    events: list[TraceEvent]
    tool_calls: tuple[ToolCallRecord, ...]
    end_states: tuple[EndStateRecord, ...]


@dataclass(frozen=True)
class _Derived:
    """The run derived again: its summary, every event in the
    orchestrator's order, and which task runs were carried or made again
    as stopped."""

    summary: RunSummary
    events: list[TraceEvent]
    carried: dict[tuple[str, int], TaskRunResult]
    restopped: tuple[tuple[str, int], ...]


async def _derive(
    rebuilt: RebuiltRun,
    records: _Records,
    evaluators: Sequence[Evaluator],
    checker: PolicyChecker,
    created_at: datetime,
) -> _Derived:
    """The run's summary derived again from its records, as the
    orchestrator derived it, through the same functions: each task run
    judged, made again as stopped, or carried; its score events and event
    count, stamped with the bundle's own time; the summary's counts and
    pass^k; then the policy verdict."""
    run_config = rebuilt.run_config
    runs = task_runs(rebuilt.tasks, run_config.repetitions)
    summary = new_summary(run_config.run_id, runs)
    # R6: the preflight ran a script in an environment the bundle does not
    # keep; it is carried.
    summary.preflight = list(records.summary.preflight)
    recorded = {(str(r.task_id), r.repetition): r for r in records.summary.task_results}
    carried: dict[tuple[str, int], TaskRunResult] = {}
    restopped: list[tuple[str, int]] = []
    all_events: list[TraceEvent] = []
    for task, repetition in runs:
        key = (str(task.task_id), repetition)
        run = records.of_run(*key)
        was = recorded.get(key)
        result: TaskRunResult
        if terminal_of(run.events) is None and was is not None and _is_stop(was):
            cause = str(was.evaluation.not_evaluated_cause)
            if cause in DERIVED_STOPS:
                result = stopped(
                    task,
                    str(was.output.error),
                    "infrastructure",
                    run.events,
                    evaluators,
                    repetition=repetition,
                )
                restopped.append(key)
            else:
                # R6: nothing in the records to derive it from; carried.
                result = was
                carried[key] = was
        else:
            result = await judge(
                task,
                run.events,
                run.tool_calls,
                run.end_states,
                repetition,
                evaluators,
            )
        events = run.events + scored(result, run_config.run_id, run.events, created_at)
        tally(summary, result)
        all_events.extend(events)
    await close_summary(
        summary,
        rebuilt.tasks,
        all_events,
        checker,
        run_policy_spec(records.policy_spec),
        records.tool_calls,
        records.end_states,
        run_config,
    )
    return _Derived(summary, all_events, carried, tuple(restopped))


def _is_stop(result: TaskRunResult) -> bool:
    """A recorded result the orchestrator made for a run it stopped: its
    output says infra_failure and no evaluator evaluated it."""
    evaluations = result.evaluations or [result.evaluation]
    return is_carried(result.output.termination_reason) and not any(
        e.evaluated for e in evaluations
    )


def _trace_as_recorded(
    recorded: Sequence[TraceEvent],
    derived: Sequence[TraceEvent],
    created_at: datetime,
) -> list[TraceEvent]:
    """The trace in the bundle's own order, with each ``evaluator.score``
    event replaced by the one derived again for the same task run and
    position, keeping the recorded event's timestamp: when the run scored
    is a record, not a result. The run writes its trace in the order events
    reached the store, which concurrency and preflight-stopped runs can make
    differ from the orchestrator's order; the agent's own events are
    records. A derived score event with no recorded place goes at the end,
    stamped with the bundle's own time."""
    fresh: dict[tuple[str, int], list[TraceEvent]] = {}
    for event in derived:
        if event.kind == TraceEventKind.EVALUATOR_SCORE:
            fresh.setdefault((str(event.task_id), event.repetition), []).append(event)
    out: list[TraceEvent] = []
    for event in recorded:
        if event.kind != TraceEventKind.EVALUATOR_SCORE:
            out.append(event)
            continue
        queue = fresh.get((str(event.task_id), event.repetition))
        if queue:
            out.append(dataclasses.replace(queue.pop(0), timestamp=event.timestamp))
    out.extend(
        dataclasses.replace(e, timestamp=created_at)
        for queue in fresh.values()
        for e in queue
    )
    return out


# ---------------------------------------------------------------------------
# Comparing
# ---------------------------------------------------------------------------


def _by_run(entries: Sequence[Any]) -> dict[tuple[str, int], list[dict[str, Any]]]:
    runs: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for entry in entries:
        if isinstance(entry, dict):
            key = (str(entry.get("task_id")), int(entry.get("repetition", 0)))
            runs.setdefault(key, []).append(entry)
    return runs


def _compare_results(
    loaded: LoadedBundle,
    files: Mapping[str, bytes],
    carried: Mapping[tuple[str, int], TaskRunResult],
) -> tuple[tuple[Difference, ...], tuple[Difference, ...]]:
    """R3, each recorded file against the one the writer renders from the
    derived results: every evaluation of every task run that was not
    carried, the summary's counts and pass^k, the policy verdict, and each
    control's state and verdicts. Returns the deciding differences and the
    ones only shown (notes)."""
    deciding: list[Difference] = []
    shown: list[Difference] = []
    recorded_runs = _by_run(loaded.evaluations)
    derived_runs = _by_run(json.loads(files["evaluations.json"]))
    for key in sorted(set(recorded_runs) | set(derived_runs)):
        if key in carried:
            continue
        old, new = recorded_runs.get(key, []), derived_runs.get(key, [])
        run = f"{key[0]}/{key[1]}"
        if len(old) != len(new):
            deciding.append(Difference(run, "evaluations", len(old), len(new)))
            continue
        for was, now in zip(old, new):
            where = f"{run} {was.get('evaluator_id')}"
            deciding += [
                Difference(where, name, was.get(name), now.get(name))
                for name in EVALUATION_FIELDS
                if was.get(name) != now.get(name)
            ]
            if was.get("notes") != now.get("notes"):
                shown.append(
                    Difference(where, "notes", was.get("notes"), now.get("notes"))
                )
    summary = json.loads(files["summary.json"])
    deciding += [
        Difference("summary", name, loaded.summary.get(name), summary.get(name))
        for name in SUMMARY_FIELDS
        if loaded.summary.get(name) != summary.get(name)
    ]
    policy_was = loaded.policy or {}
    policy_now = json.loads(files["policy.json"]) or {}
    deciding += [
        Difference("policy", name, policy_was.get(name), policy_now.get(name))
        for name in POLICY_FIELDS
        if policy_was.get(name) != policy_now.get(name)
    ]
    shown += [
        Difference("policy", name, policy_was.get(name), policy_now.get(name))
        for name in ("checker_id", "policy_id", "notes")
        if policy_was.get(name) != policy_now.get(name)
    ]
    deciding += _compare_controls(
        (loaded.controls or {}).get("controls") or [],
        json.loads(files[CONTROLS_FILENAME])["controls"],
    )
    return tuple(deciding), tuple(shown)


def _compare_controls(
    recorded: list[dict[str, Any]], derived: list[dict[str, Any]]
) -> list[Difference]:
    """R3: every control's state and verdicts."""
    old = {c.get("id"): c for c in recorded}
    new = {c.get("id"): c for c in derived}
    found = []
    for control_id in sorted(set(old) | set(new), key=str):
        was, now = old.get(control_id), new.get(control_id)
        where = f"control {control_id}"
        if was is None or now is None:
            found.append(Difference(where, "present", was is not None, now is not None))
            continue
        found += [
            Difference(where, name, was.get(name), now.get(name))
            for name in ("state", "verdicts")
            if was.get(name) != now.get(name)
        ]
    return found


def _first_line_difference(name: str, path: Path, data: bytes) -> Difference | None:
    """Decision 9: a report compared byte for byte; when they differ, the
    first line that does."""
    recorded = path.read_bytes()
    if recorded == data:
        return None
    old = recorded.decode("utf-8", "replace").split("\n")
    new = data.decode("utf-8", "replace").split("\n")
    line = next(
        (i for i, (a, b) in enumerate(zip(old, new)) if a != b), min(len(old), len(new))
    )
    return Difference(
        name,
        f"line {line + 1}",
        old[line] if line < len(old) else "(end of file)",
        new[line] if line < len(new) else "(end of file)",
    )


def _provenance(
    loaded: LoadedBundle,
    rebuilt: RebuiltRun,
    configured: Sequence[str],
    evaluators: Sequence[Evaluator],
    files: Mapping[str, bytes],
) -> tuple[ProvenanceItem, ...]:
    """R2: the five items that identify the code, recorded against
    running. A bundle that records no pack (a toy run) has nothing to hold
    the pack's hashes to; a pack that records no data hash, where the
    installed pack has none either, has no data hash to compare."""
    plan = rebuilt.plan

    def item(name: str, recorded: Any, running: Any) -> ProvenanceItem:
        return ProvenanceItem(
            name, recorded, running, "same" if recorded == running else "differs"
        )

    items = [
        item("package_sha256", loaded.metadata.get("package_sha256"), package_sha256())
    ]
    if plan.pack_id is None:
        items += [
            ProvenanceItem(name, None, None, "not compared", NO_PACK)
            for name in ("pack scenarios_sha256", "pack data_sha256")
        ]
    else:
        items.append(
            item(
                "pack scenarios_sha256",
                plan.scenarios_sha256,
                scenarios_sha256(rebuilt.pack),
            )
        )
        running_data = pack_data_sha256(rebuilt.pack)
        if plan.data_sha256 is None and running_data is None:
            items.append(
                ProvenanceItem(
                    "pack data_sha256", None, None, "not compared", NO_DATA_HASH
                )
            )
        else:
            items.append(item("pack data_sha256", plan.data_sha256, running_data))
    items.append(
        item(
            "catalogue files",
            (loaded.controls or {}).get("catalogue"),
            json.loads(files[CONTROLS_FILENAME])["catalogue"],
        )
    )
    # The configured kinds only: a result the run synthesised for a kind it
    # does not configure names no evaluator.
    running_ids = {e.evaluator_kind: e.evaluator_id for e in evaluators}
    recorded_ids = {
        str(e.get("evaluator_kind")): e.get("evaluator_id")
        for e in loaded.evaluations
        if isinstance(e, dict) and e.get("evaluator_kind") in configured
    }
    items.append(
        item(
            "evaluator ids",
            dict(sorted(recorded_ids.items())),
            {k: running_ids.get(k) for k in sorted(recorded_ids)},
        )
    )
    return tuple(items)


def _carried(recorded: RunSummary, derived: _Derived) -> tuple[str, ...]:
    """R6: what was taken as recorded, and from where."""
    lines = []
    failed = [p for p in recorded.preflight if not p.passed]
    if recorded.preflight:
        detail = (
            "; failed: " + ", ".join(str(p.task_id) for p in failed) if failed else ""
        )
        lines.append(
            f"the preflight of {len(recorded.preflight)} scenario(s), as summary.json "
            f"records it{detail}"
        )
    for (task_id, repetition), result in derived.carried.items():
        lines.append(
            f"task run {task_id}/{repetition}, stopped before the agent finished "
            f"({result.evaluation.not_evaluated_cause}): its recorded result"
        )
    for task_id, repetition in derived.restopped:
        lines.append(
            f"task run {task_id}/{repetition}, stopped by its infrastructure: the "
            "reason it records, its result made again from it"
        )
    finished = (
        len(recorded.task_results) - len(derived.carried) - len(derived.restopped)
    )
    if finished > 0:
        lines.append(
            f"the agent's output of {finished} task run(s), from their "
            "agent.terminal events"
        )
    return tuple(lines)


# ---------------------------------------------------------------------------
# Writing outside the bundle
# ---------------------------------------------------------------------------


def inside(target: Path, bundle: Path) -> bool:
    """Whether *target* is in *bundle*, by file identity, never by
    spelling: from the nearest existing ancestor of the resolved target up
    to the root, any directory that is the bundle's (st_dev, st_ino). Case,
    symlinks, ``..`` and firmlinks are all seen through."""
    home = bundle.stat()
    identity = (home.st_dev, home.st_ino)
    path = target.resolve()
    while not path.exists() and path != path.parent:
        path = path.parent
    for directory in (path, *path.parents):
        info = directory.stat()
        if (info.st_dev, info.st_ino) == identity:
            return True
    return False


def shares_a_file(target: Path, bundle: Path) -> bool:
    """Whether *target* exists and is one of the bundle's files under
    another name (a hard link)."""
    if not target.is_file():
        return False
    info = target.stat()
    return any(
        (f.stat().st_dev, f.stat().st_ino) == (info.st_dev, info.st_ino)
        for f in bundle.iterdir()
        if f.is_file()
    )


def write_replacing(path: Path, data: bytes) -> None:
    """Write *data* at *path* through a new file in the same folder, moved
    onto *path*: an existing file's inode is never written through, so a
    hard link or a symlink at *path* is replaced, not followed."""
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _plain(text: str) -> str:
    """*text* with every non-printable character escaped: the bundle's own
    strings never reach a terminal as control sequences."""
    return "".join(c if c.isprintable() else repr(c)[1:-1] for c in text)


def _show(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    text = _plain(text)
    return text if len(text) <= 120 else text[:117] + "..."


def verdict_line(result: Rescore) -> str:
    """Whether the verdicts reproduced: R2's line, shown under every
    outcome. Carried task runs are not counted: nothing was derived."""
    scope = (
        f"{result.evaluations} evaluation(s) over {result.task_runs} task run(s), "
        "the summary's counts and pass^k, the policy verdict and the controls"
    )
    if result.verdicts_reproduce:
        return f"Verdicts: reproduce -- {scope}"
    return (
        f"Verdicts: do not reproduce -- {len(result.verdicts)} difference(s) in {scope}"
    )


def render_text(result: Rescore) -> str:
    """What ``agentevalops rescore`` prints. No clock, and no path but the
    bundle's as given: two rescores of one bundle print the same."""
    lines = [
        f"Bundle:  {_plain(str(result.bundle))}",
        f"Layer:   {LAYER} (the recorded tool calls, end states and agent events "
        "judged again; the agent is not run)",
        "Code:    " + ("changed" if result.code_changed else "unchanged"),
    ]
    for item in result.provenance:
        if item.status == "not compared":
            lines.append(f"  {item.name:22s} {item.note}")
        elif item.status == "same":
            lines.append(f"  {item.name:22s} same")
        else:
            lines.append(
                f"  {item.name:22s} differs: recorded {_show(item.recorded)}, "
                f"running {_show(item.running)}"
            )
    lines.append(verdict_line(result))
    lines += [
        f"  ! {_plain(d.where)}: {d.field} recorded {_show(d.recorded)}, "
        f"rescored {_show(d.rescored)}"
        for d in result.verdicts
    ]
    if not result.reports:
        lines.append("Reports:  report.md and evidence_report.md are byte-identical")
    for d in result.reports:
        lines.append(f"Reports:  {d.where} differs at {d.field}")
        lines.append(f"  recorded: {_show(d.recorded)}")
        lines.append(f"  rescored: {_show(d.rescored)}")
    lines += [
        f"  note: {_plain(d.where)}: {d.field} recorded {_show(d.recorded)}, "
        f"rescored {_show(d.rescored)} (shown, does not decide)"
        for d in result.notes
    ]
    if result.differing_files:
        lines.append(
            "Files:    differing in bytes: " + ", ".join(result.differing_files)
        )
    else:
        lines.append("Files:    every recomputed file is byte-identical")
    lines.append("Carried (taken as recorded, not recomputed):")
    lines += [f"  - {_plain(c)}" for c in result.carried] or ["  - nothing"]
    if result.outcome == "CODE_CHANGED":
        under = "reproduce" if result.verdicts_reproduce else "do not reproduce"
        lines.append(
            f"RESCORE: CODE_CHANGED -- the verdicts {under} under the running code"
        )
    else:
        lines.append(f"RESCORE: {result.outcome}")
    return "\n".join(lines)


def to_json(result: Rescore) -> dict[str, Any]:
    """The machine-readable result (``--json``): the outcome, the
    provenance comparison, and every difference."""

    def diffs(items: Sequence[Difference]) -> list[dict[str, Any]]:
        return [
            {
                "where": d.where,
                "field": d.field,
                "recorded": d.recorded,
                "rescored": d.rescored,
            }
            for d in items
        ]

    return {
        "outcome": result.outcome,
        "exit_code": result.exit_code,
        "layer": LAYER,
        "bundle": str(result.bundle),
        "code_changed": result.code_changed,
        "provenance": [
            {
                "item": i.name,
                "status": i.status,
                "recorded": i.recorded,
                "running": i.running,
                **({"note": i.note} if i.note else {}),
            }
            for i in result.provenance
        ],
        "verdicts_reproduce": result.verdicts_reproduce,
        "task_runs": result.task_runs,
        "evaluations": result.evaluations,
        "verdicts_moved": diffs(result.verdicts),
        "reports": diffs(result.reports),
        "notes": diffs(result.notes),
        "files_differing": list(result.differing_files),
        "carried": list(result.carried),
    }


def cannot_json(bundle: Path, reason: str) -> dict[str, Any]:
    """The machine-readable result when the bundle cannot be rescored."""
    return {
        "outcome": "CANNOT_RESCORE",
        "exit_code": EXIT_CANNOT_RESCORE,
        "layer": LAYER,
        "bundle": str(bundle),
        "reason": reason,
    }
