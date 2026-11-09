"""docs/triage.md against the code (ROADMAP R38, slice 4): the passing
share it names is the library's default and the command's."""

from __future__ import annotations

import inspect
from pathlib import Path

from agentevalops.cli import triage
from agentevalops.triage.frame import DEFAULT_PASSING_SHARE

DOC = Path(__file__).resolve().parents[1] / "docs" / "triage.md"


def test_docs_triage_names_the_passing_share_default() -> None:
    text = DOC.read_text("utf-8")
    assert f"{DEFAULT_PASSING_SHARE} unless `--passing-share` sets it" in text
    assert "never skipped" in text
    default = inspect.signature(triage).parameters["passing_share"].default
    assert default == DEFAULT_PASSING_SHARE
