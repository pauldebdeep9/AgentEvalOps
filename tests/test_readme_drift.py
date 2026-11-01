"""README drift guard (ROADMAP R31, done early): the structure diagram's
packages are the top-level packages under ``src/agentevalops/``.

The first ``mermaid`` block in README.md names every package of the
distribution as a node, plus the two top-level modules (``cli`` and
``registry``). A package added or removed without the diagram following
fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
PACKAGE_DIR = ROOT / "src" / "agentevalops"

_NODE = re.compile(r"^\s*([a-z_]+)\[", re.MULTILINE)


def _first_mermaid_block(text: str) -> str:
    match = re.search(r"```mermaid\n(.*?)```", text, re.DOTALL)
    assert match, "README.md has no mermaid block"
    return match.group(1)


def _diagram_nodes() -> set[str]:
    block = _first_mermaid_block(README.read_text(encoding="utf-8"))
    nodes = set(_NODE.findall(block))
    # subgraph declarations also match the pattern; they are not packages
    subgraphs = set(re.findall(r"^\s*subgraph\s+([a-z_]+)\[", block, re.MULTILINE))
    return nodes - subgraphs


def _packages() -> set[str]:
    return {
        p.name
        for p in PACKAGE_DIR.iterdir()
        if p.is_dir() and p.name != "__pycache__" and (p / "__init__.py").exists()
    }


def _modules() -> set[str]:
    return {p.stem for p in PACKAGE_DIR.glob("*.py") if p.name != "__init__.py"}


def test_the_structure_diagram_names_every_package_and_nothing_else() -> None:
    nodes = _diagram_nodes()
    modules = _modules()
    assert modules <= nodes, (
        f"top-level modules missing from the diagram: {modules - nodes}"
    )
    assert nodes - modules == _packages(), (
        f"diagram packages {sorted(nodes - modules)} != "
        f"packages on disk {sorted(_packages())}"
    )


def test_the_guard_reads_the_real_layout() -> None:
    assert {"core", "cli", "registry"} <= _diagram_nodes()
    assert "core" in _packages() and "cli" in _modules()
