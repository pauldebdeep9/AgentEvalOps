"""BundleValidator — static integrity checks for a local result bundle.

``validate_bundle`` checks:

1. Bundle path is an existing directory.
2. All required content files are present.
3. ``manifest.json`` is present and parseable.
4. ``bundle_format_version`` in the manifest is a supported value.
5. Every file listed in the manifest exists on disk.
6. File sizes match the manifest.
7. SHA-256 checksums match the manifest.
8. All JSON files (``*.json``) parse successfully.
9. ``traces.jsonl`` parses line-by-line.
10. ``summary.json`` ``trace_event_count`` (if present) matches the number
    of non-empty lines in ``traces.jsonl``.
11. The manifest ``required_files`` list agrees with the canonical set.

The function never raises; it returns a ``BundleValidationResult``.
Only ``BundleReader`` raises ``BundleError`` for unreadable bundles.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

from agentevalops.bundles.constants import (
    BUNDLE_FORMAT_VERSION,
    MANIFEST_FILENAME,
    REQUIRED_BUNDLE_FILES,
)

#: Bundle format versions that this validator understands.
SUPPORTED_FORMAT_VERSIONS: frozenset[str] = frozenset({BUNDLE_FORMAT_VERSION})


@dataclasses.dataclass
class BundleValidationResult:
    """Structured result returned by ``validate_bundle``.

    Attributes
    ----------
    valid:
        ``True`` when all checks passed with no errors.
    errors:
        List of error descriptions (each represents a failed check).
    warnings:
        List of warning descriptions (non-fatal observations).
    checked_files:
        Number of content files whose size and checksum were verified.
    """

    valid: bool
    errors: list[str]
    warnings: list[str]
    checked_files: int


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_bundle(bundle_path: Path) -> BundleValidationResult:
    """Validate the bundle at *bundle_path* and return a structured result.

    All checks are read-only.  The bundle is never mutated.

    Parameters
    ----------
    bundle_path:
        Path to a directory previously written by ``BundleWriter``.

    Returns
    -------
    BundleValidationResult
        ``valid`` is ``False`` if any error was found.
    """
    errors: list[str] = []
    warnings: list[str] = []
    checked_files: int = 0

    # ------------------------------------------------------------------ #
    # 1. Directory check
    # ------------------------------------------------------------------ #
    if not bundle_path.exists():
        errors.append(f"Bundle path does not exist: {bundle_path}")
        return BundleValidationResult(
            valid=False, errors=errors, warnings=warnings,
            checked_files=checked_files,
        )
    if not bundle_path.is_dir():
        errors.append(f"Bundle path is not a directory: {bundle_path}")
        return BundleValidationResult(
            valid=False, errors=errors, warnings=warnings,
            checked_files=checked_files,
        )

    # ------------------------------------------------------------------ #
    # 2. Required content files presence
    # ------------------------------------------------------------------ #
    for filename in REQUIRED_BUNDLE_FILES:
        if not (bundle_path / filename).exists():
            errors.append(f"Missing required file: {filename}")

    # ------------------------------------------------------------------ #
    # 3. Manifest presence and parse
    # ------------------------------------------------------------------ #
    manifest_path = bundle_path / MANIFEST_FILENAME
    if not manifest_path.exists():
        errors.append(f"Missing manifest file: {MANIFEST_FILENAME}")
        # Without a manifest we cannot run checksum or version checks.
        return BundleValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            checked_files=checked_files,
        )

    try:
        manifest_text = manifest_path.read_text(encoding="utf-8")
        manifest: object = json.loads(manifest_text)
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"Cannot parse {MANIFEST_FILENAME}: {exc}")
        return BundleValidationResult(
            valid=False, errors=errors, warnings=warnings,
            checked_files=checked_files,
        )

    if not isinstance(manifest, dict):
        errors.append(
            f"{MANIFEST_FILENAME} must be a JSON object, "
            f"got {type(manifest).__name__}"
        )
        return BundleValidationResult(
            valid=False, errors=errors, warnings=warnings,
            checked_files=checked_files,
        )

    # ------------------------------------------------------------------ #
    # 4. bundle_format_version
    # ------------------------------------------------------------------ #
    fmt_version = manifest.get("bundle_format_version")
    if fmt_version is None:
        errors.append(
            f"{MANIFEST_FILENAME} is missing 'bundle_format_version'"
        )
    elif fmt_version not in SUPPORTED_FORMAT_VERSIONS:
        errors.append(
            f"Unsupported bundle_format_version '{fmt_version}'. "
            f"Supported: {sorted(SUPPORTED_FORMAT_VERSIONS)}"
        )

    # ------------------------------------------------------------------ #
    # 5–7. Per-file: existence, size, checksum
    # ------------------------------------------------------------------ #
    files_section = manifest.get("files", {})
    if not isinstance(files_section, dict):
        errors.append(f"{MANIFEST_FILENAME} 'files' must be a JSON object")
        files_section = {}

    for filename, info in files_section.items():
        file_path = bundle_path / filename
        if not file_path.exists():
            errors.append(
                f"Manifest lists '{filename}' but file does not exist"
            )
            continue
        if not isinstance(info, dict):
            warnings.append(
                f"Manifest entry for '{filename}' is not an object; "
                "skipping size/checksum checks"
            )
            continue

        # Size check
        actual_size = file_path.stat().st_size
        expected_size = info.get("size_bytes")
        if expected_size is not None and actual_size != expected_size:
            errors.append(
                f"Size mismatch for '{filename}': "
                f"expected {expected_size} bytes, got {actual_size} bytes"
            )

        # Checksum check
        expected_sha256 = info.get("sha256")
        if expected_sha256 is not None:
            actual_sha256 = _sha256_bytes(file_path.read_bytes())
            if actual_sha256 != expected_sha256:
                errors.append(
                    f"SHA-256 mismatch for '{filename}': "
                    f"expected {expected_sha256}, got {actual_sha256}"
                )
            checked_files += 1

    # Ensure every required file that exists has a manifest entry (otherwise
    # a tampered file could escape checksum verification).
    for filename in REQUIRED_BUNDLE_FILES:
        if (bundle_path / filename).exists() and filename not in files_section:
            errors.append(
                f"Manifest 'files' section is missing an entry for "
                f"required file '{filename}'"
            )

    # ------------------------------------------------------------------ #
    # 8. JSON files parse
    # ------------------------------------------------------------------ #
    json_files = [
        f for f in REQUIRED_BUNDLE_FILES
        if f.endswith(".json") and (bundle_path / f).exists()
    ]
    for filename in json_files:
        path = bundle_path / filename
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            errors.append(f"Cannot parse '{filename}': {exc}")

    # ------------------------------------------------------------------ #
    # 9. traces.jsonl line-by-line parse
    # ------------------------------------------------------------------ #
    traces_path = bundle_path / "traces.jsonl"
    trace_line_count = 0
    if traces_path.exists():
        try:
            text = traces_path.read_text(encoding="utf-8")
            for lineno, raw_line in enumerate(text.splitlines(), start=1):
                stripped = raw_line.strip()
                if not stripped:
                    continue
                try:
                    obj = json.loads(stripped)
                    if not isinstance(obj, dict):
                        errors.append(
                            f"traces.jsonl line {lineno}: "
                            "expected a JSON object"
                        )
                    else:
                        trace_line_count += 1
                except json.JSONDecodeError as exc:
                    errors.append(
                        f"traces.jsonl line {lineno}: invalid JSON: {exc}"
                    )
        except OSError as exc:
            errors.append(f"Cannot read traces.jsonl: {exc}")

    # ------------------------------------------------------------------ #
    # 10. trace_event_count cross-check
    # ------------------------------------------------------------------ #
    summary_path = bundle_path / "summary.json"
    if summary_path.exists():
        try:
            summary_obj = json.loads(
                summary_path.read_text(encoding="utf-8")
            )
            if isinstance(summary_obj, dict):
                tec = summary_obj.get("trace_event_count")
                if isinstance(tec, int) and tec != trace_line_count:
                    errors.append(
                        f"trace_event_count mismatch: "
                        f"summary.json says {tec}, "
                        f"traces.jsonl has {trace_line_count} lines"
                    )
        except (json.JSONDecodeError, OSError):
            pass  # already caught by check 8

    # ------------------------------------------------------------------ #
    # 11. required_files agreement
    # ------------------------------------------------------------------ #
    manifest_required = manifest.get("required_files")
    if isinstance(manifest_required, list):
        canonical = set(REQUIRED_BUNDLE_FILES)
        listed = set(manifest_required)
        extra = listed - canonical
        missing_from_list = canonical - listed
        if extra:
            warnings.append(
                f"Manifest required_files lists unknown files: "
                f"{sorted(extra)}"
            )
        if missing_from_list:
            warnings.append(
                f"Manifest required_files is missing canonical files: "
                f"{sorted(missing_from_list)}"
            )

    return BundleValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        checked_files=checked_files,
    )
