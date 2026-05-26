"""LocalReplayVerifier — deterministic consistency checks on a loaded bundle.

Replay in AgentEvalOps means "bundle verification": read the saved artifacts,
confirm their internal consistency, and return a structured ``ReplaySummary``.
No agents, models, tools, or benchmarks are executed.  The original bundle
files are never mutated.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from agentevalops.bundles.constants import MANIFEST_FILENAME
from agentevalops.bundles.reader import LoadedBundle
from agentevalops.bundles.validator import validate_bundle


@dataclasses.dataclass
class ReplaySummary:
    """Result of a bundle replay/verification run.

    Attributes
    ----------
    run_id:
        The ``run_id`` found in ``metadata.json`` (empty string if absent).
    bundle_path:
        Path to the bundle directory that was verified.
    trace_event_count:
        Number of trace events parsed from ``traces.jsonl``.
    evaluation_count:
        Number of evaluations in ``evaluations.json``.
    policy_verdict:
        The ``verdict`` string from ``policy.json``, or ``None`` when the
        bundle contains no policy verdict.
    checks_passed:
        ``True`` when all consistency checks passed; ``False`` otherwise.
    failures:
        Human-readable descriptions of every failed check (empty if
        ``checks_passed`` is ``True``).
    bundle_format_version:
        The ``bundle_format_version`` from ``manifest.json``, or ``None``
        when the bundle has no manifest.
    manifest_valid:
        ``True`` when a manifest was present and its checksums all passed,
        ``False`` when checksum/size failures were detected, or ``None`` when
        no manifest was available.
    """

    run_id: str
    bundle_path: Path
    trace_event_count: int
    evaluation_count: int
    policy_verdict: str | None
    checks_passed: bool
    failures: list[str]
    bundle_format_version: str | None = None
    manifest_valid: bool | None = None


class LocalReplayVerifier:
    """Verify the internal consistency of a ``LoadedBundle``.

    All checks are deterministic and read-only.  No new files are created.
    No agent or model code is executed.

    Parameters
    ----------
    bundle:
        A fully loaded bundle returned by ``BundleReader.read()``.
    """

    def __init__(self, bundle: LoadedBundle) -> None:
        self._bundle = bundle

    def verify(self) -> ReplaySummary:
        """Run all consistency checks and return a ``ReplaySummary``."""
        failures: list[str] = []

        # ---- manifest validation (non-fatal; surfaced as diagnostics) ----
        bundle_format_version: str | None = None
        manifest_valid: bool | None = None

        manifest = self._bundle.manifest
        if manifest is not None:
            bundle_format_version = manifest.get(
                "bundle_format_version"
            ) or None
            # Run full validator to check checksums
            val_result = validate_bundle(self._bundle.bundle_path)
            manifest_valid = val_result.valid
            if not val_result.valid:
                for err in val_result.errors:
                    failures.append(f"[manifest] {err}")
        else:
            # No manifest — pre-WBS-8 bundle; note but don’t fail.
            manifest_path = self._bundle.bundle_path / MANIFEST_FILENAME
            if not manifest_path.exists():
                pass  # acceptable for legacy bundles read with strict=False

        # ---- run_id presence & cross-check --------------------------
        meta_run_id = str(self._bundle.metadata.get("run_id", ""))
        if not meta_run_id:
            failures.append(
                "metadata.json does not contain 'run_id'"
            )

        summary_run_id = str(self._bundle.summary.get("run_id", ""))
        if meta_run_id and summary_run_id and meta_run_id != summary_run_id:
            failures.append(
                f"run_id mismatch: metadata='{meta_run_id}', "
                f"summary='{summary_run_id}'"
            )

        # ---- traces not empty ---------------------------------------
        trace_count = len(self._bundle.traces)
        if trace_count == 0:
            failures.append("traces.jsonl contains no events")

        # ---- evaluations not empty ----------------------------------
        eval_count = len(self._bundle.evaluations)
        if eval_count == 0:
            failures.append("evaluations.json contains no evaluations")

        # ---- policy verdict -----------------------------------------
        policy_verdict: str | None = None
        if self._bundle.policy is not None:
            policy_verdict = str(
                self._bundle.policy.get("verdict", "")
            ) or None

        # ---- cross-check: sum of per-task event_counts == len(traces)
        task_results = self._bundle.summary.get("task_results")
        if isinstance(task_results, list) and task_results:
            expected = sum(
                int(tr.get("event_count", 0))
                for tr in task_results
                if isinstance(tr, dict)
            )
            if expected != trace_count:
                failures.append(
                    f"trace event count mismatch: "
                    f"sum of task event_counts={expected}, "
                    f"actual len(traces)={trace_count}"
                )

        # ---- cross-check: metadata task_count == len(evaluations) ---
        meta_task_count = self._bundle.metadata.get("task_count")
        if isinstance(meta_task_count, int) and meta_task_count != eval_count:
            failures.append(
                f"task count mismatch: "
                f"metadata task_count={meta_task_count}, "
                f"evaluations count={eval_count}"
            )

        return ReplaySummary(
            run_id=meta_run_id,
            bundle_path=self._bundle.bundle_path,
            trace_event_count=trace_count,
            evaluation_count=eval_count,
            policy_verdict=policy_verdict,
            checks_passed=len(failures) == 0,
            failures=failures,
            bundle_format_version=bundle_format_version,
            manifest_valid=manifest_valid,
        )
