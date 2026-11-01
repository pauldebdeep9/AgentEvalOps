"""A real format 0.5 bundle kept as a legacy fixture (ROADMAP R28 (b2)).

``tests/fixtures/bundles/seed-narrow-0.5`` was written by
``agentevalops run`` at `9161d38`, the last format 0.5 code, for the seed
pack's mock run narrowed to TE1, TC1 and PC1 under the shipped system
declaration. It was run from a directory outside any checkout, with
relative paths, so it records no path from the machine that wrote it and
its ``git_sha`` is ``"unavailable"``. Its bytes are what its manifest
checksums, so ``.gitattributes`` and the pre-commit config leave
``tests/fixtures/`` alone.

This test must pass unchanged across the 0.6 bump: a 0.5 bundle keeps
validating, reading and gating as it did. It asserts nothing about the
code-drift warning, which depends on the checkout running the test.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from agentevalops.bundles.constants import peek_declared_format_version
from agentevalops.bundles.reader import BundleReader
from agentevalops.bundles.validator import validate_bundle
from agentevalops.cli import app

FIXTURE = Path(__file__).resolve().parent / "fixtures/bundles/seed-narrow-0.5"


def test_the_fixture_declares_format_0_5() -> None:
    assert peek_declared_format_version(FIXTURE) == "0.5"


def test_the_fixture_validates_with_no_errors() -> None:
    result = validate_bundle(FIXTURE)
    assert result.errors == []
    assert result.valid
    assert result.checked_files == 11


def test_the_reader_loads_the_fixture() -> None:
    loaded = BundleReader(FIXTURE).read()
    assert loaded.metadata["schema_version"] == "0.5"
    assert loaded.metadata["git_sha"] == "unavailable"
    assert loaded.summary["total_tasks"] == 3
    assert [r["task_id"] for r in loaded.summary["pass_k"]] == ["TE1", "TC1", "PC1"]
    assert loaded.test_plan is not None
    assert loaded.test_plan["selected"] == ["TE1", "TC1", "PC1"]
    assert loaded.controls is None
    assert len(loaded.end_states) == 3


def test_the_gate_passes_the_fixture_as_it_did_when_written() -> None:
    """Recorded at 9161d38: `agentevalops gate --bundle` exited 0."""
    result = CliRunner().invoke(app, ["gate", "--bundle", str(FIXTURE)])
    assert result.exit_code == 0, result.output
    assert "GATE: PASS" in result.output


def test_validate_bundle_on_the_cli_passes() -> None:
    result = CliRunner().invoke(app, ["validate-bundle", "--bundle", str(FIXTURE)])
    assert result.exit_code == 0, result.output
