"""What code produced a bundle (docs/adr/0007 D8; ROADMAP R28).

- ``package_sha256`` identifies the package's code and data on any install:
  a hash over every ``.py`` and ``.yaml`` file under the package directory,
  keyed by POSIX relative path, sorted, with CRLF read as LF, ``__pycache__``
  skipped. The catalogue and the seed pack's data decide verdicts, so they
  are in it; a ``.pyc`` or a stray ``.DS_Store`` is not. A test holds every
  tracked file under ``src/agentevalops`` to these two suffixes, so a new
  kind of data file cannot change results without changing the hash.
- ``package_git_provenance`` is ``(git_sha, git_dirty)`` of the checkout the
  package was installed from in editable mode, read where
  ``direct_url.json`` says, and ``"unavailable"`` for any other install --
  even if ``git`` would find some other repository around the files.
- ``invocation_git_provenance`` is the same pair for the caller's working
  directory: what bundles up to format 0.5 record as ``git_sha``.

The git probes take an injectable ``run`` (``subprocess.run``'s signature)
and direct-URL reader, so tests need not depend on this checkout's state.
Format 0.6 records all three (R28 (b2)); until then the writer records the
invocation pair only, as before.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from importlib import metadata
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

#: Recorded when a value cannot be determined: never ``None``, never made
#: up (docs/adr/0002, Axis 4).
UNAVAILABLE = "unavailable"

#: The suffixes ``package_sha256`` covers: code and the data that decides
#: verdicts.
HASHED_SUFFIXES: frozenset[str] = frozenset({".py", ".yaml"})

DISTRIBUTION = "agentevalops"

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def package_dir() -> Path:
    """The directory the running ``agentevalops`` package was imported from."""
    import agentevalops

    return Path(agentevalops.__file__).resolve().parent


def package_files(root: Path) -> list[Path]:
    """The files ``package_sha256`` covers under *root*, in hash order."""
    found = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix in HASHED_SUFFIXES
        and "__pycache__" not in path.relative_to(root).parts
    ]
    return sorted(found, key=lambda path: path.relative_to(root).as_posix())


def package_sha256(root: Path | None = None) -> str:
    """sha256 over *root* (default: the running package): for each file,
    its POSIX relative path, a NUL, its bytes with CRLF as LF, a NUL."""
    base = package_dir() if root is None else root
    digest = hashlib.sha256()
    for path in package_files(base):
        digest.update(path.relative_to(base).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
        digest.update(b"\0")
    return digest.hexdigest()


def git_state(
    directory: Path | None = None, *, run: Runner | None = None
) -> tuple[str, bool | None]:
    """Best-effort ``(git_sha, git_dirty)`` for *directory* (the process's
    working directory when ``None``). Never raises.

    ``(UNAVAILABLE, None)`` if ``git`` is missing, times out, or the
    directory is not a checkout; ``(sha, None)`` if the sha was found but
    the working tree's state was not -- a made-up "clean" is the false
    provenance claim this exists to avoid.
    """
    runner = run or subprocess.run
    try:
        sha_result = runner(
            ["git", "rev-parse", "HEAD"],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return (UNAVAILABLE, None)
    sha = sha_result.stdout.strip()
    if sha_result.returncode != 0 or not sha:
        return (UNAVAILABLE, None)
    try:
        status_result = runner(
            ["git", "status", "--porcelain"],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return (sha, None)
    if status_result.returncode != 0:
        return (sha, None)
    return (sha, bool(status_result.stdout.strip()))


def _direct_url() -> str | None:
    try:
        return metadata.distribution(DISTRIBUTION).read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return None


def editable_source(
    direct_url: Callable[[], str | None] | None = None,
) -> Path | None:
    """The checkout an editable install points at, from ``direct_url.json``
    (PEP 610); ``None`` for any other install or an unreadable record."""
    text = (direct_url or _direct_url)()
    if not text:
        return None
    try:
        record: Any = json.loads(text)
    except ValueError:
        return None
    if not isinstance(record, dict):
        return None
    dir_info = record.get("dir_info")
    if not isinstance(dir_info, dict) or dir_info.get("editable") is not True:
        return None
    url = urlparse(str(record.get("url", "")))
    if url.scheme != "file":
        return None
    source = Path(unquote(url.path))
    return source if source.is_dir() else None


def package_git_provenance(
    *,
    direct_url: Callable[[], str | None] | None = None,
    run: Runner | None = None,
) -> tuple[str, bool | None]:
    """``(git_sha, git_dirty)`` of the package's own source tree: the
    editable install's checkout, or ``(UNAVAILABLE, None)``."""
    source = editable_source(direct_url)
    if source is None:
        return (UNAVAILABLE, None)
    return git_state(source, run=run)


def invocation_git_provenance(
    cwd: Path | None = None, *, run: Runner | None = None
) -> tuple[str, bool | None]:
    """``(git_sha, git_dirty)`` of the caller's working directory."""
    return git_state(cwd, run=run)
