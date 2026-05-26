"""BundleReader — load and validate a local result bundle from disk.

A bundle directory must contain the content files produced by ``BundleWriter``
plus a ``manifest.json`` written last.  ``BundleReader`` checks file presence,
parses each file, and returns a ``LoadedBundle`` dataclass.  Any structural
problem raises ``BundleError`` with a clear message; no content is silently
ignored.

Backward compatibility
----------------------
Bundles written before WBS 8 do not contain ``manifest.json``.  Pass
``strict=False`` to ``BundleReader`` to tolerate missing manifests (the
``manifest`` field on ``LoadedBundle`` will be ``None``).
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any, cast

from agentevalops.bundles.constants import MANIFEST_FILENAME, REQUIRED_BUNDLE_FILES
from agentevalops.core.errors import BundleError

# Backward-compat alias used by tests that imported BUNDLE_FILES from writer.
BUNDLE_FILES = REQUIRED_BUNDLE_FILES


@dataclasses.dataclass
class LoadedBundle:
    """In-memory representation of a parsed result bundle.

    All JSON files are stored as plain Python structures so that the caller
    (e.g. ``LocalReplayVerifier``) can interrogate them without re-parsing.

    Fields
    ------
    bundle_path:
        Absolute path to the bundle directory that was read.
    metadata:
        Parsed contents of ``metadata.json`` (always a dict).
    config:
        Parsed contents of ``config.json`` (always a dict).
    traces:
        Parsed lines of ``traces.jsonl`` (list of dicts, one per event).
    evaluations:
        Parsed contents of ``evaluations.json`` (list).
    policy:
        Parsed contents of ``policy.json`` — a dict, or ``None`` when the
        bundle was written without a policy verdict.
    summary:
        Parsed contents of ``summary.json`` (always a dict).
    manifest:
        Parsed contents of ``manifest.json``, or ``None`` when the bundle was
        written before WBS 8 and strict mode is disabled.
    """

    bundle_path: Path
    metadata: dict[str, Any]
    config: dict[str, Any]
    traces: list[dict[str, Any]]
    evaluations: list[Any]
    policy: dict[str, Any] | None
    summary: dict[str, Any]
    manifest: dict[str, Any] | None = None


class BundleReader:
    """Read and validate a local result bundle directory.

    Parameters
    ----------
    bundle_path:
        Path to the bundle directory (created by ``BundleWriter``).
    strict:
        When ``True`` (default), ``manifest.json`` is required and a
        ``BundleError`` is raised if it is absent.  Set to ``False`` to
        tolerate pre-WBS-8 bundles that have no manifest.

    Raises
    ------
    BundleError
        If the path is not a directory, if required files are missing, or
        if any file contains malformed content.
    """

    def __init__(self, bundle_path: Path, *, strict: bool = True) -> None:
        self._path = bundle_path
        self._strict = strict

    def read(self) -> LoadedBundle:
        """Parse and return the full bundle.  Raises ``BundleError`` on any
        structural problem.
        """
        self._validate_is_directory()
        self._validate_files_present()

        metadata = self._read_json_dict("metadata.json")
        config = self._read_json_dict("config.json")
        traces = self._read_jsonl("traces.jsonl")
        evaluations = self._read_json_list("evaluations.json")
        policy = self._read_json_dict_or_null("policy.json")
        summary = self._read_json_dict("summary.json")
        # report.md existence already confirmed by _validate_files_present;
        # its content is not parsed — structural presence is sufficient.

        manifest = self._read_manifest()

        return LoadedBundle(
            bundle_path=self._path,
            metadata=metadata,
            config=config,
            traces=traces,
            evaluations=evaluations,
            policy=policy,
            summary=summary,
            manifest=manifest,
        )

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _validate_is_directory(self) -> None:
        if not self._path.exists():
            raise BundleError(
                f"Bundle path does not exist: {self._path}"
            )
        if not self._path.is_dir():
            raise BundleError(
                f"Bundle path is not a directory: {self._path}"
            )

    def _validate_files_present(self) -> None:
        missing = [
            f for f in BUNDLE_FILES if not (self._path / f).exists()
        ]
        if missing:
            raise BundleError(
                f"Bundle '{self._path}' is missing required files: "
                + ", ".join(missing)
            )

    def _read_manifest(self) -> dict[str, Any] | None:
        """Return parsed manifest dict, or None / raise depending on strict."""
        path = self._path / MANIFEST_FILENAME
        if not path.exists():
            if self._strict:
                raise BundleError(
                    f"Bundle '{self._path}' is missing '{MANIFEST_FILENAME}'. "
                    "Use BundleReader(path, strict=False) for pre-WBS-8 bundles."
                )
            return None
        return self._read_json_dict(MANIFEST_FILENAME)

    # ------------------------------------------------------------------
    # Low-level parse helpers
    # ------------------------------------------------------------------

    def _read_raw_json(self, filename: str) -> Any:
        """Return the parsed JSON value from *filename* (any type)."""
        path = self._path / filename
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise BundleError(
                f"Cannot read '{filename}': {exc}"
            ) from exc
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise BundleError(
                f"Invalid JSON in '{filename}': {exc}"
            ) from exc

    def _read_json_dict(self, filename: str) -> dict[str, Any]:
        raw = self._read_raw_json(filename)
        if not isinstance(raw, dict):
            raise BundleError(
                f"'{filename}' must contain a JSON object, "
                f"got {type(raw).__name__}"
            )
        return cast(dict[str, Any], raw)

    def _read_json_list(self, filename: str) -> list[Any]:
        raw = self._read_raw_json(filename)
        if not isinstance(raw, list):
            raise BundleError(
                f"'{filename}' must contain a JSON array, "
                f"got {type(raw).__name__}"
            )
        return raw  # narrowed to list[Any] by isinstance guard above

    def _read_json_dict_or_null(
        self, filename: str
    ) -> dict[str, Any] | None:
        """Return a dict, or ``None`` when the file contains JSON ``null``."""
        raw = self._read_raw_json(filename)
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise BundleError(
                f"'{filename}' must be a JSON object or null, "
                f"got {type(raw).__name__}"
            )
        return cast(dict[str, Any], raw)

    def _read_jsonl(self, filename: str) -> list[dict[str, Any]]:
        """Parse a newline-delimited JSON file; empty lines are skipped."""
        path = self._path / filename
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise BundleError(
                f"Cannot read '{filename}': {exc}"
            ) from exc

        results: list[dict[str, Any]] = []
        for lineno, raw_line in enumerate(text.splitlines(), start=1):
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise BundleError(
                    f"Invalid JSON on line {lineno} of '{filename}': {exc}"
                ) from exc
            if not isinstance(obj, dict):
                raise BundleError(
                    f"Expected a JSON object on line {lineno} of "
                    f"'{filename}', got {type(obj).__name__}"
                )
            results.append(cast(dict[str, Any], obj))
        return results
