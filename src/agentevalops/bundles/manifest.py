"""Manifest generation for result bundles.

``generate_manifest`` computes a ``manifest.json`` structure over an existing
bundle directory.  The manifest records:

- bundle format version,
- ISO-8601 timestamp of generation,
- list of required files,
- per-file size (bytes) and SHA-256 checksum,
- high-level run metadata extracted from ``metadata.json`` and ``config.json``,
- writer name and version.

Checksum convention
-------------------
Checksums cover the *content files* listed in ``REQUIRED_BUNDLE_FILES``.
``manifest.json`` is written *after* all content files are in place; it is
**not** checksummed inside itself.  This avoids a circular dependency and is
the documented convention for this bundle format.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentevalops import __version__
from agentevalops.bundles.constants import (
    BUNDLE_FORMAT_VERSION,
    MANIFEST_FILENAME,
    REQUIRED_BUNDLE_FILES,
)


def _sha256_file(path: Path) -> str:
    """Return the hex-encoded SHA-256 digest of the bytes in *path*."""
    h = hashlib.sha256()
    data = path.read_bytes()
    h.update(data)
    return h.hexdigest()


def _extract_run_fields(bundle_dir: Path) -> dict[str, Any]:
    """Best-effort extraction of high-level run fields from bundle content."""
    run: dict[str, Any] = {}

    meta_path = bundle_dir / "metadata.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(meta, dict):
                run_id = meta.get("run_id")
                if run_id is not None:
                    run["run_id"] = run_id
        except (json.JSONDecodeError, OSError):
            pass

    config_path = bundle_dir / "config.json"
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(cfg, dict):
                config_name = cfg.get("config_name")
                if config_name is not None:
                    run["config_name"] = config_name
                rc = cfg.get("run_config")
                if isinstance(rc, dict):
                    benchmark_id = rc.get("benchmark_id")
                    if benchmark_id is not None:
                        run["benchmark"] = benchmark_id
                    scenario = rc.get("benchmark_scenario")
                    if scenario is not None:
                        run["scenario"] = scenario
        except (json.JSONDecodeError, OSError):
            pass

    return run


def generate_manifest(bundle_dir: Path) -> dict[str, Any]:
    """Generate a manifest dict for the bundle at *bundle_dir*.

    All *REQUIRED_BUNDLE_FILES* that exist in *bundle_dir* are checksummed.
    Files that are missing are omitted from the ``"files"`` section (the
    validator will later report them as missing).

    Parameters
    ----------
    bundle_dir:
        Path to the bundle directory whose content files have already been
        written.

    Returns
    -------
    dict
        A plain-Python dict suitable for JSON serialization.
    """
    files: dict[str, dict[str, Any]] = {}
    for filename in REQUIRED_BUNDLE_FILES:
        path = bundle_dir / filename
        if path.exists():
            size = path.stat().st_size
            sha256 = _sha256_file(path)
            files[filename] = {"size_bytes": size, "sha256": sha256}

    return {
        "bundle_format_version": BUNDLE_FORMAT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest_filename": MANIFEST_FILENAME,
        "required_files": sorted(REQUIRED_BUNDLE_FILES),
        "files": dict(sorted(files.items())),
        "run": _extract_run_fields(bundle_dir),
        "writer": {
            "name": "agentevalops",
            "version": __version__,
        },
    }


def write_manifest(bundle_dir: Path) -> Path:
    """Generate the manifest and write it to *bundle_dir/manifest.json*.

    Parameters
    ----------
    bundle_dir:
        Directory that already contains the content bundle files.

    Returns
    -------
    Path
        Path to the written ``manifest.json`` file.
    """
    manifest = generate_manifest(bundle_dir)
    out_path = bundle_dir / MANIFEST_FILENAME
    out_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return out_path
