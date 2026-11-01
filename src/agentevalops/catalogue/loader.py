"""Load and validate the control catalogue (ADR-0005 D5; ROADMAP R22).

Each YAML file holds one version of one source document: a ``source`` block
that pins it (title, publisher, version, dates, URL, the sha256 of the file
retrieved) and the ``controls`` cited from it. A new source version is a new
data file, not a code change (ROADMAP §5 risk 2).

Control IDs are ``<source-id>:<locator>`` -- e.g. ``mgf-agentic:2.3.2`` or
``starter-kit:risk.data-leakage`` -- and carry no version, so scenarios that
cite them survive a new version of the source.

A source of ``process`` entries (the testing framework's 112 checks,
docs/adr/0007 D10) also carries a ``principles`` map -- number to this
project's short label -- and ``process_counts``, the total and the count of
each classification, which the loader holds the entries to.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from agentevalops.core.errors import ConfigurationError

#: The only IP posture v0 knows: cite identifiers, write our own labels,
#: never reproduce the source's text (ADR-0005 D5).
IP_POSTURES: frozenset[str] = frozenset({"identifiers-and-own-labels"})

CONTROL_KINDS: frozenset[str] = frozenset({"section", "risk", "process"})

#: A process entry's fields (docs/adr/0007 D10). ``scope`` is the source's
#: own scoping; ``classification`` is what a test run contributes
#: (docs/imda-alignment.md §2).
PROCESS_SCOPES: frozenset[str] = frozenset({"both", "generative", "traditional"})
CLASSIFICATIONS: tuple[str, ...] = (
    "direct",
    "partial",
    "supporting",
    "later",
    "organisational",
    "traditional-only",
)
#: The classes a scenario may cite; the others are evidenced by artefacts,
#: by a later version, or by the organisation alone (D11).
SCENARIO_CLASSES: frozenset[str] = frozenset({"direct", "partial"})
_EVIDENCED_CLASSES: frozenset[str] = frozenset({"direct", "partial", "supporting"})
_LATER_VERSIONS: frozenset[str] = frozenset({"v0.6", "v0.7"})

#: The files of a format 0.6 bundle (docs/adr/0007 D1), which an
#: ``artefact:`` evidence pointer must name. The catalogue imports only
#: ``core``, so the list lives here; ``tests/test_catalogue.py`` holds it
#: to ``bundles.constants``.
BUNDLE_0_6_FILES: frozenset[str] = frozenset(
    {
        "metadata.json",
        "config.json",
        "traces.jsonl",
        "evaluations.json",
        "policy.json",
        "summary.json",
        "report.md",
        "replay_command.txt",
        "tool_calls.jsonl",
        "end_states.jsonl",
        "test_plan.json",
        "controls.json",
        "evidence_report.md",
        "manifest.json",
        "manifest.json.sig",
    }
)
#: An evidence pointer: the citing scenarios, or a 0.6 file and optionally
#: a field in it.
SCENARIOS_EVIDENCE = "scenarios"
_ARTEFACT = re.compile(r"artefact:([A-Za-z0-9_.]+)(?:#([A-Za-z0-9_.]+))?")
_ROW = re.compile(r"R\d+|built")

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
    #: A ``process`` entry's fields (docs/adr/0007 D10); unset otherwise.
    principle: int | None = None
    scope: str = ""
    technical_test: bool = False
    classification: str = ""
    available_from: str = ""
    evidence: tuple[str, ...] = ()
    rows: tuple[str, ...] = ()


@dataclass(frozen=True)
class Catalogue:
    sources: dict[str, Source]
    controls: dict[str, Control]
    #: Source id -> principle number -> this project's short label, for a
    #: source of ``process`` entries.
    principles: dict[str, dict[int, str]] = field(default_factory=dict)

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


def _texts(value: Any) -> tuple[str, ...]:
    return tuple(_text(item) for item in value or [])


#: PyYAML's libyaml-backed parser when the wheel has it: the same safe
#: loading, faster on the 112 process entries the tests load many times.
_SAFE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


@dataclass(frozen=True)
class _File:
    """One data file as parsed, before the catalogue-wide checks."""

    source: Source
    controls: list[Control]
    principles: dict[int, str]
    process_counts: dict[str, int] | None


def _principles(raw: Any, name: str) -> dict[int, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise CatalogueError(f"{name}: 'principles' must be a mapping")
    principles: dict[int, str] = {}
    for number, label in raw.items():
        if not isinstance(number, int) or isinstance(number, bool):
            raise CatalogueError(f"{name}: principle number {number!r} is not an int")
        principles[number] = " ".join(_text(label).split())
    return principles


def _counts(raw: Any, name: str) -> dict[str, int] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or not all(
        isinstance(v, int) and not isinstance(v, bool) for v in raw.values()
    ):
        raise CatalogueError(f"{name}: 'process_counts' must map names to ints")
    return {str(k): v for k, v in raw.items()}


def _parse_file(path: Path) -> _File:
    raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_SAFE_LOADER)
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
        principle = c.get("principle")
        technical_test = c.get("technical_test", False)
        controls.append(
            Control(
                id=_text(c.get("id")),
                source=_text(c.get("source")) or source.id,
                kind=_text(c.get("kind")),
                locator=_text(c.get("locator")),
                label=" ".join(_text(c.get("label")).split()),
                links=_texts(c.get("links")),
                principle=(
                    principle
                    if isinstance(principle, int) and not isinstance(principle, bool)
                    else None
                ),
                scope=_text(c.get("scope")),
                technical_test=technical_test is True,
                classification=_text(c.get("classification")),
                available_from=_text(c.get("available_from")),
                evidence=_texts(c.get("evidence")),
                rows=_texts(c.get("rows")),
            )
        )
    return _File(
        source=source,
        controls=controls,
        principles=_principles(raw.get("principles"), path.name),
        process_counts=_counts(raw.get("process_counts"), path.name),
    )


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


def _check_claims(where: str, *texts: str) -> None:
    """ADR-0005 D10: no certification or compliance claim anywhere."""
    for text in texts:
        if _CLAIM.search(text):
            raise CatalogueError(
                f"{where}: certification or compliance claim: {text!r}"
            )


def _check_wording(where: str, *texts: str) -> None:
    """ADR-0005 D5 and D10: no 'AI Verify' in an ID, a label or anything
    that names an artefact, and no claim."""
    for text in texts:
        if _AI_VERIFY.search(text):
            raise CatalogueError(f"{where}: 'AI Verify' may not appear: {text!r}")
    _check_claims(where, *texts)


def _check_process(control: Control, principles: dict[int, str]) -> None:
    """docs/adr/0007 D10: a process entry carries every field, consistently."""
    where = f"control '{control.id}'"
    if control.principle not in principles:
        raise CatalogueError(
            f"{where}: principle {control.principle!r} is not in the source's "
            "principles map"
        )
    if control.locator.split(".")[0] != str(control.principle):
        raise CatalogueError(
            f"{where}: locator '{control.locator}' is not under principle "
            f"{control.principle}"
        )
    if control.scope not in PROCESS_SCOPES:
        raise CatalogueError(f"{where}: scope must be one of {sorted(PROCESS_SCOPES)}")
    if control.classification not in CLASSIFICATIONS:
        raise CatalogueError(
            f"{where}: classification must be one of {list(CLASSIFICATIONS)}"
        )
    later = control.classification == "later"
    if later != bool(control.available_from):
        raise CatalogueError(
            f"{where}: available_from is set exactly when the class is 'later'"
        )
    if later and control.available_from not in _LATER_VERSIONS:
        raise CatalogueError(
            f"{where}: available_from must be one of {sorted(_LATER_VERSIONS)}"
        )
    evidenced = control.classification in _EVIDENCED_CLASSES
    if evidenced != bool(control.evidence):
        raise CatalogueError(
            f"{where}: evidence is listed exactly when the class is one of "
            f"{sorted(_EVIDENCED_CLASSES)}"
        )
    cites = SCENARIOS_EVIDENCE in control.evidence
    if cites != (control.classification in SCENARIO_CLASSES):
        raise CatalogueError(
            f"{where}: '{SCENARIOS_EVIDENCE}' is evidence exactly when the class "
            f"is one of {sorted(SCENARIO_CLASSES)} (docs/adr/0007 D11)"
        )
    for pointer in control.evidence:
        if pointer == SCENARIOS_EVIDENCE:
            continue
        match = _ARTEFACT.fullmatch(pointer)
        if match is None or match.group(1) not in BUNDLE_0_6_FILES:
            raise CatalogueError(
                f"{where}: evidence '{pointer}' does not name a format 0.6 bundle file"
            )
    for row in control.rows:
        if not _ROW.fullmatch(row):
            raise CatalogueError(f"{where}: row '{row}' is not 'R<n>' or 'built'")


def _check_counts(
    source_id: str, controls: list[Control], declared: dict[str, int]
) -> None:
    """docs/adr/0007 D10: the source has exactly the declared number of
    process entries, in each class and in total."""
    found = {name: 0 for name in CLASSIFICATIONS}
    for control in controls:
        found[control.classification] += 1
    expected_keys = {"total", *CLASSIFICATIONS}
    if set(declared) != expected_keys:
        raise CatalogueError(
            f"source '{source_id}': process_counts must name exactly "
            f"{sorted(expected_keys)}"
        )
    if len(controls) != declared["total"]:
        raise CatalogueError(
            f"source '{source_id}': {len(controls)} process entries, "
            f"{declared['total']} declared"
        )
    for name in CLASSIFICATIONS:
        if found[name] != declared[name]:
            raise CatalogueError(
                f"source '{source_id}': {found[name]} '{name}' entries, "
                f"{declared[name]} declared"
            )


def _validate(files: list[_File]) -> None:
    """Every catalogue rule; raises ``CatalogueError`` on the first breach."""
    sources = [f.source for f in files]
    controls = [c for f in files for c in f.controls]
    source_ids: set[str] = set()
    for source in sources:
        if source.id in source_ids:
            raise CatalogueError(f"duplicate source id '{source.id}'")
        source_ids.add(source.id)
        _check_pinned(source)
        _check_wording(f"source '{source.id}'", source.id)
        # The one exemption (docs/adr/0007 D10): a title and publisher cite
        # the publisher's own names, so they may carry the framework's name;
        # they still may not claim an outcome.
        _check_claims(f"source '{source.id}'", source.title, source.publisher)

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
        _check_wording(
            f"control '{control.id}'",
            control.id,
            control.label,
            *control.evidence,
            *control.rows,
        )

    for f in files:
        where = f"source '{f.source.id}'"
        for number, label in f.principles.items():
            if not label:
                raise CatalogueError(f"{where}: principle {number} has no label")
            _check_wording(f"{where}: principle {number}", label)
        process = [c for c in f.controls if c.kind == "process"]
        if process and not f.principles:
            raise CatalogueError(f"{where}: process entries need a 'principles' map")
        if process and f.process_counts is None:
            raise CatalogueError(
                f"{where}: process entries need 'process_counts' (docs/adr/0007 D10)"
            )
        for control in process:
            _check_process(control, f.principles)
        if f.process_counts is not None:
            _check_counts(f.source.id, process, f.process_counts)

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
    files = [
        _parse_file(Path(path)) for path in (_data_files() if paths is None else paths)
    ]
    _validate(files)
    return Catalogue(
        sources={f.source.id: f.source for f in files},
        controls={c.id: c for f in files for c in f.controls},
        principles={f.source.id: f.principles for f in files if f.principles},
    )
