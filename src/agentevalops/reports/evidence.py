"""The evidence report, ``evidence_report.md`` (docs/adr/0007 D7, amended
2026-11-02; ROADMAP R28).

Every one of the testing framework's 112 checks, by principle, with where
in the bundle the evidence for it is -- never an answer. The Implemented and
Elaboration columns are the organisation's and stay empty. A check points
at a scenario only when that scenario executed in this run (ADR-0005 D5).

The cells are short; ``controls.json`` keeps the full lists. A run-level
check (a catalogue entry marked ``run_level``) reads "every scenario in the
plan" and its artefact pointers, and the Scenarios table lists each pack
scenario once with its pass^k or the plan's reason. No cell carries a count
or a rate: there is no aggregate (ADR-0005 D2). For a scripted run the
warning sentence is in the header once, and every cell with evidence from
the run carries a "(scripted)" tag, so a row copied out keeps it.

``render_evidence_report`` is a pure function of the catalogue, the plan,
``controls.json``'s content (``plans.coverage.control_coverage``), the
summary, the runner and the run's ``created_at``: no clock is read, so two
renders from one run are byte-identical. R28 (b2) has the writer call it,
reading ``runner_id`` from ``config.json``; until then it is library code.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentevalops.catalogue import Catalogue, Control
from agentevalops.core.schemas import RunPlan, RunSummary
from agentevalops.reports.markdown import (
    MATCH_LIMITS,
    NO_PLAN_SENTENCE,
    RUNNER_NOT_RECORDED,
    SCRIPTED_RUNNER,
    SIGNATURE_LIMITS,
    plan_section,
    run_created_line,
)

SCRIPTED_SENTENCE = (
    "This run played scripted trajectories; it is not evidence of an "
    "agent's behaviour until a model-backed run (ROADMAP R30)."
)
SCRIPTED_TAG = "(scripted)"
EVERY_SCENARIO = "every scenario in the plan"
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
#: The five coverage states in words, with the legend's meaning of each.
STATE_LEGEND: dict[str, tuple[str, str]] = {
    "covered": (
        "covered",
        "every scenario the plan selected that cites the control was "
        "evaluated; each one's pass^k stands beside it, passing or not",
    ),
    "partly_covered": (
        "partly covered",
        "some of those scenarios were evaluated and some were not",
    ),
    "uncovered": (
        "uncovered",
        "the plan selected scenarios that cite the control, and none was evaluated",
    ),
    "not_in_plan": (
        "not in plan",
        "scenarios in the pack cite the control, and the plan selected none of them",
    ),
    "not_in_pack": ("not in pack", "no scenario in the tested pack cites the control"),
}


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _state(entry: Mapping[str, Any]) -> str:
    return STATE_LEGEND[entry["state"]][0]


def _pointer(evidence: str) -> str:
    """``artefact:test_plan.json#system.intended_use`` as
    ``test_plan.json → system.intended_use``."""
    target = evidence.removeprefix("artefact:")
    file, _, field = target.partition("#")
    return f"`{file}` → `{field}`" if field else f"`{file}`"


def _artefacts(control: Control) -> list[str]:
    return [_pointer(e) for e in control.evidence if e.startswith("artefact:")]


def _refs(verdict: Mapping[str, Any]) -> str:
    """``tool_calls PC7/0/2, PC7/0/4; end_states PC7/0/6``: refs written
    ``stream task/repetition/index``, grouped by stream, the end-state refs
    last."""
    by_stream: dict[str, list[str]] = {}
    for ref in [*(verdict.get("refs") or ()), *(verdict.get("end_states") or ())]:
        by_stream.setdefault(str(ref["stream"]), []).append(
            f"{ref['task_id']}/{ref['repetition']}/{ref['index']}"
        )
    return "; ".join(
        f"{stream} {', '.join(items)}" for stream, items in by_stream.items()
    )


def _verdict_word(pass_k: bool | None, cause: str | None) -> str:
    if pass_k is None:
        return f"not evaluated ({cause})"
    return "passed" if pass_k else "failed"


def _verdict(verdict: Mapping[str, Any], *, refs: bool = True) -> str:
    """``PC7 passed (tool_calls PC7/0/2; end_states PC7/0/6)``."""
    text = (
        f"{verdict['scenario_id']} {_verdict_word(verdict['pass_k'], verdict['cause'])}"
    )
    pointers = _refs(verdict) if refs and verdict["pass_k"] is not None else ""
    return f"{text} ({pointers})" if pointers else text


def _ran(entry: Mapping[str, Any]) -> bool:
    return any(v["pass_k"] is not None for v in entry["verdicts"])


def _tagged(text: str, scripted: bool) -> str:
    return f"{text} {SCRIPTED_TAG}" if scripted else text


def _scenario_cell(control: Control, entry: Mapping[str, Any], scripted: bool) -> str:
    """A direct or partial check's cell."""
    state = _state(entry)
    if entry["state"] == "not_in_pack":
        return state
    if entry["state"] == "not_in_plan":
        if control.run_level:
            return state
        return f"{state}: {', '.join(entry['excluded'])} (see Scenarios)"
    if control.run_level:
        text = "; ".join([f"{state}: {EVERY_SCENARIO}", *_artefacts(control)])
    else:
        text = f"{state}: " + "; ".join(_verdict(v) for v in entry["verdicts"])
        if _ran(entry) and _artefacts(control):
            text += "; also " + ", ".join(_artefacts(control))
    return _tagged(text, scripted and _ran(entry))


def _evidence_cell(
    control: Control,
    entry: Mapping[str, Any],
    declared: bool,
    scripted: bool,
) -> str:
    klass = control.classification
    if klass in ("direct", "partial"):
        return _scenario_cell(control, entry, scripted)
    if klass == "supporting":
        if not declared:
            return "no test plan was declared"
        return _tagged(", ".join(_artefacts(control)), scripted)
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
    runner_id: str | None,
    created_at: str,
) -> list[str]:
    run_id = controls["run_id"]
    lines = [f"# Evidence report — {run_id}", ""]
    lines.append(f"- Run: `{run_id}`")
    lines.append(f"- {run_created_line(created_at)}")
    if plan is not None and plan.system is not None:
        lines.append(f"- System: `{plan.system.system_id}`")
        lines.append(f"- Pack: `{plan.pack_id}` {plan.pack_version}")
    lines.append(f"- Runner: `{runner_id}`" if runner_id else "- Runner: not recorded")
    lines.append("")
    if runner_id is None:
        lines += [f"**{RUNNER_NOT_RECORDED}**", ""]
    if runner_id == SCRIPTED_RUNNER:
        lines += [
            f"**{SCRIPTED_SENTENCE}** Every cell with evidence from this run "
            f"is tagged {SCRIPTED_TAG}.",
            "",
        ]
    if plan is None or not plan.declared:
        lines += [NO_PLAN_SENTENCE, ""]
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
        "was evaluated in this run. `controls.json` holds every list in full.",
        "",
        "## Coverage states",
        "",
        *(f"- **{word}**: {meaning}." for word, meaning in STATE_LEGEND.values()),
        "",
    ]


def _scenarios_table(
    controls: Mapping[str, Any], plan: RunPlan | None, scripted: bool
) -> list[str]:
    """Each pack scenario once: a selected one with its pass^k, or its cause
    when pass^k is null; any other with the plan's reason."""
    scenarios: Mapping[str, Mapping[str, Any]] = controls["scenarios"]
    if not scenarios:
        return []
    lines = ["## Scenarios", ""]
    if plan is not None:
        runs = "time" if plan.repetitions == 1 else "times"
        lines += [
            f"Each selected scenario ran k = {plan.repetitions} {runs}; its "
            "pass^k is yes only if every run passed.",
            "",
        ]
    lines.append("| Scenario | Category | Plan | pass^k |")
    lines.append("|---|---|---|---|")
    for scenario_id, entry in scenarios.items():
        category = entry.get("category") or "uncategorised"
        if entry["selected"]:
            if entry["pass_k"] is None:
                verdict = f"not evaluated ({entry['not_evaluated_cause']})"
            else:
                verdict = _tagged("yes" if entry["pass_k"] else "no", scripted)
            lines.append(f"| {scenario_id} | {category} | selected | {verdict} |")
        else:
            lines.append(
                f"| {scenario_id} | {category} | "
                f"excluded: {_cell(str(entry['reason']))} | not run |"
            )
    lines.append("")
    return lines


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
    scripted: bool,
) -> list[str]:
    """The other sources' controls: state and each citing scenario's
    verdict, without refs -- the per-check cells and ``controls.json`` have
    them."""
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
            if entry["verdicts"]:
                verdicts = "; ".join(_verdict(v, refs=False) for v in entry["verdicts"])
                verdicts = _tagged(verdicts, scripted and _ran(entry))
            elif entry["excluded"]:
                verdicts = f"{', '.join(entry['excluded'])} (see Scenarios)"
            else:
                verdicts = "no scenario cites it"
            lines.append(
                f"| {control.locator} | {_cell(control.label)} | {_state(entry)} | "
                f"{_cell(verdicts)} |"
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


def _limits(declared: bool) -> list[str]:
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
        lines.append("- No scenario was selected against a declaration.")
    lines.append(f"- {SIGNATURE_LIMITS}")
    lines.append(f"- {MATCH_LIMITS}")
    lines.append("")
    return lines


def render_evidence_report(
    catalogue: Catalogue,
    plan: RunPlan | None,
    controls: Mapping[str, Any],
    summary: RunSummary,
    *,
    runner_id: str | None,
    created_at: str,
) -> str:
    """``evidence_report.md`` for one run (docs/adr/0007 D7, amended
    2026-11-02).

    *controls* is ``controls.json``'s content; *plan* is ``None`` for a run
    that declared nothing, which the report says in one sentence and shows
    nothing else of. *created_at* is the run's own timestamp, as
    ``metadata.json`` records it -- the report reads no clock. When
    *runner_id* is ``None`` -- not recorded -- the header says the report
    cannot tell whether the run was scripted. When
    *runner_id* is ``mock`` the scripted-run sentence is in the header and
    each cell with evidence from the run carries the ``(scripted)`` tag.
    """
    declared = bool(controls["declared"]) and plan is not None and plan.declared
    scripted = runner_id == SCRIPTED_RUNNER
    entries: Mapping[str, Mapping[str, Any]] = {
        c["id"]: c for c in controls["controls"]
    }
    lines = _header(controls, plan, runner_id, created_at)
    lines += _about()
    if plan is not None and declared:
        lines += plan_section(plan, summary)
        lines += _scenarios_table(controls, plan, scripted)
    lines += _principle_tables(catalogue, entries, declared, scripted)
    lines += _source_sections(catalogue, entries, scripted)
    lines += _sources(controls)
    lines += _limits(declared)
    return "\n".join(lines)
