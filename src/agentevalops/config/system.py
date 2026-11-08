"""System declaration loader: ``system.yaml`` into a ``SystemDeclaration``
(docs/adr/0005 D6; ROADMAP R27, testing-framework 3.6.1 and 3.7.1).

The declaration is the scoping step: what the system under test is (its
tools, with permission class, involvement level, data sensitivity,
reversibility and external exposure), what it is for and not for, and the
business metrics its owner names. The plan (``plans/``) selects scenarios
and thresholds from it; this module only reads and validates it.

Every rule here rejects, never repairs: an unknown key, a blank text, an
out-of-vocabulary value or a contradiction (an irreversible tool declared
reversible) is a ``ConfigurationError`` naming the file and the field.
Repairing silently is how a stale declaration would survive into a plan.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, cast, get_args

import yaml

from agentevalops.core.errors import ConfigurationError
from agentevalops.core.schemas import (
    GATE_KINDS,
    INVOLVEMENT_LEVELS,
    MGF_CATEGORIES,
    BusinessMetric,
    DeclaredTool,
    GateKind,
    InvolvementLevel,
    RunLimits,
    SystemDeclaration,
    ThresholdOverride,
    ToolDataClass,
    ToolPermission,
)

__all__ = [
    "BOOTSTRAP_SAMPLES_DEFAULT",
    "bootstrap_samples_problem",
    "load_system_declaration",
]

#: R36: resamples per pass^k interval when neither home sets them.
BOOTSTRAP_SAMPLES_DEFAULT = 10_000
#: The fewest resamples an interval may be drawn from; 0 turns them off.
_BOOTSTRAP_SAMPLES_MIN = 100


def bootstrap_samples_problem(value: int) -> str | None:
    """What is wrong with *value* as ``bootstrap_samples``, or None: the one
    rule both homes apply -- 0 (no intervals) or at least 100."""
    if value == 0 or value >= _BOOTSTRAP_SAMPLES_MIN:
        return None
    return (
        f"bootstrap_samples must be 0 (off) or at least {_BOOTSTRAP_SAMPLES_MIN}, "
        f"got {value}"
    )

#: Derived from the ``ToolSpec`` literals, never hand-kept (the loader's
#: allowlists drifted once; see ``config/loader.py``).
_PERMISSIONS: tuple[str, ...] = get_args(ToolPermission)
_DATA_CLASSES: tuple[str, ...] = get_args(ToolDataClass)

_TOP_KEYS: frozenset[str] = frozenset(
    {
        "system_id",
        "pack_id",
        "involvement",
        "tools",
        "intended_use",
        "out_of_scope",
        "business_metrics",
        "repetitions",
        "seed",
        "bootstrap_samples",
        "thresholds",
        "run_limits",
    }
)
_TOOL_KEYS: frozenset[str] = frozenset(
    {"name", "permission", "data_class", "reversible", "external"}
)
_METRIC_KEYS: frozenset[str] = frozenset({"name", "definition"})
_THRESHOLD_KEYS: frozenset[str] = frozenset({"min_pass_k", "gate"})
_RUN_LIMIT_KEYS: frozenset[str] = frozenset({"max_run_cost_usd", "max_run_tokens"})


class _Reject(Exception):
    """Internal: a validation failure, before the file name is prefixed."""


def _text(raw: dict[str, Any], key: str, where: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _Reject(f"{where}{key} must be a non-empty string")
    return value.strip()


def _mapping(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _Reject(f"{what} must be a mapping, got {type(value).__name__}")
    return {str(k): v for k, v in value.items()}


def _only_keys(raw: dict[str, Any], allowed: frozenset[str], what: str) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise _Reject(
            f"{what} has unknown key(s) {unknown}; allowed: {sorted(allowed)}"
        )


def _bool(raw: dict[str, Any], key: str, where: str) -> bool:
    value = raw.get(key)
    if not isinstance(value, bool):
        raise _Reject(f"{where}{key} must be true or false, got {value!r}")
    return value


def _int(value: Any, what: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _Reject(f"{what} must be an integer, got {value!r}")
    if minimum is not None and value < minimum:
        raise _Reject(f"{what} must be at least {minimum}, got {value}")
    return value


def _tool(raw: Any, index: int) -> DeclaredTool:
    where = f"tools[{index}]."
    item = _mapping(raw, f"tools[{index}]")
    _only_keys(item, _TOOL_KEYS, f"tools[{index}]")
    name = _text(item, "name", where)
    permission = item.get("permission")
    if permission not in _PERMISSIONS:
        raise _Reject(
            f"{where}permission must be one of {list(_PERMISSIONS)}, got {permission!r}"
        )
    data_class = item.get("data_class", "normal")
    if data_class not in _DATA_CLASSES:
        raise _Reject(
            f"{where}data_class must be one of {list(_DATA_CLASSES)}, "
            f"got {data_class!r}"
        )
    reversible = (
        _bool(item, "reversible", where)
        if "reversible" in item
        else (permission != "irreversible")
    )
    external = _bool(item, "external", where) if "external" in item else False
    if reversible == (permission == "irreversible"):
        raise _Reject(
            f"{where}reversible must be {permission != 'irreversible'} for a "
            f"'{permission}' tool: an irreversible tool goes through the approval "
            "gate (docs/adr/0006 D5), and a tool that does not cannot be "
            "declared irreversible"
        )
    return DeclaredTool(
        name=name,
        permission=cast(ToolPermission, permission),
        data_class=cast(ToolDataClass, data_class),
        reversible=reversible,
        external=external,
    )


def _tools(raw: Any) -> tuple[DeclaredTool, ...]:
    if not isinstance(raw, list) or not raw:
        raise _Reject("tools must be a non-empty list, one entry per tool")
    tools = tuple(_tool(item, i) for i, item in enumerate(raw))
    seen: set[str] = set()
    for tool in tools:
        if tool.name in seen:
            raise _Reject(f"tools names '{tool.name}' twice")
        seen.add(tool.name)
    return tools


def _strings(raw: Any, what: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(
        isinstance(s, str) and s.strip() for s in raw
    ):
        raise _Reject(f"{what} must be a list of non-empty strings")
    return tuple(s.strip() for s in raw)


def _metrics(raw: Any) -> tuple[BusinessMetric, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise _Reject("business_metrics must be a list")
    metrics: list[BusinessMetric] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise _Reject(
                f"business_metrics[{i}] must be a mapping with name and definition"
            )
        entry = {str(k): v for k, v in item.items()}
        _only_keys(entry, _METRIC_KEYS, f"business_metrics[{i}]")
        where = f"business_metrics[{i}]."
        metrics.append(
            BusinessMetric(
                name=_text(entry, "name", where),
                definition=_text(entry, "definition", where),
            )
        )
    return tuple(metrics)


def _thresholds(raw: Any) -> tuple[ThresholdOverride, ...]:
    if raw is None:
        return ()
    block = _mapping(raw, "thresholds")
    overrides: list[ThresholdOverride] = []
    for category, value in block.items():
        if category not in MGF_CATEGORIES:
            raise _Reject(
                f"thresholds names '{category}', which is not a category; "
                f"the categories are {list(MGF_CATEGORIES)}"
            )
        entry = _mapping(value, f"thresholds.{category}")
        _only_keys(entry, _THRESHOLD_KEYS, f"thresholds.{category}")
        if not entry:
            raise _Reject(f"thresholds.{category} must set min_pass_k, gate or both")
        min_pass_k: float | None = None
        if "min_pass_k" in entry:
            value_raw = entry["min_pass_k"]
            if isinstance(value_raw, bool) or not isinstance(value_raw, (int, float)):
                raise _Reject(f"thresholds.{category}.min_pass_k must be a number")
            min_pass_k = float(value_raw)
            if not 0.0 <= min_pass_k <= 1.0:
                raise _Reject(
                    f"thresholds.{category}.min_pass_k must be between 0 and 1, "
                    f"got {min_pass_k}"
                )
        gate: GateKind | None = None
        if "gate" in entry:
            gate_raw = entry["gate"]
            if gate_raw not in GATE_KINDS:
                raise _Reject(
                    f"thresholds.{category}.gate must be one of {list(GATE_KINDS)}, "
                    f"got {gate_raw!r}"
                )
            gate = cast(GateKind, gate_raw)
        overrides.append(
            ThresholdOverride(category=category, min_pass_k=min_pass_k, gate=gate)
        )
    return tuple(overrides)


def _run_limits(value: Any) -> RunLimits | None:
    """R35: the run-level budget. Absent, empty or both caps unset: off."""
    if value is None:
        return None
    raw = _mapping(value, "run_limits")
    _only_keys(raw, _RUN_LIMIT_KEYS, "run_limits")
    cost = raw.get("max_run_cost_usd")
    if cost is not None:
        if isinstance(cost, bool) or not isinstance(cost, (int, float)):
            raise _Reject(f"run_limits.max_run_cost_usd must be a number, got {cost!r}")
        if cost <= 0:
            raise _Reject(f"run_limits.max_run_cost_usd must be > 0, got {cost}")
        cost = float(cost)
    tokens = raw.get("max_run_tokens")
    if tokens is not None:
        tokens = _int(tokens, "run_limits.max_run_tokens", minimum=1)
    if cost is None and tokens is None:
        return None
    return RunLimits(max_run_cost_usd=cost, max_run_tokens=tokens)


def _bootstrap_samples(value: Any) -> int | None:
    """R36: ``bootstrap_samples`` as declared; None when not declared."""
    if value is None:
        return None
    samples = _int(value, "bootstrap_samples")
    problem = bootstrap_samples_problem(samples)
    if problem is not None:
        raise _Reject(problem)
    return samples


def _parse(raw: dict[str, Any], sha256: str) -> SystemDeclaration:
    _only_keys(raw, _TOP_KEYS, "the declaration")
    involvement = raw.get("involvement")
    if involvement not in INVOLVEMENT_LEVELS:
        raise _Reject(
            f"involvement must be one of {list(INVOLVEMENT_LEVELS)}, "
            f"got {involvement!r}"
        )
    return SystemDeclaration(
        system_id=_text(raw, "system_id", ""),
        pack_id=_text(raw, "pack_id", ""),
        involvement=cast(InvolvementLevel, involvement),
        tools=_tools(raw.get("tools")),
        intended_use=_text(raw, "intended_use", ""),
        out_of_scope=_strings(raw.get("out_of_scope"), "out_of_scope"),
        business_metrics=_metrics(raw.get("business_metrics")),
        repetitions=_int(raw.get("repetitions", 1), "repetitions", minimum=1),
        seed=_int(raw.get("seed", 0), "seed"),
        bootstrap_samples=_bootstrap_samples(raw.get("bootstrap_samples")),
        thresholds=_thresholds(raw.get("thresholds")),
        run_limits=_run_limits(raw.get("run_limits")),
        sha256=sha256,
    )


def load_system_declaration(path: Path) -> SystemDeclaration:
    """Load and validate *path* as a system declaration.

    Raises ``ConfigurationError``, naming the file, for a missing or
    unreadable file, malformed YAML, a non-mapping document, an unknown
    key anywhere, a missing or blank ``system_id``, ``pack_id`` or
    ``intended_use``, an involvement level outside ``INVOLVEMENT_LEVELS``,
    an empty or repeated tool, a permission or data class outside the
    ``ToolSpec`` vocabulary, ``reversible`` disagreeing with the
    permission, a business metric without a definition, ``repetitions``
    below 1, a non-integer ``seed``, a ``bootstrap_samples`` that is not 0
    or at least 100, or a threshold outside the categories, the ``[0, 1]``
    range or the gate kinds.
    """
    if not path.exists():
        raise ConfigurationError(f"System declaration not found: {path}")
    if not path.is_file():
        raise ConfigurationError(f"System declaration is not a file: {path}")
    try:
        data = path.read_bytes()
        raw_loaded = yaml.safe_load(data.decode("utf-8"))
    except OSError as exc:
        raise ConfigurationError(f"{path.name}: cannot read: {exc}") from exc
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise ConfigurationError(f"Malformed YAML in {path}: {exc}") from exc
    if not isinstance(raw_loaded, dict):
        raise ConfigurationError(
            f"{path.name}: a system declaration must be a YAML mapping, "
            f"got {type(raw_loaded).__name__}"
        )
    try:
        return _parse(
            {str(k): v for k, v in raw_loaded.items()},
            hashlib.sha256(data).hexdigest(),
        )
    except _Reject as exc:
        raise ConfigurationError(f"{path.name}: {exc}") from None
