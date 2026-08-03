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
#: docs/adr/0002-outcome-taxonomy.md). Bumped 0.1 -> 0.2 on 2026-08-05 for
#: the additive taxonomy/provenance fields (RunSummary.not_evaluated_tasks,
#: EvaluationResult.evaluated, ResultBundleMetadata.status/git_sha/
#: git_dirty, RunConfig.model). Bumped 0.2 -> 0.3 for RunConfig.deployment/
#: .api_version (docs/adr/0004-cloud-backend-two-axes.md, Axis B --
#: AzureOpenAIRunner). Both bumps are additive/backward-compatible (old
#: bundles simply lack the new fields; nothing new is required), so both
#: are minor bumps, not breaks.
BUNDLE_FORMAT_VERSION: str = "0.3"

#: Older format versions that validate_bundle()/BundleReader still accept,
#: even though BundleWriter never produces them anymore. "0.1" bundles
#: predate the 2026-08-05 taxonomy/provenance fields; "0.2" bundles predate
#: RunConfig.deployment/.api_version. Readers must treat the absence of any
#: of these fields as the documented default, not as an error. Keep entries
#: here only as long as real bundles in that format exist that anyone still
#: needs to read (the preserved incident fixture and the runs/toy-smoke*
#: bundles on disk, as of 2026-08-05, cover "0.1"); do not add a new legacy
#: entry speculatively.
LEGACY_BUNDLE_FORMAT_VERSIONS: frozenset[str] = frozenset({"0.1", "0.2"})

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
