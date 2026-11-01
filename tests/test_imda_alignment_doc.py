"""docs/imda-alignment.md agrees with the control catalogue (docs/adr/0007
D12; ROADMAP R28 (a) slice 3).

The document stays hand-written: its value is the reasoning around the
tables. Since R28 the classifications live in the catalogue, and §12 says
to change the catalogue first and the document in the same commit. This
test enforces that rule: §4's 112 rows, §3's 14 checks, §2's class table
and §4's counts table are each compared with the catalogue, in both
directions.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import fast_yaml
import pytest

from agentevalops.catalogue import (
    CLASSIFICATIONS,
    SCENARIO_CLASSES,
    Catalogue,
    Control,
    load_catalogue,
)

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/imda-alignment.md"
DATA = ROOT / "src/agentevalops/catalogue/data"
SOURCE = "testing-framework"

#: The document's words for a class and the catalogue's, in the order §2's
#: class table and the counts table use. A row's class cell writes a later
#: check as "Later (v0.7)".
_CLASS_WORDS = {
    "Direct": "direct",
    "Partial": "partial",
    "Supporting": "supporting",
    "Later": "later",
    "Organisational": "organisational",
    "Traditional AI only": "traditional-only",
}
_SCOPE_WORDS = {"Both": "both", "GenAI": "generative", "Traditional": "traditional"}
_ROW = re.compile(r"^\| ([^|]+?) \| (.+) \|$")


def _section(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def _cells(line: str) -> list[str] | None:
    match = _ROW.match(line)
    if not match or set(line) <= {"|", "-", " ", ":"}:
        return None
    return [cell.strip() for cell in line.strip("|").split(" | ")]


def _applies_to(control: Control) -> str:
    words = {v: k for k, v in _SCOPE_WORDS.items()}[control.scope]
    return f"{words}, technical test" if control.technical_test else words


def _class_word(control: Control) -> str:
    word = {v: k for k, v in _CLASS_WORDS.items()}[control.classification]
    if control.classification == "later":
        return f"{word} ({control.available_from})"
    return word


def _rows_cell(control: Control) -> str:
    if control.classification == "later":
        return control.available_from
    return ", ".join(control.rows) if control.rows else "—"


def _process(catalogue: Catalogue) -> dict[str, Control]:
    return {
        c.locator: c
        for c in catalogue.controls.values()
        if c.source == SOURCE and c.kind == "process"
    }


def differences(text: str, catalogue: Catalogue) -> list[str]:
    """Every way the document and the catalogue disagree; empty if none."""
    found: list[str] = []
    process = _process(catalogue)
    principles = catalogue.principles[SOURCE]

    # §4: one table per principle, every row against its entry.
    section4 = _section(text, "## 4. All 112 checks", "### Counts by principle")
    seen: dict[str, int] = {}
    principle = 0
    for line in section4.splitlines():
        heading = re.match(r"^### (\d+)\. (.+)$", line)
        if heading:
            principle = int(heading.group(1))
            # 11's heading is the framework's own name, read as an
            # identifier (docs/adr/0007, Open questions); 1-10 are the
            # short labels the catalogue carries.
            if principle != 11 and heading.group(2) != principles.get(principle):
                found.append(
                    f"§4 heading {principle}: doc {heading.group(2)!r}, "
                    f"catalogue {principles.get(principle)!r}"
                )
            continue
        cells = _cells(line)
        if cells is None or not re.fullmatch(r"\d+\.\d+\.\d+", cells[0]):
            continue
        check, applies, label, klass, _contributes, rows = cells
        seen[check] = principle
        control = process.get(check)
        if control is None:
            found.append(f"§4 {check}: not in the catalogue")
            continue
        expected = {
            "principle": (principle, control.principle),
            "applies to": (applies, _applies_to(control)),
            "label": (label, control.label),
            "class": (klass, _class_word(control)),
            "rows": (rows, _rows_cell(control)),
        }
        for name, (doc, cat) in expected.items():
            if doc != cat:
                found.append(f"§4 {check} {name}: doc {doc!r}, catalogue {cat!r}")
    for locator in sorted(set(process) - set(seen)):
        found.append(f"§4: catalogue entry {locator} has no row")

    # §3: exactly the direct and partial checks, with the same label and class.
    section3 = _section(text, "## 3. The 14 checks", "## 4. All 112 checks")
    in_3: set[str] = set()
    for line in section3.splitlines():
        cells = _cells(line)
        if cells is None or not re.fullmatch(r"\d+\.\d+\.\d+", cells[0]):
            continue
        check, label, klass = cells[0], cells[1], cells[2]
        in_3.add(check)
        control = process.get(check)
        if control is None:
            found.append(f"§3 {check}: not in the catalogue")
            continue
        if label != control.label:
            found.append(
                f"§3 {check} label: doc {label!r}, catalogue {control.label!r}"
            )
        if klass != _class_word(control):
            found.append(
                f"§3 {check} class: doc {klass!r}, catalogue {_class_word(control)!r}"
            )
    evidenced = {
        locator
        for locator, control in process.items()
        if control.classification in SCENARIO_CLASSES
    }
    if in_3 != evidenced:
        found.append(
            f"§3: doc lists {sorted(in_3)}, catalogue's direct and partial "
            f"are {sorted(evidenced)}"
        )

    # §2's class table, recomputed.
    totals = Counter(c.classification for c in process.values())
    section2 = _section(text, "## 2. Verdict", "## 3. The 14 checks")
    for line in section2.splitlines():
        cells = _cells(line)
        if cells is None or cells[0].strip("*") not in (*_CLASS_WORDS, "Total"):
            continue
        name, count = cells[0].strip("*"), int(cells[1].strip("*"))
        want = len(process) if name == "Total" else totals[_CLASS_WORDS[name]]
        if count != want:
            found.append(f"§2 {name}: doc {count}, catalogue {want}")

    # §4's counts table, recomputed per principle and in total.
    counts = _section(text, "### Counts by principle", "## 5. ")
    for line in counts.splitlines():
        cells = _cells(line)
        if cells is None or cells[0] == "Principle":
            continue
        name = cells[0].strip("*")
        numbers = [int(c.strip("*")) for c in cells[1:]]
        if name == "Total":
            members = list(process.values())
        else:
            number = int(name.split(".")[0])
            members = [c for c in process.values() if c.principle == number]
        by_class = Counter(c.classification for c in members)
        want = [len(members), *(by_class[k] for k in _CLASS_WORDS.values())]
        if numbers != want:
            found.append(f"counts {name}: doc {numbers}, catalogue {want}")
    return found


def _catalogue_with(
    tmp_path: Path,
    locator: str,
    change: dict[str, object],
    counts: dict[str, int] | None = None,
) -> Catalogue:
    """The shipped catalogue with one process entry changed, and its
    declared counts moved by *counts* so the loader still accepts it."""
    raw = fast_yaml.safe_load((DATA / "testing-framework-2025.yaml").read_text("utf-8"))
    (entry,) = [c for c in raw["controls"] if c["locator"] == locator]
    entry.update(change)
    for key in [k for k, v in entry.items() if v is None]:
        del entry[key]
    for name, delta in (counts or {}).items():
        raw["process_counts"][name] += delta
    edited = tmp_path / "testing-framework-2025.yaml"
    edited.write_text(fast_yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    others = [p for p in sorted(DATA.glob("*.yaml")) if p.name != edited.name]
    return load_catalogue([*others, edited])


def test_the_document_and_the_catalogue_agree() -> None:
    assert differences(DOC.read_text(encoding="utf-8"), load_catalogue()) == []


def test_every_class_appears_in_the_comparison() -> None:
    """The parser reads every class the catalogue has, so a class the
    document spells differently cannot slip through as unparsed."""
    assert set(_CLASS_WORDS.values()) == set(CLASSIFICATIONS)


# Fail-first: each edit below makes the comparison report a difference.


def test_a_one_letter_change_to_a_label_in_the_document_fails() -> None:
    text = DOC.read_text(encoding="utf-8")
    edited = text.replace(
        "| 8.3.2 | GenAI | Detect personal or sensitive data in outputs |",
        "| 8.3.2 | GenAI | Detect personal or sensitive data in output |",
    )
    assert edited != text
    assert differences(edited, load_catalogue()) == [
        "§4 8.3.2 label: doc 'Detect personal or sensitive data in output', "
        "catalogue 'Detect personal or sensitive data in outputs'"
    ]


def test_a_one_letter_change_to_a_label_in_the_catalogue_fails(
    tmp_path: Path,
) -> None:
    catalogue = _catalogue_with(
        tmp_path, "1.1.1", {"label": "Policy for explaining the AI system to consumer"}
    )
    found = differences(DOC.read_text(encoding="utf-8"), catalogue)
    assert found == [
        "§4 1.1.1 label: doc 'Policy for explaining the AI system to consumers', "
        "catalogue 'Policy for explaining the AI system to consumer'"
    ]


def test_a_label_changed_in_section_3_only_fails() -> None:
    text = DOC.read_text(encoding="utf-8")
    edited = text.replace(
        "| 6.1.1 | Performance under noise and prompt perturbation | Direct | Task",
        "| 6.1.1 | Performance under noise and prompt perturbations | Direct | Task",
    )
    assert edited != text
    assert len(differences(edited, load_catalogue())) == 1


@pytest.mark.parametrize(
    ("locator", "change", "counts", "expect"),
    [
        (
            "9.1.1",
            {"classification": "organisational", "evidence": None},
            {"supporting": -1, "organisational": 1},
            "class",
        ),
        ("6.7.2", {"scope": "both"}, None, "applies to"),
        ("4.6.3", {"rows": ["R25"]}, None, "rows"),
    ],
)
def test_a_changed_entry_in_the_catalogue_fails(
    tmp_path: Path,
    locator: str,
    change: dict[str, object],
    counts: dict[str, int] | None,
    expect: str,
) -> None:
    """A reclassification breaks its row and both counts tables; a scope
    or row edit breaks its own cell."""
    catalogue = _catalogue_with(tmp_path, locator, change, counts)
    found = differences(DOC.read_text(encoding="utf-8"), catalogue)
    assert any(f"§4 {locator} {expect}:" in line for line in found)


def test_a_section_3_row_that_is_not_direct_or_partial_fails() -> None:
    text = DOC.read_text(encoding="utf-8")
    edited = text.replace(
        "## 4. All 112 checks",
        "| 1.1.6 | Report performance, scope, limits and risk handling to outside "
        "stakeholders | Supporting | x | R28 | x |\n\n## 4. All 112 checks",
    )
    found = differences(edited, load_catalogue())
    assert any(line.startswith("§3: doc lists") for line in found)


def test_a_wrong_count_in_the_counts_table_fails() -> None:
    text = DOC.read_text(encoding="utf-8")
    edited = text.replace(
        "| 8. Data Governance | 6 | 0 | 1 | 1 | 0 | 4 | 0 |",
        "| 8. Data Governance | 6 | 0 | 1 | 2 | 0 | 3 | 0 |",
    )
    assert edited != text
    assert differences(edited, load_catalogue()) == [
        "counts 8. Data Governance: doc [6, 0, 1, 2, 0, 3, 0], "
        "catalogue [6, 0, 1, 1, 0, 4, 0]"
    ]


def test_a_missing_row_fails() -> None:
    text = DOC.read_text(encoding="utf-8")
    edited = "\n".join(
        line for line in text.splitlines() if not line.startswith("| 11.2.1 |")
    )
    assert "§4: catalogue entry 11.2.1 has no row" in differences(
        edited, load_catalogue()
    )
