"""README drift guard (ROADMAP R31, done early): the structure diagram's
packages are the top-level packages under ``src/agentevalops/``.

The README's structure diagram is drawn from ``STRUCTURE`` in
scripts/render_diagrams.py, which names every package of the distribution
as a box, plus the two top-level modules (``cli`` and ``registry``). The
script holds itself to that rule before drawing (``package_problems``);
this test holds the committed data to it, so a package added or removed
without the diagram following fails here.
"""

from __future__ import annotations

from test_diagrams import diagrams


def test_the_structure_diagram_names_every_package_and_nothing_else() -> None:
    assert diagrams.package_problems() == []


def test_the_guard_reads_the_real_layout() -> None:
    names = {name for _, _, group in diagrams.STRUCTURE for name, _ in group}
    assert {"core", "cli", "registry"} <= names
    on_disk = diagrams.packages_on_disk()
    assert {"core", "replay", "cli", "registry"} <= on_disk
    assert "__pycache__" not in on_disk
