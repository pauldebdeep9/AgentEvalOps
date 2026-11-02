"""Check that no catalogue text shares five consecutive words with a source.

The docs/imda-alignment.md §12 standard, applied to the control catalogue
(ADR-0005 D5; CLAUDE.md, "Third-party framework text"). Every text field of
every control and every principle label is checked (docs/adr/0007 D10) --
not a source's title and publisher, which cite the document by its own
name. The source PDFs are never committed, so this is a dev script, not a
test, and CI cannot run it:

    $ENV/bin/python scripts/check_catalogue_ip.py \\
        ~/Documents/aeo-sources/mgf-for-agentic-ai.pdf \\
        ~/Documents/aeo-sources/starter-kit-llm-testing.pdf \\
        ~/Documents/aeo-sources/aivtf-2025.pdf

Each PDF is named with the catalogue source whose sha256 it matches, or
flagged if it matches none. ``--extra FILE`` also checks each non-empty
line of FILE (e.g. labels about to be added, or a run's rendered reports).
Needs ``pdftotext`` (poppler).

Known overlaps are on ``scripts/ip_allowlist.yaml``: each entry one
five-word run with its reason, and only of three kinds the script verifies
against the catalogue -- a check id with its scope (and our label), a
principle's number with its label, a source's id, title and publisher.
Prose can never be on it. Overlaps are reported as known or new; the
script exits 1 on any new overlap or any PDF that is not a pinned source,
and 2 if the allowlist itself breaks its rules.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

import yaml

from agentevalops.catalogue import Catalogue, load_catalogue

RUN = 5
ALLOWLIST = Path(__file__).with_name("ip_allowlist.yaml")
KINDS = ("check-id-and-scope", "principle-name", "source-citation")
#: The scope words the evidence report prints beside a check
#: (reports/evidence.py).
_SCOPE = {
    "both": "both",
    "generative": "generative AI",
    "traditional": "traditional AI",
}


@dataclasses.dataclass(frozen=True)
class Allowed:
    """One known overlap: a five-word run, its kind and the reason it may."""

    run: str
    kind: str
    reason: str


def _words(text: str) -> list[str]:
    text = re.sub(r"-\s*\n\s*", "", text)  # re-join words hyphenated at line ends
    return re.findall(r"[a-z0-9]+", text.lower().replace("’", "'"))


def _runs(words: list[str]) -> set[tuple[str, ...]]:
    return {tuple(words[i : i + RUN]) for i in range(len(words) - RUN + 1)}


def _pdftotext() -> str:
    found = shutil.which("pdftotext")
    sibling = Path(sys.executable).with_name("pdftotext")
    if found:
        return found
    if sibling.exists():
        return str(sibling)
    sys.exit("pdftotext not found (install poppler)")


def _pdf_runs(pdf: Path, tool: str) -> set[tuple[str, ...]]:
    text = subprocess.run(
        [tool, str(pdf), "-"], capture_output=True, text=True, check=True
    ).stdout
    return _runs(_words(text))


def _texts(catalogue: Catalogue) -> list[tuple[str, str]]:
    """Every text field of every control, and every principle label."""
    texts: list[tuple[str, str]] = []
    for control in catalogue.controls.values():
        for field in dataclasses.fields(control):
            value = getattr(control, field.name)
            values = value if isinstance(value, tuple) else (value,)
            texts += [
                (f"{control.id}#{field.name}", v)
                for v in values
                if isinstance(v, str) and v
            ]
    for source_id, principles in catalogue.principles.items():
        texts += [
            (f"{source_id}:principle-{n}", label) for n, label in principles.items()
        ]
    return texts


def load_allowlist(path: Path = ALLOWLIST) -> list[Allowed]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [
        Allowed(str(e.get("run", "")), str(e.get("kind", "")), str(e.get("reason", "")))
        for e in raw.get("allowed") or []
    ]


def derivable(catalogue: Catalogue) -> dict[str, set[str]]:
    """Every five-word run each allowlist kind may name, from the catalogue
    itself: what the reports print for a check, a principle or a source."""
    checks: set[tuple[str, ...]] = set()
    for control in catalogue.controls.values():
        if control.kind != "process":
            continue
        scope = _SCOPE.get(control.scope, control.scope)
        if control.technical_test:
            scope += ", technical test"
        checks |= _runs(_words(f"{control.locator} {scope} {control.label}"))
    principles = {
        run
        for labels in catalogue.principles.values()
        for n, label in labels.items()
        for run in _runs(_words(f"{n} {label}"))
    }
    sources = {
        run
        for s in catalogue.sources.values()
        for run in _runs(_words(f"{s.id} {s.title} {s.publisher}"))
    }
    return {
        kind: {" ".join(run) for run in runs}
        for kind, runs in zip(KINDS, (checks, principles, sources))
    }


def allowlist_problems(entries: Iterable[Allowed], catalogue: Catalogue) -> list[str]:
    """Why an allowlist breaks its rules; empty when it keeps them."""
    allowed_runs = derivable(catalogue)
    problems: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if entry.kind not in KINDS:
            problems.append(f"{entry.run!r}: kind must be one of {list(KINDS)}")
            continue
        if not entry.reason.strip():
            problems.append(f"{entry.run!r}: needs a reason")
        if " ".join(_words(entry.run)) != entry.run or len(entry.run.split()) != RUN:
            problems.append(f"{entry.run!r}: not one normalised {RUN}-word run")
        elif entry.run not in allowed_runs[entry.kind]:
            problems.append(
                f"{entry.run!r}: not a {entry.kind} run of the catalogue -- only "
                "identifiers and cited names may be allowed, never prose"
            )
        if entry.run in seen:
            problems.append(f"{entry.run!r}: listed twice")
        seen.add(entry.run)
    return problems


def split_known(
    hits: dict[str, list[str]], allowed: set[str]
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """A text's overlapping runs per PDF, split into known and new."""
    known = {pdf: [r for r in runs if r in allowed] for pdf, runs in hits.items()}
    new = {pdf: [r for r in runs if r not in allowed] for pdf, runs in hits.items()}
    return (
        {pdf: runs for pdf, runs in known.items() if runs},
        {pdf: runs for pdf, runs in new.items() if runs},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pdfs", nargs="+", type=Path)
    parser.add_argument("--extra", type=Path, help="file of extra lines to check")
    parser.add_argument("--allowlist", type=Path, default=ALLOWLIST)
    args = parser.parse_args()

    catalogue = load_catalogue()
    entries = load_allowlist(args.allowlist)
    problems = allowlist_problems(entries, catalogue)
    for problem in problems:
        print(f"ALLOWLIST {problem}")
    if problems:
        return 2
    allowed = {entry.run for entry in entries}
    pinned = {s.sha256: s.id for s in catalogue.sources.values()}
    unpinned = 0
    for pdf in args.pdfs:
        source_id = pinned.get(hashlib.sha256(pdf.read_bytes()).hexdigest())
        if source_id is None:
            unpinned += 1
        print(f"pdf      {pdf.name}: {source_id or 'NOT A PINNED SOURCE'}")

    tool = _pdftotext()
    source_runs = {pdf.name: _pdf_runs(pdf, tool) for pdf in args.pdfs}

    texts = _texts(catalogue)
    if args.extra:
        lines = args.extra.read_text(encoding="utf-8").splitlines()
        texts += [(f"extra:{n}", line) for n, line in enumerate(lines, 1) if line]

    known_texts = new_texts = 0
    for where, text in texts:
        words = _words(text)
        hits = {
            name: sorted(" ".join(run) for run in _runs(words) & runs)
            for name, runs in source_runs.items()
        }
        known, new = split_known(hits, allowed)
        if new:
            new_texts += 1
            print(f"OVERLAP  {where}: {new}")
        elif known:
            known_texts += 1
            print(f"known    {where}: {known}")
        else:
            note = "" if len(words) >= RUN else f" (under {RUN} words)"
            print(f"ok       {where}{note}")
    print(
        f"{len(texts)} checked against {len(source_runs)} PDF(s): "
        f"{known_texts} known overlap(s), {new_texts} new"
    )
    return 1 if new_texts or unpinned else 0


if __name__ == "__main__":
    sys.exit(main())
