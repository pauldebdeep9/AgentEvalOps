"""The evidence report, ``evidence_report.md`` (docs/adr/0007 D7; ROADMAP
R28).

Every one of the testing framework's 112 checks, by principle, with where
in the bundle the evidence for it is -- never an answer. The Implemented and
Elaboration columns are the organisation's and stay empty. A check points
at a scenario only when that scenario executed in this run (ADR-0005 D5).

``render_evidence_report`` is a pure function of the catalogue, the plan,
``controls.json``'s content (``plans.coverage.control_coverage``), the
summary, the runner and the run's ``created_at``: no clock is read, so two
renders from one run are byte-identical. R28 (b) has the writer call it,
reading ``runner_id`` from ``config.json``; until then it is library code.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentevalops.catalogue import Catalogue, Control
from agentevalops.core.schemas import RunPlan, RunSummary
from agentevalops.reports.markdown import plan_section

#: The runner whose trajectories are scripted (docs/adr/0007 D4, D7).
SCRIPTED_RUNNER = "mock"
SCRIPTED_SENTENCE = (
    "This run played scripted trajectories; it is not evidence of an "
    "agent's behaviour until a model-backed run (ROADMAP R30)."
)
#: This project's names for the other sources' sections.
_SOURCE_HEADINGS = {"mgf-agentic": "MGF-Agentic", "starter-kit": "Starter Kit"}
_SCOPE_WORDS = {
    "both": "both",
    "generative": "generative AI",
    "traditional": "traditional AI",
}
_CLASS_WORDS = {
    "direct": "direct",
    "partial": "partial",
    "supporting": "supporting",
    "later": "later",
    "organisational": "organisational",
    "traditional-only": "traditional AI only",
}
_STATE_WORDS = {
    "covered": "covered",
    "partly_covered": "partly covered",
    "uncovered": "uncovered",
    "not_in_plan": "not in plan",
    "undeclared": "undeclared",
}


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _pointer(evidence: str) -> str:
    """``artefact:test_plan.json#system.intended_use`` as
    ``test_plan.json → system.intended_use``."""
    target = evidence.removeprefix("artefact:")
    file, _, field = target.partition("#")
    return f"`{file}` → `{field}`" if field else f"`{file}`"


def _refs(verdict: Mapping[str, Any]) -> str:
    """``tool_calls PC7/0/2, PC7/0/4; end_states PC7/0``: refs written
    ``stream task/repetition/index``, grouped by stream."""
    by_stream: dict[str, list[str]] = {}
    for ref in verdict.get("refs") or ():
        by_stream.setdefault(str(ref["stream"]), []).append(
            f"{ref['task_id']}/{ref['repetition']}/{ref['index']}"
        )
    groups = [f"{stream} {', '.join(items)}" for stream, items in by_stream.items()]
    end_states = verdict.get("end_states") or ()
    if end_states:
        task = verdict["scenario_id"]
        groups.append("end_states " + ", ".join(f"{task}/{r}" for r in end_states))
    return "; ".join(groups)


def _verdict(verdict: Mapping[str, Any]) -> str:
    """``PC7 passed 1/1 (tool_calls PC7/0/2; end_states PC7/0)``."""
    scenario = verdict["scenario_id"]
    if verdict["pass_k"] is None:
        return f"{scenario} not evaluated ({verdict['cause']})"
    word = "passed" if verdict["pass_k"] else "failed"
    text = f"{scenario} {word} {verdict['passed']}/{verdict['k']}"
    refs = _refs(verdict)
    return f"{text} ({refs})" if refs else text


def _scenario_evidence(entry: Mapping[str, Any]) -> str:
    """A direct or partial check's cell: the state, each citing scenario's
    verdict when it ran, or why it did not."""
    state = _STATE_WORDS[entry["state"]]
    if entry["state"] == "undeclared":
        return state
    if entry["state"] == "not_in_plan":
        reasons = "; ".join(
            f"{scenario} excluded ({reason})"
            for scenario, reason in entry["excluded"].items()
        )
        return f"{state}: {reasons}"
    parts = [_verdict(v) for v in entry["verdicts"]]
    return f"{state}: " + "; ".join(parts)


def _has_pointer(entry: Mapping[str, Any]) -> bool:
    return any(v["pass_k"] is not None for v in entry["verdicts"])


def _evidence_cell(
    control: Control,
    entry: Mapping[str, Any],
    declared: bool,
    scripted: bool,
) -> str:
    if not declared:
        return "undeclared"
    klass = control.classification
    if klass in ("direct", "partial"):
        text = _scenario_evidence(entry)
        if _has_pointer(entry):
            artefacts = [_pointer(e) for e in control.evidence if e != "scenarios"]
            if artefacts:
                text += "; also " + ", ".join(artefacts)
            if scripted:
                text += f". {SCRIPTED_SENTENCE}"
        return text
    if klass == "supporting":
        text = ", ".join(_pointer(e) for e in control.evidence)
        return f"{text}. {SCRIPTED_SENTENCE}" if scripted else text
    if klass == "later":
        return f"evidence from {control.available_from}"
    if klass == "organisational":
        return "requires the organisation's evidence"
    return "applies only if the system also contains a traditional model"


def _scope(control: Control) -> str:
    words = _SCOPE_WORDS[control.scope]
    return f"{words}, technical test" if control.technical_test else words


def _class(control: Control) -> str:
    words = _CLASS_WORDS[control.classification]
    if control.classification == "later":
        return f"{words} ({control.available_from})"
    return words


def _header(
    controls: Mapping[str, Any],
    plan: RunPlan | None,
    runner_id: str,
    created_at: str,
) -> list[str]:
    run_id = controls["run_id"]
    lines = [f"# Evidence report — {run_id}", ""]
    lines.append(f"- Run: `{run_id}`")
    lines.append(f"- Created: {created_at} (the run's, from `metadata.json`)")
    if plan is None:
        lines.append("- System: no declaration")
        lines.append("- Pack: none declared")
    else:
        lines.append(f"- System: `{plan.system.system_id}`")
        lines.append(f"- Pack: `{plan.pack_id}` {plan.pack_version}")
    lines.append(f"- Runner: `{runner_id}`")
    lines.append("")
    if runner_id == SCRIPTED_RUNNER:
        lines += [f"**{SCRIPTED_SENTENCE}**", ""]
    if plan is None:
        lines += [
            "No system was declared for this run, so no scenario was selected "
            "against a declaration and the run evidences no control.",
            "",
        ]
    return lines


def _about() -> list[str]:
    return [
        "## What this report is and is not",
        "",
        "- It points at evidence in this bundle. It never answers a check: "
        "Yes, No or N/A, and the elaboration, are the organisation's, and "
        "the Implemented and Elaboration columns are left empty for them.",
        "- It makes no claim that the system meets the framework or any "
        "other document, and no claim on its publishers' behalf.",
        "- Checks are named by identifier and described in this project's "
        "own labels, never in the framework's text; look a check up by its "
        "identifier for the framework's words.",
        "- A check points at a scenario only if that scenario executed and "
        "was evaluated in this run.",
        "",
    ]


def _principle_tables(
    catalogue: Catalogue,
    entries: Mapping[str, Mapping[str, Any]],
    declared: bool,
    scripted: bool,
) -> list[str]:
    """One table per principle of each source of process entries -- the
    testing framework's (docs/adr/0007 D10)."""
    lines = ["## Testing framework checks, by principle", ""]
    for source_id, principles in catalogue.principles.items():
        process = [
            c
            for c in catalogue.controls.values()
            if c.source == source_id and c.kind == "process"
        ]
        lines += _tables(process, principles, entries, declared, scripted)
    return lines


def _tables(
    process: list[Control],
    principles: Mapping[int, str],
    entries: Mapping[str, Mapping[str, Any]],
    declared: bool,
    scripted: bool,
) -> list[str]:
    lines: list[str] = []
    for number in sorted(principles):
        lines += [f"### {number}. {principles[number]}", ""]
        lines.append(
            "| Check | Scope | Label | Class | Evidence from this run | "
            "Implemented | Elaboration |"
        )
        lines.append("|---|---|---|---|---|---|---|")
        for control in (c for c in process if c.principle == number):
            evidence = _evidence_cell(control, entries[control.id], declared, scripted)
            lines.append(
                f"| {control.locator} | {_scope(control)} | {_cell(control.label)} "
                f"| {_class(control)} | {_cell(evidence)} |  |  |"
            )
        lines.append("")
    return lines


def _source_sections(
    catalogue: Catalogue,
    entries: Mapping[str, Mapping[str, Any]],
    declared: bool,
) -> list[str]:
    lines: list[str] = []
    for source in catalogue.sources.values():
        if source.id in catalogue.principles:
            continue
        heading = _SOURCE_HEADINGS.get(source.id, source.id)
        lines += [f"## {heading} v{source.version}", ""]
        lines.append("| Control | Label | State | Verdicts |")
        lines.append("|---|---|---|---|")
        for control in catalogue.controls.values():
            if control.source != source.id:
                continue
            entry = entries[control.id]
            state = _STATE_WORDS[entry["state"]] if declared else "undeclared"
            verdicts = (
                "; ".join(_verdict(v) for v in entry["verdicts"]) if declared else ""
            )
            lines.append(
                f"| {control.locator} | {_cell(control.label)} | {state} | "
                f"{_cell(verdicts) or '—'} |"
            )
        lines.append("")
    return lines


def _sources(controls: Mapping[str, Any]) -> list[str]:
    lines = ["## Sources", ""]
    lines.append(
        "The documents this run's control ids were resolved against, pinned "
        "by the sha256 of the file retrieved. Titles and publishers are the "
        "publishers' own names. The publishers have not reviewed or endorsed "
        "this reading of their documents."
    )
    lines.append("")
    lines.append(
        "| Source | Title | Publisher | Version | Published | URL | sha256 | "
        "Retrieved |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for s in controls["sources"]:
        lines.append(
            f"| `{s['id']}` | {_cell(s['title'])} | {_cell(s['publisher'])} | "
            f"{s['version']} | {s['published']} | {s['url']} | `{s['sha256']}` | "
            f"{s['retrieved_on']} |"
        )
    lines.append("")
    return lines


def _limits(declared: bool, scripted: bool) -> list[str]:
    lines = ["## What this report does not say", ""]
    lines.append(
        "- No composite: each scenario's pass^k stands beside each control it "
        "evidences, and nothing is added up across scenarios, controls or "
        "principles."
    )
    lines.append(
        "- No verdict on the organisation: the evidence describes the agent's "
        "behaviour in the tested domain pack, not in the organisation's own "
        "system."
    )
    if not declared:
        lines.append("- This run declared no system, so it evidences no control.")
    if scripted:
        lines.append(f"- {SCRIPTED_SENTENCE}")
    lines.append("")
    return lines


def render_evidence_report(
    catalogue: Catalogue,
    plan: RunPlan | None,
    controls: Mapping[str, Any],
    summary: RunSummary,
    *,
    runner_id: str,
    created_at: str,
) -> str:
    """``evidence_report.md`` for one run (docs/adr/0007 D7).

    *controls* is ``controls.json``'s content; *plan* is ``None`` for a run
    that declared nothing. *created_at* is the run's own timestamp, as
    ``metadata.json`` records it -- the report reads no clock. The
    scripted-run sentence appears in the header, on every evidence pointer
    and in the closing section exactly when *runner_id* is ``mock``.
    """
    declared = bool(controls["declared"]) and plan is not None
    scripted = runner_id == SCRIPTED_RUNNER
    entries: Mapping[str, Mapping[str, Any]] = {
        c["id"]: c for c in controls["controls"]
    }
    lines = _header(controls, plan, runner_id, created_at)
    lines += _about()
    if plan is not None:
        lines += plan_section(plan, summary)
    lines += _principle_tables(catalogue, entries, declared, scripted)
    lines += _source_sections(catalogue, entries, declared)
    lines += _sources(controls)
    lines += _limits(declared, scripted)
    return "\n".join(lines)
