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
                          git_dirty, package_sha256, the invocation's git,
                          platform_version, run_id, the plan's hash, the
                          resolved model id (docs/adr/0003 Q4; 0007 D8).
                          Required starting at format "0.4"; see
                          bundles/constants.py's required_files_for_version.
    tool_calls.jsonl, end_states.jsonl — the harness streams (format 0.5)
    test_plan.json      — the plan, registered before the first task; from
                          format 0.6 every bundle has one (docs/adr/0007 D2)
    controls.json       — control coverage (0.6, docs/adr/0007 D5, D6)
    evidence_report.md  — the testing framework's checks with evidence
                          pointers (0.6, docs/adr/0007 D7)
    manifest.json       — bundle format version, file sizes, SHA-256 checksums

The manifest is always written *last* so that its checksums cover all
content files.  ``manifest.json`` is not checksummed inside itself.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from agentevalops import __version__
from agentevalops.bundles.constants import (
    BUNDLE_FORMAT_VERSION,
    CONTROLS_FILENAME,
    END_STATES_FILENAME,
    EVIDENCE_REPORT_FILENAME,
    REPLAY_COMMAND_FILENAME,
    REQUIRED_BUNDLE_FILES,
    TOOL_CALLS_FILENAME,
)
from agentevalops.bundles.manifest import write_manifest
from agentevalops.bundles.provenance import (
    UNAVAILABLE,
    git_state,
    package_git_provenance,
    package_sha256,
)
from agentevalops.bundles.serializers import to_jsonable
from agentevalops.core.errors import BundleError
from agentevalops.core.schemas import (
    EndStateRecord,
    PolicySpec,
    ResultBundleMetadata,
    RunConfig,
    RunPlan,
    RunSummary,
    ToolCallRecord,
    TraceEvent,
)
from agentevalops.plans.builder import undeclared_plan
from agentevalops.plans.coverage import EvidenceInputs, control_coverage
from agentevalops.plans.io import write_plan
from agentevalops.reports.evidence import render_evidence_report
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
_GIT_SHA_UNAVAILABLE: str = UNAVAILABLE


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
    checkout" (those can differ, e.g. an installed wheel). What format 0.5
    records as ``git_sha``; the logic is ``provenance.git_state``, which
    format 0.6 also points at the package's own tree (docs/adr/0007 D8).
    """
    return git_state(cwd)


def _capture_package_provenance() -> tuple[str, bool | None]:
    """``(git_sha, git_dirty)`` of the package's own source tree, the
    format 0.6 meaning of ``git_sha`` (docs/adr/0007 D8): the editable
    install's checkout, or ``"unavailable"``."""
    return package_git_provenance()


@dataclass(frozen=True)
class _Provenance:
    """One capture of what code produced the bundle (docs/adr/0007 D8),
    shared by metadata.json and replay_command.txt."""

    git_sha: str
    git_dirty: bool | None
    package_sha256: str
    invocation_git_sha: str
    invocation_git_dirty: bool | None


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
        benchmark_options: dict[str, Any] | None = None,
        tool_calls: list[ToolCallRecord] | None = None,
        end_states: list[EndStateRecord] | None = None,
        scenario_info: Mapping[str, Mapping[str, Any]] | None = None,
        test_plan_sha256: str | None = None,
        plan: RunPlan | None = None,
        *,
        evidence: EvidenceInputs,
        runner_id: str | None,
        mock_trajectory: str | None,
        evaluators: Sequence[str] = (),
    ) -> Path:
        """Write all bundle files including manifest; return output dir.

        ``evidence`` (docs/adr/0007 D13, decision 7 as amended 2026-11-02):
        the catalogue and every pack scenario's descriptor, from which
        ``controls.json`` and ``evidence_report.md`` are written. Required:
        a guessed default would mark every control "not in pack".

        Every format 0.6 bundle has a plan (D2). A caller that passes none
        gets the undeclared plan, built from the run config and the
        summary's task ids, written as ``test_plan.json`` and registered;
        a caller that passes a plan without its registered hash gets it
        written and registered here.

        ``runner_id``, ``mock_trajectory`` and ``evaluators`` (D4) go to
        ``config.json``; the reports call a run scripted by ``runner_id``.
        The two runner fields are required, like ``evidence`` (R28
        follow-up, 2026-11-02): a caller passes ``None`` when a value is
        legitimately absent -- ``mock_trajectory`` for a model-backed run --
        and never by leaving it out. A ``None`` ``runner_id`` makes both
        reports say the runner is not recorded.

        ``test_plan_sha256`` (docs/adr/0005 D6; R27): the hash of
        ``test_plan.json`` the caller registered before the first task ran,
        recorded in metadata.json so the validator can hold the file to it.
        The writer never touches the plan file itself. ``plan`` is the same
        plan as built, for the report to repeat (content, not shape).

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
        if plan is None:
            task_ids = dict.fromkeys(str(r.task_id) for r in summary.task_results)
            plan = undeclared_plan(
                run_config,
                list(task_ids),
                config_name=config_name,
                evaluators=evaluators,
            )
            test_plan_sha256 = None
        if test_plan_sha256 is None:
            test_plan_sha256 = write_plan(self._dir, plan)
        # Captured once and threaded through -- metadata.json and
        # replay_command.txt must agree by construction (one source of
        # truth), not by independent probes that could observe different
        # states if the tree changed between them.
        provenance = _Provenance(
            *_capture_package_provenance(),
            package_sha256(),
            *_capture_git_provenance(),
        )
        created_at = datetime.now(timezone.utc)
        self._write_metadata(
            run_config, summary, status, provenance, test_plan_sha256, created_at
        )
        self._write_config(
            run_config,
            policy_spec,
            config_name,
            benchmark_id,
            benchmark_scenario,
            benchmark_options or {},
            runner_id=runner_id,
            mock_trajectory=mock_trajectory,
            evaluators=evaluators,
        )
        self._write_traces(all_events)
        # Format 0.5's harness streams (docs/adr/0006 D19): always written,
        # empty when nothing was recorded, so a 0.5 bundle never lacks them.
        self._write_jsonl(TOOL_CALLS_FILENAME, tool_calls or [])
        self._write_jsonl(END_STATES_FILENAME, end_states or [])
        self._write_evaluations(summary)
        self._write_policy(summary)
        self._write_summary(summary)
        self._write_report(
            run_config, summary, all_events, config_name, scenario_info, plan, runner_id
        )
        # docs/adr/0007 D5-D7: coverage from the plan, the summary, the
        # scenarios and the catalogue; the report rendered from it, dated by
        # the run's own created_at so a re-render gives the same bytes.
        controls = control_coverage(
            evidence.catalogue,
            plan,
            evidence.scenarios,
            summary,
            plan_sha256=test_plan_sha256,
            end_states=end_states or [],
        )
        self._write_json(CONTROLS_FILENAME, controls)
        report = render_evidence_report(
            evidence.catalogue,
            plan,
            controls,
            summary,
            runner_id=runner_id,
            created_at=created_at.isoformat(),
        )
        (self._dir / EVIDENCE_REPORT_FILENAME).write_text(
            report + "\n", encoding="utf-8"
        )
        self._write_replay_command(
            run_config, config_name, provenance, test_plan_sha256
        )
        # Manifest is written last so checksums cover all content files.
        write_manifest(self._dir)
        return self._dir

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _prepare_dir(self) -> None:
        if self._dir.exists() and not self._overwrite:
            existing = [f for f in BUNDLE_FILES if (self._dir / f).exists()]
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
        provenance: _Provenance,
        test_plan_sha256: str | None,
        created_at: datetime,
    ) -> None:
        metadata = ResultBundleMetadata(
            schema_version=BUNDLE_FORMAT_VERSION,
            run_id=run_config.run_id,
            created_at=created_at,
            platform_version=__version__,
            backend_id=run_config.backend_id,
            task_count=summary.total_tasks,
            sealed=(status == "sealed"),
            status=status,
            git_sha=provenance.git_sha,
            git_dirty=provenance.git_dirty,
            test_plan_sha256=test_plan_sha256,
            package_sha256=provenance.package_sha256,
            invocation_git_sha=provenance.invocation_git_sha,
            invocation_git_dirty=provenance.invocation_git_dirty,
        )
        self._write_json("metadata.json", metadata)

    def _write_config(
        self,
        run_config: RunConfig,
        policy_spec: PolicySpec | None,
        config_name: str,
        benchmark_id: str = "",
        benchmark_scenario: str = "",
        benchmark_options: dict[str, Any] | None = None,
        *,
        runner_id: str | None = None,
        mock_trajectory: str | None = None,
        evaluators: Sequence[str] = (),
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
        # docs/adr/0006 D25 (format 0.5): the options the benchmark was
        # built with, always recorded.
        data["benchmark_options"] = dict(benchmark_options or {})
        # docs/adr/0007 D4 (format 0.6): which runner played the run, whether
        # it played a script, and the evaluator kinds configured.
        data["runner_id"] = runner_id
        data["mock_trajectory"] = mock_trajectory
        data["evaluators"] = list(evaluators)
        self._write_json("config.json", data)

    def _write_traces(self, events: list[TraceEvent]) -> None:
        path = self._dir / "traces.jsonl"
        with open(path, "w", encoding="utf-8") as fh:
            for event in events:
                fh.write(json.dumps(to_jsonable(event), ensure_ascii=False))
                fh.write("\n")

    def _write_jsonl(self, filename: str, items: list[Any]) -> None:
        with open(self._dir / filename, "w", encoding="utf-8") as fh:
            for item in items:
                fh.write(json.dumps(to_jsonable(item), ensure_ascii=False))
                fh.write("\n")

    def _write_evaluations(self, summary: RunSummary) -> None:
        """One entry per evaluator per task run (docs/adr/0006 D13, format
        0.5), each an ``EvaluationResult`` plus the task run it belongs to.
        A task run without per-evaluator results contributes its verdict,
        so every task run appears."""
        entries = []
        for r in summary.task_results:
            for evaluation in r.evaluations or [r.evaluation]:
                entry = to_jsonable(evaluation)
                entry["task_id"] = r.task_id
                entry["repetition"] = r.repetition
                entries.append(entry)
        self._write_json("evaluations.json", entries)

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
        scenario_info: Mapping[str, Mapping[str, Any]] | None = None,
        plan: RunPlan | None = None,
        runner_id: str | None = None,
    ) -> None:
        """report.md. *scenario_info* (R26) is the tasks' outcome groups and
        variants from the benchmark, for the outcomes section; *plan* (R27)
        the pre-registered plan, for the report to repeat -- content in the
        report, never a change to the bundle's shape."""
        generator = MarkdownReportGenerator()
        report = asyncio.run(
            generator.render(
                run_config,
                summary,
                all_events,
                config_name,
                scenario_info,
                plan,
                runner_id,
            )
        )
        path = self._dir / "report.md"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(report)

    def _write_replay_command(
        self,
        run_config: RunConfig,
        config_name: str,
        provenance: _Provenance,
        test_plan_sha256: str | None,
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

        def dirty(value: bool | None) -> str:
            return "unknown" if value is None else str(value)

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
            '# platform_version alone is too coarse to answer "did the code',
            '# change": every bundle this repo has produced records the same',
            "# value across dozens of commits. package_sha256 answers it on",
            "# any install: the hash of the package's .py and .yaml files.",
            "# From format 0.6 git_sha is the package's own source tree (an",
            "# editable install's checkout, else unavailable); invocation_*",
            "# is the directory the run was started from (docs/adr/0007 D8).",
            f"package_sha256: {provenance.package_sha256}",
            f"git_sha: {provenance.git_sha}",
            f"git_dirty: {dirty(provenance.git_dirty)}",
            f"invocation_git_sha: {provenance.invocation_git_sha}",
            f"invocation_git_dirty: {dirty(provenance.invocation_git_dirty)}",
            f"test_plan_sha256: {test_plan_sha256 or _GIT_SHA_UNAVAILABLE}",
        ]
        if run_config.model is not None:
            lines.append(f"model: {run_config.model}")
        if run_config.deployment is not None:
            endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
            resource = (
                _azure_resource_name_from_endpoint(endpoint) if endpoint else None
            )
            lines.append(f"azure_resource: {resource or _GIT_SHA_UNAVAILABLE}")

        path = self._dir / REPLAY_COMMAND_FILENAME
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
