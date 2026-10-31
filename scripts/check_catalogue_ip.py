"""Check that no catalogue label shares five consecutive words with a source.

The docs/imda-alignment.md §12 standard, applied to the control catalogue
(ADR-0005 D5; CLAUDE.md, "Third-party framework text"). The source PDFs are
never committed, so this is a dev script, not a test, and CI cannot run it:

    $ENV/bin/python scripts/check_catalogue_ip.py \\
        ~/Documents/aeo-sources/mgf-for-agentic-ai.pdf \\
        ~/Documents/aeo-sources/starter-kit-llm-testing.pdf

``--extra FILE`` also checks each non-empty line of FILE (e.g. labels about
to be added). Needs ``pdftotext`` (poppler). Exits 1 if any label overlaps.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

from agentevalops.catalogue import load_catalogue

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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pdfs", nargs="+", type=Path)
    parser.add_argument("--extra", type=Path, help="file of extra lines to check")
    args = parser.parse_args()

    tool = _pdftotext()
    source_runs = {pdf.name: _pdf_runs(pdf, tool) for pdf in args.pdfs}

    texts = [(c.id, c.label) for c in load_catalogue().controls.values()]
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
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
