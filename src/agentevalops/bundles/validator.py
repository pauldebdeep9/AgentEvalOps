"""BundleValidator — the single structural entry point for a local result
bundle (docs/adr/0003-replay-semantics.md, Q1).

``validate_bundle`` checks:

1. Bundle path is an existing directory.
2. All required content files are present (version-aware -- see
   ``bundles.constants.required_files_for_version``).
3. ``manifest.json`` is present and parseable.
4. ``bundle_format_version`` in the manifest is a supported value.
5. Every file listed in the manifest exists on disk.
6. File sizes match the manifest.
7. SHA-256 checksums match the manifest.
8. All JSON files (``*.json``) parse successfully.
9. ``traces.jsonl`` parses line-by-line.
10. ``summary.json`` ``trace_event_count`` (if present) matches the number
    of non-empty lines in ``traces.jsonl``.
11. The manifest ``required_files`` list agrees with the version-aware
    canonical set.
12. Cross-file consistency (``LocalReplayVerifier``, folded in rather than
    exposed as a parallel ``replay`` check -- ADR-0003 Q1/D2): does
    ``metadata.json``'s ``run_id`` agree with ``summary.json``'s, do
    per-task event counts sum to the actual trace length, and so on.
13. Code-drift, as a warning only (ADR-0003 Q4/D5): does the bundle's
    recorded ``git_sha`` match the current checkout. Never an error, never
    changes ``valid`` -- a bundle written last month is still structurally
    valid today; this is a heads-up for anyone about to *compare* against
    it, not a validity criterion.

The function never raises; it returns a ``BundleValidationResult``. Only
``BundleReader`` raises ``BundleError`` for unreadable bundles.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

from agentevalops.bundles.constants import (
    BUNDLE_FORMAT_VERSION,
    LEGACY_BUNDLE_FORMAT_VERSIONS,
    MANIFEST_FILENAME,
    peek_declared_format_version,
    required_files_for_version,
)
from agentevalops.bundles.reader import BundleReader
from agentevalops.bundles.writer import _GIT_SHA_UNAVAILABLE, _capture_git_provenance
from agentevalops.core.errors import BundleError
from agentevalops.replay.local import LocalReplayVerifier

#: Bundle format versions that this validator understands: the current one
#: plus any legacy versions still readable (see LEGACY_BUNDLE_FORMAT_VERSIONS'
#: own docstring for the backward-compatibility policy).
SUPPORTED_FORMAT_VERSIONS: frozenset[str] = (
    frozenset({BUNDLE_FORMAT_VERSION}) | LEGACY_BUNDLE_FORMAT_VERSIONS
)


@dataclasses.dataclass
class BundleValidationResult:
    """Structured result returned by ``validate_bundle``.

    Fields below the first four are absorbed from the now-deleted
    ``replay.local.ReplaySummary`` (docs/adr/0003, D2) -- every one of them
    was already a structural fact, not a behavioral-comparison outcome, so
    they belong on the single validation result rather than a second,
    parallel summary type.

    Attributes
    ----------
    valid:
        ``True`` when all checks passed with no errors.
    errors:
        List of error descriptions (each represents a failed check).
    warnings:
        List of warning descriptions (non-fatal observations) -- including
        code-drift findings (D5), which never affect ``valid``.
    checked_files:
        Number of content files whose size and checksum were verified.
    run_id:
        The ``run_id`` found in ``metadata.json``, or ``None`` if absent.
    bundle_format_version:
        The ``bundle_format_version`` from ``manifest.json``, or ``None``
        when the bundle has no (parseable) manifest.
    manifest_valid:
        ``True`` when a manifest was present and its checksums all passed,
        ``False`` when checksum/size failures were detected, or ``None``
        when no manifest was available at all.
    trace_event_count:
        Number of trace events parsed from ``traces.jsonl``.
    evaluation_count:
        Number of evaluations in ``evaluations.json``.
    policy_verdict:
        The ``verdict`` string from ``policy.json``, or ``None`` when the
        bundle contains no policy verdict.
    """

    valid: bool
    errors: list[str]
    warnings: list[str]
    checked_files: int
    run_id: str | None = None
    bundle_format_version: str | None = None
    manifest_valid: bool | None = None
    trace_event_count: int = 0
    evaluation_count: int = 0
    policy_verdict: str | None = None


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
    run_id: str | None = None
    trace_event_count: int = 0
    evaluation_count: int = 0
    policy_verdict: str | None = None
    bundle_git_sha: str | None = None
    bundle_git_dirty: bool | None = None

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
    # 2. Required content files presence (version-aware, docs/adr/0003 D3)
    # ------------------------------------------------------------------ #
    # Peeked independently of the real manifest parse below (which reports
    # its own errors for a missing/corrupt manifest) -- this selects which
    # required-file *set* applies before that parse runs. A bundle we can't
    # even determine the declared version of falls back to the pre-"0.4"
    # set, the conservative choice: never invent a requirement for a file
    # a bundle was never obligated to have.
    declared_version = peek_declared_format_version(bundle_path)
    required_files = required_files_for_version(declared_version)

    for filename in required_files:
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
    bundle_format_version: str | None = (
        fmt_version if isinstance(fmt_version, str) else None
    )
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

    manifest_had_mismatch = False
    for filename, info in files_section.items():
        file_path = bundle_path / filename
        if not file_path.exists():
            errors.append(
                f"Manifest lists '{filename}' but file does not exist"
            )
            manifest_had_mismatch = True
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
            manifest_had_mismatch = True

        # Checksum check
        expected_sha256 = info.get("sha256")
        if expected_sha256 is not None:
            actual_sha256 = _sha256_bytes(file_path.read_bytes())
            if actual_sha256 != expected_sha256:
                errors.append(
                    f"SHA-256 mismatch for '{filename}': "
                    f"expected {expected_sha256}, got {actual_sha256}"
                )
                manifest_had_mismatch = True
            checked_files += 1

    # Ensure every required file that exists has a manifest entry (otherwise
    # a tampered file could escape checksum verification).
    for filename in required_files:
        if (bundle_path / filename).exists() and filename not in files_section:
            errors.append(
                f"Manifest 'files' section is missing an entry for "
                f"required file '{filename}'"
            )
            manifest_had_mismatch = True

    manifest_valid: bool | None = not manifest_had_mismatch

    # ------------------------------------------------------------------ #
    # 8. JSON files parse
    # ------------------------------------------------------------------ #
    json_files = [
        f for f in required_files
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
    trace_event_count = trace_line_count

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
    # Absorbed-field extraction (docs/adr/0003, D2): evaluation_count,
    # policy_verdict, run_id, and the bundle's own recorded git provenance
    # (needed for the drift warning below).
    # ------------------------------------------------------------------ #
    evaluations_path = bundle_path / "evaluations.json"
    if evaluations_path.exists():
        try:
            ev_obj = json.loads(evaluations_path.read_text(encoding="utf-8"))
            if isinstance(ev_obj, list):
                evaluation_count = len(ev_obj)
        except (json.JSONDecodeError, OSError):
            pass  # already caught by check 8

    policy_path = bundle_path / "policy.json"
    if policy_path.exists():
        try:
            policy_obj = json.loads(policy_path.read_text(encoding="utf-8"))
            if isinstance(policy_obj, dict):
                verdict = policy_obj.get("verdict")
                policy_verdict = verdict if isinstance(verdict, str) else None
        except (json.JSONDecodeError, OSError):
            pass  # already caught by check 8

    metadata_path = bundle_path / "metadata.json"
    if metadata_path.exists():
        try:
            meta_obj = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(meta_obj, dict):
                rid = meta_obj.get("run_id")
                run_id = rid if isinstance(rid, str) else None
                sha = meta_obj.get("git_sha")
                bundle_git_sha = sha if isinstance(sha, str) else None
                dirty = meta_obj.get("git_dirty")
                bundle_git_dirty = dirty if isinstance(dirty, bool) else None
        except (json.JSONDecodeError, OSError):
            pass  # already caught by check 8

    # ------------------------------------------------------------------ #
    # 11. required_files agreement (version-aware, docs/adr/0003 D3)
    # ------------------------------------------------------------------ #
    manifest_required = manifest.get("required_files")
    if isinstance(manifest_required, list):
        canonical = set(required_files_for_version(fmt_version))
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

    # ------------------------------------------------------------------ #
    # 12. Cross-file consistency (LocalReplayVerifier, folded in per D2)
    # ------------------------------------------------------------------ #
    try:
        loaded = BundleReader(bundle_path, strict=False).read()
    except BundleError:
        # Bundle too broken to load at all (e.g. several required files
        # missing) -- checks 1-11 above already reported the real reasons;
        # there is nothing further to cross-check.
        loaded = None
    if loaded is not None:
        cross_errors, cross_warnings = LocalReplayVerifier(loaded).check()
        errors.extend(cross_errors)
        warnings.extend(cross_warnings)

    # ------------------------------------------------------------------ #
    # 13. Code-drift warning (docs/adr/0003, D5) -- never an error, never
    # changes `valid`. Skipped entirely for bundles that predate git_sha
    # (format "0.1"), where metadata.json simply has no such field.
    # ------------------------------------------------------------------ #
    if bundle_git_sha is not None:
        current_sha, current_dirty = _capture_git_provenance()
        determinate = (
            bundle_git_sha != _GIT_SHA_UNAVAILABLE
            and current_sha != _GIT_SHA_UNAVAILABLE
            and bundle_git_dirty is False
            and current_dirty is False
        )
        if not determinate:
            warnings.append(
                "Code-drift check inconclusive: cannot determine whether "
                "the code has changed since this bundle was written "
                f"(bundle git_sha={bundle_git_sha!r}, "
                f"git_dirty={bundle_git_dirty!r}; current checkout "
                f"git_sha={current_sha!r}, git_dirty={current_dirty!r})."
            )
        elif bundle_git_sha != current_sha:
            warnings.append(
                f"Code drift: this bundle was written at git_sha "
                f"{bundle_git_sha}, but the current checkout is at "
                f"{current_sha}. Structural validity is unaffected -- a "
                "bundle stays valid regardless of code version -- but a "
                "behavioral comparison against it would not be meaningful "
                "without accounting for this."
            )

    return BundleValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        checked_files=checked_files,
        run_id=run_id,
        bundle_format_version=bundle_format_version,
        manifest_valid=manifest_valid,
        trace_event_count=trace_event_count,
        evaluation_count=evaluation_count,
        policy_verdict=policy_verdict,
    )
