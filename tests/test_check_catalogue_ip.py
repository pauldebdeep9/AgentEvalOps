"""The IP script's allowlist of known overlaps (scripts/check_catalogue_ip.py,
scripts/ip_allowlist.yaml; R28 follow-up, 2026-11-02).

The script needs the source PDFs, which are never committed, so CI cannot
run it. These tests check what can be checked without them: the shipped
allowlist keeps its rules -- every entry an identifier or a cited name the
catalogue itself yields, never prose -- and a run that is not on it is
still reported as a new overlap.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from agentevalops.catalogue import load_catalogue

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_catalogue_ip.py"
CATALOGUE = load_catalogue()


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_catalogue_ip", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ip = _load_script()


def test_the_shipped_allowlist_keeps_its_rules() -> None:
    entries = ip.load_allowlist()
    assert len(entries) == 32
    assert ip.allowlist_problems(entries, CATALOGUE) == []
    assert {e.kind for e in entries} == set(ip.KINDS)


@pytest.mark.parametrize(
    ("entry", "problem"),
    [
        # Fail-first: five words of prose are refused whatever their kind.
        (("risks arise when agents act", "source-citation", "x"), "never prose"),
        (("risks arise when agents act", "check-id-and-scope", "x"), "never prose"),
        # A check id with words that are not its scope or its label.
        (("1 1 3 generative ai", "principle-name", "x"), "never prose"),
        (("1 1 3 agents decide", "check-id-and-scope", "x"), "never prose"),
        (("1 1 3 generative", "check-id-and-scope", "x"), "5-word run"),
        (("1 1 3 Generative AI", "check-id-and-scope", "x"), "5-word run"),
        (("1 1 3 generative ai", "a-new-kind", "x"), "kind must be"),
        (("1 1 3 generative ai", "check-id-and-scope", " "), "needs a reason"),
    ],
)
def test_an_entry_that_breaks_the_rules_is_refused(
    entry: tuple[str, str, str], problem: str
) -> None:
    problems = ip.allowlist_problems([ip.Allowed(*entry)], CATALOGUE)
    assert any(problem in p for p in problems), problems


def test_an_entry_listed_twice_is_refused() -> None:
    entry = ip.Allowed("1 1 3 generative ai", "check-id-and-scope", "x")
    assert any(
        "listed twice" in p for p in ip.allowlist_problems([entry, entry], CATALOGUE)
    )


def test_a_new_overlap_still_fails_beside_known_ones() -> None:
    """Fail-first: an allowlist must never hide a run it does not name."""
    allowed = {e.run for e in ip.load_allowlist()}
    hits = {
        "aivtf-2025.pdf": ["1 1 3 generative ai", "alpha beta gamma delta epsilon"],
        "mgf-for-agentic-ai.pdf": ["model ai governance framework for"],
    }
    known, new = ip.split_known(hits, allowed)
    assert new == {"aivtf-2025.pdf": ["alpha beta gamma delta epsilon"]}
    assert known == {
        "aivtf-2025.pdf": ["1 1 3 generative ai"],
        "mgf-for-agentic-ai.pdf": ["model ai governance framework for"],
    }


def test_only_known_overlaps_leave_nothing_new() -> None:
    allowed = {e.run for e in ip.load_allowlist()}
    known, new = ip.split_known({"aivtf-2025.pdf": ["8 3 2 generative ai"]}, allowed)
    assert new == {} and known == {"aivtf-2025.pdf": ["8 3 2 generative ai"]}
