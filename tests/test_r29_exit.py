"""ROADMAP R29's exit criteria for session (b), one test each, in the row's
order (docs/adr/0008; the (a) criteria are tests/test_signing.py,
tests/test_three_records.py and tests/test_report_dating.py). Each uses the
rescore tests' own helpers, so what the row claims is checked the way the
slice checks it. Run against the code before rescore (`1d57db5`), every
test but the last failed -- rescore did not exist; the last, that no
runtime dependency was added, holds before and after by construction.

The row's first criterion reads "altering a recorded verdict in
evaluations.json ... yields DIFFER". Altered in that file alone, the three
records of the result disagree and validate-bundle fails the bundle (C1),
so the structural layer stops it first and rescore cannot rescore it
(ADR-0005 D7's order); altered consistently in all three, it is DIFFER.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from reference_bundles import ReferenceBundles
from test_rescore import FIXTURES, GOLDEN, _code_changed, _forged, _last, _rescore
from test_three_records import FLIPS

from agentevalops.bundles.manifest import write_manifest
from agentevalops.bundles.provenance import package_dir, package_sha256
from agentevalops.bundles.validator import validate_bundle
from agentevalops.replay import rescore as rescore_module

if sys.version_info >= (3, 11):
    import tomllib
else:  # pytest depends on tomli below 3.11
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]

# (b) Altering a recorded verdict in evaluations.json -- even with all three
# records edited and the checksums re-sealed -- yields DIFFER.


def test_a_verdict_altered_in_all_three_records_and_resealed_is_a_differ(
    reference_bundles: ReferenceBundles, tmp_path: Path
) -> None:
    bundle = _forged(reference_bundles, tmp_path)
    assert validate_bundle(bundle).errors == []
    result = _rescore(bundle)
    assert result.exit_code == 1, result.output
    assert _last(result) == "RESCORE: DIFFER"


def test_a_verdict_altered_in_evaluations_json_alone_stops_at_the_structural_layer(
    reference_bundles: ReferenceBundles, tmp_path: Path
) -> None:
    """Altered in evaluations.json alone, the three records disagree and
    validate-bundle fails it (C1): the structural layer runs first
    (ADR-0005 D7), so rescore cannot rescore it -- never a MATCH."""
    bundle = reference_bundles["seed"].copy(tmp_path / "one")
    FLIPS["evaluations.json"](bundle)
    write_manifest(bundle)
    assert any("disagree" in e for e in validate_bundle(bundle).errors)
    result = _rescore(bundle)
    assert result.exit_code == 2, result.output
    assert "RESCORE: CANNOT RESCORE -- the bundle does not validate" in result.output


# (b) A one-byte change to a .py or .yaml file of the package yields
# CODE_CHANGED (amended at acceptance: the code is identified by
# package_sha256).


@pytest.mark.parametrize("suffix", [".py", ".yaml"])
def test_a_one_byte_package_change_is_code_changed(
    reference_bundles: ReferenceBundles,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
) -> None:
    package = tmp_path / "agentevalops"
    shutil.copytree(
        package_dir(), package, ignore=shutil.ignore_patterns("__pycache__")
    )
    target = sorted(package.rglob(f"*{suffix}"))[-1]
    target.write_bytes(target.read_bytes() + b" ")
    monkeypatch.setattr(
        rescore_module, "package_sha256", lambda: package_sha256(package)
    )
    _code_changed(_rescore(reference_bundles["known-bad"].path), "package_sha256")


# (b) The golden fixtures assert the verdict line under CODE_CHANGED, not
# MATCH.


@pytest.mark.parametrize("name", GOLDEN)
def test_the_golden_fixtures_assert_the_verdict_line(name: str) -> None:
    lines = _rescore(FIXTURES / name).output.splitlines()
    (verdict,) = [line for line in lines if line.startswith("Verdicts: ")]
    assert verdict.startswith("Verdicts: reproduce -- 10 evaluation(s) over 4 ")


# (b) Rescore compares report.md and evidence_report.md byte for byte.


@pytest.mark.parametrize("name", ["report.md", "evidence_report.md"])
def test_one_byte_in_either_report_is_a_differ(
    reference_bundles: ReferenceBundles, tmp_path: Path, name: str
) -> None:
    bundle = reference_bundles["toy"].copy(tmp_path / "report")
    (bundle / name).write_bytes((bundle / name).read_bytes() + b" ")
    write_manifest(bundle)
    result = _rescore(bundle)
    assert result.exit_code == 1, result.output
    assert f"Reports:  {name} differs at line " in result.output


# (b) The pyproject.toml diff adds no runtime dependency.


def test_no_runtime_dependency_was_added() -> None:
    """The runtime dependencies by name, as at 422bf40 (ADR-0008 accepted,
    before R29 was built): signing calls ssh-keygen, and rescore the
    package's own code. A floor raised later is not a dependency added."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    names = {canonicalize_name(Requirement(d).name) for d in project["dependencies"]}
    assert names == {"typer", "pyyaml"}
