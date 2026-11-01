"""The evidence-report renderer (reports/evidence.py; docs/adr/0007 D7;
ROADMAP R28 (a) slice 4), on synthetic plans and summaries over the shipped
catalogue and the seed pack's citations. The writer does not call it until
R28 (b)."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import evidence_inputs as inputs
import pytest

from agentevalops.catalogue import load_catalogue
from agentevalops.domain_packs.seed import SeedDomainPack
from agentevalops.plans import control_coverage
from agentevalops.reports import evidence
from agentevalops.reports.evidence import SCRIPTED_SENTENCE, render_evidence_report

CATALOGUE = load_catalogue()
DESCRIPTORS = {
    s.scenario_id: inputs.descriptor(*s.control_ids, category=s.category)
    for s in SeedDomainPack().scenarios()
}
#: The plan leaves out the counterfactual pair, and PC7 and PC8 are not
#: evaluated: 7.2.1 is then not in the plan and 8.3.2 uncovered, so neither
#: may point at anything.
EXCLUDED = (("RB6", "no protected attribute declared"), ("RB7", "same"))
SELECTED = tuple(s for s in DESCRIPTORS if s not in {"RB6", "RB7"})
OUTCOMES = {
    s: "not_evaluated" if s in {"PC7", "PC8"} else "failed" if s == "TC2" else "passed"
    for s in SELECTED
}


def _render(runner_id: str = "mock", *, declared: bool = True) -> str:
    plan = inputs.plan(selected=SELECTED, excluded=EXCLUDED) if declared else None
    summary = inputs.summary(OUTCOMES)
    controls = control_coverage(CATALOGUE, plan, DESCRIPTORS, summary)
    return render_evidence_report(
        CATALOGUE,
        plan,
        controls,
        summary,
        runner_id=runner_id,
        created_at=inputs.CREATED_AT,
    )


def _rows(text: str) -> dict[str, list[str]]:
    """The principle tables' rows, by check id: every cell after the id."""
    section = text.split("## Testing framework checks, by principle", 1)[1]
    section = section.split("\n## ", 1)[0]
    rows: dict[str, list[str]] = {}
    for line in section.splitlines():
        match = re.match(r"^\| (\d+\.\d+\.\d+) \|(.*)\|$", line)
        if match:
            cells = re.split(r"(?<!\\)\|", match.group(2))
            rows[match.group(1)] = [c.strip() for c in cells]
    return rows


def _section(text: str, heading: str) -> str:
    return text.split(f"\n## {heading}\n", 1)[1].split("\n## ", 1)[0]


# ---------------------------------------------------------------------------
# Every check, answers blank (D7)
# ---------------------------------------------------------------------------


def test_all_112_checks_are_listed_with_the_answer_columns_empty() -> None:
    rows = _rows(_render())
    assert len(rows) == 112
    for check, cells in rows.items():
        scope, label, klass, evidence_cell, implemented, elaboration = cells
        assert (implemented, elaboration) == ("", ""), check
        control = CATALOGUE.control(f"testing-framework:{check}")
        assert label == control.label


def test_checks_are_grouped_under_this_projects_principle_labels() -> None:
    text = _render()
    headings = re.findall(r"^### (\d+)\. (.+)$", text, flags=re.M)
    assert headings == [
        (str(n), label)
        for n, label in sorted(CATALOGUE.principles["testing-framework"].items())
    ]


def test_the_plan_is_repeated_verbatim() -> None:
    text = _render()
    assert f"- Intended use: {inputs.INTENDED_USE}" in text
    assert "; ".join(inputs.OUT_OF_SCOPE) in text
    assert f"`{inputs.METRIC.name}` -- {inputs.METRIC.definition}" in text
    assert "Business metrics (recorded, not evaluated)" in text


# ---------------------------------------------------------------------------
# Evidence cells
# ---------------------------------------------------------------------------


def test_a_check_whose_scenarios_ran_points_at_their_records() -> None:
    cell = _rows(_render())["4.1.1"][3]
    assert cell.startswith("covered: RB4 passed 2/2 (tool_calls RB4/0/2, RB4/0/4,")
    assert "end_states RB4/0, RB4/1" in cell


def test_a_failing_scenario_shows_failed_beside_covered() -> None:
    cell = _rows(_render())["3.12.1"][3]
    assert cell.startswith("partly covered:")
    assert "TC2 failed 0/2 (tool_calls TC2/0/2" in cell


@pytest.mark.parametrize(
    ("check", "start"),
    [
        ("7.2.1", "not in plan: RB6 excluded (no protected attribute declared)"),
        ("8.3.2", "uncovered: PC7 not evaluated (infrastructure); PC8 not evaluated"),
    ],
)
def test_no_pointer_for_a_check_whose_scenarios_did_not_run(
    check: str, start: str
) -> None:
    cell = _rows(_render())[check][3]
    assert cell.startswith(start)
    assert "tool_calls" not in cell and "end_states" not in cell
    assert "trace " not in cell and "→" not in cell
    assert SCRIPTED_SENTENCE not in cell


@pytest.mark.parametrize(
    ("check", "cell"),
    [
        ("3.4.1", "evidence from v0.7"),
        ("1.1.1", "requires the organisation's evidence"),
        ("2.1.1", "applies only if the system also contains a traditional model"),
    ],
)
def test_checks_without_run_evidence_say_why(check: str, cell: str) -> None:
    assert _rows(_render())[check][3] == cell


def test_a_supporting_check_points_at_its_artefacts() -> None:
    cell = _rows(_render("openai"))["1.2.1"][3]
    assert cell == (
        "`test_plan.json` → `system.intended_use`, "
        "`test_plan.json` → `system.out_of_scope`"
    )


def test_scope_and_class_are_written_in_words() -> None:
    rows = _rows(_render())
    assert rows["7.2.1"][0] == "generative AI, technical test"
    assert rows["3.4.1"][2] == "later (v0.7)"
    assert rows["7.1.1"][2] == "traditional AI only"


# ---------------------------------------------------------------------------
# The scripted-run sentence (D7, D4)
# ---------------------------------------------------------------------------


def _has_pointer(cell: str) -> bool:
    return "`" in cell or "tool_calls " in cell or "trace " in cell


def test_a_mock_run_carries_the_sentence_in_the_header_and_on_every_pointer() -> None:
    text = _render("mock")
    header = text.split("## What this report is and is not", 1)[0]
    assert SCRIPTED_SENTENCE in header
    pointers = [c[3] for c in _rows(text).values() if _has_pointer(c[3])]
    # The 32 supporting checks, and the 12 direct or partial ones whose
    # scenarios ran (7.2.1 and 8.3.2 did not).
    assert len(pointers) == 44
    assert all(SCRIPTED_SENTENCE in cell for cell in pointers)
    assert SCRIPTED_SENTENCE in _section(text, "What this report does not say")


def test_a_model_backed_run_carries_no_sentence() -> None:
    assert SCRIPTED_SENTENCE not in _render("openai")


# ---------------------------------------------------------------------------
# Determinism and wording (D7)
# ---------------------------------------------------------------------------


def test_two_renders_are_byte_identical() -> None:
    assert _render().encode("utf-8") == _render().encode("utf-8")


def test_created_at_is_the_runs_own() -> None:
    header = _render().split("\n## ", 1)[0]
    assert f"- Created: {inputs.CREATED_AT}" in header
    assert "Generated" not in _render()


def test_the_renderer_reads_no_clock() -> None:
    """Fail-first guard: no import of a clock can slip in later."""
    tree = ast.parse(Path(evidence.__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert imported.isdisjoint({"datetime", "time"})


@pytest.mark.parametrize("runner_id", ["mock", "openai"])
@pytest.mark.parametrize("declared", [True, False])
def test_no_claim_words_and_the_framework_name_only_in_sources(
    runner_id: str, declared: bool
) -> None:
    text = _render(runner_id, declared=declared)
    assert not re.search(r"\b(compliant|certified)\b", text, flags=re.I)
    sources = _section(text, "Sources")
    assert "AI Verify" in sources
    assert "AI Verify" not in text.replace(sources, "")


# ---------------------------------------------------------------------------
# An undeclared run, and the other sources
# ---------------------------------------------------------------------------


def test_an_undeclared_run_says_so_and_points_at_nothing() -> None:
    text = _render(declared=False)
    assert "- System: no declaration" in text
    assert "## Test plan" not in text
    assert {cells[3] for cells in _rows(text).values()} == {"undeclared"}
    assert "it evidences no control" in _section(text, "What this report does not say")


def test_mgf_and_starter_kit_controls_show_state_and_verdicts() -> None:
    text = _render()
    mgf = _section(text, "MGF-Agentic v1.5")
    assert re.search(r"^\| 2\.3\.2 \| .+ \| covered \| TE1 passed 2/2 ", mgf, re.M)
    assert re.search(r"^\| 2\.2\.2 \| .+ \| undeclared \| — \|$", mgf, re.M)
    kit = _section(text, "Starter Kit v1.0")
    assert re.search(
        r"^\| 3\.3 \| Bias in decision making \| not in plan \|", kit, re.M
    )


def _sections(text: str) -> list[str]:
    return re.findall(r"^## (.+)$", text, flags=re.M)


def test_the_sections_come_in_the_adrs_order() -> None:
    assert _sections(_render()) == [
        "What this report is and is not",
        "Test plan (pre-registered)",
        "Testing framework checks, by principle",
        "MGF-Agentic v1.5",
        "Starter Kit v1.0",
        "Sources",
        "What this report does not say",
    ]


def test_a_synthetic_catalogue_renders_too(tmp_path: Path) -> None:
    """Nothing in the renderer is tied to the shipped catalogue's size."""
    catalogue = inputs.catalogue(tmp_path)
    plan = inputs.plan(selected=("S1",))
    summary = inputs.summary({"S1": "passed"})
    descriptors: dict[str, Any] = {"S1": inputs.descriptor("doc:1", "tf:1.1.1")}
    text = render_evidence_report(
        catalogue,
        plan,
        control_coverage(catalogue, plan, descriptors, summary),
        summary,
        runner_id="openai",
        created_at=inputs.CREATED_AT,
    )
    rows = _rows(text)
    assert list(rows) == ["1.1.1", "1.2.1", "1.10.1", "2.1.1", "2.2.1", "2.3.1"]
    assert rows["1.1.1"][3].endswith("; also `test_plan.json` → `thresholds`")
    assert rows["1.2.1"][3] == "undeclared"
    assert "## doc v1.0" in text
