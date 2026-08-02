"""BundleWriter — writes a complete local result bundle to disk.

Bundle layout (all files in *output_dir*):

    metadata.json     — schema version, run_id, timestamps, platform
    config.json       — RunConfig + PolicySpec that produced the run
    traces.jsonl      — one JSON object per TraceEvent (newline-delimited)
    evaluations.json  — list of EvaluationResult objects, one per task
    policy.json       — PolicyVerdict (or null if no policy check was run)
    summary.json      — RunSummary (counts, cost, tokens, task results)
    report.md         — human-readable markdown report
    manifest.json     — bundle format version, file sizes, SHA-256 checksums

The manifest is always written *last* so that its checksums cover all
content files.  ``manifest.json`` is not checksummed inside itself.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from agentevalops import __version__
from agentevalops.bundles.constants import BUNDLE_FORMAT_VERSION, REQUIRED_BUNDLE_FILES
from agentevalops.bundles.manifest import write_manifest
from agentevalops.bundles.serializers import to_jsonable
from agentevalops.core.errors import BundleError
from agentevalops.core.schemas import (
    PolicySpec,
    ResultBundleMetadata,
    RunConfig,
    RunSummary,
    TraceEvent,
)
from agentevalops.reports.markdown import MarkdownReportGenerator

#: Re-exported for backward compatibility with code that imports BUNDLE_FILES
#: from this module.  New code should import from bundles.constants directly.
BUNDLE_FILES: tuple[str, ...] = REQUIRED_BUNDLE_FILES

#: Sentinel recorded in ``metadata.json.git_sha`` when the process isn't
#: running inside a git checkout, or ``git`` itself isn't available. Never
#: ``None``, never fabricated -- see ``ResultBundleMetadata``'s docstring
#: and docs/adr/0002-outcome-taxonomy.md, Axis 4.
_GIT_SHA_UNAVAILABLE: str = "unavailable"


def _capture_git_provenance(cwd: Path | None = None) -> tuple[str, bool | None]:
    """Best-effort ``(git_sha, git_dirty)`` capture. Never raises.

    ``cwd`` defaults to the process's actual working directory when
    ``None`` -- this deliberately answers "is *this* invocation running
    inside a git checkout," not "is the package's own source tree a git
    checkout" (those can differ, e.g. an installed wheel). Accepting an
    explicit ``cwd`` is what makes this testable against a real, disposable
    temporary git repository rather than mocking subprocess calls.

    Returns ``(_GIT_SHA_UNAVAILABLE, None)`` if ``git`` is missing, times
    out, or the directory isn't a checkout. Returns ``(sha, None)`` (not
    ``(sha, False)``) if the SHA was found but dirty-state couldn't be
    determined -- a fabricated "clean" is exactly the false provenance
    claim this function exists to avoid.
    """
    try:
        sha_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return (_GIT_SHA_UNAVAILABLE, None)

    sha = sha_result.stdout.strip()
    if sha_result.returncode != 0 or not sha:
        return (_GIT_SHA_UNAVAILABLE, None)

    try:
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return (sha, None)

    if status_result.returncode != 0:
        return (sha, None)

    dirty = bool(status_result.stdout.strip())
    return (sha, dirty)


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
        benchmark_id: str = "",
        benchmark_scenario: str = "",
        status: Literal["sealed", "interrupted", "partial"] = "sealed",
    ) -> Path:
        """Write all bundle files including manifest; return output dir.

        ``status`` (docs/adr/0002, Axis 4): the caller's own knowledge of
        *why* it's writing right now -- ``"sealed"`` for a normal run that
        completed (the default; covers a run with some ``not_evaluated``
        tasks too, since the run itself finished), ``"interrupted"`` for a
        ``KeyboardInterrupt``/cancellation, ``"partial"`` for anything else
        that stopped the caller from reaching a normal return. This
        function itself has no way to infer that from ``summary`` alone --
        a deliberately empty/best-effort ``RunSummary`` and a real, fully
        populated one are the same shape, so the caller must say which.
        """
        self._prepare_dir()
        self._write_metadata(run_config, summary, status)
        self._write_config(
            run_config, policy_spec, config_name,
            benchmark_id, benchmark_scenario,
        )
        self._write_traces(all_events)
        self._write_evaluations(summary)
        self._write_policy(summary)
        self._write_summary(summary)
        self._write_report(run_config, summary, all_events, config_name)
        # Manifest is written last so checksums cover all content files.
        write_manifest(self._dir)
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
        self,
        run_config: RunConfig,
        summary: RunSummary,
        status: Literal["sealed", "interrupted", "partial"],
    ) -> None:
        git_sha, git_dirty = _capture_git_provenance()
        metadata = ResultBundleMetadata(
            schema_version=BUNDLE_FORMAT_VERSION,
            run_id=run_config.run_id,
            created_at=datetime.now(timezone.utc),
            platform_version=__version__,
            backend_id=run_config.backend_id,
            task_count=summary.total_tasks,
            sealed=(status == "sealed"),
            status=status,
            git_sha=git_sha,
            git_dirty=git_dirty,
        )
        self._write_json("metadata.json", metadata)

    def _write_config(
        self,
        run_config: RunConfig,
        policy_spec: PolicySpec | None,
        config_name: str,
        benchmark_id: str = "",
        benchmark_scenario: str = "",
    ) -> None:
        data: dict[str, Any] = {
            "config_name": config_name,
            "run_config": run_config,
            "policy_spec": policy_spec,
        }
        if benchmark_id:
            data["benchmark_id"] = benchmark_id
        if benchmark_scenario:
            data["benchmark_scenario"] = benchmark_scenario
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
        generator = MarkdownReportGenerator()
        report = asyncio.run(
            generator.render(run_config, summary, all_events, config_name)
        )
        path = self._dir / "report.md"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(report)
