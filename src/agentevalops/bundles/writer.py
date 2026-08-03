"""BundleWriter — writes a complete local result bundle to disk.

Bundle layout (all files in *output_dir*):

    metadata.json       — schema version, run_id, timestamps, platform
    config.json         — RunConfig + PolicySpec that produced the run
    traces.jsonl        — one JSON object per TraceEvent (newline-delimited)
    evaluations.json    — list of EvaluationResult objects, one per task
    policy.json         — PolicyVerdict (or null if no policy check was run)
    summary.json        — RunSummary (counts, cost, tokens, task results)
    report.md           — human-readable markdown report
    replay_command.txt  — structural provenance: invocation, git_sha/
                          git_dirty, platform_version, run_id, resolved
                          model id (docs/adr/0003-replay-semantics.md Q4).
                          Required starting at format "0.4"; see
                          bundles/constants.py's required_files_for_version.
    manifest.json       — bundle format version, file sizes, SHA-256 checksums

The manifest is always written *last* so that its checksums cover all
content files.  ``manifest.json`` is not checksummed inside itself.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from agentevalops import __version__
from agentevalops.bundles.constants import (
    BUNDLE_FORMAT_VERSION,
    REPLAY_COMMAND_FILENAME,
    REQUIRED_BUNDLE_FILES,
)
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
#: and docs/adr/0002-outcome-taxonomy.md, Axis 4. Reused verbatim in
#: replay_command.txt for the Azure resource name when
#: ``AZURE_OPENAI_ENDPOINT`` is unset -- one sentinel string, one meaning
#: ("cannot determine"), used everywhere a provenance value is unavailable.
_GIT_SHA_UNAVAILABLE: str = "unavailable"


def _azure_resource_name_from_endpoint(endpoint: str) -> str | None:
    """Extract just the resource-name subdomain from an Azure OpenAI
    endpoint URL (e.g. ``https://my-resource.openai.azure.com`` ->
    ``"my-resource"``) -- never the full URL.

    A bundle is portable and shareable (DESIGN.md), so a tenant-specific
    endpoint URL must not go in one (row 13b ticket's D5). The resource
    name answers "which Azure resource produced this" without embedding a
    URL a recipient could dial into. Returns ``None`` if *endpoint* doesn't
    parse to a usable hostname.
    """
    host = urlparse(endpoint).netloc or urlparse(endpoint).path
    host = host.split("@")[-1].split(":")[0].strip()
    if not host:
        return None
    resource = host.split(".")[0]
    return resource or None


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
        # Captured once and threaded through -- metadata.json and
        # replay_command.txt must agree on git_sha/git_dirty by
        # construction (one source of truth), not by two independent
        # subprocess calls that could theoretically observe different
        # states if the tree changed between them.
        git_sha, git_dirty = _capture_git_provenance()
        self._write_metadata(run_config, summary, status, git_sha, git_dirty)
        self._write_config(
            run_config, policy_spec, config_name,
            benchmark_id, benchmark_scenario,
        )
        self._write_traces(all_events)
        self._write_evaluations(summary)
        self._write_policy(summary)
        self._write_summary(summary)
        self._write_report(run_config, summary, all_events, config_name)
        self._write_replay_command(run_config, config_name, git_sha, git_dirty)
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
        git_sha: str,
        git_dirty: bool | None,
    ) -> None:
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

    def _write_replay_command(
        self,
        run_config: RunConfig,
        config_name: str,
        git_sha: str,
        git_dirty: bool | None,
    ) -> None:
        """Write ``replay_command.txt`` -- structural provenance, not a
        behavioral replay mechanism (docs/adr/0003-replay-semantics.md Q4).

        Human-readable text, not JSON: this file is for a person deciding
        whether a bundle is still trustworthy to compare against, not a
        machine-parsed contract. ``git_dirty`` and an unavailable
        ``git_sha``/Azure resource name are rendered as explicit,
        unambiguous values -- never blank, never omitted -- per
        ``ResultBundleMetadata``'s own docstring: *"cannot determine" must
        never read as a match by omission*.
        """
        dirty_str = "unknown" if git_dirty is None else str(git_dirty)
        lines = [
            "# replay_command.txt -- structural provenance for this bundle.",
            "#",
            "# This does NOT re-execute the run (no agents, models, tools,",
            "# or benchmarks). It is what `agentevalops validate-bundle`",
            "# reads to check whether this bundle's own files are still",
            "# internally consistent, plus enough provenance for a human to",
            "# judge whether the code that produced it has since changed.",
            "# Behavioral replay is not yet implemented -- see",
            "# docs/adr/0003-replay-semantics.md.",
            "#",
            "# Reconstructable invocation (point --output at a NEW,",
            "# empty directory -- do not overwrite this bundle):",
            "#",
            f"#   agentevalops run --config {config_name or '<unknown>'} "
            "--output <new-directory>",
            "#",
            "# The resolved configuration that actually produced this run",
            "# is recorded in this bundle's own config.json, not duplicated",
            "# here -- a YAML file on disk can be edited after the fact;",
            "# config.json cannot.",
            "",
            f"run_id: {run_config.run_id}",
            f"platform_version: {__version__}",
            "# platform_version alone is too coarse to answer \"did the code",
            "# change\": every bundle this repo has produced to date records",
            "# the same value across dozens of intervening commits. git_sha",
            "# below is the field that actually answers that question.",
            f"git_sha: {git_sha}",
            f"git_dirty: {dirty_str}",
        ]
        if run_config.model is not None:
            lines.append(f"model: {run_config.model}")
        if run_config.deployment is not None:
            endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
            resource = (
                _azure_resource_name_from_endpoint(endpoint)
                if endpoint
                else None
            )
            lines.append(f"azure_resource: {resource or _GIT_SHA_UNAVAILABLE}")

        path = self._dir / REPLAY_COMMAND_FILENAME
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
