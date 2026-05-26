"""Config loader — load, validate, and parse YAML run configuration files.

This module is the single place where YAML is turned into core schema objects.
The CLI and tests both call :func:`load_run_config`; neither duplicates the
parsing logic.

Public API
----------
``load_run_config(path)``
    Load a YAML file from *path*, validate every field, and return a
    :class:`LoadedRunConfig`.  Raises :class:`ConfigurationError` for any
    invalid input (missing file, malformed YAML, bad values, etc.).

``LoadedRunConfig``
    Dataclass that carries the parsed :class:`RunConfig`, :class:`PolicySpec`,
    and a few mock-runner parameters used by the CLI demo scenarios.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agentevalops.benchmarks.toy.scenarios import SUPPORTED_SCENARIOS
from agentevalops.core.errors import ConfigurationError
from agentevalops.core.schemas import PolicySpec, ResourceLimits, RunConfig
from agentevalops.core.types import AgentId, BackendId, RunId

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SUPPORTED_BACKENDS: frozenset[str] = frozenset({"local", "aws"})
_SUPPORTED_BENCHMARKS: frozenset[str] = frozenset({"toy"})

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class LoadedRunConfig:
    """Fully validated, parsed run configuration from a YAML file.

    Attributes
    ----------
    path:
        Absolute path to the source YAML file.
    raw:
        Unmodified dictionary returned by ``yaml.safe_load``.
    run_config:
        Parsed :class:`RunConfig` including resource limits.
    policy_spec:
        Parsed :class:`PolicySpec` (defaults to a no-constraint policy).
    benchmark_id:
        Benchmark to run (currently only ``"toy"``).
    mock_fail:
        When ``True`` the :class:`MockAgentRunner` emits failed terminal
        events so all tasks return ``success=False``.  Used by the
        ``toy_failure`` scenario.
    mock_cost_per_task_usd:
        Per-task cost the :class:`MockAgentRunner` reports in its terminal
        payload.  Used by the ``toy_policy_violation`` scenario.
    benchmark_scenario:
        Named scenario within the toy benchmark (``"smoke"``, ``"failure"``,
        ``"policy_violation"``, ``"trace_limit"``, ``"mixed"``).
        Defaults to ``"smoke"``.
    """

    path: Path
    raw: dict[str, Any]
    run_config: RunConfig
    policy_spec: PolicySpec
    benchmark_id: str
    mock_fail: bool = False
    mock_cost_per_task_usd: float = 0.0
    benchmark_scenario: str = "smoke"


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------


def load_run_config(path: Path) -> LoadedRunConfig:
    """Load and validate *path* as a run configuration YAML file.

    Parameters
    ----------
    path:
        Path to the YAML config file (need not be absolute).

    Returns
    -------
    LoadedRunConfig
        Fully validated, ready-to-use config.

    Raises
    ------
    ConfigurationError
        For any of the following problems:

        - File does not exist.
        - Path is a directory, not a file.
        - File content is not valid YAML.
        - YAML top-level value is not a mapping.
        - ``resource_limits.*`` values are out of range.
        - ``policy.*`` values are out of range.
        - ``backend_id`` is not one of the supported values.
        - ``benchmark_id`` is not one of the supported values.
        - ``mock_cost_per_task_usd`` is negative.
    """
    # ------------------------------------------------------------------
    # File-system checks
    # ------------------------------------------------------------------
    if not path.exists():
        raise ConfigurationError(f"Config file not found: {path}")
    if not path.is_file():
        raise ConfigurationError(f"Config path is not a file: {path}")

    # ------------------------------------------------------------------
    # YAML parsing
    # ------------------------------------------------------------------
    try:
        with path.open(encoding="utf-8") as fh:
            raw_loaded = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ConfigurationError(
            f"Malformed YAML in {path}: {exc}"
        ) from exc

    if not isinstance(raw_loaded, dict):
        kind = type(raw_loaded).__name__
        raise ConfigurationError(
            f"Config must be a YAML mapping (got {kind}): {path}"
        )

    raw: dict[str, Any] = raw_loaded

    # ------------------------------------------------------------------
    # resource_limits
    # ------------------------------------------------------------------
    rl_raw = raw.get("resource_limits", {})
    if not isinstance(rl_raw, dict):
        raise ConfigurationError(
            f"resource_limits must be a YAML mapping, "
            f"got {type(rl_raw).__name__}"
        )

    max_tokens = int(rl_raw.get("max_tokens", 100_000))
    max_wall_seconds = float(rl_raw.get("max_wall_seconds", 3_600.0))
    rl_max_cost = float(rl_raw.get("max_cost_usd", 10.0))

    if max_tokens <= 0:
        raise ConfigurationError(
            f"resource_limits.max_tokens must be > 0, got {max_tokens}"
        )
    if max_wall_seconds <= 0:
        raise ConfigurationError(
            f"resource_limits.max_wall_seconds must be > 0, "
            f"got {max_wall_seconds}"
        )
    if rl_max_cost < 0:
        raise ConfigurationError(
            f"resource_limits.max_cost_usd must be >= 0, got {rl_max_cost}"
        )

    resource_limits = ResourceLimits(
        max_tokens=max_tokens,
        max_wall_seconds=max_wall_seconds,
        max_cost_usd=rl_max_cost,
    )

    # ------------------------------------------------------------------
    # backend_id
    # ------------------------------------------------------------------
    backend_id_str = str(raw.get("backend_id", "local"))
    if backend_id_str not in _SUPPORTED_BACKENDS:
        raise ConfigurationError(
            f"Unsupported backend_id '{backend_id_str}'. "
            f"Supported values: {sorted(_SUPPORTED_BACKENDS)}"
        )

    # ------------------------------------------------------------------
    # run_config
    # ------------------------------------------------------------------
    run_config = RunConfig(
        run_id=RunId(str(raw.get("run_id", "unnamed-run"))),
        agent_id=AgentId(str(raw.get("agent_id", "mock-agent-v1"))),
        backend_id=BackendId(backend_id_str),
        max_concurrent_tasks=int(raw.get("max_concurrent_tasks", 1)),
        resource_limits=resource_limits,
    )

    # ------------------------------------------------------------------
    # benchmark_id
    # ------------------------------------------------------------------
    benchmark_id = str(raw.get("benchmark_id", "toy"))
    if benchmark_id not in _SUPPORTED_BENCHMARKS:
        raise ConfigurationError(
            f"Unsupported benchmark_id '{benchmark_id}'. "
            f"Supported values: {sorted(_SUPPORTED_BENCHMARKS)}"
        )

    # ------------------------------------------------------------------
    # policy
    # ------------------------------------------------------------------
    policy_raw = raw.get("policy", {})
    if not isinstance(policy_raw, dict):
        raise ConfigurationError(
            f"policy must be a YAML mapping, "
            f"got {type(policy_raw).__name__}"
        )

    p_max_cost: float | None = None
    if "max_cost_usd" in policy_raw:
        p_max_cost = float(policy_raw["max_cost_usd"])
        if p_max_cost < 0:
            raise ConfigurationError(
                f"policy.max_cost_usd must be >= 0, got {p_max_cost}"
            )

    p_max_trace: int | None = None
    if "max_trace_events" in policy_raw:
        p_max_trace = int(policy_raw["max_trace_events"])
        if p_max_trace < 1:
            raise ConfigurationError(
                f"policy.max_trace_events must be >= 1, "
                f"got {p_max_trace}"
            )

    deny_raw = policy_raw.get("deny_tool_ids", [])
    if not isinstance(deny_raw, list):
        raise ConfigurationError(
            f"policy.deny_tool_ids must be a YAML list, "
            f"got {type(deny_raw).__name__}"
        )
    deny_tool_ids: tuple[str, ...] = tuple(str(t) for t in deny_raw)

    policy_spec = PolicySpec(
        policy_id=str(policy_raw.get("policy_id", "default")),
        max_cost_usd=p_max_cost,
        max_trace_events=p_max_trace,
        deny_tool_ids=deny_tool_ids,
    )

    # ------------------------------------------------------------------
    # mock runner parameters (demo/test knobs)
    # ------------------------------------------------------------------
    mock_fail = bool(raw.get("mock_fail", False))

    mock_cost_raw = float(raw.get("mock_cost_per_task_usd", 0.0))
    if mock_cost_raw < 0:
        raise ConfigurationError(
            f"mock_cost_per_task_usd must be >= 0, got {mock_cost_raw}"
        )

    # ------------------------------------------------------------------
    # benchmark_scenario
    # ------------------------------------------------------------------
    benchmark_scenario = str(raw.get("benchmark_scenario", "smoke"))
    if benchmark_scenario not in SUPPORTED_SCENARIOS:
        raise ConfigurationError(
            f"Unsupported benchmark_scenario '{benchmark_scenario}'. "
            f"Supported values: {sorted(SUPPORTED_SCENARIOS)}"
        )

    return LoadedRunConfig(
        path=path,
        raw=raw,
        run_config=run_config,
        policy_spec=policy_spec,
        benchmark_id=benchmark_id,
        mock_fail=mock_fail,
        mock_cost_per_task_usd=mock_cost_raw,
        benchmark_scenario=benchmark_scenario,
    )
