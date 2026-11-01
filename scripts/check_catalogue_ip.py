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
line of FILE (e.g. labels about to be added). Needs ``pdftotext``
(poppler). Exits 1 if any text overlaps or any PDF is not a pinned source.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

from agentevalops.catalogue import Catalogue, load_catalogue

RUN = 5


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pdfs", nargs="+", type=Path)
    parser.add_argument("--extra", type=Path, help="file of extra lines to check")
    args = parser.parse_args()

    catalogue = load_catalogue()
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

    failed = 0
    for where, text in texts:
        words = _words(text)
        hits = {
            name: sorted(" ".join(run) for run in _runs(words) & runs)
            for name, runs in source_runs.items()
        }
        hits = {name: found for name, found in hits.items() if found}
        if hits:
            failed += 1
            print(f"OVERLAP  {where}: {hits}")
        else:
            note = "" if len(words) >= RUN else f" (under {RUN} words)"
            print(f"ok       {where}{note}")
    print(f"{len(texts)} checked against {len(source_runs)} PDF(s): {failed} overlap")
    return 1 if failed or unpinned else 0


if __name__ == "__main__":
    sys.exit(main())
