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

from agentevalops.bundles.reader import LoadedBundle


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
                f"run_id mismatch: metadata='{meta_run_id}', "
                f"summary='{summary_run_id}'"
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

        # ---- cross-check: metadata task_count == len(evaluations) ------
        meta_task_count = self._bundle.metadata.get("task_count")
        if isinstance(meta_task_count, int) and meta_task_count != eval_count:
            errors.append(
                f"task count mismatch: "
                f"metadata task_count={meta_task_count}, "
                f"evaluations count={eval_count}"
            )

        return errors, warnings
