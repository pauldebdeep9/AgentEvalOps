"""Rescore: a bundle's verdicts derived again from its own records with the
running code, and compared (docs/adr/0008 R1-R6; R29 (b2)).

MATCH is tested on bundles this code wrote in the session (T1); the golden
fixtures, which an older commit wrote, assert only the verdict line. The
outcome is decided by R3's results and both reports' bytes; CODE_CHANGED by
R2's five items, with the verdict line still reported. Rescore never writes
into a bundle and reads no clock.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from reference_bundles import (
    KINDS,
    Reference,
    ReferenceBundles,
    edited_seed_data,
    registry_with_broken_pack,
)
from test_bundle_0_6 import _CLAIM
from test_signing import _allowed, _key, home  # noqa: F401 -- home is a fixture
from test_three_records import EVALUATOR, FLIPS, REP, TASK
from typer.testing import CliRunner

from agentevalops import registry as registry_module
from agentevalops.bundles import manifest as manifest_module
from agentevalops.bundles import serializers as serializers_module
from agentevalops.bundles import writer as writer_module
from agentevalops.bundles.manifest import write_manifest
from agentevalops.bundles.provenance import package_dir, package_sha256
from agentevalops.bundles.validator import validate_bundle
from agentevalops.catalogue import load_catalogue
from agentevalops.cli import app
from agentevalops.core import schemas as schemas_module
from agentevalops.domain_packs.seed import SeedDomainPack
from agentevalops.evaluators.deterministic import DeterministicEvaluator
from agentevalops.plans import builder as builder_module
from agentevalops.registry import KIND_DOMAIN_PACK, KIND_EVALUATOR
from agentevalops.replay import rescore as rescore_module
from agentevalops.replay.rescore import REPORTS

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/bundles"
GOLDEN = ["seed-narrow-0.6", "seed-narrow-known-bad-0.6"]
REPRODUCE = "Verdicts: reproduce -- "


def _rescore(bundle: Path, *extra: str) -> Any:
    return CliRunner().invoke(app, ["rescore", "--bundle", str(bundle), *extra])


def _last(result: Any) -> str:
    return str(result.output).rstrip().splitlines()[-1]


def _hashes(bundle: Path) -> dict[str, str]:
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in bundle.iterdir()
    }


def _with_broken_pack(
    reference_bundles: ReferenceBundles, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The edited pack the carried-preflight run used, registered under its
    own name, so that the run rebuilds as it ran."""
    monkeypatch.setattr(
        registry_module,
        "default_registry",
        registry_with_broken_pack(reference_bundles.broken_pack_data),
    )


# ---------------------------------------------------------------------------
# MATCH: every bundle this code wrote
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_a_bundle_this_code_wrote_rescores_as_a_match(
    reference_bundles: ReferenceBundles, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    if kind == "carried-preflight":
        _with_broken_pack(reference_bundles, monkeypatch)
    result = _rescore(reference_bundles[kind].path)
    assert result.exit_code == 0, result.output
    assert _last(result) == "RESCORE: MATCH"
    assert "Code:    unchanged" in result.output
    assert any(line.startswith(REPRODUCE) for line in result.output.splitlines())
    assert "Files:    every recomputed file is byte-identical" in result.output


def test_a_toy_run_records_no_pack_and_says_so(
    reference_bundles: ReferenceBundles,
) -> None:
    out = _rescore(reference_bundles["toy"].path).output
    assert "pack scenarios_sha256  not compared: no pack recorded" in out
    assert "pack data_sha256       not compared: no pack recorded" in out


def test_what_is_carried_is_listed(
    reference_bundles: ReferenceBundles, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run stopped by its infrastructure is made again from the reason it
    records, through the run's own ``stopped``; one stopped by its
    scenario's preflight is carried as recorded, and named."""
    infra = _rescore(reference_bundles["carried-infra"].path).output
    assert (
        "task run PC5/0, stopped by its infrastructure: the reason it records, "
        "its result made again from it"
    ) in infra
    assert "the agent's output of 3 task run(s), from their agent.terminal" in infra
    _with_broken_pack(reference_bundles, monkeypatch)
    preflight = _rescore(reference_bundles["carried-preflight"].path).output
    assert (
        "the preflight of 4 scenario(s), as summary.json records it; failed: TE3"
        in (preflight)
    )
    assert (
        "task run TE3/0, stopped before the agent finished (environment_defect): "
        "its recorded result"
    ) in preflight
    assert "Verdicts: reproduce -- 7 evaluation(s) over 3 task run(s)" in preflight


# ---------------------------------------------------------------------------
# DIFFER: the careful forger, and a report edited alone
# ---------------------------------------------------------------------------


def _forged(reference_bundles: ReferenceBundles, tmp_path: Path) -> Path:
    """One verdict flipped in all three of its records, the manifest
    re-sealed: validate-bundle cannot see it (ADR-0008 C1)."""
    bundle = reference_bundles["seed"].copy(tmp_path / "forged")
    for flip in FLIPS.values():
        flip(bundle)
    write_manifest(bundle)
    return bundle


def test_the_careful_forger_passes_validation_and_rescore_names_the_verdict(
    reference_bundles: ReferenceBundles, tmp_path: Path
) -> None:
    bundle = _forged(reference_bundles, tmp_path)
    assert validate_bundle(bundle).errors == []
    result = _rescore(bundle)
    assert result.exit_code == 1, result.output
    assert _last(result) == "RESCORE: DIFFER"
    assert "Verdicts: do not reproduce -- " in result.output
    assert f"  ! {TASK}/{REP} {EVALUATOR}: passed recorded " in result.output


@pytest.mark.parametrize("name", REPORTS)
def test_a_report_edited_alone_differs_at_its_first_changed_line(
    reference_bundles: ReferenceBundles, tmp_path: Path, name: str
) -> None:
    bundle = reference_bundles["seed"].copy(tmp_path / "edited")
    lines = (bundle / name).read_text("utf-8").split("\n")
    lines[5] = lines[5] + " (edited)"
    (bundle / name).write_text("\n".join(lines), encoding="utf-8")
    write_manifest(bundle)
    assert validate_bundle(bundle).errors == []
    result = _rescore(bundle)
    assert result.exit_code == 1, result.output
    assert _last(result) == "RESCORE: DIFFER"
    assert f"Reports:  {name} differs at line 6" in result.output
    assert any(line.startswith(REPRODUCE) for line in result.output.splitlines())


# ---------------------------------------------------------------------------
# CODE_CHANGED: each of R2's five items, with the verdict line
# ---------------------------------------------------------------------------


def _code_changed(result: Any, item: str, under: str = "reproduce") -> None:
    """CODE_CHANGED on *item*, still reporting whether the verdicts
    reproduce under the running code (R2)."""
    assert result.exit_code == 3, result.output
    assert "Code:    changed" in result.output
    assert any(
        line.startswith(f"  {item}") and " differs: recorded " in line
        for line in result.output.splitlines()
    ), result.output
    assert any(
        line.startswith(f"Verdicts: {under} -- ") for line in result.output.splitlines()
    ), result.output
    assert _last(result) == (
        f"RESCORE: CODE_CHANGED -- the verdicts {under} under the running code"
    )


@pytest.mark.parametrize("suffix", [".py", ".yaml"])
def test_a_one_byte_change_to_a_package_file_is_code_changed(
    reference_bundles: ReferenceBundles,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
) -> None:
    """The running package is a copy with one byte added to one file."""
    package = tmp_path / "agentevalops"
    shutil.copytree(
        package_dir(), package, ignore=shutil.ignore_patterns("__pycache__")
    )
    target = sorted(package.rglob(f"*{suffix}"))[0]
    target.write_bytes(target.read_bytes() + b"#")
    bundle = reference_bundles["seed"].path
    recorded = json.loads((bundle / "metadata.json").read_text("utf-8"))
    assert package_sha256(package) != recorded["package_sha256"]
    monkeypatch.setattr(
        rescore_module, "package_sha256", lambda: package_sha256(package)
    )
    result = _rescore(bundle)
    _code_changed(result, "package_sha256")
    assert _last(result) == (
        "RESCORE: CODE_CHANGED -- the verdicts reproduce under the running code"
    )


def test_a_pack_data_change_is_code_changed(
    reference_bundles: ReferenceBundles,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def edit(doc: dict[str, Any]) -> None:
        doc["scenarios"][0]["description"] += " Edited."

    data = edited_seed_data(tmp_path, "scenarios.yaml", edit)
    fresh = registry_module.build_default_registry()
    fresh.register(KIND_DOMAIN_PACK, "seed", lambda: SeedDomainPack(data), replace=True)
    monkeypatch.setattr(registry_module, "default_registry", fresh)
    result = _rescore(reference_bundles["seed"].path)
    _code_changed(result, "pack scenarios_sha256")
    _code_changed(result, "pack data_sha256")


def test_a_catalogue_change_is_code_changed(
    reference_bundles: ReferenceBundles,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shipped = [
        Path(str(f)) for f in (package_dir() / "catalogue" / "data").glob("*.yaml")
    ]
    copies = []
    for path in sorted(shipped):
        copy = tmp_path / path.name
        copy.write_bytes(path.read_bytes())
        copies.append(copy)
    copies[0].write_bytes(copies[0].read_bytes() + b"\n# edited\n")
    monkeypatch.setattr(
        rescore_module, "load_catalogue", lambda: load_catalogue(copies)
    )
    _code_changed(_rescore(reference_bundles["seed"].path), "catalogue files")


def test_a_changed_evaluator_id_is_code_changed(
    reference_bundles: ReferenceBundles, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Renamed(DeterministicEvaluator):
        evaluator_id = "deterministic-v2"

    fresh = registry_module.build_default_registry()
    fresh.register(KIND_EVALUATOR, "deterministic", Renamed, replace=True)
    monkeypatch.setattr(registry_module, "default_registry", fresh)
    result = _rescore(reference_bundles["seed"].path)
    _code_changed(result, "evaluator ids")
    assert '"deterministic": "deterministic-v2"' in result.output


# ---------------------------------------------------------------------------
# Cannot rescore (exit 2)
# ---------------------------------------------------------------------------


def test_a_format_0_5_bundle_cannot_be_rescored() -> None:
    result = _rescore(FIXTURES / "seed-narrow-0.5")
    assert result.exit_code == 2, result.output
    assert (
        "RESCORE: CANNOT RESCORE -- format 0.5 predates package_sha256; rescore "
        "needs format 0.6"
    ) in result.output


def test_an_invalid_bundle_cannot_be_rescored(
    reference_bundles: ReferenceBundles, tmp_path: Path
) -> None:
    bundle = reference_bundles["toy"].copy(tmp_path / "invalid")
    (bundle / "summary.json").write_text("{", encoding="utf-8")
    result = _rescore(bundle)
    assert result.exit_code == 2, result.output
    assert "RESCORE: CANNOT RESCORE -- the bundle does not validate" in result.output


def test_a_benchmark_that_is_not_installed_cannot_be_rescored(
    reference_bundles: ReferenceBundles, tmp_path: Path
) -> None:
    bundle = reference_bundles["toy"].copy(tmp_path / "elsewhere")
    config = json.loads((bundle / "config.json").read_text("utf-8"))
    config["benchmark_id"] = "no-such-benchmark"
    (bundle / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    write_manifest(bundle)
    assert validate_bundle(bundle).errors == []
    result = _rescore(bundle)
    assert result.exit_code == 2, result.output
    assert (
        "'no-such-benchmark'" in result.output and "is not installed" in result.output
    )


def test_a_missing_bundle_cannot_be_rescored(tmp_path: Path) -> None:
    result = _rescore(tmp_path / "nothing")
    assert result.exit_code == 2
    assert "RESCORE: CANNOT RESCORE -- bundle path not found" in result.output


# ---------------------------------------------------------------------------
# The golden fixtures: the verdict line only (ADR-0008 T1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", GOLDEN)
def test_the_golden_fixtures_verdicts_reproduce(name: str) -> None:
    """Written at 40da4b7: every later commit changes the code, so only the
    verdict line is asserted, never the outcome."""
    result = _rescore(FIXTURES / name)
    assert (
        "Verdicts: reproduce -- 10 evaluation(s) over 4 task run(s), the summary's "
        "counts and pass^k, the policy verdict and the controls"
    ) in result.output.splitlines()


# ---------------------------------------------------------------------------
# Read-only, outside the bundle, and no clock
# ---------------------------------------------------------------------------


def test_rescore_never_writes_into_the_bundle(
    reference_bundles: ReferenceBundles, tmp_path: Path
) -> None:
    bundle = reference_bundles["seed"].path
    before = _hashes(bundle)
    out, report = tmp_path / "out", tmp_path / "rescore.json"
    result = _rescore(bundle, "--json", str(report), "--output", str(out))
    assert result.exit_code == 0, result.output
    assert _hashes(bundle) == before
    written = sorted(p.name for p in out.iterdir())
    assert "manifest.json" not in written and "test_plan.json" not in written
    for name in written:
        assert (out / name).read_bytes() == (bundle / name).read_bytes(), name
    doc = json.loads(report.read_text("utf-8"))
    assert doc["outcome"] == "MATCH" and doc["exit_code"] == 0
    assert doc["layer"] == "evaluation" and doc["verdicts_reproduce"] is True


@pytest.mark.parametrize("flag", ["--json", "--output"])
@pytest.mark.parametrize("inner", ["sub/target", "."])
def test_a_target_inside_the_bundle_is_refused(
    reference_bundles: ReferenceBundles, tmp_path: Path, flag: str, inner: str
) -> None:
    bundle = reference_bundles["toy"].copy(tmp_path / "bundle")
    before = _hashes(bundle)
    result = _rescore(bundle, flag, str(bundle / inner))
    assert result.exit_code == 2, result.output
    assert f"RESCORE: CANNOT RESCORE -- {flag} " in result.output
    assert "is inside the bundle" in result.output
    assert _hashes(bundle) == before and not (bundle / "sub").exists()


def test_an_output_folder_that_is_not_empty_is_refused(
    reference_bundles: ReferenceBundles, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "mine.txt").write_text("keep", encoding="utf-8")
    result = _rescore(reference_bundles["toy"].path, "--output", str(out))
    assert result.exit_code == 2
    assert "must be a new or empty folder" in result.output
    assert [p.name for p in out.iterdir()] == ["mine.txt"]


class _Stopped(datetime):
    """A clock that must not be read."""

    @classmethod
    def now(cls, tz: Any = None) -> Any:
        raise AssertionError("rescore read the clock")


def test_two_rescores_agree_and_no_clock_is_read(
    reference_bundles: ReferenceBundles,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """Every clock the package reads, made to fail: rescore still gives the
    same text, JSON and files. The typed loader's cache of type hints,
    warmed by the first rescore, is cleared afterwards, so the stand-in
    clock cannot outlive the test."""
    bundle = reference_bundles["known-bad"].path

    def once(label: str) -> tuple[str, str, dict[str, bytes]]:
        out, report = tmp_path / f"out-{label}", tmp_path / f"{label}.json"
        result = _rescore(bundle, "--json", str(report), "--output", str(out))
        assert result.exit_code == 0, result.output
        files = {p.name: p.read_bytes() for p in out.iterdir()}
        return result.output, report.read_text("utf-8"), files

    first, second = once("first"), once("second")
    request.addfinalizer(serializers_module._hints.cache_clear)
    for module in (schemas_module, builder_module, manifest_module, writer_module):
        monkeypatch.setattr(module, "datetime", _Stopped)
    stopped_clock = once("no-clock")
    assert first == second == stopped_clock


# ---------------------------------------------------------------------------
# What a MATCH cannot see, and a signature can (ADR-0008 E)
# ---------------------------------------------------------------------------


def test_a_consistent_edit_of_the_records_rescores_as_a_match_and_breaks_a_signature(
    reference_bundles: ReferenceBundles,
    home: Path,  # noqa: F811
) -> None:
    """A recorded tool call edited, every derived file brought into line from
    rescore's own --output, the manifest re-sealed: validate-bundle passes
    and rescore says MATCH -- both claims paragraphs say so -- and only the
    signature made before the edit shows it."""
    bundle = reference_bundles["seed"].copy(home / "bundle")
    key = _key(home, "alice")
    signed = CliRunner().invoke(
        app, ["sign", "--bundle", str(bundle), "--key", str(key)]
    )
    assert signed.exit_code == 0, signed.output
    allowed = _allowed(home, alice=key)
    assert validate_bundle(bundle, allowed_signers=allowed).errors == []

    path = bundle / "tool_calls.jsonl"
    records = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
    for record in records:
        if (record["task_id"], record["tool_name"]) == ("TE1", "status_update"):
            record["arguments"]["decision"] = "rejected"
            record["arguments"]["reason_codes"] = ["INC-OVER"]
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
        encoding="utf-8",
    )
    write_manifest(bundle)
    derived = home / "derived"
    moved = _rescore(bundle, "--output", str(derived))
    assert moved.exit_code == 1, moved.output
    assert "  ! TE1/0 " in moved.output

    for file in derived.iterdir():
        (bundle / file.name).write_bytes(file.read_bytes())
    write_manifest(bundle)
    assert validate_bundle(bundle).errors == []
    assert _rescore(bundle).exit_code == 0
    unsigned = validate_bundle(bundle, allowed_signers=allowed)
    assert any(e.startswith("Signature not verified") for e in unsigned.errors)


# ---------------------------------------------------------------------------
# The wording guard (ADR-0005 D10, ADR-0007 D7) over rescore's output
# ---------------------------------------------------------------------------


def test_rescore_output_makes_no_claim(
    reference_bundles: ReferenceBundles,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed: Reference = reference_bundles["seed"]
    outputs = []
    for name, bundle in (
        ("match", seed.path),
        ("differ", _forged(reference_bundles, tmp_path)),
        ("legacy", FIXTURES / "seed-narrow-0.5"),
        ("golden", FIXTURES / GOLDEN[1]),
    ):
        report = tmp_path / f"{name}.json"
        result = _rescore(bundle, "--json", str(report))
        outputs.append(result.output)
        if report.exists():
            outputs.append(report.read_text("utf-8"))
    for text in outputs:
        assert not _CLAIM.search(text), text
        assert "ai verify" not in text.lower()


# ---------------------------------------------------------------------------
# What decides, one result at a time; what is only shown (R3)
# ---------------------------------------------------------------------------


def _resealed(
    reference_bundles: ReferenceBundles, tmp_path: Path, name: str, change: Any
) -> Path:
    bundle = reference_bundles["seed"].copy(tmp_path / "edited")
    doc = json.loads((bundle / name).read_text("utf-8"))
    change(doc)
    (bundle / name).write_text(json.dumps(doc, indent=2), encoding="utf-8")
    write_manifest(bundle)
    assert validate_bundle(bundle).errors == []
    return bundle


def _control_state(doc: dict[str, Any]) -> None:
    doc["controls"][0]["state"] = "uncovered"


def _policy_verdict(doc: dict[str, Any]) -> None:
    doc["verdict"] = "fail"


def _summary_pass_k(doc: dict[str, Any]) -> None:
    doc["pass_k"][0]["passed"], doc["pass_k"][0]["failed"] = 0, 1


@pytest.mark.parametrize(
    ("name", "change", "named"),
    [
        ("controls.json", _control_state, ": state recorded "),
        ("policy.json", _policy_verdict, "  ! policy: verdict recorded "),
        ("summary.json", _summary_pass_k, "  ! summary: pass_k recorded "),
    ],
)
def test_each_result_r3_names_decides_on_its_own(
    reference_bundles: ReferenceBundles,
    tmp_path: Path,
    name: str,
    change: Any,
    named: str,
) -> None:
    result = _rescore(_resealed(reference_bundles, tmp_path, name, change))
    assert result.exit_code == 1, result.output
    assert named in result.output
    assert _last(result) == "RESCORE: DIFFER"


def test_notes_and_formatting_are_shown_and_do_not_decide(
    reference_bundles: ReferenceBundles, tmp_path: Path
) -> None:
    """A note edited in both records that carry it, and evaluations.json
    re-indented: listed, and still a MATCH."""
    bundle = reference_bundles["seed"].copy(tmp_path / "noted")
    for name in ("evaluations.json", "summary.json"):
        path = bundle / name
        doc = json.loads(path.read_text("utf-8"))
        entries = (
            doc if name == "evaluations.json" else doc["task_results"][0]["evaluations"]
        )
        entries[0]["notes"] = "rewritten"
        path.write_text(json.dumps(doc, indent=4), encoding="utf-8")
    write_manifest(bundle)
    assert validate_bundle(bundle).errors == []
    result = _rescore(bundle)
    assert result.exit_code == 0, result.output
    assert "  note: TE1/0 deterministic-v1: notes recorded rewritten, rescored " in (
        result.output
    )
    assert "Files:    differing in bytes: evaluations.json, summary.json" in (
        result.output
    )


# ---------------------------------------------------------------------------
# CODE_CHANGED still names the verdicts that moved (R2)
# ---------------------------------------------------------------------------


def test_code_changed_still_names_the_verdicts_that_moved(
    reference_bundles: ReferenceBundles,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _forged(reference_bundles, tmp_path)
    monkeypatch.setattr(rescore_module, "package_sha256", lambda: "0" * 64)
    result = _rescore(bundle)
    _code_changed(result, "package_sha256", under="do not reproduce")
    assert f"  ! {TASK}/{REP} {EVALUATOR}: passed recorded " in result.output


def test_an_evaluator_whose_id_is_its_kind_is_still_compared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Written with an evaluator whose id equals its kind, rescored with a
    new id for it: R2's fifth item sees it."""

    class Plain(DeterministicEvaluator):
        evaluator_id = "deterministic"

    class Renamed(DeterministicEvaluator):
        evaluator_id = "deterministic-v2"

    written = registry_module.build_default_registry()
    written.register(KIND_EVALUATOR, "deterministic", Plain, replace=True)
    monkeypatch.setattr(registry_module, "default_registry", written)
    bundle = tmp_path / "plain"
    run = CliRunner().invoke(
        app,
        [
            "run",
            "--config",
            str(ROOT / "configs/toy_smoke.yaml"),
            "--output",
            str(bundle),
        ],
    )
    assert run.exit_code == 0, run.output
    assert _rescore(bundle).exit_code == 0
    rescored = registry_module.build_default_registry()
    rescored.register(KIND_EVALUATOR, "deterministic", Renamed, replace=True)
    monkeypatch.setattr(registry_module, "default_registry", rescored)
    _code_changed(_rescore(bundle), "evaluator ids")


# ---------------------------------------------------------------------------
# An undeclared run on a pack: the pack is compared, never skipped
# ---------------------------------------------------------------------------


def _packed(data: Path | None) -> registry_module.Registry:
    from agentevalops.benchmarks.agentic_baseline import AgenticBaselineBenchmark
    from agentevalops.registry import KIND_BENCHMARK

    fresh = registry_module.build_default_registry()
    fresh.register(
        KIND_BENCHMARK,
        "packed-baseline",
        lambda scenarios=None: AgenticBaselineBenchmark(
            SeedDomainPack(data), scenarios=scenarios
        ),
    )
    return fresh


def test_an_undeclared_run_on_a_pack_has_its_pack_compared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fast_yaml

    monkeypatch.setattr(registry_module, "default_registry", _packed(None))
    doc = fast_yaml.safe_load(
        (ROOT / "configs/agentic_baseline_mock.yaml").read_text("utf-8")
    )
    del doc["system"]
    doc["benchmark_id"] = "packed-baseline"
    doc["benchmark_options"] = {"scenarios": ["TE1", "TC1"]}
    config = tmp_path / "run.yaml"
    config.write_text(fast_yaml.safe_dump(doc), encoding="utf-8")
    bundle = tmp_path / "undeclared"
    run = CliRunner().invoke(
        app, ["run", "--config", str(config), "--output", str(bundle)]
    )
    assert run.exit_code == 0, run.output
    same = _rescore(bundle)
    assert same.exit_code == 0, same.output
    assert "  pack scenarios_sha256  same" in same.output
    assert "  pack data_sha256       same" in same.output

    def edit(doc: dict[str, Any]) -> None:
        doc["scenarios"][0]["description"] += " Edited."

    monkeypatch.setattr(
        registry_module,
        "default_registry",
        _packed(edited_seed_data(tmp_path, "scenarios.yaml", edit)),
    )
    moved = _rescore(bundle)
    _code_changed(moved, "pack scenarios_sha256")
    _code_changed(moved, "pack data_sha256")


def test_the_json_result_says_no_pack_was_recorded(
    reference_bundles: ReferenceBundles, tmp_path: Path
) -> None:
    report = tmp_path / "toy.json"
    assert _rescore(reference_bundles["toy"].path, "--json", str(report)).exit_code == 0
    items = {i["item"]: i for i in json.loads(report.read_text("utf-8"))["provenance"]}
    for name in ("pack scenarios_sha256", "pack data_sha256"):
        assert items[name]["status"] == "not compared"
        assert items[name]["note"] == "not compared: no pack recorded"


# ---------------------------------------------------------------------------
# Cannot rescore: never a traceback, never DIFFER's exit
# ---------------------------------------------------------------------------


def test_a_pack_that_is_not_installed_cannot_be_rescored(
    reference_bundles: ReferenceBundles,
) -> None:
    result = _rescore(reference_bundles["carried-preflight"].path)
    assert result.exit_code == 2, result.output
    assert "seed-te3-broken" in result.output and "not installed" in result.output


def test_a_record_without_its_time_cannot_be_rescored(
    reference_bundles: ReferenceBundles, tmp_path: Path
) -> None:
    """Loaded, it would take the clock's time; rescore reads no clock."""
    bundle = reference_bundles["seed"].copy(tmp_path / "timeless")
    path = bundle / "tool_calls.jsonl"
    lines = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
    del lines[0]["timestamp"]
    path.write_text("".join(json.dumps(r) + "\n" for r in lines), encoding="utf-8")
    write_manifest(bundle)
    assert validate_bundle(bundle).errors == []
    result = _rescore(bundle)
    assert result.exit_code == 2, result.output
    assert "tool_calls.jsonl line 1 records no timestamp" in result.output


@pytest.mark.parametrize(
    ("name", "edit"),
    [
        ("metadata.json", "created_at"),
        ("replay_command.txt", "not utf-8"),
    ],
)
def test_an_error_after_validation_is_cannot_rescore(
    reference_bundles: ReferenceBundles, tmp_path: Path, name: str, edit: str
) -> None:
    """Fail-first: these raised a traceback and exited 1, DIFFER's code."""
    bundle = reference_bundles["seed"].copy(tmp_path / "broken")
    path = bundle / name
    if edit == "created_at":
        doc = json.loads(path.read_text("utf-8"))
        doc["created_at"] = "not a date"
        path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    else:
        path.write_bytes(path.read_bytes() + b"\xff")
    write_manifest(bundle)
    assert validate_bundle(bundle).errors == []
    report = tmp_path / "result.json"
    result = _rescore(bundle, "--json", str(report))
    assert result.exit_code == 2, result.output
    assert "RESCORE: CANNOT RESCORE -- " in result.output
    assert "Traceback" not in result.output
    doc = json.loads(report.read_text("utf-8"))
    assert doc["outcome"] == "CANNOT_RESCORE" and doc["exit_code"] == 2


def test_rescore_never_builds_or_runs_a_runner(
    reference_bundles: ReferenceBundles, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentevalops.registry import KIND_RUNNER

    def no_runner(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("rescore built a runner")

    fresh = registry_module.build_default_registry()
    fresh.register(KIND_RUNNER, "mock", no_runner, replace=True)
    monkeypatch.setattr(registry_module, "default_registry", fresh)
    result = _rescore(reference_bundles["seed"].path)
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# Stopped or finished, from the records (R6)
# ---------------------------------------------------------------------------


def test_a_relabel_with_its_terminal_event_deleted_is_carried_and_named(
    reference_bundles: ReferenceBundles, tmp_path: Path
) -> None:
    """The known limit, which the MATCH paragraph describes: a finished run
    relabelled as stopped by its configuration, with its agent.terminal
    event deleted and every derived record made to match, validates; with
    no terminal event, rescore carries it -- named -- and says MATCH. Only a
    signature made when the bundle was written shows the edit."""
    from test_carried_results import _relabel_as_stopped

    bundle = reference_bundles["seed"].copy(tmp_path / "relabelled")
    _relabel_as_stopped(bundle, "TE1", drop_terminal=True)
    out = tmp_path / "derived"
    first = _rescore(bundle, "--output", str(out))
    for file in out.iterdir():
        (bundle / file.name).write_bytes(file.read_bytes())
    write_manifest(bundle)
    assert validate_bundle(bundle).errors == []
    result = _rescore(bundle)
    assert result.exit_code == 0, (first.output, result.output)
    assert (
        "task run TE1/0, stopped before the agent finished (configuration): its "
        "recorded result"
    ) in result.output


# ---------------------------------------------------------------------------
# Writing outside the bundle: by file identity, never through an inode
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flag", ["--json", "--output"])
def test_a_symlink_into_the_bundle_is_refused(
    reference_bundles: ReferenceBundles, tmp_path: Path, flag: str
) -> None:
    bundle = reference_bundles["toy"].copy(tmp_path / "bundle")
    link = tmp_path / "link"
    link.symlink_to(bundle, target_is_directory=True)
    before = _hashes(bundle)
    result = _rescore(bundle, flag, str(link / "target"))
    assert result.exit_code == 2, result.output
    assert "is inside the bundle" in result.output
    assert _hashes(bundle) == before


@pytest.mark.parametrize("flag", ["--json", "--output"])
def test_a_dot_dot_path_into_the_bundle_is_refused(
    reference_bundles: ReferenceBundles, tmp_path: Path, flag: str
) -> None:
    bundle = reference_bundles["toy"].copy(tmp_path / "bundle")
    (tmp_path / "beside").mkdir()
    result = _rescore(bundle, flag, str(tmp_path / "beside" / ".." / "bundle" / "x"))
    assert result.exit_code == 2, result.output
    assert "is inside the bundle" in result.output


@pytest.mark.parametrize("flag", ["--json", "--output"])
def test_a_differently_cased_path_into_the_bundle_is_refused(
    reference_bundles: ReferenceBundles, tmp_path: Path, flag: str
) -> None:
    """Fail-first: compared by spelling, BUNDLE/x got past the check on a
    case-insensitive disk and rescore wrote into the bundle."""
    bundle = reference_bundles["toy"].copy(tmp_path / "Bundle")
    if not (tmp_path / "BUNDLE").exists():
        pytest.skip("this disk is case-sensitive: BUNDLE is another folder")
    before = _hashes(bundle)
    result = _rescore(bundle, flag, str(tmp_path / "BUNDLE" / "summary.json"))
    assert result.exit_code == 2, result.output
    assert "is inside the bundle" in result.output
    assert _hashes(bundle) == before


def test_a_hard_link_to_a_bundle_file_is_never_written_through(
    reference_bundles: ReferenceBundles, tmp_path: Path
) -> None:
    """Fail-first: --json rewrote the inode it shared with report.md."""
    bundle = reference_bundles["toy"].copy(tmp_path / "bundle")
    link = tmp_path / "result.json"
    link.hardlink_to(bundle / "report.md")
    before = _hashes(bundle)
    result = _rescore(bundle, "--json", str(link))
    assert result.exit_code == 2, result.output
    assert "is one of the bundle's files by another name" in result.output
    assert _hashes(bundle) == before


def test_write_replacing_replaces_a_hard_link_instead_of_writing_through(
    tmp_path: Path,
) -> None:
    from agentevalops.replay.rescore import write_replacing

    original = tmp_path / "original"
    original.write_text("keep", encoding="utf-8")
    link = tmp_path / "link"
    link.hardlink_to(original)
    write_replacing(link, b"new")
    assert original.read_text("utf-8") == "keep"
    assert link.read_bytes() == b"new"


@pytest.mark.parametrize("where", ["inside-output", "a-folder", "no-folder"])
def test_a_json_target_that_cannot_be_written_is_refused_before_rescoring(
    reference_bundles: ReferenceBundles, tmp_path: Path, where: str
) -> None:
    out = tmp_path / "out"
    target = {
        "inside-output": out / "result.json",
        "a-folder": tmp_path,
        "no-folder": tmp_path / "missing" / "result.json",
    }[where]
    result = _rescore(
        reference_bundles["toy"].path, "--output", str(out), "--json", str(target)
    )
    assert result.exit_code == 2, result.output
    assert "Verdicts:" not in result.output
    assert not out.exists()


def test_control_characters_in_the_bundle_reach_no_terminal(
    reference_bundles: ReferenceBundles, tmp_path: Path
) -> None:
    """A report line hiding the outcome line with a terminal escape is
    printed escaped."""
    bundle = reference_bundles["toy"].copy(tmp_path / "escaped")
    lines = (bundle / "report.md").read_text("utf-8").split("\n")
    lines[5] = "RESCORE: MATCH\x1b[8m"
    (bundle / "report.md").write_text("\n".join(lines), encoding="utf-8")
    write_manifest(bundle)
    result = _rescore(bundle)
    assert result.exit_code == 1, result.output
    assert "\x1b" not in result.output
    assert "recorded: RESCORE: MATCH\\x1b[8m" in result.output


class _Packless:
    """The packed benchmark, without its pack."""

    def __init__(self, scenarios: Any = None) -> None:
        from agentevalops.benchmarks.agentic_baseline import AgenticBaselineBenchmark

        self._inner = AgenticBaselineBenchmark(SeedDomainPack(), scenarios=scenarios)

    def __getattr__(self, name: str) -> Any:
        if name == "pack":
            raise AttributeError(name)
        return getattr(self._inner, name)


def _undeclared_run(tmp_path: Path, pack: Any, monkeypatch: pytest.MonkeyPatch) -> Path:
    import fast_yaml

    from agentevalops.benchmarks.agentic_baseline import AgenticBaselineBenchmark
    from agentevalops.registry import KIND_BENCHMARK

    fresh = registry_module.build_default_registry()
    fresh.register(
        KIND_BENCHMARK,
        "packed-baseline",
        lambda scenarios=None: AgenticBaselineBenchmark(pack(), scenarios=scenarios),
    )
    monkeypatch.setattr(registry_module, "default_registry", fresh)
    doc = fast_yaml.safe_load(
        (ROOT / "configs/agentic_baseline_mock.yaml").read_text("utf-8")
    )
    del doc["system"]
    doc["benchmark_id"] = "packed-baseline"
    doc["benchmark_options"] = {"scenarios": ["TE1"]}
    config = tmp_path / "run.yaml"
    config.write_text(fast_yaml.safe_dump(doc), encoding="utf-8")
    bundle = tmp_path / "bundle"
    run = CliRunner().invoke(
        app, ["run", "--config", str(config), "--output", str(bundle)]
    )
    assert run.exit_code == 0, run.output
    return bundle


def test_a_pack_with_no_data_hash_on_either_side_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pack that is not DataHashed, written and rescored: the one lookup
    of a pack's data hash, which the plan builder and rescore share, finds
    none on either side."""
    monkeypatch.setattr(builder_module, "pack_data_sha256", lambda pack: None)
    monkeypatch.setattr(rescore_module, "pack_data_sha256", lambda pack: None)
    bundle = _undeclared_run(tmp_path, SeedDomainPack, monkeypatch)
    plan = json.loads((bundle / "test_plan.json").read_text("utf-8"))
    assert plan["pack"]["data_sha256"] is None
    result = _rescore(bundle)
    assert result.exit_code == 0, result.output
    assert (
        "  pack data_sha256       not compared: neither pack has a data hash"
        in result.output
    )
    assert "  pack scenarios_sha256  same" in result.output


def test_a_recorded_pack_the_installed_benchmark_lacks_cannot_be_rescored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentevalops.registry import KIND_BENCHMARK

    bundle = _undeclared_run(tmp_path, SeedDomainPack, monkeypatch)
    packless = registry_module.build_default_registry()
    packless.register(KIND_BENCHMARK, "packed-baseline", _Packless)
    monkeypatch.setattr(registry_module, "default_registry", packless)
    result = _rescore(bundle)
    assert result.exit_code == 2, result.output
    assert (
        "the bundle records pack 'seed', but the installed benchmark" in result.output
    )
