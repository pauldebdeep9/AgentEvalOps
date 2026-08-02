"""Bundle format constants.

These constants define the canonical shape of a result bundle produced by
``BundleWriter``.  They are shared by the writer, reader, manifest generator,
and validator so that there is a single authoritative list of required files.
"""

from __future__ import annotations

#: Increment when the bundle layout changes. The single source of truth for
#: "the bundle format version" -- both metadata.json's schema_version and
#: manifest.json's bundle_format_version are sourced from this one constant
#: (bundles/writer.py), not two independently hardcoded literals. Before
#: 2026-08-05 they were: "0.1" here vs. a separate "0.1.0" literal in
#: bundles/writer.py's _write_metadata, never cross-checked against
#: anything, that happened to agree with nothing in particular (see
#: docs/adr/0002-outcome-taxonomy.md). Bumped 0.1 -> 0.2 this session for
#: the additive taxonomy/provenance fields (RunSummary.not_evaluated_tasks,
#: EvaluationResult.evaluated, ResultBundleMetadata.status/git_sha/
#: git_dirty, RunConfig.model) -- all backward-compatible (old bundles
#: simply lack the fields; nothing new is required), so this is a minor
#: bump, not a break.
BUNDLE_FORMAT_VERSION: str = "0.2"

#: Older format versions that validate_bundle()/BundleReader still accept,
#: even though BundleWriter never produces them anymore. "0.1" bundles
#: predate every field listed above -- readers must treat their absence as
#: the documented default, not as an error. Keep this only as long as real
#: "0.1" bundles exist that anyone still needs to read (the preserved
#: incident fixture and the runs/toy-smoke* bundles on disk, as of
#: 2026-08-05); do not add a new legacy entry speculatively.
LEGACY_BUNDLE_FORMAT_VERSIONS: frozenset[str] = frozenset({"0.1"})

#: Files that every valid bundle must contain.
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
