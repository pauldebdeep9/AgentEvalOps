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
#: so all are minor bumps, not breaks.
BUNDLE_FORMAT_VERSION: str = "0.4"

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
LEGACY_BUNDLE_FORMAT_VERSIONS: frozenset[str] = frozenset({"0.1", "0.2", "0.3"})

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


def required_files_for_version(version: str | None) -> tuple[str, ...]:
    """Return the required bundle files for a bundle's *declared* format
    version, not the version this running code would currently produce.

    ``version`` is normally read from the bundle's own ``manifest.json``
    (via :func:`peek_declared_format_version`) -- it answers "what does
    this bundle on disk claim to be," not "what version is current."
    ``version=None`` (manifest missing, unparseable, or lacking the key --
    exactly the cases where we can't even tell what a bundle claims to be)
    is the conservative default: the pre-"0.4" flat set, the same
    requirement every version through "0.3" already had. Only a bundle that
    explicitly declares the current ``BUNDLE_FORMAT_VERSION`` is held to the
    newer, larger set -- a bundle declaring some other/unrecognized version
    string also falls back to the flat set rather than guessing.
    """
    if version == BUNDLE_FORMAT_VERSION:
        return REQUIRED_BUNDLE_FILES_0_4
    return REQUIRED_BUNDLE_FILES


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
