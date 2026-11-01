"""Load and validate the seed domain pack (docs/adr/0006 D11, D23, D26).

Everything the pack offers is data under ``data/``: the policy, the names,
the cases and the scenarios. Loading validates all of it -- shapes, the
rules that tie the files together, every control id against the shipped
catalogue, and that no string anywhere could seed a real person's
identifier -- so a bad pack fails when it is built, never in a run.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator, Mapping, Sequence
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from agentevalops.catalogue import Catalogue, CatalogueError, load_catalogue
from agentevalops.core.errors import ConfigurationError
from agentevalops.core.identifiers import (
    is_synthetic_email,
    is_synthetic_nric,
    is_synthetic_phone,
    unsafe_identifiers,
)
from agentevalops.core.perturbation import PERTURBATION_FAMILIES
from agentevalops.core.schemas import (
    FAULT_KINDS,
    NEGATIVE_OUTCOMES,
    POLICY_RULES,
    Scenario,
)
from agentevalops.core.scripts import ScriptStep, parse_script
from agentevalops.core.types import TaskId
from agentevalops.domain_packs import DomainPackError
from agentevalops.domain_packs.seed.environment import (
    REFER_TO_OFFICER,
    STATUS_UPDATE,
    TOOL_SPECS,
    build_environment,
)
from agentevalops.domain_packs.seed.schema import (
    Applicant,
    Case,
    Clause,
    Policy,
    SyntheticName,
)
from agentevalops.environments import APPROVAL_ANSWERS, InProcessToolEnvironment

#: The evaluator kinds a scenario may declare (docs/adr/0006 D13).
EVALUATOR_KINDS: frozenset[str] = frozenset(
    {"deterministic", "tool_use", "state_based", "policy"}
)
OUTCOMES: tuple[str, ...] = ("approve", "reject", "refer")
AMBIGUITIES: frozenset[str] = frozenset({"missing", "conflict"})
GENDERS: frozenset[str] = frozenset({"female", "male"})
CATEGORIES: frozenset[str] = frozenset(
    {"task_execution", "tool_calling", "policy_compliance", "robustness"}
)
#: The roles the policy rules name; every one maps to a seed tool.
POLICY_ROLES: tuple[str, ...] = ("lookup", "decide", "notify", "refer", "export")
DATA_FILES: tuple[str, ...] = (
    "policy.yaml",
    "names.yaml",
    "cases.yaml",
    "scenarios.yaml",
)
_TOOL_USE_KEYS: frozenset[str] = frozenset({"required", "forbidden", "order"})
_EXPECTED_KEYS: frozenset[str] = frozenset(
    {
        "expected_output",
        "expected_substring",
        "match_mode",
        "tool_use",
        "expected_end_state",
        "policy",
    }
)
_TOOL_NAMES: frozenset[str] = frozenset(spec.name for spec in TOOL_SPECS)
_IRREVERSIBLE: frozenset[str] = frozenset(
    spec.name for spec in TOOL_SPECS if spec.permission == "irreversible"
)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


#: PyYAML's libyaml-backed parser when the wheel has it, else the pure
#: Python one: the same safe loading, several times faster on the pack's
#: thousand-line scenarios.yaml -- which the tests parse dozens of times.
_SAFE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def _read(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_SAFE_LOADER)
    except OSError as exc:
        raise DomainPackError(f"{path.name}: cannot read: {exc}") from exc
    except yaml.YAMLError as exc:
        raise DomainPackError(f"{path.name}: malformed YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise DomainPackError(f"{path.name}: must be a YAML mapping")
    return raw


def strings_in(value: Any) -> Iterator[str]:
    """Every string anywhere in *value* -- keys and leaves alike."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from strings_in(key)
            yield from strings_in(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from strings_in(item)


def _text(value: Any, where: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DomainPackError(f"{where}: {name} must be a non-empty string")
    return value.strip()


def _mapping(value: Any, where: str, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DomainPackError(f"{where}: {name} must be a mapping")
    return value


def _items(value: Any, where: str, name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise DomainPackError(f"{where}: {name} must be a non-empty list")
    return [_mapping(item, where, f"each entry of {name}") for item in value]


def _strings(
    value: Any, where: str, name: str, *, allow_empty: bool = False
) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise DomainPackError(f"{where}: {name} must be a list of strings")
    if not value and not allow_empty:
        raise DomainPackError(f"{where}: {name} must not be empty")
    if len(set(value)) != len(value):
        raise DomainPackError(f"{where}: {name} repeats an entry")
    return tuple(value)


# ---------------------------------------------------------------------------
# The files
# ---------------------------------------------------------------------------


def _policy(raw: dict[str, Any]) -> Policy:
    where = "policy.yaml"
    scheme = _text(raw.get("scheme"), where, "scheme")
    codes_raw = _mapping(raw.get("reason_codes"), where, "reason_codes")
    if not codes_raw:
        raise DomainPackError(f"{where}: reason_codes must not be empty")
    reason_codes = {
        _text(code, where, "a reason code"): _text(label, where, f"the label of {code}")
        for code, label in codes_raw.items()
    }
    clauses = []
    for item in _items(raw.get("clauses"), where, "clauses"):
        clauses.append(
            Clause(
                id=_text(item.get("id"), where, "a clause id"),
                title=_text(item.get("title"), where, "a clause title"),
                text=" ".join(_text(item.get("text"), where, "a clause text").split()),
            )
        )
    ids = [c.id for c in clauses]
    if len(set(ids)) != len(ids):
        raise DomainPackError(f"{where}: clause ids repeat")
    roles_raw = _mapping(raw.get("roles"), where, "roles")
    roles = {
        _text(k, where, "a role"): _text(v, where, f"roles.{k}")
        for k, v in roles_raw.items()
    }
    missing = sorted(set(POLICY_ROLES) - set(roles))
    if missing:
        raise DomainPackError(f"{where}: roles must name a tool for {missing}")
    unknown_roles = sorted(set(roles) - set(POLICY_ROLES))
    if unknown_roles:
        raise DomainPackError(f"{where}: unknown role(s) {unknown_roles}")
    not_tools = sorted(v for v in roles.values() if v not in _TOOL_NAMES)
    if not_tools:
        raise DomainPackError(f"{where}: roles name {not_tools}, not seed tools")
    case_argument = _text(raw.get("case_argument", "case_id"), where, "case_argument")
    export_domains = _strings(raw.get("export_domains"), where, "export_domains")
    for domain in export_domains:
        if "@" in domain or "." not in domain or domain != domain.lower():
            raise DomainPackError(
                f"{where}: export_domains entry '{domain}' is not a lowercase "
                "domain name"
            )
    destination_argument = _text(
        raw.get("destination_argument", "destination"), where, "destination_argument"
    )
    fields_raw = _mapping(raw.get("notice_fields"), where, "notice_fields")
    notice_fields = {
        _text(k, where, "a notice field"): _text(v, where, f"notice_fields.{k}")
        for k, v in fields_raw.items()
    }
    if set(notice_fields) != {"disclosure", "reason_codes"}:
        raise DomainPackError(
            f"{where}: notice_fields must name exactly 'disclosure' and "
            f"'reason_codes', got {sorted(notice_fields)}"
        )
    body_argument = _text(
        raw.get("notice_body_argument", "body"), where, "notice_body_argument"
    )
    masking = _strings(
        raw.get("notice_masking", []), where, "notice_masking", allow_empty=True
    )
    unknown_kinds = sorted(set(masking) - {"nric", "phone", "email"})
    if unknown_kinds:
        raise DomainPackError(
            f"{where}: notice_masking has unknown kind(s) {unknown_kinds}; the kinds "
            "are nric, phone and email"
        )
    synthetic = raw.get("synthetic_identifiers", False)
    if not isinstance(synthetic, bool):
        raise DomainPackError(f"{where}: synthetic_identifiers must be true or false")
    return Policy(
        scheme=scheme,
        reason_codes=reason_codes,
        clauses=tuple(clauses),
        roles=roles,
        case_argument=case_argument,
        export_domains=export_domains,
        destination_argument=destination_argument,
        notice_fields=notice_fields,
        notice_body_argument=body_argument,
        notice_masking=masking,
        synthetic_identifiers=synthetic,
    )


def _names(raw: dict[str, Any]) -> tuple[SyntheticName, ...]:
    where = "names.yaml"
    groups = _strings(raw.get("groups"), where, "groups")
    names = []
    for item in _items(raw.get("names"), where, "names"):
        name = SyntheticName(
            name=_text(item.get("name"), where, "a name"),
            gender=_text(item.get("gender"), where, "a gender"),
            group=_text(item.get("group"), where, "a group"),
        )
        if name.gender not in GENDERS:
            raise DomainPackError(
                f"{where}: '{name.name}': gender must be one of {sorted(GENDERS)}"
            )
        if name.group not in groups:
            raise DomainPackError(
                f"{where}: '{name.name}': group must be one of {list(groups)}"
            )
        names.append(name)
    if len({n.name for n in names}) != len(names):
        raise DomainPackError(f"{where}: names repeat")
    for gender in sorted(GENDERS):
        for group in groups:
            if not any(n.gender == gender and n.group == group for n in names):
                raise DomainPackError(
                    f"{where}: no {gender} name for group '{group}', so R26 "
                    "cannot pair every group"
                )
    return tuple(names)


def _cases(
    raw: dict[str, Any], policy: Policy, names: Sequence[SyntheticName]
) -> dict[str, Case]:
    where = "cases.yaml"
    by_name = {n.name: n for n in names}
    cases: dict[str, Case] = {}
    for item in _items(raw.get("cases"), where, "cases"):
        case_id = _text(item.get("case_id"), where, "case_id")
        if case_id in cases:
            raise DomainPackError(f"{where}: case id '{case_id}' repeats")
        here = f"{where}: case {case_id}"
        a = _mapping(item.get("applicant"), here, "applicant")
        name = _text(a.get("name"), here, "applicant.name")
        gender = _text(a.get("gender"), here, "applicant.gender")
        if name not in by_name:
            raise DomainPackError(f"{here}: '{name}' is not in names.yaml")
        if by_name[name].gender != gender:
            raise DomainPackError(
                f"{here}: '{name}' is {by_name[name].gender} in names.yaml, "
                f"not {gender}"
            )
        age = a.get("age")
        if isinstance(age, bool) or not isinstance(age, int) or age <= 0:
            raise DomainPackError(f"{here}: applicant.age must be a positive integer")
        nric = _text(a.get("nric"), here, "applicant.nric")
        phone = str(a.get("phone", "")).strip()
        email = _text(a.get("email"), here, "applicant.email")
        if not is_synthetic_nric(nric):
            raise DomainPackError(
                f"{here}: nric '{nric}' is not a synthetic id (docs/adr/0006 D26)"
            )
        if not is_synthetic_phone(phone):
            raise DomainPackError(
                f"{here}: phone '{phone}' is not a synthetic number (docs/adr/0006 D26)"
            )
        if not is_synthetic_email(email):
            raise DomainPackError(
                f"{here}: email '{email}' is not a synthetic address "
                "(docs/adr/0006 D26)"
            )
        expected = _mapping(item.get("expected"), here, "expected")
        outcome = _text(expected.get("outcome"), here, "expected.outcome")
        if outcome not in OUTCOMES:
            raise DomainPackError(
                f"{here}: expected.outcome must be one of {list(OUTCOMES)}"
            )
        codes = _strings(expected.get("reason_codes"), here, "expected.reason_codes")
        unknown = sorted(set(codes) - set(policy.reason_codes))
        if unknown:
            raise DomainPackError(f"{here}: unknown reason code(s) {unknown}")
        ambiguity = expected.get("ambiguity")
        if ambiguity is not None and ambiguity not in AMBIGUITIES:
            raise DomainPackError(
                f"{here}: expected.ambiguity must be one of {sorted(AMBIGUITIES)}"
            )
        if (outcome == "refer") != (ambiguity is not None):
            raise DomainPackError(
                f"{here}: an ambiguous case is referred and a referred case is "
                "ambiguous; outcome and ambiguity disagree"
            )
        cases[case_id] = Case(
            case_id=case_id,
            applicant=Applicant(name, gender, age, nric, phone, email),
            facts=dict(_mapping(item.get("facts"), here, "facts")),
            outcome=outcome,
            reason_codes=codes,
            ambiguity=ambiguity,
        )
    return cases


def _script(raw: Any, where: str, name: str) -> tuple[ScriptStep, ...]:
    steps_raw = _items(raw, where, name)
    try:
        steps = parse_script(steps_raw)
    except ConfigurationError as exc:
        raise DomainPackError(f"{where}: {name}: {exc}") from exc
    for step in steps:
        if step.kind == "call" and step.tool not in _TOOL_NAMES:
            raise DomainPackError(
                f"{where}: {name} calls '{step.tool}', which is not a seed tool"
            )
    return steps


def _calls(steps: Sequence[ScriptStep]) -> list[str]:
    return [s.tool for s in steps if s.kind == "call"]


def _tool_use_block(raw: Any, where: str) -> dict[str, Any]:
    block = _mapping(raw, where, "expected.tool_use")
    unknown = sorted(set(block) - _TOOL_USE_KEYS)
    if unknown:
        raise DomainPackError(
            f"{where}: expected.tool_use has unknown key(s) {unknown}"
        )
    if not block:
        raise DomainPackError(f"{where}: expected.tool_use declares nothing")
    named: list[str] = []
    for entry in block.get("required", []) or []:
        call = _mapping(entry, where, "each required call")
        named.append(_text(call.get("tool"), where, "a required call's tool"))
        if "arguments" in call:
            _mapping(call["arguments"], where, "a required call's arguments")
    for entry in block.get("forbidden", []) or []:
        if isinstance(entry, str):
            named.append(entry)
        else:
            call = _mapping(entry, where, "each forbidden call")
            named.append(_text(call.get("tool"), where, "a forbidden call's tool"))
    if "order" in block:
        named.extend(_strings(block["order"], where, "expected.tool_use.order"))
    unknown_tools = sorted(set(named) - _TOOL_NAMES)
    if unknown_tools:
        raise DomainPackError(
            f"{where}: expected.tool_use names {unknown_tools}, not seed tools"
        )
    return block


def _approvals(raw: Any, where: str) -> dict[str, tuple[str, ...]]:
    """The scenario's scripted approver answers (docs/adr/0006 D5): per
    irreversible seed tool, a non-empty list of approve / deny / timeout."""
    schedule: dict[str, tuple[str, ...]] = {}
    for tool, answers in _mapping(raw, where, "approvals").items():
        if tool not in _IRREVERSIBLE:
            raise DomainPackError(
                f"{where}: approvals names '{tool}', which is not an irreversible "
                f"seed tool ({sorted(_IRREVERSIBLE)})"
            )
        # Answers may repeat ([approve, approve] is two approvals), so this
        # is not _strings, which refuses repeats.
        if (
            not isinstance(answers, list)
            or not answers
            or not all(isinstance(a, str) for a in answers)
        ):
            raise DomainPackError(
                f"{where}: approvals.{tool} must be a non-empty list of strings"
            )
        given = tuple(answers)
        unknown = sorted(set(given) - APPROVAL_ANSWERS)
        if unknown:
            raise DomainPackError(
                f"{where}: approvals.{tool} has unknown answer(s) {unknown}; use "
                f"{sorted(APPROVAL_ANSWERS)}"
            )
        schedule[str(tool)] = given
    return schedule


def _policy_block(raw: Any, where: str, case: Case, policy: Policy) -> dict[str, Any]:
    """The scenario's ``policy`` expectation: its rules, plus what the
    ``policy`` evaluator needs to apply them to this pack and case."""
    block = _mapping(raw, where, "expected.policy")
    unknown = sorted(set(block) - {"rules", "allowed_tools", "call_budget"})
    if unknown:
        raise DomainPackError(f"{where}: expected.policy has unknown key(s) {unknown}")
    rules = _strings(block.get("rules"), where, "expected.policy.rules")
    unknown_rules = sorted(set(rules) - POLICY_RULES)
    if unknown_rules:
        raise DomainPackError(
            f"{where}: expected.policy.rules has unknown rule(s) {unknown_rules}; "
            f"the rules are {sorted(POLICY_RULES)}"
        )
    result: dict[str, Any] = {
        "rules": list(rules),
        "roles": dict(policy.roles),
        "case_argument": policy.case_argument,
        "case_id": case.case_id,
        "ambiguous": case.ambiguous,
        "export_domains": list(policy.export_domains),
        "destination_argument": policy.destination_argument,
        "expected_reason_codes": list(case.reason_codes),
        "notice_fields": dict(policy.notice_fields),
        "addressee_identifiers": {
            "nric": case.applicant.nric,
            "phone": case.applicant.phone,
            "email": case.applicant.email,
        },
        "notice_body_argument": policy.notice_body_argument,
        "mask": list(policy.notice_masking),
        "synthetic_identifiers": policy.synthetic_identifiers,
    }
    if ("least_privilege" in rules) != ("allowed_tools" in block):
        raise DomainPackError(
            f"{where}: expected.policy.allowed_tools goes with the least_privilege "
            "rule: declare both or neither"
        )
    if ("call_budget" in rules) != ("call_budget" in block):
        raise DomainPackError(
            f"{where}: expected.policy.call_budget goes with the call_budget rule: "
            "declare both or neither"
        )
    if "call_budget" in block:
        budget = block["call_budget"]
        if isinstance(budget, bool) or not isinstance(budget, int) or budget < 1:
            raise DomainPackError(
                f"{where}: expected.policy.call_budget must be a positive integer"
            )
        result["call_budget"] = budget
    if "allowed_tools" in block:
        allowed = _strings(
            block["allowed_tools"], where, "expected.policy.allowed_tools"
        )
        strangers = sorted(set(allowed) - _TOOL_NAMES)
        if strangers:
            raise DomainPackError(
                f"{where}: expected.policy.allowed_tools names {strangers}, "
                "not seed tools"
            )
        result["allowed_tools"] = list(allowed)
    return result


def _faults(raw: Any, where: str) -> list[dict[str, Any]]:
    """The scenario's scheduled faults (docs/adr/0006 D6): each names a
    seed tool, a positive call number and a fault kind."""
    schedule: list[dict[str, Any]] = []
    for position, item in enumerate(_items(raw, where, "faults") if raw else []):
        tool = _text(item.get("tool"), where, f"faults[{position}].tool")
        if tool not in _TOOL_NAMES:
            raise DomainPackError(
                f"{where}: faults[{position}] names '{tool}', which is not a seed tool"
            )
        kind = _text(item.get("kind"), where, f"faults[{position}].kind")
        if kind not in FAULT_KINDS:
            raise DomainPackError(
                f"{where}: faults[{position}] has unknown kind '{kind}'; the kinds are "
                f"{sorted(FAULT_KINDS)}"
            )
        call = item.get("call", 1)
        if isinstance(call, bool) or not isinstance(call, int) or call < 1:
            raise DomainPackError(
                f"{where}: faults[{position}].call must be a positive integer"
            )
        schedule.append({"tool": tool, "call": call, "kind": kind})
    return schedule


def _injections(raw: Any, where: str) -> list[dict[str, Any]]:
    """The scenario's injected payloads (docs/adr/0006 D6): each has a
    unique id, names a seed tool, carries a non-empty payload, and applies
    to one call or, without ``call``, to every call."""
    schedule: list[dict[str, Any]] = []
    seen: set[str] = set()
    for position, item in enumerate(_items(raw, where, "injections") if raw else []):
        ident = _text(item.get("id"), where, f"injections[{position}].id")
        if ident in seen:
            raise DomainPackError(
                f"{where}: injections[{position}] id '{ident}' repeats"
            )
        seen.add(ident)
        tool = _text(item.get("tool"), where, f"injections[{position}].tool")
        if tool not in _TOOL_NAMES:
            raise DomainPackError(
                f"{where}: injections[{position}] names '{tool}', which is not a "
                "seed tool"
            )
        payload = _text(item.get("payload"), where, f"injections[{position}].payload")
        call = item.get("call")
        if call is not None and (
            isinstance(call, bool) or not isinstance(call, int) or call < 1
        ):
            raise DomainPackError(
                f"{where}: injections[{position}].call must be a positive integer "
                "or absent"
            )
        entry: dict[str, Any] = {"id": ident, "tool": tool, "payload": payload}
        if call is not None:
            entry["call"] = call
        schedule.append(entry)
    return schedule


#: What a counterfactual may swap (R26; testing-framework 7.2.1). Only the
#: name for now: it changes the name's ethnic group within a gender-matched
#: pair and nothing else about the case.
COUNTERFACTUAL_SWAPS: frozenset[str] = frozenset({"name"})


def _counterfactual(
    item: dict[str, Any],
    here: str,
    done: Mapping[str, Scenario],
    cases: Mapping[str, Case],
    names: Sequence[SyntheticName],
) -> tuple[Scenario, dict[str, Any]]:
    """A counterfactual scenario derives everything but its own trajectories,
    evaluators, controls, outcome group and description from its base: the
    base's case with one protected attribute changed, the base's
    expectations and environment, and the consistency rule that holds the
    variant to the base's known-good calls."""
    block = _mapping(item.get("counterfactual"), here, "counterfactual")
    unknown = sorted(set(block) - {"base", "swap", "to"})
    if unknown:
        raise DomainPackError(f"{here}: counterfactual has unknown key(s) {unknown}")
    base_id = _text(block.get("base"), here, "counterfactual.base")
    if base_id not in done:
        raise DomainPackError(
            f"{here}: counterfactual.base '{base_id}' is not a scenario declared "
            "before this one"
        )
    base = done[base_id]
    if base.variant:
        raise DomainPackError(
            f"{here}: counterfactual.base '{base_id}' is itself a variant"
        )
    swap = _text(block.get("swap"), here, "counterfactual.swap")
    if swap not in COUNTERFACTUAL_SWAPS:
        raise DomainPackError(
            f"{here}: counterfactual.swap must be one of {sorted(COUNTERFACTUAL_SWAPS)}"
        )
    to = _text(block.get("to"), here, "counterfactual.to")
    case = cases[str(base.expected["case_id"])]
    by_name = {n.name: n for n in names}
    if to not in by_name:
        raise DomainPackError(f"{here}: counterfactual.to '{to}' is not in names.yaml")
    original = by_name.get(case.applicant.name)
    target = by_name[to]
    if original is None or target.gender != case.applicant.gender:
        raise DomainPackError(
            f"{here}: counterfactual.to '{to}' must have the same gender as "
            f"'{case.applicant.name}' ({case.applicant.gender})"
        )
    if target.group == original.group:
        raise DomainPackError(
            f"{here}: counterfactual.to '{to}' is in the same group as "
            f"'{case.applicant.name}' ({original.group}); the swap changes nothing"
        )
    for key in ("case_id", "approvals", "faults", "injections", "expected"):
        if key in item:
            raise DomainPackError(
                f"{here}: a counterfactual takes its {key} from the base; do not "
                "declare it"
            )
    base_calls = [
        {"call": step["call"], "arguments": dict(step.get("arguments", {}))}
        for step in base.known_good
        if "call" in step
    ]
    variant = {
        "kind": "counterfactual",
        "base": base_id,
        "attribute": swap,
        "from": case.applicant.name,
        "to": to,
        "unchanged": {"gender": case.applicant.gender, "age": case.applicant.age},
        "group_from": original.group,
        "group_to": target.group,
    }
    expected = dict(base.expected)
    policy_block = dict(expected.get("policy") or {})
    rules = list(policy_block.get("rules") or [])
    if "counterfactual_consistency" not in rules:
        rules.append("counterfactual_consistency")
    policy_block["rules"] = rules
    policy_block["base_calls"] = [
        {"tool": c["call"], "arguments": c["arguments"]} for c in base_calls
    ]
    expected["policy"] = policy_block
    return base, {
        "expected": expected,
        "variant": variant,
        "environment": dict(base.environment),
        "allowed_evaluators": set(base.applicable_evaluators) | {"policy"},
    }


def _perturbation(
    item: dict[str, Any], here: str, done: Mapping[str, Scenario]
) -> tuple[Scenario, dict[str, Any]]:
    """A perturbation scenario is its base with the task text varied at run
    time (docs/adr/0006 D6; testing-framework 6.1.1): it takes the base's
    case, expectations, environment and description, and declares the
    family and intensity. Its own trajectories are the base's, since a
    scripted agent does not read the text."""
    block = _mapping(item.get("perturbation"), here, "perturbation")
    unknown = sorted(set(block) - {"base", "family", "intensity"})
    if unknown:
        raise DomainPackError(f"{here}: perturbation has unknown key(s) {unknown}")
    base_id = _text(block.get("base"), here, "perturbation.base")
    if base_id not in done:
        raise DomainPackError(
            f"{here}: perturbation.base '{base_id}' is not a scenario declared "
            "before this one"
        )
    base = done[base_id]
    if base.variant:
        raise DomainPackError(
            f"{here}: perturbation.base '{base_id}' is itself a variant"
        )
    family = _text(block.get("family"), here, "perturbation.family")
    if family not in PERTURBATION_FAMILIES:
        raise DomainPackError(
            f"{here}: perturbation.family must be one of {list(PERTURBATION_FAMILIES)}"
        )
    intensity = block.get("intensity", 0.15)
    if (
        isinstance(intensity, bool)
        or not isinstance(intensity, (int, float))
        or not 0 < intensity <= 1
    ):
        raise DomainPackError(
            f"{here}: perturbation.intensity must be a number in (0, 1]"
        )
    for key in (
        "case_id",
        "approvals",
        "faults",
        "injections",
        "expected",
        "description",
    ):
        if key in item:
            raise DomainPackError(
                f"{here}: a perturbation takes its {key} from the base; do not "
                "declare it"
            )
    variant = {
        "kind": "perturbation",
        "base": base_id,
        "family": family,
        "intensity": float(intensity),
    }
    return base, {
        "expected": dict(base.expected),
        "variant": variant,
        "environment": dict(base.environment),
        "allowed_evaluators": set(base.applicable_evaluators) | {"policy"},
        "description": base.description,
    }


def _scenarios(
    raw: dict[str, Any],
    cases: Mapping[str, Case],
    catalogue: Catalogue,
    policy: Policy,
    names: Sequence[SyntheticName] = (),
) -> tuple[Scenario, ...]:
    where = "scenarios.yaml"
    scenarios: list[Scenario] = []
    seen: set[str] = set()
    for item in _items(raw.get("scenarios"), where, "scenarios"):
        scenario_id = _text(item.get("scenario_id"), where, "scenario_id")
        if scenario_id in seen:
            raise DomainPackError(f"{where}: scenario id '{scenario_id}' repeats")
        seen.add(scenario_id)
        here = f"{where}: scenario {scenario_id}"
        category = _text(item.get("category"), here, "category")
        if category not in CATEGORIES:
            raise DomainPackError(
                f"{here}: category must be one of {sorted(CATEGORIES)}"
            )
        outcome_group = _text(item.get("outcome_group"), here, "outcome_group")
        if outcome_group not in NEGATIVE_OUTCOMES:
            raise DomainPackError(
                f"{here}: outcome_group must be one of {list(NEGATIVE_OUTCOMES)}"
            )
        if "counterfactual" in item and "perturbation" in item:
            raise DomainPackError(f"{here}: a scenario is one variant, not two")
        if "counterfactual" in item or "perturbation" in item:
            done = {s.scenario_id: s for s in scenarios}
            if "counterfactual" in item:
                base, derived = _counterfactual(item, here, done, cases, names)
            else:
                base, derived = _perturbation(item, here, done)
            evaluators = _strings(
                item.get("applicable_evaluators"), here, "applicable_evaluators"
            )
            strangers = sorted(set(evaluators) - derived["allowed_evaluators"])
            if strangers:
                raise DomainPackError(
                    f"{here}: applicable_evaluators {strangers} are not the base's "
                    "evaluators or 'policy'"
                )
            if "counterfactual" in item and "policy" not in evaluators:
                raise DomainPackError(
                    f"{here}: a counterfactual applies the policy evaluator (its "
                    "consistency rule)"
                )
            # Keep only the base's expectations for the evaluators the
            # variant applies, so the two stay consistent.
            owned = {
                "deterministic": (
                    "expected_output",
                    "expected_substring",
                    "match_mode",
                ),
                "tool_use": ("tool_use",),
                "state_based": ("expected_end_state",),
            }
            derived["expected"] = {
                key: value
                for key, value in derived["expected"].items()
                if all(
                    key not in keys or kind in evaluators
                    for kind, keys in owned.items()
                )
            }
            control_ids = _strings(item.get("control_ids"), here, "control_ids")
            for control_id in control_ids:
                try:
                    catalogue.control(control_id)
                except CatalogueError:
                    raise DomainPackError(
                        f"{here}: unknown control id '{control_id}' "
                        "(not in the catalogue)"
                    ) from None
            known_good = _script(item.get("known_good"), here, "known_good")
            if known_good[-1].kind != "finish":
                raise DomainPackError(f"{here}: known_good must end with 'finish'")
            known_bad = _script(item.get("known_bad"), here, "known_bad")
            description = derived.get("description") or " ".join(
                _text(item.get("description"), here, "description").split()
            )
            scenarios.append(
                Scenario(
                    scenario_id=scenario_id,
                    description=description,
                    category=category,
                    known_good=tuple(_as_data(step) for step in known_good),
                    known_bad=tuple(_as_data(step) for step in known_bad),
                    applicable_evaluators=evaluators,
                    control_ids=control_ids,
                    expected=derived["expected"],
                    environment=derived["environment"],
                    outcome_group=outcome_group,
                    variant=derived["variant"],
                )
            )
            continue
        case_id = _text(item.get("case_id"), here, "case_id")
        if case_id not in cases:
            raise DomainPackError(f"{here}: unknown case '{case_id}'")
        evaluators = _strings(
            item.get("applicable_evaluators"), here, "applicable_evaluators"
        )
        unknown_kinds = sorted(set(evaluators) - EVALUATOR_KINDS)
        if unknown_kinds:
            raise DomainPackError(
                f"{here}: applicable_evaluators has unknown kind(s) {unknown_kinds}"
            )
        control_ids = _strings(item.get("control_ids"), here, "control_ids")
        for control_id in control_ids:
            try:
                catalogue.control(control_id)
            except CatalogueError:
                raise DomainPackError(
                    f"{here}: unknown control id '{control_id}' (not in the catalogue)"
                ) from None
        known_good = _script(item.get("known_good"), here, "known_good")
        if known_good[-1].kind != "finish":
            raise DomainPackError(f"{here}: known_good must end with 'finish'")
        known_bad = _script(item.get("known_bad"), here, "known_bad")
        approvals = _approvals(item.get("approvals", {}), here)
        faults = _faults(item.get("faults"), here)
        injections = _injections(item.get("injections"), here)

        expected = _mapping(item.get("expected"), here, "expected")
        unknown_keys = sorted(set(expected) - _EXPECTED_KEYS)
        if unknown_keys:
            raise DomainPackError(f"{here}: expected has unknown key(s) {unknown_keys}")
        declares = {
            "deterministic": "expected_output" in expected
            or "expected_substring" in expected,
            "tool_use": "tool_use" in expected,
            "state_based": "expected_end_state" in expected,
            "policy": "policy" in expected,
        }
        for kind, declared in declares.items():
            applies = kind in evaluators
            if applies and not declared:
                raise DomainPackError(
                    f"{here}: '{kind}' applies but expected declares nothing for it"
                )
            if declared and not applies:
                raise DomainPackError(
                    f"{here}: expected declares inputs for '{kind}', which does "
                    "not apply"
                )
        if declares["tool_use"]:
            _tool_use_block(expected["tool_use"], here)
        if declares["state_based"]:
            _mapping(
                expected["expected_end_state"], here, "expected.expected_end_state"
            )
        if declares["policy"]:
            expected = {
                **expected,
                "policy": _policy_block(
                    expected["policy"], here, cases[case_id], policy
                ),
            }

        # The pack's rule for ambiguous cases (testing-framework 10.3.2): the
        # known-good refers and never decides; the known-bad decides.
        good_calls, bad_calls = _calls(known_good), _calls(known_bad)
        if cases[case_id].ambiguous:
            if (
                REFER_TO_OFFICER.name not in good_calls
                or STATUS_UPDATE.name in good_calls
            ):
                raise DomainPackError(
                    f"{here}: case {case_id} is ambiguous, so known_good must "
                    f"call {REFER_TO_OFFICER.name} and never {STATUS_UPDATE.name}"
                )
            if STATUS_UPDATE.name not in bad_calls:
                raise DomainPackError(
                    f"{here}: case {case_id} is ambiguous, so known_bad must "
                    f"decide it with {STATUS_UPDATE.name}"
                )
        elif REFER_TO_OFFICER.name in good_calls and (
            STATUS_UPDATE.name not in good_calls
            or good_calls.index(REFER_TO_OFFICER.name)
            < good_calls.index(STATUS_UPDATE.name)
        ):
            # A clear case is never referred instead of decided; it may be
            # referred after the decision (R25: when the approver refuses
            # the notice). A task that neither decides nor refers -- an
            # export on an officer's instruction -- is allowed.
            raise DomainPackError(
                f"{here}: case {case_id} has a clear outcome, so known_good may "
                f"call {REFER_TO_OFFICER.name} only after {STATUS_UPDATE.name}"
            )

        scenarios.append(
            Scenario(
                scenario_id=scenario_id,
                description=" ".join(
                    _text(item.get("description"), here, "description").split()
                ),
                category=category,
                known_good=tuple(_as_data(step) for step in known_good),
                known_bad=tuple(_as_data(step) for step in known_bad),
                applicable_evaluators=evaluators,
                control_ids=control_ids,
                expected={"case_id": case_id, **expected},
                environment={
                    "approvals": approvals,
                    "faults": faults,
                    "injections": injections,
                },
                outcome_group=outcome_group,
            )
        )
    return tuple(scenarios)


def _as_data(step: ScriptStep) -> dict[str, Any]:
    """A step back as the data ``parse_script`` accepts."""
    if step.kind == "call":
        data: dict[str, Any] = {"call": step.tool, "arguments": dict(step.arguments)}
        if step.report_as is not None:
            data["report_as"] = step.report_as
        return data
    return {step.kind: step.text}


# ---------------------------------------------------------------------------
# The pack
# ---------------------------------------------------------------------------


class SeedDomainPack:
    """The seed domain, loaded from ``data/`` and validated (docs/adr/0006
    D23). ``data_dir`` lets a test load an edited copy; ``catalogue``
    defaults to the shipped one."""

    pack_id: str = "seed"
    pack_version: str = "0.1.0"

    def __init__(
        self, data_dir: Path | None = None, *, catalogue: Catalogue | None = None
    ) -> None:
        if data_dir is None:
            data_dir = Path(
                str(resources.files("agentevalops.domain_packs.seed") / "data")
            )
        self._raw = {name: _read(Path(data_dir) / name) for name in DATA_FILES}
        # docs/adr/0006 D26, over every string in every file, before the
        # files are interpreted: nothing in the pack may look like a real
        # person's identifier.
        findings = sorted(
            {
                found
                for raw in self._raw.values()
                for text in strings_in(raw)
                for found in unsafe_identifiers(text)
            }
        )
        if findings:
            raise DomainPackError(
                f"identifier(s) that could be a real person's: {findings} "
                "(docs/adr/0006 D26)"
            )
        self._policy = _policy(self._raw["policy.yaml"])
        self._names = _names(self._raw["names.yaml"])
        self._cases = _cases(self._raw["cases.yaml"], self._policy, self._names)
        self._scenarios = _scenarios(
            self._raw["scenarios.yaml"],
            self._cases,
            catalogue or load_catalogue(),
            self._policy,
            self._names,
        )

    # -- the DomainPack protocol ------------------------------------------

    def scenarios(self) -> Sequence[Scenario]:
        return self._scenarios

    def build_environment(
        self, scenario: Scenario, task_id: TaskId
    ) -> InProcessToolEnvironment:
        if scenario.scenario_id not in {s.scenario_id for s in self._scenarios}:
            raise DomainPackError(
                f"scenario '{scenario.scenario_id}' is not in the seed pack"
            )
        return build_environment(
            task_id,
            self.cases_for(scenario),
            self._policy,
            approvals=scenario.environment.get("approvals"),
            faults=scenario.environment.get("faults"),
            injections=scenario.environment.get("injections"),
        )

    def cases_for(self, scenario: Scenario) -> Mapping[str, Case]:
        """The cases on file for *scenario*: the pack's, with a counterfactual
        variant's case carrying the swapped attribute and nothing else
        changed (R26; testing-framework 7.2.1)."""
        variant = scenario.variant
        if variant.get("kind") != "counterfactual":
            return dict(self._cases)
        case_id = str(scenario.expected["case_id"])
        case = self._cases[case_id]
        swapped = dataclasses.replace(
            case,
            applicant=dataclasses.replace(case.applicant, name=str(variant["to"])),
        )
        return {**self._cases, case_id: swapped}

    # -- the data, for tests and later rows -------------------------------

    @property
    def policy(self) -> Policy:
        return self._policy

    @property
    def cases(self) -> Mapping[str, Case]:
        return dict(self._cases)

    @property
    def names(self) -> tuple[SyntheticName, ...]:
        return self._names

    @property
    def tools(self) -> tuple[Any, ...]:
        return TOOL_SPECS

    def name_pairs(self) -> tuple[tuple[str, str], ...]:
        """R26's name-swap pairs: two names of the same gender from different
        groups, every such combination, in a fixed order."""
        pairs: list[tuple[str, str]] = []
        for gender in sorted(GENDERS):
            same = [n for n in self._names if n.gender == gender]
            for i, first in enumerate(same):
                for second in same[i + 1 :]:
                    if first.group != second.group:
                        pairs.append((first.name, second.name))
        return tuple(pairs)

    def data_strings(self) -> tuple[str, ...]:
        """Every string in every data file, for the identifier scan."""
        return tuple(text for raw in self._raw.values() for text in strings_in(raw))
