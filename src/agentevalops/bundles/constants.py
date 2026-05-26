"""Bundle format constants.

These constants define the canonical shape of a result bundle produced by
``BundleWriter``.  They are shared by the writer, reader, manifest generator,
and validator so that there is a single authoritative list of required files.
"""

from __future__ import annotations

#: Increment when the bundle layout changes in a backward-incompatible way.
BUNDLE_FORMAT_VERSION: str = "0.1"

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
