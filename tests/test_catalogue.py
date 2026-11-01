"""Tests for the control catalogue (catalogue/loader.py; ROADMAP R22 part 3)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import fast_yaml
import pytest

from agentevalops.catalogue import CatalogueError, load_catalogue
from agentevalops.catalogue.loader import (
    BUNDLE_0_6_FILES,
    CLASSIFICATIONS,
    SCENARIO_CLASSES,
)
from agentevalops.core.errors import ConfigurationError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SHA = "a" * 64


def _source(**overrides: Any) -> dict[str, Any]:
    source: dict[str, Any] = {
        "id": "doc",
        "title": "A Test Document",
        "publisher": "Test Publisher",
        "version": "1.0",
        "published": "2026-01-15",
        "url": "https://example.test/doc.pdf",
        "sha256": _SHA,
        "retrieved_on": "2026-10-31",
        "ip_posture": "identifiers-and-own-labels",
    }
    source.update(overrides)
    return source


def _control(**overrides: Any) -> dict[str, Any]:
    control: dict[str, Any] = {
        "id": "doc:1.1",
        "kind": "section",
        "locator": "1.1",
        "label": "Our own label for section one",
        "links": [],
    }
    control.update(overrides)
    return control


def _write(
    tmp_path: Path,
    source: dict[str, Any],
    controls: list[dict[str, Any]],
    name: str = "doc.yaml",
) -> Path:
    path = tmp_path / name
    path.write_text(
        fast_yaml.safe_dump({"source": source, "controls": controls}), encoding="utf-8"
    )
    return path


# ---------------------------------------------------------------------------
# The shipped catalogue
# ---------------------------------------------------------------------------

#: Every entry R22 part (3) asks for.
_R22_CONTROLS = {
    "mgf-agentic:1.1.3",
    "mgf-agentic:1.2.2",
    "mgf-agentic:2.1.1",
    "mgf-agentic:2.2.2",
    "mgf-agentic:2.3.2",
    "mgf-agentic:2.3.3",
    "starter-kit:risk.hallucination-inaccuracy",
    "starter-kit:risk.bias-in-decisions",
    "starter-kit:risk.undesirable-content",
    "starter-kit:risk.data-leakage",
    "starter-kit:risk.adversarial-prompts",
}


def test_shipped_catalogue_loads_with_every_r22_entry() -> None:
    catalogue = load_catalogue()
    assert _R22_CONTROLS <= set(catalogue.controls)
    for control_id in _R22_CONTROLS:
        assert catalogue.control(control_id).label


def test_shipped_sources_are_pinned_to_the_retrieved_files() -> None:
    sources = load_catalogue().sources
    mgf, kit = sources["mgf-agentic"], sources["starter-kit"]
    assert (mgf.version, mgf.published, mgf.updated) == (
        "1.5",
        dt.date(2026, 5, 20),
        dt.date(2026, 6, 5),
    )
    assert mgf.sha256 == (
        "2636e19ff1c86e862394d2fc900592e97b83c04cc35e3c8443108114b7f1dfba"
    )
    assert (kit.version, kit.published, kit.date_precision) == (
        "1.0",
        dt.date(2026, 1, 1),
        "month",
    )
    assert kit.sha256 == (
        "4c89249eb2297fc557c50a7f92d26ec23055ab24fb6979e93a7361a2b8d94c94"
    )
    for source in (mgf, kit):
        assert source.retrieved_on == dt.date(2026, 10, 31)
        assert source.url.startswith("https://www.imda.gov.sg/")


def test_shipped_links_resolve() -> None:
    catalogue = load_catalogue()
    for control in catalogue.controls.values():
        for target in control.links:
            assert target in catalogue.controls


def test_unknown_control_id_lookup_raises() -> None:
    with pytest.raises(CatalogueError, match="unknown control id"):
        load_catalogue().control("doc:9.9")


def test_catalogue_error_is_a_configuration_error() -> None:
    assert issubclass(CatalogueError, ConfigurationError)


def test_valid_minimal_file_loads(tmp_path: Path) -> None:
    path = _write(tmp_path, _source(), [_control()])
    catalogue = load_catalogue([path])
    assert catalogue.control("doc:1.1").source == "doc"


# ---------------------------------------------------------------------------
# Guards (each was seen failing against a loader with no validation)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["title", "publisher", "version", "published", "url", "sha256", "retrieved_on"],
)
def test_unpinned_source_is_rejected(tmp_path: Path, field: str) -> None:
    """R22's exit test: a source missing any pin is refused."""
    path = _write(tmp_path, _source(**{field: ""}), [_control()])
    with pytest.raises(CatalogueError, match="not pinned"):
        load_catalogue([path])


@pytest.mark.parametrize(
    "overrides",
    [
        {"sha256": "abc123"},
        {"sha256": "A" * 64},
        {"url": "http://example.test/doc.pdf"},
        {"ip_posture": "anything-goes"},
        {"date_precision": "year"},
    ],
)
def test_badly_pinned_source_is_rejected(
    tmp_path: Path, overrides: dict[str, Any]
) -> None:
    path = _write(tmp_path, _source(**overrides), [_control()])
    with pytest.raises(CatalogueError):
        load_catalogue([path])


def test_unknown_source_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, _source(), [_control(source="other")])
    with pytest.raises(CatalogueError, match="unknown source"):
        load_catalogue([path])


def test_duplicate_control_id_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, _source(), [_control(), _control(label="Another")])
    with pytest.raises(CatalogueError, match="duplicate control id"):
        load_catalogue([path])


def test_duplicate_source_is_rejected(tmp_path: Path) -> None:
    first = _write(tmp_path, _source(), [_control()], name="a.yaml")
    second = _write(tmp_path, _source(), [_control(id="doc:1.2")], name="b.yaml")
    with pytest.raises(CatalogueError, match="duplicate source id"):
        load_catalogue([first, second])


@pytest.mark.parametrize("label", ["", "   ", None])
def test_empty_label_is_rejected(tmp_path: Path, label: str | None) -> None:
    path = _write(tmp_path, _source(), [_control(label=label)])
    with pytest.raises(CatalogueError, match="empty label"):
        load_catalogue([path])


@pytest.mark.parametrize(
    ("source_overrides", "control_overrides"),
    [
        ({}, {"id": "doc:ai-verify-1"}),
        ({"id": "aiverify", "title": "AI Verify Something"}, {"id": "aiverify:1"}),
        ({}, {"label": "Mapped to AI Verify"}),
    ],
)
def test_ai_verify_in_an_id_or_name_is_rejected(
    tmp_path: Path,
    source_overrides: dict[str, Any],
    control_overrides: dict[str, Any],
) -> None:
    """ADR-0005 D5: the name is not ours to use in identifiers or names."""
    path = _write(
        tmp_path, _source(**source_overrides), [_control(**control_overrides)]
    )
    with pytest.raises(CatalogueError, match="AI Verify"):
        load_catalogue([path])


@pytest.mark.parametrize(
    "label",
    [
        "Agent is compliant with the guidance",
        "Certified safe tool calling",
        "Certification of agents",
    ],
)
def test_compliance_claims_are_rejected(tmp_path: Path, label: str) -> None:
    """ADR-0005 D10: no certification or compliance claim, anywhere."""
    path = _write(tmp_path, _source(), [_control(label=label)])
    with pytest.raises(CatalogueError, match="claim"):
        load_catalogue([path])


@pytest.mark.parametrize(
    "overrides",
    [
        {"title": "The AI Verify Framework"},
        {"publisher": "AI Verify Foundation, Singapore"},
    ],
)
def test_ai_verify_in_a_source_title_or_publisher_loads(
    tmp_path: Path, overrides: dict[str, Any]
) -> None:
    """docs/adr/0007 D10, the guard's one exemption: a title and a
    publisher cite the publisher's own names (finding 7)."""
    path = _write(tmp_path, _source(**overrides), [_control()])
    assert load_catalogue([path]).sources["doc"]


def test_compliance_claim_in_a_source_title_is_still_rejected(
    tmp_path: Path,
) -> None:
    path = _write(tmp_path, _source(title="AI Verify Certified List"), [_control()])
    with pytest.raises(CatalogueError, match="claim"):
        load_catalogue([path])


def test_compliance_claim_in_a_source_field_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, _source(publisher="Certified Body"), [_control()])
    with pytest.raises(CatalogueError, match="claim"):
        load_catalogue([path])


def test_policy_compliance_as_a_category_name_is_allowed(tmp_path: Path) -> None:
    """'compliance' names a test category (MGF-Agentic §2.3.2); only claims
    ('compliant', 'certified') are refused."""
    path = _write(
        tmp_path, _source(), [_control(label="Test policy compliance and robustness")]
    )
    assert load_catalogue([path]).control("doc:1.1").label


@pytest.mark.parametrize(
    "control_id", ["doc", "Doc:1.1", "other:1.1", "doc:", "doc:1 1"]
)
def test_malformed_control_id_is_rejected(tmp_path: Path, control_id: str) -> None:
    path = _write(tmp_path, _source(), [_control(id=control_id)])
    with pytest.raises(CatalogueError, match="control id"):
        load_catalogue([path])


def test_unknown_link_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, _source(), [_control(links=["doc:9.9"])])
    with pytest.raises(CatalogueError, match="links to unknown"):
        load_catalogue([path])


def test_self_link_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, _source(), [_control(links=["doc:1.1"])])
    with pytest.raises(CatalogueError, match="links to itself"):
        load_catalogue([path])


def test_unknown_kind_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, _source(), [_control(kind="clause")])
    with pytest.raises(CatalogueError, match="kind"):
        load_catalogue([path])


def test_missing_locator_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, _source(), [_control(locator="")])
    with pytest.raises(CatalogueError, match="locator"):
        load_catalogue([path])


# ---------------------------------------------------------------------------
# File-shape errors
# ---------------------------------------------------------------------------


def test_file_without_source_block_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("controls: []\n", encoding="utf-8")
    with pytest.raises(CatalogueError, match="source"):
        load_catalogue([path])


def test_controls_must_be_a_list(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        fast_yaml.safe_dump({"source": _source(), "controls": {"a": 1}}),
        encoding="utf-8",
    )
    with pytest.raises(CatalogueError, match="list"):
        load_catalogue([path])


def test_each_control_must_be_a_mapping(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        fast_yaml.safe_dump({"source": _source(), "controls": ["doc:1.1"]}),
        encoding="utf-8",
    )
    with pytest.raises(CatalogueError, match="mapping"):
        load_catalogue([path])


def test_bad_date_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, _source(published="20 May 2026"), [_control()])
    with pytest.raises(CatalogueError, match="ISO date"):
        load_catalogue([path])


def test_yaml_dates_and_datetimes_are_accepted(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        _source(
            published=dt.date(2026, 1, 15),
            retrieved_on=dt.datetime(2026, 10, 31, 9, 0),
        ),
        [_control()],
    )
    source = load_catalogue([path]).sources["doc"]
    assert source.retrieved_on == dt.date(2026, 10, 31)


# ---------------------------------------------------------------------------
# The testing framework's 112 process entries (docs/adr/0007 D10; R28 (a))
# ---------------------------------------------------------------------------

_TF_FILE = (
    Path(__file__).resolve().parents[1]
    / "src/agentevalops/catalogue/data/testing-framework-2025.yaml"
)

#: docs/imda-alignment.md §2's counts.
_TF_COUNTS = {
    "direct": 2,
    "partial": 12,
    "supporting": 32,
    "later": 9,
    "organisational": 49,
    "traditional-only": 8,
}


def _tf_raw() -> dict[str, Any]:
    raw: dict[str, Any] = fast_yaml.safe_load(_TF_FILE.read_text(encoding="utf-8"))
    return raw


def _tf_write(tmp_path: Path, raw: dict[str, Any]) -> Path:
    path = tmp_path / "testing-framework.yaml"
    path.write_text(fast_yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


def _tf_entry(raw: dict[str, Any], locator: str) -> dict[str, Any]:
    entry: dict[str, Any] = next(
        c for c in raw["controls"] if c["locator"] == locator
    )
    return entry


def test_shipped_testing_framework_has_112_entries_in_the_review_classes() -> None:
    catalogue = load_catalogue()
    process = [
        c for c in catalogue.controls.values() if c.source == "testing-framework"
    ]
    assert len(process) == 112
    assert all(c.kind == "process" for c in process)
    found = {name: 0 for name in CLASSIFICATIONS}
    for control in process:
        found[control.classification] += 1
    assert found == _TF_COUNTS
    # The framework's own scoping and technical-test tags (§12 of the doc).
    assert sum(c.scope == "generative" for c in process) == 10
    assert sum(c.scope == "traditional" for c in process) == 8
    assert {c.locator for c in process if c.technical_test} == {
        "2.1.1",
        "4.1.1",
        "6.1.1",
        "7.1.1",
        "7.2.1",
    }
    assert {c.locator for c in process if c.classification in SCENARIO_CLASSES} == {
        "1.1.3", "1.1.4", "3.6.1", "3.7.1", "3.12.1", "4.1.1", "4.3.1",
        "4.10.1", "5.9.1", "6.1.1", "7.2.1", "8.3.2", "9.9.2", "10.3.2",
    }  # fmt: skip


def test_shipped_principles_are_this_projects_short_labels() -> None:
    principles = load_catalogue().principles["testing-framework"]
    assert sorted(principles) == list(range(1, 12))
    assert principles[11] == "Inclusive growth and well-being"
    assert principles[3] == "Repeatability / Reproducibility"


def test_testing_framework_is_pinned_to_the_retrieved_file() -> None:
    source = load_catalogue().sources["testing-framework"]
    assert source.sha256 == (
        "363e59d140896b2ab75dffe508b03a8339e164fcfdab192b61862fb86c282379"
    )
    assert (source.version, source.published, source.retrieved_on) == (
        "2025",
        dt.date(2025, 5, 29),
        dt.date(2026, 11, 1),
    )
    assert source.url == "https://file.go.gov.sg/aivtf-pdf.pdf"


def test_later_entries_name_their_version_and_evidenced_ones_their_pointers() -> None:
    for control in load_catalogue().controls.values():
        if control.kind != "process":
            continue
        assert bool(control.available_from) == (control.classification == "later")
        if control.classification in SCENARIO_CLASSES:
            assert "scenarios" in control.evidence
        elif control.classification == "supporting":
            assert control.evidence
            assert all(e.startswith("artefact:") for e in control.evidence)
        else:
            assert control.evidence == ()


def test_bundle_0_6_files_are_the_0_5_set_and_adr_0007s_additions() -> None:
    """The list the loader checks pointers against cannot drift from the
    bundle's own. Until R28 (b) adds them to ``bundles.constants``, the 0.6
    additions are named here as ADR-0007 D1 names them."""
    from agentevalops.bundles.constants import (
        MANIFEST_FILENAME,
        TEST_PLAN_FILENAME,
        required_files_for_version,
    )

    assert BUNDLE_0_6_FILES == {
        *required_files_for_version("0.5"),
        TEST_PLAN_FILENAME,
        "controls.json",
        "evidence_report.md",
        MANIFEST_FILENAME,
        "manifest.json.sig",
    }


def test_the_shipped_file_loads_alone(tmp_path: Path) -> None:
    catalogue = load_catalogue([_tf_write(tmp_path, _tf_raw())])
    assert len(catalogue.controls) == 112


# Fail-first: each guard below was seen failing against the loader before
# the process checks existed.


def test_a_missing_entry_is_rejected(tmp_path: Path) -> None:
    """111 entries against a declared 112."""
    raw = _tf_raw()
    raw["controls"] = [c for c in raw["controls"] if c["locator"] != "11.2.1"]
    with pytest.raises(CatalogueError, match="111 process entries, 112 declared"):
        load_catalogue([_tf_write(tmp_path, raw)])


def test_a_wrong_class_count_is_rejected(tmp_path: Path) -> None:
    raw = _tf_raw()
    _tf_entry(raw, "3.7.1")["classification"] = "direct"
    with pytest.raises(CatalogueError, match="3 'direct' entries, 2 declared"):
        load_catalogue([_tf_write(tmp_path, raw)])


def test_counts_must_name_every_class(tmp_path: Path) -> None:
    raw = _tf_raw()
    del raw["process_counts"]["later"]
    with pytest.raises(CatalogueError, match="must name exactly"):
        load_catalogue([_tf_write(tmp_path, raw)])


def test_process_entries_need_declared_counts(tmp_path: Path) -> None:
    raw = _tf_raw()
    del raw["process_counts"]
    with pytest.raises(CatalogueError, match="process_counts"):
        load_catalogue([_tf_write(tmp_path, raw)])


def test_process_entries_need_a_principles_map(tmp_path: Path) -> None:
    raw = _tf_raw()
    del raw["principles"]
    with pytest.raises(CatalogueError, match="principles"):
        load_catalogue([_tf_write(tmp_path, raw)])


@pytest.mark.parametrize(
    ("locator", "change", "message"),
    [
        ("3.4.1", {"available_from": None}, "available_from is set exactly"),
        ("1.1.1", {"available_from": "v0.7"}, "available_from is set exactly"),
        ("3.4.1", {"available_from": "v0.9"}, "available_from must be"),
        ("1.1.6", {"evidence": ["artefact:notes.txt"]}, "format 0.6 bundle file"),
        ("1.1.6", {"evidence": ["evidence_report.md"]}, "format 0.6 bundle file"),
        ("1.1.6", {"evidence": ["scenarios"]}, "'scenarios' is evidence exactly"),
        ("8.3.2", {"evidence": ["artefact:evaluations.json"]}, "'scenarios' is"),
        ("1.1.1", {"evidence": ["artefact:report.md"]}, "evidence is listed exactly"),
        ("1.1.6", {"evidence": []}, "evidence is listed exactly"),
        ("1.1.1", {"scope": "agentic"}, "scope must be"),
        ("1.1.1", {"classification": "maybe"}, "classification must be"),
        ("1.1.1", {"principle": 12}, "not in the source's principles map"),
        ("1.1.1", {"principle": 2}, "is not under principle 2"),
        ("1.1.3", {"rows": ["W12"]}, "is not 'R<n>' or 'built'"),
        ("1.1.6", {"run_level": True}, "can be run_level"),
        ("1.1.1", {"run_level": True}, "can be run_level"),
    ],
)
def test_a_malformed_process_entry_is_rejected(
    tmp_path: Path, locator: str, change: dict[str, Any], message: str
) -> None:
    raw = _tf_raw()
    entry = _tf_entry(raw, locator)
    for key, value in change.items():
        if value is None:
            entry.pop(key, None)
        else:
            entry[key] = value
    with pytest.raises(CatalogueError, match=message):
        load_catalogue([_tf_write(tmp_path, raw)])


def test_ai_verify_in_a_principle_label_is_rejected(tmp_path: Path) -> None:
    raw = _tf_raw()
    raw["principles"][1] = "AI Verify transparency"
    with pytest.raises(CatalogueError, match="AI Verify"):
        load_catalogue([_tf_write(tmp_path, raw)])


def test_ai_verify_in_a_process_label_is_rejected(tmp_path: Path) -> None:
    raw = _tf_raw()
    _tf_entry(raw, "1.1.1")["label"] = "AI Verify explanation policy"
    with pytest.raises(CatalogueError, match="AI Verify"):
        load_catalogue([_tf_write(tmp_path, raw)])


def test_the_shipped_source_title_carries_the_publishers_own_name() -> None:
    """The exemption is exercised by real data, not only a fixture."""
    source = load_catalogue().sources["testing-framework"]
    assert "AI Verify" in source.title
    assert "AI Verify" in source.publisher


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"principles": ["Transparency"]}, "'principles' must be a mapping"),
        ({"principles": {"one": "Transparency"}}, "is not an int"),
        ({"process_counts": {"total": "112"}}, "must map names to ints"),
        ({"process_counts": [112]}, "must map names to ints"),
    ],
)
def test_malformed_principles_or_counts_are_rejected(
    tmp_path: Path, change: dict[str, Any], message: str
) -> None:
    raw = _tf_raw()
    raw.update(change)
    with pytest.raises(CatalogueError, match=message):
        load_catalogue([_tf_write(tmp_path, raw)])


def test_an_empty_principle_label_is_rejected(tmp_path: Path) -> None:
    raw = _tf_raw()
    raw["principles"][4] = "  "
    with pytest.raises(CatalogueError, match="principle 4 has no label"):
        load_catalogue([_tf_write(tmp_path, raw)])
