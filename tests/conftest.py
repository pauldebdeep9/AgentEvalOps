"""Session-wide fixtures (R29 (b2) Part 1): the shared reference bundles."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from reference_bundles import ReferenceBundles, build, changes, snapshot


@pytest.fixture(scope="session")
def reference_bundles(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[ReferenceBundles]:
    """The five reference bundles (``reference_bundles.py``), written once
    per session and never edited in place. At the end of the session every
    file is hashed again: a test that changed, added or removed a file in a
    shared bundle fails the session here."""
    bundles, data_dir = build(tmp_path_factory.mktemp("reference"))
    before = snapshot(bundles)
    yield ReferenceBundles(bundles, data_dir)
    changed = changes(before, bundles)
    assert not changed, (
        "a test changed a shared reference bundle in place (copy it first, "
        f"Reference.copy): {changed}"
    )
