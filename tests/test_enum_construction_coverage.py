"""Guards against declaring an enum member nobody ever constructs.

Three independent instances of the same pattern surfaced across two
sessions: ``TerminationReason.INFRA_FAILURE`` (defined, never constructed
anywhere in ``src/`` outside its own definition), ``Verdict.WARN`` (defined
since the schema's original authoring, wired for the first time 2026-08-03),
and ``ResultBundleMetadata.sealed`` (a bool field hardcoded to a constant at
its one write site, read back nowhere — a different shape, not covered by
this file; see ``docs/adr/0002-outcome-taxonomy.md``'s "A recurring
pattern" section, and this session's report, for why a field-write/read
check is not built here).

This test makes the enum-member half of that pattern mechanically visible:
for every member of every enum below, either it is constructed somewhere in
non-test ``src/``, or it is named in ``_ALLOWLIST`` with a comment pointing
at the roadmap item that will wire it. The goal is not to forbid
forward-declared vocabulary — closed enums are supposed to name states
before every state has code behind it — it is to make an unwired member a
deliberate, documented choice instead of a silent one that nobody notices
for two sessions.

Scope: ``TraceEventKind``, ``Verdict``, ``TerminationReason`` — every enum
currently defined in ``core/schemas.py``. ``TraceEventKind`` is included
even though some of its members are more "process" than "outcome"
(``AGENT_PLAN``, ``AGENT_TOOL_CALL``) rather than a terminal state, because
scoping this check to only the two enums already known to have the problem
would have missed ``AGENT_OBSERVATION``/``EVALUATOR_SCORE``/
``POLICY_VERDICT`` — this test found those three itself, the first time it
was written.

Detection method: does the literal text ``EnumClassName.MEMBER_NAME``
appear anywhere in a non-test ``.py`` file under ``src/agentevalops/``,
outside ``core/schemas.py`` itself (the enum's own definition can't
self-reference its own members via ``ClassName.``-prefixed attribute
access, so excluding it is a no-op today, kept explicit for robustness).
This is a textual proxy for "constructed," not a construction-vs-comparison
distinction via AST — a member referenced only in a dead comparison
(``if x == TerminationReason.INFRA_FAILURE``, never actually assigned
anywhere) would pass this check without genuinely being wired. That is a
real, accepted limitation: the check is a cheap, mechanical floor ("does
this text appear anywhere at all"), not a proof of full liveness. It is
exactly strong enough to have caught all three known instances of the
pattern (each had zero occurrences, not a comparison-only false negative).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agentevalops.core.schemas import TerminationReason, TraceEventKind, Verdict

_SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "agentevalops"
_SCHEMAS_FILE = _SRC_ROOT / "core" / "schemas.py"

#: Enums audited by this test. Add a new enum here when one is added to
#: core/schemas.py -- there is no way to discover this automatically without
#: also picking up unrelated enums from other modules, which is not this
#: test's job.
_AUDITED_ENUMS: tuple[type, ...] = (TraceEventKind, Verdict, TerminationReason)

#: Members deliberately reserved for future work. Every entry MUST carry a
#: comment naming the roadmap item that will wire it -- see this module's
#: docstring. Do not remove an entry just because this test is inconveniently
#: failing; wire the member for real, or leave it here with its reference.
_ALLOWLIST: dict[type, frozenset[str]] = {
    # TerminationReason.INFRA_FAILURE wired for real 2026-08-05
    # (docs/adr/0002, Axis 1: orchestration/local.py's _run_task now
    # catches InfrastructureError/ConfigurationError per task and
    # constructs a real AgentOutput carrying this value) -- no longer
    # allowlisted. Left as an explicit empty entry, not removed, so the
    # "no roadmap-item comment" rule stays visible even for an enum with
    # nothing currently reserved.
    TerminationReason: frozenset(),
    TraceEventKind: frozenset(
        {
            # ROADMAP.md row 14 (SWE-bench spike, W14+) via
            # agents/langgraph_runner.py's own module docstring, which
            # defers file/sandbox observation tooling to "a code-editing
            # benchmark (SWE-bench, later)" -- a richer agent loop's
            # natural companion event, distinct from AGENT_TOOL_RESULT.
            "AGENT_OBSERVATION",
            # ROADMAP.md §0's 2026-08-04 entry: orchestration/local.py's
            # run() computes RunSummary.score_result but never emits a
            # corresponding trace event. No roadmap week assigned; found
            # while building this test, not previously tracked.
            "EVALUATOR_SCORE",
            # Same gap, the policy-check half -- see the same ROADMAP.md
            # §0 2026-08-04 entry. run() computes RunSummary.policy_verdict
            # but never emits one of these either.
            "POLICY_VERDICT",
        }
    ),
    Verdict: frozenset(),  # WARN wired 2026-08-03; nothing reserved here.
}


def _non_test_src_files() -> list[Path]:
    return sorted(_SRC_ROOT.rglob("*.py"))


def _construction_site_count(enum_cls: type, member_name: str) -> int:
    """Count textual occurrences of ``EnumClass.MEMBER`` in non-test src/."""
    pattern = re.compile(
        rf"\b{re.escape(enum_cls.__name__)}\.{re.escape(member_name)}\b"
    )
    count = 0
    for path in _non_test_src_files():
        if path == _SCHEMAS_FILE:
            continue
        count += len(pattern.findall(path.read_text(encoding="utf-8")))
    return count


@pytest.mark.parametrize(
    "enum_cls", _AUDITED_ENUMS, ids=lambda c: c.__name__
)
def test_every_enum_member_is_constructed_or_allowlisted(
    enum_cls: type,
) -> None:
    allowlisted = _ALLOWLIST.get(enum_cls, frozenset())
    unwired = [
        member.name
        for member in enum_cls
        if member.name not in allowlisted
        and _construction_site_count(enum_cls, member.name) == 0
    ]
    assert not unwired, (
        f"{enum_cls.__name__} member(s) {unwired} are never constructed "
        "anywhere in non-test src/agentevalops/, and are not in "
        "_ALLOWLIST. Either wire them (construct a real instance somewhere "
        "in src/) or add them to _ALLOWLIST with a comment naming the "
        "roadmap item that will wire them -- see this file's module "
        "docstring."
    )


def test_allowlist_has_no_stale_entries() -> None:
    """The inverse check: an allowlisted member that's actually been wired
    since should be removed, not left to silently hide that it happened.
    """
    stale = [
        f"{enum_cls.__name__}.{name}"
        for enum_cls, members in _ALLOWLIST.items()
        for name in members
        if _construction_site_count(enum_cls, name) > 0
    ]
    assert not stale, (
        f"Allowlist entries {stale} are now constructed somewhere in "
        "src/agentevalops/ -- remove them from _ALLOWLIST, they are no "
        "longer unwired."
    )
