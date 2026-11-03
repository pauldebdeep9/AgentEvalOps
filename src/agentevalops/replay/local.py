"""LocalReplayVerifier — cross-file consistency checks over a loaded bundle.

These are structural checks, not behavioral replay: no agents, models,
tools, or benchmarks are executed, and the original bundle files are never
mutated. ``bundles.validator.validate_bundle()`` is the single structural
entry point (docs/adr/0003-replay-semantics.md, Q1) -- this class is an
internal helper it calls, not a parallel public API. It returns raw
``(errors, warnings)`` lists that ``validate_bundle()`` merges into its own
``BundleValidationResult`` rather than a separate summary type: keeping a
second, structural-only result type around once ``BundleValidationResult``
already covers the same ground is exactly the "two things sharing one name"
confusion ADR-0003's Context section describes, just one type over instead
of one CLI command.
"""

from __future__ import annotations

import json
from typing import Any

from agentevalops.bundles.reader import LoadedBundle
from agentevalops.bundles.serializers import LoadError, load_trace_event, to_jsonable
from agentevalops.orchestration.verdicts import (
    is_carried,
    output_from_terminal,
    terminal_of,
)

#: The first format whose traces carry one ``evaluator.score`` event per
#: evaluation result (docs/adr/0007 D3).
_SCORE_EVENTS_FROM = (0, 6)


#: The fields every record of an evaluation result carries -- its entry in
#: evaluations.json, its place in summary.json's task results and its
#: evaluator.score event (docs/adr/0008 C1).
_RESULT_FIELDS = (
    "evaluator_id",
    "evaluator_kind",
    "applicable",
    "evaluated",
    "passed",
    "score",
    "not_evaluated_cause",
    "refs",
)

Run = tuple[str, int]


def _by_run(items: list[tuple[Run, dict[str, Any]]]) -> dict[Run, list[dict[str, Any]]]:
    grouped: dict[Run, list[dict[str, Any]]] = {}
    for run, item in items:
        grouped.setdefault(run, []).append(item)
    return grouped


def _three_records(bundle: LoadedBundle) -> list[str]:
    """ADR-0008 C1: each evaluation result is recorded three times -- in
    evaluations.json, in summary.json's task results and as an
    evaluator.score event -- and the three must agree, run by run and in
    order. This catches an inconsistent edit; a careful edit of all three
    with the manifest re-sealed is a signature's or rescore's to catch."""
    entries = _by_run(
        [
            ((str(e.get("task_id")), int(e.get("repetition", 0))), e)
            for e in bundle.evaluations
            if isinstance(e, dict)
        ]
    )
    summary: list[tuple[Run, dict[str, Any]]] = []
    for result in bundle.summary.get("task_results") or []:
        if not isinstance(result, dict):
            continue
        run = (str(result.get("task_id")), int(result.get("repetition", 0)))
        evaluations = result.get("evaluations") or [result.get("evaluation") or {}]
        summary += [(run, e) for e in evaluations if isinstance(e, dict)]
    events = _by_run(
        [
            (
                (str(e.get("task_id")), int(e.get("repetition", 0))),
                e.get("payload") or {},
            )
            for e in bundle.traces
            if e.get("kind") == "evaluator.score"
        ]
    )
    records = {
        "evaluations.json": entries,
        "summary.json": _by_run(summary),
        "traces.jsonl": events,
    }
    errors: list[str] = []
    runs = sorted(set().union(*records.values()))
    for task_id, repetition in runs:
        lists = {
            name: by_run.get((task_id, repetition), [])
            for name, by_run in records.items()
        }
        counts = {name: len(items) for name, items in lists.items()}
        where = f"{task_id}/{repetition}"
        if len(set(counts.values())) != 1:
            errors.append(
                f"evaluation results disagree at {where}: "
                + ", ".join(f"{n} in {name}" for name, n in counts.items())
            )
            continue
        for position in range(counts["evaluations.json"]):
            row = {name: items[position] for name, items in lists.items()}
            for field in _RESULT_FIELDS:
                values = {name: item.get(field) for name, item in row.items()}
                if len({json.dumps(v, sort_keys=True) for v in values.values()}) > 1:
                    evaluator = row["evaluations.json"].get("evaluator_id")
                    errors.append(
                        f"evaluation results disagree at {where} ({evaluator}): "
                        f"{field} is "
                        + ", ".join(f"{v!r} in {name}" for name, v in values.items())
                    )
    return errors


def _run_of(item: dict[str, Any]) -> Run:
    return (str(item.get("task_id")), int(item.get("repetition", 0)))


def _carried(bundle: LoadedBundle) -> list[str]:
    """ADR-0008 R6: what rescore takes as recorded instead of recomputing --
    each scenario's preflight, the cause of a task run never evaluated, and
    the agent's output -- must fit the records it is carried with.

    A run stopped by its scenario's failed preflight never reached the
    agent: a failed preflight is recorded for its scenario, and it has no
    agent trace event, tool call or end state. A run not evaluated because
    of the preflight or the infrastructure is not evaluated by any of its
    evaluators, and its output says ``infra_failure``. An evaluated run has
    its end state.

    Whether a run was stopped or finished is read from the records, not the
    summary (R29 (b2)): a run the orchestrator stopped -- its output says
    ``infra_failure`` and no evaluator evaluated it -- never finished, so
    it has no ``agent.terminal`` event; every other run's output is what
    its terminal event gives (the run's own ``terminal_of`` and
    ``output_from_terminal``), whatever its output says."""
    agent_events = {
        _run_of(e) for e in bundle.traces if e.get("kind") != "evaluator.score"
    }
    terminals: dict[Run, list[dict[str, Any]]] = {}
    for e in bundle.traces:
        if e.get("kind") == "agent.terminal":
            terminals.setdefault(_run_of(e), []).append(e)
    tool_calls = {_run_of(r) for r in bundle.tool_calls if isinstance(r, dict)}
    end_states = {_run_of(s) for s in bundle.end_states if isinstance(s, dict)}
    failed_preflight = {
        str(p.get("task_id"))
        for p in bundle.summary.get("preflight") or []
        if isinstance(p, dict) and not p.get("passed")
    }
    errors: list[str] = []
    for result in bundle.summary.get("task_results") or []:
        if not isinstance(result, dict):
            continue
        run = _run_of(result)
        where = f"{run[0]}/{run[1]}"
        evaluations = [
            e
            for e in result.get("evaluations") or [result.get("evaluation") or {}]
            if isinstance(e, dict)
        ]
        causes = {e.get("not_evaluated_cause") for e in evaluations}
        evaluated = any(e.get("evaluated") for e in evaluations)
        output = result.get("output") or {}
        reason = output.get("termination_reason")
        for cause in ("environment_defect", "infrastructure"):
            if cause not in causes:
                continue
            if causes != {cause} or evaluated:
                errors.append(
                    f"carried result does not fit at {where}: not evaluated "
                    f"({cause}) by some of its evaluators, not all"
                )
            if not is_carried(reason):
                errors.append(
                    f"carried result does not fit at {where}: not evaluated "
                    f"({cause}), but its output's termination_reason is "
                    f"{reason!r}, not 'infra_failure'"
                )
        if "environment_defect" in causes:
            if run[0] not in failed_preflight:
                errors.append(
                    f"carried result does not fit at {where}: stopped by its "
                    "scenario's preflight, but summary.json records no failed "
                    f"preflight for {run[0]}"
                )
            found = [
                name
                for name, runs in (
                    ("agent trace events", agent_events),
                    ("tool calls", tool_calls),
                    ("an end state", end_states),
                )
                if run in runs
            ]
            if found:
                errors.append(
                    f"carried result does not fit at {where}: stopped by its "
                    "scenario's preflight before the agent ran, but the bundle "
                    f"has {', '.join(found)} for it"
                )
        if evaluated and run not in end_states:
            errors.append(
                f"carried result does not fit at {where}: evaluated, but "
                "end_states.jsonl has no end state for it"
            )
        if is_carried(reason) and not evaluated:
            if run in terminals:
                errors.append(
                    f"carried result does not fit at {where}: stopped before "
                    "any evaluator judged it (its output says infra_failure), "
                    "but its agent finished: it has an agent.terminal event"
                )
            continue
        try:
            event = terminal_of([load_trace_event(t) for t in terminals.get(run, [])])
        except LoadError as exc:
            errors.append(f"carried result does not fit at {where}: {exc}")
            continue
        if output != to_jsonable(output_from_terminal(event)):
            errors.append(
                f"carried result does not fit at {where}: its output is not "
                "what its agent.terminal event records"
            )
    return errors


def _version(value: object) -> tuple[int, ...] | None:
    try:
        return tuple(int(part) for part in str(value).split("."))
    except ValueError:
        return None


class LocalReplayVerifier:
    """Cross-file consistency checks over a ``LoadedBundle``.

    Complements (does not duplicate) ``validate_bundle()``'s own manifest/
    checksum/JSON-parse checks: these look *across* the already-parsed
    files for internal agreement (does ``metadata.json``'s ``run_id`` match
    ``summary.json``'s, do the recorded per-task event counts sum to the
    actual trace length) rather than at any one file in isolation.

    Parameters
    ----------
    bundle:
        A ``LoadedBundle`` returned by ``BundleReader.read()`` (typically
        with ``strict=False``, since ``validate_bundle()`` must still be
        able to report *something* for a bundle too broken to load
        strictly).
    """

    def __init__(self, bundle: LoadedBundle) -> None:
        self._bundle = bundle

    def check(self) -> tuple[list[str], list[str]]:
        """Return ``(errors, warnings)`` from cross-file consistency checks.

        Every finding here is currently an error (parity with this class's
        pre-2026-08 behavior, where all of these were unconditional
        failures) -- the ``warnings`` list exists so a future check can add
        a softer finding without changing this method's return shape again.
        """
        errors: list[str] = []
        warnings: list[str] = []

        # ---- run_id presence & cross-check ------------------------------
        meta_run_id = str(self._bundle.metadata.get("run_id", ""))
        if not meta_run_id:
            errors.append("metadata.json does not contain 'run_id'")

        summary_run_id = str(self._bundle.summary.get("run_id", ""))
        if meta_run_id and summary_run_id and meta_run_id != summary_run_id:
            errors.append(
                f"run_id mismatch: metadata='{meta_run_id}', summary='{summary_run_id}'"
            )

        # ---- traces not empty -------------------------------------------
        trace_count = len(self._bundle.traces)
        if trace_count == 0:
            errors.append("traces.jsonl contains no events")

        # ---- evaluations not empty ---------------------------------------
        eval_count = len(self._bundle.evaluations)
        if eval_count == 0:
            errors.append("evaluations.json contains no evaluations")

        # ---- cross-check: sum of per-task event_counts == len(traces) --
        task_results = self._bundle.summary.get("task_results")
        if isinstance(task_results, list) and task_results:
            expected = sum(
                int(tr.get("event_count", 0))
                for tr in task_results
                if isinstance(tr, dict)
            )
            if expected != trace_count:
                errors.append(
                    f"trace event count mismatch: "
                    f"sum of task event_counts={expected}, "
                    f"actual len(traces)={trace_count}"
                )

        # ---- cross-check: metadata task_count == task runs evaluated -----
        # Before format 0.5 evaluations.json held one entry per task; from
        # 0.5 it holds one per evaluator per task run, each naming its task
        # run (docs/adr/0006 D13), so count distinct task runs instead.
        evaluated_runs = eval_count
        entries = self._bundle.evaluations
        if any(isinstance(e, dict) and "task_id" in e for e in entries):
            evaluated_runs = len(
                {
                    (e.get("task_id"), e.get("repetition", 0))
                    for e in self._bundle.evaluations
                    if isinstance(e, dict)
                }
            )
        # ---- 0.6: one evaluator.score event per evaluation result (D3) ---
        # The bundle's declared format: the manifest's, as the validator
        # reads it, else metadata.json's.
        manifest = self._bundle.manifest or {}
        version = _version(
            manifest.get("bundle_format_version")
            or self._bundle.metadata.get("schema_version")
        )
        if version is not None and version >= _SCORE_EVENTS_FROM:
            scores = sum(
                1 for e in self._bundle.traces if e.get("kind") == "evaluator.score"
            )
            errors.extend(_three_records(self._bundle))
            errors.extend(_carried(self._bundle))
            if scores != eval_count:
                errors.append(
                    f"evaluator.score event count mismatch: {scores} in "
                    f"traces.jsonl, {eval_count} entries in evaluations.json"
                )

        meta_task_count = self._bundle.metadata.get("task_count")
        if isinstance(meta_task_count, int) and meta_task_count != evaluated_runs:
            errors.append(
                f"task count mismatch: "
                f"metadata task_count={meta_task_count}, "
                f"evaluated task runs={evaluated_runs}"
            )

        return errors, warnings
