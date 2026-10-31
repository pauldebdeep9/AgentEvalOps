"""Tests for the control catalogue (catalogue/loader.py; ROADMAP R22 part 3)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pytest
import yaml

from agentevalops.catalogue import CatalogueError, load_catalogue
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
        yaml.safe_dump({"source": source, "controls": controls}), encoding="utf-8"
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
        ({"title": "The AI Verify Framework"}, {}),
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
        yaml.safe_dump({"source": _source(), "controls": {"a": 1}}), encoding="utf-8"
    )
    with pytest.raises(CatalogueError, match="list"):
        load_catalogue([path])


def test_each_control_must_be_a_mapping(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        yaml.safe_dump({"source": _source(), "controls": ["doc:1.1"]}),
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
