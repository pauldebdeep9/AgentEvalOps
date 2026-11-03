"""What a signature and a rescore MATCH say, in both reports' "What this
report does not say" (docs/adr/0008 E; R29 (b2)).

A report is written before anyone signs or rescores its bundle, so the two
paragraphs stay general: each says what the check would and would not
show, and that the report does not say whether it happened. The MATCH
paragraph says plainly that rescore cannot see a consistent edit to the
records, and that only a signature made when the bundle was written can.
"""

from __future__ import annotations

from reference_bundles import ReferenceBundles

from agentevalops.reports.markdown import MATCH_LIMITS, SIGNATURE_LIMITS

LIMITS = "## What this report does not say"


def _section(text: str) -> str:
    after = text.split(LIMITS, 1)[1]
    return after.split("\n## ", 1)[0]


def test_both_reports_say_what_a_signature_and_a_match_do_not(
    reference_bundles: ReferenceBundles,
) -> None:
    """Fail-first: report.md had no such section, and the evidence report's
    said nothing of either."""
    for kind in ("toy", "seed"):
        for name in ("report.md", "evidence_report.md"):
            text = (reference_bundles[kind].path / name).read_text("utf-8")
            assert text.count(LIMITS) == 1, (kind, name)
            section = _section(text)
            assert f"- {SIGNATURE_LIMITS}" in section, (kind, name)
            assert f"- {MATCH_LIMITS}" in section, (kind, name)


def test_neither_paragraph_suggests_the_check_happened() -> None:
    assert SIGNATURE_LIMITS.endswith(
        "This report does not say whether the bundle has been signed."
    )
    assert MATCH_LIMITS.endswith(
        "This report does not say whether the bundle has been rescored."
    )
    for text in (SIGNATURE_LIMITS, MATCH_LIMITS):
        for claim in ("was signed", "is signed", "was rescored", "rescored as"):
            assert claim not in text.replace("rescore as a MATCH", "")


def test_the_match_paragraph_says_what_rescore_cannot_see() -> None:
    assert "It cannot see a consistent edit to those records" in MATCH_LIMITS
    assert "tool calls, end states or traces" in MATCH_LIMITS
    assert (
        "only a signature made when the bundle was written can show the edit"
        in MATCH_LIMITS
    )
    for unseen in ("dependencies", "Python", "plugin evaluator"):
        assert unseen in MATCH_LIMITS
