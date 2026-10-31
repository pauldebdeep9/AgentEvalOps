"""Scripted trajectories as data (docs/adr/0006 D11).

A script is a sequence of steps -- ``{"call": tool, "arguments": {...}}``,
``{"say": text}`` or ``{"finish": answer}``. ``MockAgentRunner`` runs one;
domain packs declare one known-good and one known-bad per scenario; the
orchestrator's preflight runs the known-good. The grammar lives in ``core``
so that all three agree on it without importing each other.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from agentevalops.core.errors import ConfigurationError


@dataclass(frozen=True)
class ScriptStep:
    """One step of a scripted trajectory (docs/adr/0006 D11).

    ``call`` runs ``tool`` with ``arguments`` through the task's environment
    and reports it -- as ``report_as`` instead, if set, which makes a
    deliberately divergent script for the self-report cross-check. ``say``
    is text the scripted agent produces; ``finish`` ends with ``text`` as
    the answer.
    """

    kind: Literal["call", "say", "finish"]
    tool: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    text: str = ""
    report_as: str | None = None


def parse_script(
    raw: Sequence[Mapping[str, Any] | ScriptStep],
) -> tuple[ScriptStep, ...]:
    """Turn script data -- ``{"call": tool, "arguments": {...}}``,
    ``{"say": text}`` or ``{"finish": answer}`` -- into steps. Raises
    ``ConfigurationError`` on anything else, so a broken script fails when
    the runner is built, not halfway through a run."""
    steps = []
    for position, item in enumerate(raw):
        if isinstance(item, ScriptStep):
            steps.append(item)
            continue
        kinds = [k for k in ("call", "say", "finish") if k in item]
        if len(kinds) != 1:
            raise ConfigurationError(
                f"script step {position}: needs exactly one of 'call', 'say' "
                f"or 'finish', got {sorted(item)}"
            )
        kind = kinds[0]
        value = item[kind]
        if not isinstance(value, str) or (kind == "call" and not value):
            raise ConfigurationError(
                f"script step {position}: '{kind}' must be a "
                f"{'tool name' if kind == 'call' else 'string'}"
            )
        arguments = item.get("arguments", {})
        if not isinstance(arguments, Mapping):
            raise ConfigurationError(
                f"script step {position}: 'arguments' must be a mapping"
            )
        report_as = item.get("report_as")
        steps.append(
            ScriptStep(
                kind=kind,  # type: ignore[arg-type]
                tool=value if kind == "call" else "",
                arguments=dict(arguments),
                text=value if kind != "call" else "",
                report_as=str(report_as) if report_as is not None else None,
            )
        )
    return tuple(steps)


__all__ = ["ScriptStep", "parse_script"]
