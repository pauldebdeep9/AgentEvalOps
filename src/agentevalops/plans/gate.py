"""The fail-closed gate (docs/adr/0005 D6; ROADMAP R27; testing-framework
5.9.1): a bundle judged against the plan it registered.

``judge`` reads two documents from the bundle -- ``test_plan.json``'s
thresholds and selection, ``summary.json``'s pass^k per category -- and
holds every threshold to the run:

- a **hard** gate is met only when the category's pass^k rate reaches the
  threshold *and* every one of its scenarios was evaluated. A not-evaluated
  scenario fails the gate whatever the evaluated ones did ("fails closed"),
  and so does a gated category no scenario ran in;
- a **comparative** threshold is reported and never gates; where it says
  "below" it says it does not gate, so the word cannot be read as a
  tolerated failure (owner's condition, 2026-11-01);
- "not evaluated" is always followed by the causes.

The gate cannot judge, and says so instead of passing or failing, when the
plan sets no threshold, the summary carries no pass^k, or the run did not
execute exactly the scenarios the plan selected. The caller (``cli.py``)
validates the bundle first, which holds ``test_plan.json`` to the hash
registered before the first task ran. The bundle is never modified.

Exit codes: 0 every hard gate met, 1 any hard gate failed, 2 cannot judge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "EXIT_CANNOT_JUDGE",
    "EXIT_FAIL",
    "EXIT_PASS",
    "GateError",
    "GateLine",
    "GateResult",
    "judge",
]

EXIT_PASS: int = 0
EXIT_FAIL: int = 1
EXIT_CANNOT_JUDGE: int = 2


class GateError(Exception):
    """The gate cannot judge this bundle; the message says why."""


@dataclass(frozen=True)
class GateLine:
    """One plan threshold held against the run."""

    category: str
    min_pass_k: float
    min_pass_k_source: str
    gate: str
    gate_source: str
    gated: bool
    scenarios: int
    passed: int
    observed: float | None
    not_evaluated: int
    not_evaluated_causes: dict[str, int] = field(default_factory=dict)
    verdict: str = ""


@dataclass(frozen=True)
class GateResult:
    passed: bool
    lines: tuple[GateLine, ...]
    reasons: tuple[str, ...]


def _causes(causes: dict[str, int]) -> str:
    return ", ".join(f"{cause} x{n}" if n > 1 else cause for cause, n in causes.items())


def _rate(value: float) -> str:
    return str(round(value, 2))


def judge(plan: dict[str, Any], summary: dict[str, Any]) -> GateResult:
    """Hold *summary*'s pass^k to *plan*'s thresholds (documents as read
    from the bundle). Raises ``GateError`` when there is nothing to judge
    or the run did not follow its plan. Reads plan versions "1" and "2"; a
    version "2" plan with ``declared: false`` (docs/adr/0007 D2) cannot be
    judged."""
    if plan.get("declared") is False:
        raise GateError(
            "no test plan was declared before this run: nothing was registered "
            "to judge it against (docs/adr/0007 D2)"
        )
    thresholds = plan.get("thresholds")
    if not isinstance(thresholds, list) or not thresholds:
        raise GateError(
            "the plan sets no threshold: neither the pack nor the owner declared "
            "one, so there is nothing to gate"
        )
    pass_k = summary.get("pass_k")
    roll_ups_raw = summary.get("pass_k_by_category")
    if not isinstance(pass_k, list) or not isinstance(roll_ups_raw, list):
        raise GateError(
            "summary.json carries no pass_k: the bundle predates repetitions "
            "(ROADMAP R27) and cannot be gated"
        )
    ran = {str(entry.get("task_id")) for entry in pass_k if isinstance(entry, dict)}
    selected = {str(s) for s in plan.get("selected", [])}
    if ran != selected:
        missing = sorted(selected - ran)
        extra = sorted(ran - selected)
        raise GateError(
            "the run did not follow its plan: "
            + (f"planned but not run {missing}" if missing else "")
            + ("; " if missing and extra else "")
            + (f"run but not planned {extra}" if extra else "")
        )
    roll_ups = {str(r.get("category")): r for r in roll_ups_raw if isinstance(r, dict)}
    lines: list[GateLine] = []
    reasons: list[str] = []
    for threshold in thresholds:
        category = str(threshold["category"])
        min_pass_k = float(threshold["min_pass_k"])
        gate = str(threshold["gate"])
        hard = gate == "hard"
        roll_up = roll_ups.get(category)
        if roll_up is None:
            verdict = "no scenario ran" + ("; a hard gate fails closed" if hard else "")
            if hard:
                reasons.append(f"{category}: {verdict}")
            lines.append(
                GateLine(
                    category=category,
                    min_pass_k=min_pass_k,
                    min_pass_k_source=str(threshold.get("min_pass_k_source", "")),
                    gate=gate,
                    gate_source=str(threshold.get("gate_source", "")),
                    gated=hard,
                    scenarios=0,
                    passed=0,
                    observed=None,
                    not_evaluated=0,
                    verdict=verdict,
                )
            )
            continue
        scenarios = int(roll_up.get("scenarios", 0))
        passed = int(roll_up.get("passed", 0))
        not_evaluated = int(roll_up.get("not_evaluated", 0))
        causes_raw = roll_up.get("not_evaluated_causes") or {}
        causes = {str(k): int(v) for k, v in causes_raw.items()}
        observed = float(roll_up.get("pass_k_rate", 0.0))
        if not_evaluated:
            plural = "s" if not_evaluated != 1 else ""
            stem = f"{not_evaluated} scenario{plural} ({_causes(causes)})"
            if hard:
                verdict = f"not evaluated: {stem}; a hard gate fails closed"
                reasons.append(
                    f"{category}: {not_evaluated} scenario{plural} not evaluated "
                    f"({_causes(causes)}); a hard gate fails closed"
                )
            else:
                verdict = f"not evaluated: {stem}; comparative threshold; does not gate"
        elif observed >= min_pass_k:
            verdict = "met"
        elif hard:
            verdict = "below (hard gate)"
            reasons.append(
                f"{category}: pass^k {_rate(observed)} is below {min_pass_k} "
                "(hard gate)"
            )
        else:
            verdict = "below (comparative threshold; does not gate)"
        lines.append(
            GateLine(
                category=category,
                min_pass_k=min_pass_k,
                min_pass_k_source=str(threshold.get("min_pass_k_source", "")),
                gate=gate,
                gate_source=str(threshold.get("gate_source", "")),
                gated=hard,
                scenarios=scenarios,
                passed=passed,
                observed=observed,
                not_evaluated=not_evaluated,
                not_evaluated_causes=causes,
                verdict=verdict,
            )
        )
    return GateResult(passed=not reasons, lines=tuple(lines), reasons=tuple(reasons))
