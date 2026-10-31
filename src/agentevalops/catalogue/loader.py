"""Load and validate the control catalogue (ADR-0005 D5; ROADMAP R22).

Each YAML file holds one version of one source document: a ``source`` block
that pins it (title, publisher, version, dates, URL, the sha256 of the file
retrieved) and the ``controls`` cited from it. A new source version is a new
data file, not a code change (ROADMAP §5 risk 2).

Control IDs are ``<source-id>:<locator>`` -- e.g. ``mgf-agentic:2.3.2`` or
``starter-kit:risk.data-leakage`` -- and carry no version, so scenarios that
cite them survive a new version of the source.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from agentevalops.core.errors import ConfigurationError

#: The only IP posture v0 knows: cite identifiers, write our own labels,
#: never reproduce the source's text (ADR-0005 D5).
IP_POSTURES: frozenset[str] = frozenset({"identifiers-and-own-labels"})

CONTROL_KINDS: frozenset[str] = frozenset({"section", "risk"})

_DATE_PRECISIONS: frozenset[str] = frozenset({"day", "month"})
_SHA256 = re.compile(r"[0-9a-f]{64}")
#: ``<source-id>:<locator>``: lowercase letters, digits, '.' and '-'.
_CONTROL_ID = re.compile(r"[a-z0-9-]+:[a-z0-9.-]+")
_AI_VERIFY = re.compile(r"ai[\s_-]*verify", re.IGNORECASE)
#: Claims, not category names: "policy compliance" is a test category
#: (MGF-Agentic §2.3.2); "compliant" and "certified" assert an outcome.
_CLAIM = re.compile(r"\b(compliant|certif\w*)\b", re.IGNORECASE)


class CatalogueError(ConfigurationError):
    """The catalogue data is malformed or breaks a catalogue rule."""


@dataclass(frozen=True)
class Source:
    """One pinned version of one source document."""

    id: str
    title: str
    publisher: str
    version: str
    published: dt.date
    url: str
    sha256: str
    retrieved_on: dt.date
    ip_posture: str
    updated: dt.date | None = None
    #: ``"month"`` when the document gives only a month (``published`` is
    #: then the 1st); ``"day"`` otherwise.
    date_precision: str = "day"


@dataclass(frozen=True)
class Control:
    """One citable control: a section or a named risk of a source."""

    id: str
    source: str
    kind: str
    locator: str
    label: str
    links: tuple[str, ...] = ()


@dataclass(frozen=True)
class Catalogue:
    sources: dict[str, Source]
    controls: dict[str, Control]

    def control(self, control_id: str) -> Control:
        try:
            return self.controls[control_id]
        except KeyError:
            raise CatalogueError(f"unknown control id '{control_id}'") from None


def _data_files() -> list[Path]:
    root = resources.files("agentevalops.catalogue") / "data"
    return sorted(
        Path(str(entry)) for entry in root.iterdir() if entry.name.endswith(".yaml")
    )


def _as_date(value: Any, where: str) -> dt.date | None:
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        raise CatalogueError(f"{where}: not an ISO date: {value!r}") from None


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _parse_file(path: Path) -> tuple[Source, list[Control]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("source"), dict):
        raise CatalogueError(f"{path.name}: needs a 'source' mapping")
    s = raw["source"]
    where = f"{path.name}: source"
    source = Source(
        id=_text(s.get("id")),
        title=_text(s.get("title")),
        publisher=_text(s.get("publisher")),
        version=_text(s.get("version")),
        published=_as_date(s.get("published"), where),  # type: ignore[arg-type]
        url=_text(s.get("url")),
        sha256=_text(s.get("sha256")),
        retrieved_on=_as_date(s.get("retrieved_on"), where),  # type: ignore[arg-type]
        ip_posture=_text(s.get("ip_posture")),
        updated=_as_date(s.get("updated"), where),
        date_precision=_text(s.get("date_precision")) or "day",
    )
    controls_raw = raw.get("controls") or []
    if not isinstance(controls_raw, list):
        raise CatalogueError(f"{path.name}: 'controls' must be a list")
    controls = []
    for c in controls_raw:
        if not isinstance(c, dict):
            raise CatalogueError(f"{path.name}: each control must be a mapping")
        links = c.get("links") or []
        controls.append(
            Control(
                id=_text(c.get("id")),
                source=_text(c.get("source")) or source.id,
                kind=_text(c.get("kind")),
                locator=_text(c.get("locator")),
                label=" ".join(_text(c.get("label")).split()),
                links=tuple(_text(link) for link in links),
            )
        )
    return source, controls


def _check_pinned(source: Source) -> None:
    """R22: a source is usable only if the exact document is pinned."""
    pins = {
        "title": source.title,
        "publisher": source.publisher,
        "version": source.version,
        "published": source.published,
        "url": source.url,
        "sha256": source.sha256,
        "retrieved_on": source.retrieved_on,
    }
    missing = sorted(name for name, value in pins.items() if not value)
    if missing:
        raise CatalogueError(
            f"source '{source.id}' is not pinned: missing {', '.join(missing)}"
        )
    if not _SHA256.fullmatch(source.sha256):
        raise CatalogueError(
            f"source '{source.id}' is not pinned: sha256 must be 64 lowercase "
            "hex characters"
        )
    if not source.url.startswith("https://"):
        raise CatalogueError(f"source '{source.id}': url must be https")
    if source.ip_posture not in IP_POSTURES:
        raise CatalogueError(
            f"source '{source.id}': unknown ip_posture '{source.ip_posture}'"
        )
    if source.date_precision not in _DATE_PRECISIONS:
        raise CatalogueError(
            f"source '{source.id}': date_precision must be one of "
            f"{sorted(_DATE_PRECISIONS)}"
        )


def _check_wording(where: str, *texts: str) -> None:
    """ADR-0005 D5 and D10: no 'AI Verify' in an ID or name, and no
    certification or compliance claim anywhere."""
    for text in texts:
        if _AI_VERIFY.search(text):
            raise CatalogueError(f"{where}: 'AI Verify' may not appear: {text!r}")
        if _CLAIM.search(text):
            raise CatalogueError(
                f"{where}: certification or compliance claim: {text!r}"
            )


def _validate(sources: list[Source], controls: list[Control]) -> None:
    """Every catalogue rule; raises ``CatalogueError`` on the first breach."""
    source_ids: set[str] = set()
    for source in sources:
        if source.id in source_ids:
            raise CatalogueError(f"duplicate source id '{source.id}'")
        source_ids.add(source.id)
        _check_pinned(source)
        _check_wording(
            f"source '{source.id}'", source.id, source.title, source.publisher
        )

    control_ids: set[str] = set()
    for control in controls:
        if control.id in control_ids:
            raise CatalogueError(f"duplicate control id '{control.id}'")
        control_ids.add(control.id)
        if control.source not in source_ids:
            raise CatalogueError(
                f"control '{control.id}': unknown source '{control.source}'"
            )
        if not _CONTROL_ID.fullmatch(control.id) or not control.id.startswith(
            f"{control.source}:"
        ):
            raise CatalogueError(
                f"malformed control id '{control.id}': expected "
                f"'{control.source}:<locator>' in lowercase, digits, '.' or '-'"
            )
        if control.kind not in CONTROL_KINDS:
            raise CatalogueError(
                f"control '{control.id}': kind must be one of {sorted(CONTROL_KINDS)}"
            )
        if not control.locator:
            raise CatalogueError(f"control '{control.id}': missing locator")
        if not control.label:
            raise CatalogueError(f"control '{control.id}': empty label")
        _check_wording(f"control '{control.id}'", control.id, control.label)

    for control in controls:
        for target in control.links:
            if target == control.id:
                raise CatalogueError(f"control '{control.id}' links to itself")
            if target not in control_ids:
                raise CatalogueError(
                    f"control '{control.id}' links to unknown control '{target}'"
                )


def load_catalogue(paths: Iterable[Path] | None = None) -> Catalogue:
    """Load and validate the catalogue.

    ``paths`` defaults to the data files shipped in this package.
    """
    sources: list[Source] = []
    controls: list[Control] = []
    for path in _data_files() if paths is None else list(paths):
        source, file_controls = _parse_file(Path(path))
        sources.append(source)
        controls.extend(file_controls)
    _validate(sources, controls)
    return Catalogue(
        sources={s.id: s for s in sources},
        controls={c.id: c for c in controls},
    )
