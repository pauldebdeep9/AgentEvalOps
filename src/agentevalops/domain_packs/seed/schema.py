"""The seed domain's data model: what ``data/*.yaml`` loads into."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Applicant:
    """Who applied. ``gender`` and ``age`` are the attributes R26's
    counterfactual pairs vary; the name's ethnic group is varied through
    ``names.yaml``'s pairs. Identifiers follow docs/adr/0006 D26."""

    name: str
    gender: str
    age: int
    nric: str
    phone: str
    email: str


@dataclass(frozen=True)
class Case:
    """One seeded application with its ground truth."""

    case_id: str
    applicant: Applicant
    facts: dict[str, Any]
    #: ``approve``, ``reject`` or ``refer``.
    outcome: str
    reason_codes: tuple[str, ...]
    #: ``missing`` or ``conflict`` for an ambiguous case, else ``None``.
    ambiguity: str | None = None

    @property
    def ambiguous(self) -> bool:
        """An ambiguous case's only correct outcome is referral to an
        officer, never a decision (testing-framework 10.3.2)."""
        return self.ambiguity is not None


@dataclass(frozen=True)
class Clause:
    id: str
    title: str
    text: str


@dataclass(frozen=True)
class Policy:
    """The synthetic scheme's rules: the reason codes a decision may carry
    and the clauses ``policy_search`` returns."""

    scheme: str
    reason_codes: dict[str, str]
    clauses: tuple[Clause, ...]
    #: Which seed tool plays each role the policy rules name (ROADMAP R25):
    #: lookup, decide, notify, refer, export.
    roles: dict[str, str] = field(default_factory=dict)
    #: The argument that names a record's case.
    case_argument: str = "case_id"


@dataclass(frozen=True)
class SyntheticName:
    name: str
    gender: str
    group: str
