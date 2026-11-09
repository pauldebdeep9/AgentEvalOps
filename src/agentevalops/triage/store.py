"""The triage sidecar: a reviewer's verdicts beside a bundle (ROADMAP R38).

``<bundle>.triage.jsonl`` sits next to the bundle directory, never inside
it, so the bundle's files, manifest and signature stay as they were and
re-validation never sees the sidecar. It is a log: one JSON line per
annotation -- ``task_id``, ``repetition``, ``verdict``, ``note``,
``annotated_at``, the bundle's ``run_id``, and ``annotator`` only when one
was given (nothing is inferred from the environment) -- appended, never
edited. A second annotation of the same task run appends a line that
supersedes the first; a reader takes the last line per (task_id,
repetition). ``annotated_at`` comes from an injectable clock.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, get_args

__all__ = [
    "SIDECAR_SUFFIX",
    "VERDICTS",
    "Annotation",
    "TriageError",
    "Verdict",
    "append_annotation",
    "load_annotations",
    "sidecar_path",
]

Verdict = Literal["pass", "fail", "defer"]
VERDICTS: tuple[str, ...] = get_args(Verdict)
SIDECAR_SUFFIX = ".triage.jsonl"

_REQUIRED = ("task_id", "repetition", "verdict", "note", "annotated_at", "run_id")


class TriageError(ValueError):
    """A verdict, or a sidecar line, that does not fit."""


@dataclass(frozen=True)
class Annotation:
    """One reviewer verdict on one task run, as one sidecar line."""

    task_id: str
    repetition: int
    verdict: Verdict
    note: str
    annotated_at: str
    run_id: str
    annotator: str | None = None

    @property
    def key(self) -> tuple[str, int]:
        return (self.task_id, self.repetition)

    def to_line(self) -> str:
        data: dict[str, Any] = {
            "task_id": self.task_id,
            "repetition": self.repetition,
            "verdict": self.verdict,
            "note": self.note,
            "annotated_at": self.annotated_at,
            "run_id": self.run_id,
        }
        if self.annotator is not None:
            data["annotator"] = self.annotator
        return json.dumps(data, ensure_ascii=False)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sidecar_path(bundle: Path) -> Path:
    """``<bundle>.triage.jsonl``, beside the bundle directory -- never
    under it."""
    resolved = Path(bundle).resolve()
    return resolved.parent / f"{resolved.name}{SIDECAR_SUFFIX}"


def append_annotation(
    bundle: Path,
    run_id: str,
    task_id: str,
    repetition: int,
    verdict: Verdict,
    note: str,
    *,
    annotator: str | None = None,
    now: Callable[[], datetime] = _utcnow,
) -> Annotation:
    """Append one annotation to *bundle*'s sidecar, and return it."""
    if verdict not in VERDICTS:
        raise TriageError(f"verdict must be one of {list(VERDICTS)}, got {verdict!r}")
    annotation = Annotation(
        task_id=task_id,
        repetition=repetition,
        verdict=verdict,
        note=note,
        annotated_at=now().isoformat(),
        run_id=run_id,
        annotator=annotator,
    )
    with sidecar_path(bundle).open("a", encoding="utf-8") as handle:
        handle.write(annotation.to_line() + "\n")
    return annotation


def _parse(data: Any, where: str) -> Annotation:
    if not isinstance(data, dict):
        raise TriageError(f"{where}: expected an object")
    missing = [k for k in _REQUIRED if k not in data]
    unknown = sorted(set(data) - {*_REQUIRED, "annotator"})
    if missing or unknown:
        raise TriageError(f"{where}: missing {missing}, unknown {unknown}")
    repetition = data["repetition"]
    if isinstance(repetition, bool) or not isinstance(repetition, int):
        raise TriageError(f"{where}: repetition must be an integer")
    texts = [
        k
        for k in ("task_id", "note", "annotated_at", "run_id")
        if not isinstance(data[k], str)
    ]
    annotator = data.get("annotator")
    if texts or not (annotator is None or isinstance(annotator, str)):
        raise TriageError(f"{where}: {texts or ['annotator']} must be text")
    if data["verdict"] not in VERDICTS:
        raise TriageError(f"{where}: verdict must be one of {list(VERDICTS)}")
    return Annotation(
        task_id=data["task_id"],
        repetition=repetition,
        verdict=data["verdict"],
        note=data["note"],
        annotated_at=data["annotated_at"],
        run_id=data["run_id"],
        annotator=annotator,
    )


def load_annotations(bundle: Path, run_id: str) -> dict[tuple[str, int], Annotation]:
    """*bundle*'s annotations, the last per (task_id, repetition), keyed in
    the order each task run was first annotated; empty when there is no
    sidecar. A line that does not load, or that annotates another run, is
    an error naming it."""
    path = sidecar_path(bundle)
    if not path.exists():
        return {}
    annotations: dict[tuple[str, int], Annotation] = {}
    for number, line in enumerate(path.read_text("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        where = f"{path.name} line {number}"
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TriageError(f"{where}: not JSON ({exc.msg})") from None
        annotation = _parse(data, where)
        if annotation.run_id != run_id:
            raise TriageError(
                f"{where}: annotates run {annotation.run_id!r}, not {run_id!r}"
            )
        annotations[annotation.key] = annotation
    return annotations
