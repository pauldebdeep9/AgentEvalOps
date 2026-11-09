"""Bundle format constants.

These constants define the canonical shape of a result bundle produced by
``BundleWriter``.  They are shared by the writer, reader, manifest generator,
and validator so that there is a single authoritative list of required files.
"""

from __future__ import annotations

import json
from pathlib import Path

#: Increment when the bundle layout changes. The single source of truth for
#: "the bundle format version" -- both metadata.json's schema_version and
#: manifest.json's bundle_format_version are sourced from this one constant
#: (bundles/writer.py), not two independently hardcoded literals. Before
#: 2026-08-05 they were: "0.1" here vs. a separate "0.1.0" literal in
#: bundles/writer.py's _write_metadata, never cross-checked against
#: anything, that happened to agree with nothing in particular (see
#: docs/adr/0002-outcome-taxonomy.md). Bumped 0.1 -> 0.2 on 2026-08-05 for
#: the additive taxonomy/provenance fields (RunSummary.not_evaluated_tasks,
#: EvaluationResult.evaluated, ResultBundleMetadata.status/git_sha/
#: git_dirty, RunConfig.model). Bumped 0.2 -> 0.3 for RunConfig.deployment/
#: .api_version (docs/adr/0004-cloud-backend-two-axes.md, Axis B --
#: AzureOpenAIRunner). Bumped 0.3 -> 0.4 for replay_command.txt (ROADMAP row
#: 12b, docs/adr/0003-replay-semantics.md Q4) -- the first bump that adds a
#: new *required* file rather than a field on an existing one, which is why
#: REQUIRED_BUNDLE_FILES below stops being a single flat tuple; see
#: required_files_for_version(). All bumps are additive/backward-compatible
#: (old bundles simply lack what's new; nothing new is required of them),
#: so all are minor bumps, not breaks. Bumped 0.5 -> 0.6 in R28 for the
#: evidence pack (docs/adr/0007): test_plan.json, controls.json and
#: evidence_report.md required; evaluator.score events; runner fields in
#: config.json; package_sha256 and the invocation fields in metadata.json,
#: where git_sha now means the package's own tree; manifest.json.sig
#: recognised. TraceEventKind.POLICY_VERDICT left the enum with this bump
#: -- no bundle ever carried one.
BUNDLE_FORMAT_VERSION: str = "0.6"

#: Older format versions that validate_bundle()/BundleReader still accept,
#: even though BundleWriter never produces them anymore. "0.1" bundles
#: predate the 2026-08-05 taxonomy/provenance fields; "0.2" bundles predate
#: RunConfig.deployment/.api_version; "0.3" bundles predate
#: replay_command.txt. Readers must treat the absence of any of these
#: fields/files as the documented default, not as an error. Keep entries
#: here only as long as real bundles in that format exist that anyone still
#: needs to read (the preserved incident fixture and the runs/toy-smoke*
#: bundles on disk, as of 2026-08-05, cover "0.1"); do not add a new legacy
#: entry speculatively.
LEGACY_BUNDLE_FORMAT_VERSIONS: frozenset[str] = frozenset(
    {"0.1", "0.2", "0.3", "0.4", "0.5"}
)

#: Files that every bundle up through format "0.3" must contain. Kept as a
#: flat tuple -- not renamed, not restructured -- specifically so this is an
#: additive change: existing importers of this name keep working, and it
#: reads as "the set before replay_command.txt existed," not a breaking
#: rename. Use required_files_for_version() to get the right set for a
#: bundle's *actual* declared version instead of assuming this one.
REQUIRED_BUNDLE_FILES: tuple[str, ...] = (
    "metadata.json",
    "config.json",
    "traces.jsonl",
    "evaluations.json",
    "policy.json",
    "summary.json",
    "report.md",
)

#: Filename for the bundle manifest (written last; not checksummed in itself).
MANIFEST_FILENAME: str = "manifest.json"

#: Filename for the human-readable structural-provenance file (docs/adr/0003
#: Q4): the reconstructable invocation, git_sha/git_dirty, platform_version,
#: run_id, resolved model id, and (azure-openai runs only) the Azure
#: resource name. Required starting at format "0.4"; see
#: required_files_for_version().
REPLAY_COMMAND_FILENAME: str = "replay_command.txt"

#: Files required by format "0.4" -- REQUIRED_BUNDLE_FILES plus
#: replay_command.txt. A bundle at this version without it is genuinely
#: incomplete, the same way one without report.md is.
REQUIRED_BUNDLE_FILES_0_4: tuple[str, ...] = (
    *REQUIRED_BUNDLE_FILES,
    REPLAY_COMMAND_FILENAME,
)

#: Harness streams added in format "0.5" (docs/adr/0006 D19, D21): every
#: tool call as the environment recorded it, and each task run's end state.
TOOL_CALLS_FILENAME: str = "tool_calls.jsonl"
END_STATES_FILENAME: str = "end_states.jsonl"

#: The pre-registered test plan (docs/adr/0005 D6; ROADMAP R27): written
#: and hashed before the first task runs. Optional under format 0.5, where
#: only a run with a system declaration wrote one; required from 0.6, where
#: a run without one writes a minimal plan that says so (docs/adr/0007 D2).
#: metadata.json's ``test_plan_sha256`` holds it to the registered hash.
TEST_PLAN_FILENAME: str = "test_plan.json"

#: Control coverage (docs/adr/0007 D5) and the evidence report (D7):
#: required from format 0.6.
CONTROLS_FILENAME: str = "controls.json"
EVIDENCE_REPORT_FILENAME: str = "evidence_report.md"

#: A detached signature over manifest.json (docs/adr/0007 D1): optional,
#: recognised from 0.6, signed and verified from R29. Never listed in the
#: manifest and never checksummed -- it signs the manifest itself.
MANIFEST_SIGNATURE_FILENAME: str = "manifest.json.sig"

#: The namespace every bundle signature is made and verified under
#: (docs/adr/0008 S3): a signature made for anything else with the same key
#: does not verify as a bundle's.
SIGNING_NAMESPACE: str = "agentevalops-bundle"

#: Files required by format "0.5" -- the 0.4 set plus the harness streams.
REQUIRED_BUNDLE_FILES_0_5: tuple[str, ...] = (
    *REQUIRED_BUNDLE_FILES_0_4,
    TOOL_CALLS_FILENAME,
    END_STATES_FILENAME,
)

#: Files required by format "0.6" -- the 0.5 set plus the plan and the
#: evidence pack's two files (docs/adr/0007 D1).
REQUIRED_BUNDLE_FILES_0_6: tuple[str, ...] = (
    *REQUIRED_BUNDLE_FILES_0_5,
    TEST_PLAN_FILENAME,
    CONTROLS_FILENAME,
    EVIDENCE_REPORT_FILENAME,
)

#: Required files by declared version, for versions that grew the set.
_REQUIRED_FILES_BY_VERSION: dict[str, tuple[str, ...]] = {
    "0.4": REQUIRED_BUNDLE_FILES_0_4,
    "0.5": REQUIRED_BUNDLE_FILES_0_5,
    "0.6": REQUIRED_BUNDLE_FILES_0_6,
}


#: The first format whose metadata.json records ``package_sha256``, the
#: code's identity (docs/adr/0007 D8): the drift check compares it, and
#: rescore needs it (docs/adr/0008 R5).
PACKAGE_HASH_FROM: tuple[int, ...] = (0, 6)

#: R42: the citation norm a writer follows, recorded as metadata.json's
#: ``refs_version``. 1: every failing evaluation cites what it judged
#: (ADR-0006 D17) -- from R42, when state_based began citing the terminal.
#: A bundle written before R42 records none.
REFS_VERSION: int = 1
#: R42 (ADR-0008 R3, as amended): rescore holds an evaluation's refs, and a
#: control verdict's, among the deciding fields for a bundle that records
#: at least this ``refs_version`` -- or that the running code wrote; an
#: older bundle's refs are evidence, shown.
REFS_DECIDE_FROM: int = 1


def recorded_refs_era(marker: object) -> tuple[bool, str]:
    """What a bundle's recorded ``refs_version`` says of its refs (R42):
    whether the marker alone makes them decide, and the era both
    ``validate-bundle`` and ``rescore`` print. A marker is a whole number,
    not a bool, at or after ``REFS_DECIDE_FROM``; a bundle that records
    none was written before R42."""
    if (
        isinstance(marker, int)
        and not isinstance(marker, bool)
        and marker >= REFS_DECIDE_FROM
    ):
        return True, f"deciding (refs_version {marker})"
    if marker is None:
        return False, "evidence (pre-R42 bundle)"
    return False, f"evidence (unrecognised refs_version {marker!r})"


def format_tuple(version: object) -> tuple[int, ...] | None:
    """A format version as numbers, ``"0.6"`` -> ``(0, 6)``, for comparing
    versions in order; None when it is not one."""
    try:
        return tuple(int(part) for part in str(version).split("."))
    except ValueError:
        return None


def required_files_for_version(version: str | None) -> tuple[str, ...]:
    """Return the required bundle files for a bundle's *declared* format
    version, not the version this running code would currently produce.

    ``version`` is normally read from the bundle's own ``manifest.json``
    (via :func:`peek_declared_format_version`) -- it answers "what does
    this bundle on disk claim to be," not "what version is current."
    ``version=None`` (manifest missing, unparseable, or lacking the key --
    exactly the cases where we can't even tell what a bundle claims to be)
    is the conservative default: the pre-"0.4" flat set, the same
    requirement every version through "0.3" already had. A bundle that
    explicitly declares "0.4", "0.5" or "0.6" is held to that version's
    larger set (0.4 added ``replay_command.txt``; 0.5 the two harness
    streams; 0.6 the plan, ``controls.json`` and ``evidence_report.md``) -- a
    bundle declaring some other/unrecognized version string falls back to
    the flat set rather than guessing.
    """
    if version is None:
        return REQUIRED_BUNDLE_FILES
    return _REQUIRED_FILES_BY_VERSION.get(version, REQUIRED_BUNDLE_FILES)


def peek_declared_format_version(bundle_dir: Path) -> str | None:
    """Best-effort, non-raising read of ``manifest.json``'s
    ``bundle_format_version``, or ``None`` if it can't be determined.

    Deliberately separate from the real, error-reporting manifest parse in
    ``validate_bundle``/``BundleReader`` -- this exists only to pick the
    right required-file set *before* that real parse runs, so a "which
    files are required" decision doesn't have to wait on (or duplicate) the
    full manifest-validation logic. Never raises; a bundle with no manifest,
    a corrupt manifest, or a manifest missing the key all resolve to
    ``None`` here, and the real checks report the actual error separately.
    """
    manifest_path = bundle_dir / MANIFEST_FILENAME
    if not manifest_path.exists():
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    version = data.get("bundle_format_version")
    return version if isinstance(version, str) else None
