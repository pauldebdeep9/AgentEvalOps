"""The provenance library (bundles/provenance.py; docs/adr/0007 D8; ROADMAP
R28 (b1) slice 2): the package's content hash and the two git probes. The
writer records none of the new values until format 0.6 (R28 (b2)).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from agentevalops.bundles.provenance import (
    HASHED_SUFFIXES,
    UNAVAILABLE,
    editable_source,
    git_state,
    invocation_git_provenance,
    package_dir,
    package_files,
    package_git_provenance,
    package_sha256,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/agentevalops"
#: Tracked files the hash may skip: markers with no content that decides
#: anything.
_UNHASHED_ALLOWED = {"py.typed"}


def _tree(root: Path, newline: str = "\n") -> Path:
    files = {
        "__init__.py": "VALUE = 1\nOTHER = 2\n",
        "data/pack.yaml": "a: 1\nb: [x, y]\n",
        "sub/mod.py": "def f():\n    return 1\n",
    }
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.replace("\n", newline).encode("utf-8"))
    return root


# ---------------------------------------------------------------------------
# package_sha256
# ---------------------------------------------------------------------------


def test_lf_and_crlf_trees_hash_the_same(tmp_path: Path) -> None:
    lf = _tree(tmp_path / "lf")
    crlf = _tree(tmp_path / "crlf", newline="\r\n")
    assert (crlf / "sub/mod.py").read_bytes() != (lf / "sub/mod.py").read_bytes()
    assert package_sha256(crlf) == package_sha256(lf)


def test_bytecode_and_stray_files_do_not_change_the_hash(tmp_path: Path) -> None:
    tree = _tree(tmp_path / "pkg")
    before = package_sha256(tree)
    (tree / "__pycache__").mkdir()
    (tree / "__pycache__/mod.cpython-311.pyc").write_bytes(b"\x00bytecode")
    (tree / "sub/__pycache__").mkdir()
    (tree / "sub/__pycache__/x.py").write_text("# cached copy\n")
    (tree / "mod.pyc").write_bytes(b"\x00bytecode")
    (tree / ".DS_Store").write_bytes(b"\x00finder")
    assert package_sha256(tree) == before


def test_editing_a_yaml_file_changes_the_hash(tmp_path: Path) -> None:
    tree = _tree(tmp_path / "pkg")
    before = package_sha256(tree)
    (tree / "data/pack.yaml").write_text("a: 2\nb: [x, y]\n")
    assert package_sha256(tree) != before


def test_renaming_a_file_changes_the_hash(tmp_path: Path) -> None:
    tree = _tree(tmp_path / "pkg")
    before = package_sha256(tree)
    (tree / "data/pack.yaml").rename(tree / "data/pack2.yaml")
    assert package_sha256(tree) != before


def test_moving_a_file_between_directories_changes_the_hash(tmp_path: Path) -> None:
    tree = _tree(tmp_path / "pkg")
    before = package_sha256(tree)
    (tree / "data/pack.yaml").rename(tree / "sub/pack.yaml")
    assert package_sha256(tree) != before


def test_files_are_hashed_in_posix_path_order(tmp_path: Path) -> None:
    tree = _tree(tmp_path / "pkg")
    assert [p.relative_to(tree).as_posix() for p in package_files(tree)] == [
        "__init__.py",
        "data/pack.yaml",
        "sub/mod.py",
    ]


def test_the_running_package_is_this_source_tree() -> None:
    """An editable install: the package imported is ``src/agentevalops``."""
    assert package_dir() == SOURCE.resolve()
    assert package_sha256() == package_sha256(SOURCE)


# ---------------------------------------------------------------------------
# Every tracked file is hashed (a new kind of data file can't hide)
# ---------------------------------------------------------------------------


def unhashed(paths: list[str]) -> list[str]:
    """Tracked package files whose suffix the hash does not cover."""
    return sorted(
        p
        for p in paths
        if Path(p).suffix not in HASHED_SUFFIXES
        and Path(p).name not in _UNHASHED_ALLOWED
    )


def _tracked() -> list[str]:
    try:
        listed = subprocess.run(
            ["git", "ls-files", "src/agentevalops"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return listed.stdout.split()


def test_every_tracked_package_file_is_one_the_hash_covers() -> None:
    tracked = _tracked()
    assert tracked
    assert unhashed(tracked) == []


@pytest.mark.parametrize(
    "path",
    [
        "src/agentevalops/catalogue/data/extra.json",
        "src/agentevalops/domain_packs/seed/data/cases.yml",
        "src/agentevalops/.DS_Store",
    ],
)
def test_the_guard_names_a_file_the_hash_would_skip(path: str) -> None:
    assert unhashed(["src/agentevalops/__init__.py", path]) == [path]


def test_py_typed_is_the_one_allowed_marker() -> None:
    assert unhashed(["src/agentevalops/py.typed"]) == []


# ---------------------------------------------------------------------------
# The git probes, with injected inputs
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.invalid")
    _git(path, "config", "user.name", "Test")
    (path / "a.txt").write_text("a\n")
    _git(path, "add", "a.txt")
    _git(path, "commit", "-q", "-m", "first")
    return path


def _direct_url(url: str, editable: bool = True) -> str:
    return json.dumps({"url": url, "dir_info": {"editable": editable}})


def test_an_editable_install_records_its_checkouts_sha(repo: Path) -> None:
    sha, dirty = package_git_provenance(direct_url=lambda: _direct_url(repo.as_uri()))
    assert (sha, dirty) == (_git(repo, "rev-parse", "HEAD"), False)


def test_an_editable_checkout_with_changes_is_dirty(repo: Path) -> None:
    (repo / "a.txt").write_text("changed\n")
    _, dirty = package_git_provenance(direct_url=lambda: _direct_url(repo.as_uri()))
    assert dirty is True


def test_a_non_editable_install_is_unavailable_even_inside_a_repository(
    repo: Path,
) -> None:
    """A wheel unpacked inside someone else's checkout must not inherit its
    commit: there is no direct_url.json, so nothing is asked of git."""
    site = repo / "venv/site-packages/agentevalops"
    site.mkdir(parents=True)
    calls: list[Any] = []

    def run(*args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        return subprocess.run(*args, **kwargs)

    assert git_state(site) != (UNAVAILABLE, None)  # git *would* find the repo
    assert package_git_provenance(direct_url=lambda: None, run=run) == (
        UNAVAILABLE,
        None,
    )
    assert calls == []


@pytest.mark.parametrize(
    "record",
    [
        _direct_url("https://example.test/agentevalops.tar.gz"),
        json.dumps({"url": "file:///nowhere", "archive_info": {}}),
        "not json",
        json.dumps(["a list"]),
        "",
    ],
)
def test_other_install_records_are_not_editable(repo: Path, record: str) -> None:
    assert editable_source(lambda: record) is None


def test_a_non_editable_directory_install_is_not_editable(repo: Path) -> None:
    assert editable_source(lambda: _direct_url(repo.as_uri(), editable=False)) is None


def test_an_editable_record_pointing_at_a_missing_directory_is_not_used(
    tmp_path: Path,
) -> None:
    gone = tmp_path / "gone"
    assert editable_source(lambda: _direct_url(gone.as_uri())) is None


def test_this_install_is_editable_from_this_checkout() -> None:
    """How the tests run, here and in CI (``pip install -e``)."""
    source = editable_source()
    if source is None:
        pytest.skip("not an editable install")
    assert source == ROOT


def test_an_injected_git_failure_is_unavailable(repo: Path) -> None:
    def broken(*args: Any, **kwargs: Any) -> Any:
        raise FileNotFoundError("git: command not found")

    assert package_git_provenance(
        direct_url=lambda: _direct_url(repo.as_uri()), run=broken
    ) == (UNAVAILABLE, None)
    assert invocation_git_provenance(repo, run=broken) == (UNAVAILABLE, None)


def test_the_invocation_probe_reads_the_callers_directory(
    repo: Path, tmp_path: Path
) -> None:
    assert invocation_git_provenance(repo)[0] == _git(repo, "rev-parse", "HEAD")
    outside = tmp_path / "outside"
    outside.mkdir()
    assert invocation_git_provenance(outside) == (UNAVAILABLE, None)
