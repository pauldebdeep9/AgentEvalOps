"""BundleWriter — writes a complete local result bundle to disk.

Bundle layout (all files in *output_dir*):

    metadata.json     — schema version, run_id, timestamps, platform
    config.json       — RunConfig + PolicySpec that produced the run
    traces.jsonl      — one JSON object per TraceEvent (newline-delimited)
    evaluations.json  — list of EvaluationResult objects, one per task
    policy.json       — PolicyVerdict (or null if no policy check was run)
    summary.json      — RunSummary (counts, cost, tokens, task results)
    report.md         — human-readable markdown report
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentevalops import __version__
from agentevalops.bundles.serializers import to_jsonable
from agentevalops.core.errors import BundleError
from agentevalops.core.schemas import (
    PolicySpec,
    ResultBundleMetadata,
    RunConfig,
    TraceEvent,
)
from agentevalops.orchestration.local import RunSummary
from agentevalops.reports.markdown import render_report

#: Canonical set of files that constitute a result bundle.
BUNDLE_FILES: tuple[str, ...] = (
    "metadata.json",
    "config.json",
    "traces.jsonl",
    "evaluations.json",
    "policy.json",
    "summary.json",
    "report.md",
)


class BundleWriter:
    """Write a complete local result bundle to a directory.

    Parameters
    ----------
    output_dir:
        Target directory.  Created (including parents) if it does not exist.
    overwrite:
        If ``False`` (default) raises ``BundleError`` when any of the seven
        canonical bundle files already exist in *output_dir*.
        Set to ``True`` to silently overwrite.
    """

    def __init__(self, output_dir: Path, *, overwrite: bool = False) -> None:
        self._dir = output_dir
        self._overwrite = overwrite

    def write(
        self,
        run_config: RunConfig,
        summary: RunSummary,
        all_events: list[TraceEvent],
        policy_spec: PolicySpec | None = None,
        config_name: str = "",
    ) -> Path:
        """Write all seven bundle files; return the output directory path."""
        self._prepare_dir()
        self._write_metadata(run_config, summary)
        self._write_config(run_config, policy_spec, config_name)
        self._write_traces(all_events)
        self._write_evaluations(summary)
        self._write_policy(summary)
        self._write_summary(summary)
        self._write_report(run_config, summary, all_events, config_name)
        return self._dir

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _prepare_dir(self) -> None:
        if self._dir.exists() and not self._overwrite:
            existing = [
                f for f in BUNDLE_FILES if (self._dir / f).exists()
            ]
            if existing:
                raise BundleError(
                    f"Bundle directory '{self._dir}' already contains "
                    f"bundle files: {', '.join(existing)}. "
                    "Pass overwrite=True to overwrite."
                )
        self._dir.mkdir(parents=True, exist_ok=True)

    def _write_json(self, filename: str, data: Any) -> None:
        path = self._dir / filename
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(to_jsonable(data), fh, indent=2, ensure_ascii=False)
            fh.write("\n")

    def _write_metadata(
        self, run_config: RunConfig, summary: RunSummary
    ) -> None:
        metadata = ResultBundleMetadata(
            schema_version="0.1.0",
            run_id=run_config.run_id,
            created_at=datetime.now(timezone.utc),
            platform_version=__version__,
            backend_id=run_config.backend_id,
            task_count=summary.total_tasks,
            sealed=True,
        )
        self._write_json("metadata.json", metadata)

    def _write_config(
        self,
        run_config: RunConfig,
        policy_spec: PolicySpec | None,
        config_name: str,
    ) -> None:
        data: dict[str, Any] = {
            "config_name": config_name,
            "run_config": run_config,
            "policy_spec": policy_spec,
        }
        self._write_json("config.json", data)

    def _write_traces(self, events: list[TraceEvent]) -> None:
        path = self._dir / "traces.jsonl"
        with open(path, "w", encoding="utf-8") as fh:
            for event in events:
                fh.write(
                    json.dumps(to_jsonable(event), ensure_ascii=False)
                )
                fh.write("\n")

    def _write_evaluations(self, summary: RunSummary) -> None:
        self._write_json(
            "evaluations.json",
            [r.evaluation for r in summary.task_results],
        )

    def _write_policy(self, summary: RunSummary) -> None:
        self._write_json("policy.json", summary.policy_verdict)

    def _write_summary(self, summary: RunSummary) -> None:
        self._write_json("summary.json", summary)

    def _write_report(
        self,
        run_config: RunConfig,
        summary: RunSummary,
        all_events: list[TraceEvent],
        config_name: str,
    ) -> None:
        report = render_report(run_config, summary, all_events, config_name)
        path = self._dir / "report.md"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(report)
