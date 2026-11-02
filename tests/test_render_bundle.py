"""Rendering a bundle's files apart from writing them (R29 (b2) Part 2).

``render_bundle`` returns every content file's bytes; ``BundleWriter.write``
captures the stamp (the clock, the git probes, the package hash, the Azure
resource), calls it and writes what it returns, then the manifest. Rescore
calls the same function with the stamp read back from a bundle.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agentevalops.bundles import writer as writer_module
from agentevalops.bundles.constants import (
    MANIFEST_FILENAME,
    TEST_PLAN_FILENAME,
    required_files_for_version,
)
from agentevalops.cli import app

ROOT = Path(__file__).resolve().parents[1]


def _captured_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config: str
) -> tuple[Path, list[tuple[tuple[Any, ...], dict[str, Any], dict[str, bytes]]]]:
    calls: list[tuple[tuple[Any, ...], dict[str, Any], dict[str, bytes]]] = []
    original = writer_module.render_bundle

    def spy(*args: Any, **kwargs: Any) -> dict[str, bytes]:
        files = original(*args, **kwargs)
        calls.append((args, kwargs, files))
        return files

    monkeypatch.setattr(writer_module, "render_bundle", spy)
    out = tmp_path / "bundle"
    result = CliRunner().invoke(
        app, ["run", "--config", str(ROOT / config), "--output", str(out)]
    )
    assert result.exit_code == 0, result.output
    return out, calls


@pytest.mark.parametrize(
    "config", ["configs/toy_smoke.yaml", "configs/agentic_baseline_mock.yaml"]
)
def test_write_writes_what_render_returns_then_the_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config: str
) -> None:
    out, calls = _captured_run(tmp_path, monkeypatch, config)
    ((_, _, files),) = calls
    written = {p.name for p in out.iterdir()}
    assert written == set(files) | {TEST_PLAN_FILENAME, MANIFEST_FILENAME}
    assert set(files) | {TEST_PLAN_FILENAME} == set(required_files_for_version("0.6"))
    for name, data in files.items():
        assert (out / name).read_bytes() == data, name


def test_render_reads_no_clock_and_no_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same arguments, a second later and with the Azure endpoint
    changed, render the same bytes: what the writing adds is the stamp."""
    _, calls = _captured_run(tmp_path, monkeypatch, "configs/toy_smoke.yaml")
    ((args, kwargs, files),) = calls
    time.sleep(1.1)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://elsewhere.openai.azure.com")
    again = writer_module.render_bundle(*args, **kwargs)
    assert again == files
    assert list(again) == list(files)
