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
from agentevalops.reports.markdown import render_report

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
class BundleStamp:
    """What writing a bundle adds to the run's own results: when it was
    written and by what code (docs/adr/0007 D8), shared by metadata.json,
    both reports and replay_command.txt. ``BundleWriter.write`` captures it
    once -- the clock, the git probes, the package hash, the Azure resource
    -- and ``rescore`` reads it back from a bundle, so that a re-render of
    the same results with the same code gives the same bytes."""

    created_at: datetime
    platform_version: str
    git_sha: str
    git_dirty: bool | None
    package_sha256: str
    invocation_git_sha: str
    invocation_git_dirty: bool | None
    azure_resource: str | None = None

    @classmethod
    def recorded(
        cls, metadata: ResultBundleMetadata, replay_command: str
    ) -> BundleStamp:
        """The stamp a bundle was written with, read back from its
        ``metadata.json`` (loaded with the typed loader) and the
        ``azure_resource:`` line of its ``replay_command.txt`` (written
        below, by ``_replay_command``). Raises ``ValueError`` for a bundle
        that records no ``package_sha256``."""
        if metadata.package_sha256 is None:
            raise ValueError("metadata.json records no package_sha256")
        resource = None
        for line in replay_command.splitlines():
            if line.startswith(_AZURE_RESOURCE_LINE):
                value = line[len(_AZURE_RESOURCE_LINE) :]
                resource = None if value == _GIT_SHA_UNAVAILABLE else value
        return cls(
            created_at=metadata.created_at,
            platform_version=metadata.platform_version,
            git_sha=metadata.git_sha,
            git_dirty=metadata.git_dirty,
            package_sha256=metadata.package_sha256,
            invocation_git_sha=metadata.invocation_git_sha,
            invocation_git_dirty=metadata.invocation_git_dirty,
            azure_resource=resource,
        )


#: The line of replay_command.txt that names the Azure resource.
_AZURE_RESOURCE_LINE = "azure_resource: "


def _azure_resource() -> str | None:
    """The Azure resource the run's endpoint names, never the URL; None
    when ``AZURE_OPENAI_ENDPOINT`` is unset or unusable."""
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    return _azure_resource_name_from_endpoint(endpoint) if endpoint else None


def _json(data: Any) -> bytes:
    return (json.dumps(to_jsonable(data), indent=2, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )


def _jsonl(items: Sequence[Any]) -> bytes:
    return "".join(
        json.dumps(to_jsonable(item), ensure_ascii=False) + "\n" for item in items
    ).encode("utf-8")


def render_bundle(
    run_config: RunConfig,
    summary: RunSummary,
    all_events: Sequence[TraceEvent],
    *,
    plan: RunPlan,
    test_plan_sha256: str,
    stamp: BundleStamp,
    evidence: EvidenceInputs,
    runner_id: str | None,
    mock_trajectory: str | None,
    evaluators: Sequence[str] = (),
    policy_spec: PolicySpec | None = None,
    config_name: str = "",
    benchmark_id: str = "",
    benchmark_scenario: str = "",
    status: Literal["sealed", "interrupted", "partial"] = "sealed",
    benchmark_options: Mapping[str, Any] | None = None,
    tool_calls: Sequence[ToolCallRecord] = (),
    end_states: Sequence[EndStateRecord] = (),
    scenario_info: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, bytes]:
    """Every content file of a bundle, by name, as bytes, in the order
    ``BundleWriter.write`` writes them -- all but ``test_plan.json``, which
    is registered before the first task, and ``manifest.json``, which
    checksums the files on disk.

    One rendering for the run that writes a bundle and for ``rescore``,
    which renders a bundle's results again and compares (docs/adr/0008 R1,
    R3). It reads no clock, no environment and no git: everything the
    writing adds comes in *stamp*. The arguments are ``write``'s.
    """
    created_at = stamp.created_at.isoformat()
    # docs/adr/0007 D5-D7: coverage from the plan, the summary, the
    # scenarios and the catalogue; the report rendered from it, dated by
    # the run's own created_at so a re-render gives the same bytes.
    controls = control_coverage(
        evidence.catalogue,
        plan,
        evidence.scenarios,
        summary,
        plan_sha256=test_plan_sha256,
        end_states=list(end_states),
    )
    evidence_report = render_evidence_report(
        evidence.catalogue,
        plan,
        controls,
        summary,
        runner_id=runner_id,
        created_at=created_at,
    )
    return {
        "metadata.json": _metadata(
            run_config, summary, status, stamp, test_plan_sha256
        ),
        "config.json": _config(
            run_config,
            policy_spec,
            config_name,
            benchmark_id,
            benchmark_scenario,
            benchmark_options or {},
            runner_id=runner_id,
            mock_trajectory=mock_trajectory,
            evaluators=evaluators,
        ),
        "traces.jsonl": _jsonl(all_events),
        # Format 0.5's harness streams (docs/adr/0006 D19): always written,
        # empty when nothing was recorded, so a 0.5 bundle never lacks them.
        TOOL_CALLS_FILENAME: _jsonl(tool_calls),
        END_STATES_FILENAME: _jsonl(end_states),
        "evaluations.json": _evaluations(summary),
        "policy.json": _json(summary.policy_verdict),
        "summary.json": _json(summary),
        "report.md": _report(
            run_config,
            summary,
            all_events,
            config_name,
            scenario_info,
            plan,
            runner_id,
            created_at,
            tool_calls,
        ),
        CONTROLS_FILENAME: _json(controls),
        EVIDENCE_REPORT_FILENAME: (evidence_report + "\n").encode("utf-8"),
        REPLAY_COMMAND_FILENAME: _replay_command(
            run_config, config_name, stamp, test_plan_sha256
        ),
    }


def _metadata(
    run_config: RunConfig,
    summary: RunSummary,
    status: Literal["sealed", "interrupted", "partial"],
    stamp: BundleStamp,
    test_plan_sha256: str | None,
) -> bytes:
    return _json(
        ResultBundleMetadata(
            schema_version=BUNDLE_FORMAT_VERSION,
            run_id=run_config.run_id,
            created_at=stamp.created_at,
            platform_version=stamp.platform_version,
            backend_id=run_config.backend_id,
            task_count=summary.total_tasks,
            sealed=(status == "sealed"),
            status=status,
            git_sha=stamp.git_sha,
            git_dirty=stamp.git_dirty,
            test_plan_sha256=test_plan_sha256,
            package_sha256=stamp.package_sha256,
            invocation_git_sha=stamp.invocation_git_sha,
            invocation_git_dirty=stamp.invocation_git_dirty,
        )
    )


def _config(
    run_config: RunConfig,
    policy_spec: PolicySpec | None,
    config_name: str,
    benchmark_id: str = "",
    benchmark_scenario: str = "",
    benchmark_options: Mapping[str, Any] | None = None,
    *,
    runner_id: str | None = None,
    mock_trajectory: str | None = None,
    evaluators: Sequence[str] = (),
) -> bytes:
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
    return _json(data)


def _evaluations(summary: RunSummary) -> bytes:
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
    return _json(entries)


def _report(
    run_config: RunConfig,
    summary: RunSummary,
    all_events: Sequence[TraceEvent],
    config_name: str,
    scenario_info: Mapping[str, Mapping[str, Any]] | None = None,
    plan: RunPlan | None = None,
    runner_id: str | None = None,
    created_at: str | None = None,
    tool_calls: Sequence[ToolCallRecord] = (),
) -> bytes:
    """report.md. *scenario_info* (R26) is the tasks' outcome groups and
    variants from the benchmark, for the outcomes section; *plan* (R27)
    the pre-registered plan, for the report to repeat -- content in the
    report, never a change to the bundle's shape. *created_at* dates it,
    the run's own timestamp, as metadata.json records it (ADR-0008
    decision 9). *tool_calls* are the run's harness records, for the
    "Where runs first fail" matrix (R37)."""
    return render_report(
        run_config,
        summary,
        list(all_events),
        config_name,
        scenario_info,
        plan,
        runner_id,
        created_at,
        tool_calls,
    ).encode("utf-8")


def _replay_command(
    run_config: RunConfig,
    config_name: str,
    stamp: BundleStamp,
    test_plan_sha256: str | None,
) -> bytes:
    """``replay_command.txt`` -- structural provenance, not a
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
        f"platform_version: {stamp.platform_version}",
        '# platform_version alone is too coarse to answer "did the code',
        '# change": every bundle this repo has produced records the same',
        "# value across dozens of commits. package_sha256 answers it on",
        "# any install: the hash of the package's .py and .yaml files.",
        "# From format 0.6 git_sha is the package's own source tree (an",
        "# editable install's checkout, else unavailable); invocation_*",
        "# is the directory the run was started from (docs/adr/0007 D8).",
        f"package_sha256: {stamp.package_sha256}",
        f"git_sha: {stamp.git_sha}",
        f"git_dirty: {dirty(stamp.git_dirty)}",
        f"invocation_git_sha: {stamp.invocation_git_sha}",
        f"invocation_git_dirty: {dirty(stamp.invocation_git_dirty)}",
        f"test_plan_sha256: {test_plan_sha256 or _GIT_SHA_UNAVAILABLE}",
    ]
    if run_config.model is not None:
        lines.append(f"model: {run_config.model}")
    if run_config.deployment is not None:
        resource = stamp.azure_resource or _GIT_SHA_UNAVAILABLE
        lines.append(f"{_AZURE_RESOURCE_LINE}{resource}")
    return ("\n".join(lines) + "\n").encode("utf-8")


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
        # Captured once and threaded through -- metadata.json, both reports
        # and replay_command.txt must agree by construction (one source of
        # truth), not by independent probes that could observe different
        # states if the tree changed between them.
        package_git_sha, package_git_dirty = _capture_package_provenance()
        invocation_git_sha, invocation_git_dirty = _capture_git_provenance()
        stamp = BundleStamp(
            created_at=datetime.now(timezone.utc),
            platform_version=__version__,
            git_sha=package_git_sha,
            git_dirty=package_git_dirty,
            package_sha256=package_sha256(),
            invocation_git_sha=invocation_git_sha,
            invocation_git_dirty=invocation_git_dirty,
            azure_resource=_azure_resource(),
        )
        files = render_bundle(
            run_config,
            summary,
            all_events,
            plan=plan,
            test_plan_sha256=test_plan_sha256,
            stamp=stamp,
            evidence=evidence,
            runner_id=runner_id,
            mock_trajectory=mock_trajectory,
            evaluators=evaluators,
            policy_spec=policy_spec,
            config_name=config_name,
            benchmark_id=benchmark_id,
            benchmark_scenario=benchmark_scenario,
            status=status,
            benchmark_options=benchmark_options,
            tool_calls=tool_calls or (),
            end_states=end_states or (),
            scenario_info=scenario_info,
        )
        for name, data in files.items():
            (self._dir / name).write_bytes(data)
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
