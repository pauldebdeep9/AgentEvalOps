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

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agentevalops.core.errors import ConfigurationError
from agentevalops.core.schemas import PolicySpec, ResourceLimits, RunConfig
from agentevalops.core.types import BACKEND_AWS, AgentId, BackendId, RunId

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: "aws" (core.types.BACKEND_AWS) is not here: nothing implements it, and AWS
#: left v0.5 (ADR-0005 D3). Accepting it wrote "aws" into metadata.json and
#: report.md for a run that was local (ROADMAP §0, 2026-10-31, finding 3).
_SUPPORTED_BACKENDS: frozenset[str] = frozenset({"local"})


def _registered(kind: str) -> frozenset[str]:
    """The names the registry knows for *kind* (``"runner"`` or
    ``"benchmark"``): the loader's allowlists are derived from it at load
    time, never hand-kept (ROADMAP R23; ADR-0005 finding 4).

    Hand-kept lists drifted once: on 2026-08-03 ``"openai"`` was registered
    but missing from ``_SUPPORTED_RUNNERS``, so no YAML could reach it.
    Derived at call time, not import time, so anything registered later
    (e.g. through entry points, ADR-0006 D24) is accepted too.
    """
    from agentevalops import registry  # deferred: registry imports adapters

    return frozenset(registry.names(kind))

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
    benchmark_options:
        Options the CLI passes to the benchmark's constructor
        (docs/adr/0006 D25). Opaque here: the benchmark validates them.
    benchmark_scenario:
        ``benchmark_options["scenario"]``, or ``""`` -- kept because
        ``config.json`` and the manifest record it. Top-level
        ``benchmark_scenario:`` in YAML is an alias for that option.
    runner_id:
        Which registered agent runner to use (``"mock"`` by default, or
        ``"langgraph"`` for the real Claude-backed runner).  The CLI resolves
        this against the component registry.
    evaluators:
        The registered evaluators the run configures (docs/adr/0006 D13),
        ``("deterministic",)`` by default. A scenario that declares an
        evaluator the run does not configure is not evaluated, with cause
        ``configuration``.
    mock_trajectory:
        ``"known_good"`` or ``"known_bad"`` plays every task's scripted
        trajectory of that kind through the mock runner (docs/adr/0006
        D11); ``None`` keeps the mock's metadata-driven mode. Only valid
        with ``runner_id: mock`` and a benchmark that has scripts.
    """

    path: Path
    raw: dict[str, Any]
    run_config: RunConfig
    policy_spec: PolicySpec
    benchmark_id: str
    mock_fail: bool = False
    mock_cost_per_task_usd: float = 0.0
    benchmark_scenario: str = ""
    benchmark_options: dict[str, Any] = field(default_factory=dict)
    runner_id: str = "mock"
    evaluators: tuple[str, ...] = ("deterministic",)
    mock_trajectory: str | None = None

    @property
    def model(self) -> str | None:
        """Model id for model-backed runners, or ``None`` for ``mock``.

        A read-only view over ``run_config.model`` -- not a second stored
        field. Before 2026-08-05 this was its own dataclass field, set from
        the same local variable as ``run_config.model`` in
        :func:`load_run_config` but structurally capable of drifting from
        it if any other construction path ever set them differently. There
        is now exactly one place this value lives; this property exists
        only so ``loaded.model`` keeps working as a convenience accessor.
        """
        return self.run_config.model

    @property
    def deployment(self) -> str | None:
        """Azure deployment name for ``runner_id: azure-openai``, or
        ``None`` for every other runner.  Same read-only-view pattern as
        :attr:`model` above -- not a second stored field."""
        return self.run_config.deployment

    @property
    def api_version(self) -> str | None:
        """Azure OpenAI API version for ``runner_id: azure-openai``, or
        ``None`` for every other runner.  Same read-only-view pattern as
        :attr:`model` above -- not a second stored field."""
        return self.run_config.api_version


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------


def _as_int(value: Any, field_name: str) -> int:
    """``int(value)``, or a ``ConfigurationError`` naming *field_name*."""
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ConfigurationError(
            f"{field_name} must be an integer, got {value!r}"
        ) from None


def _as_float(value: Any, field_name: str) -> float:
    """``float(value)``, or a ``ConfigurationError`` naming *field_name*."""
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ConfigurationError(
            f"{field_name} must be a number, got {value!r}"
        ) from None


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
        - ``runner_id: azure-openai`` omits ``deployment`` or ``api_version``.
        - ``deployment``/``api_version`` are set for any other ``runner_id``.
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

    max_tokens = _as_int(
        rl_raw.get("max_tokens", 100_000), "resource_limits.max_tokens"
    )
    max_wall_seconds = _as_float(
        rl_raw.get("max_wall_seconds", 3_600.0), "resource_limits.max_wall_seconds"
    )
    rl_max_cost = _as_float(
        rl_raw.get("max_cost_usd", 10.0), "resource_limits.max_cost_usd"
    )

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
    if backend_id_str == BACKEND_AWS:
        raise ConfigurationError(
            "backend_id 'aws' is not implemented: AWS left v0.5 (ADR-0005 "
            "D3, docs/adr/0005-agentic-assurance-rebaseline.md), and "
            "accepting it would record 'aws' for a run that executes "
            "locally. Use backend_id: local."
        )
    if backend_id_str not in _SUPPORTED_BACKENDS:
        raise ConfigurationError(
            f"Unsupported backend_id '{backend_id_str}'. "
            f"Supported values: {sorted(_SUPPORTED_BACKENDS)}"
        )

    # ------------------------------------------------------------------
    # model / deployment / api_version (parsed early so run_config can
    # carry them -- see below). deployment/api_version are azure-openai-
    # only fields; validated against runner_id further down, once runner_id
    # itself has been parsed.
    # ------------------------------------------------------------------
    model_raw = raw.get("model")
    model = str(model_raw) if model_raw is not None else None

    deployment_raw = raw.get("deployment")
    deployment = str(deployment_raw) if deployment_raw is not None else None

    api_version_raw = raw.get("api_version")
    api_version = (
        str(api_version_raw) if api_version_raw is not None else None
    )

    # ------------------------------------------------------------------
    # run_config
    # ------------------------------------------------------------------
    run_config = RunConfig(
        run_id=RunId(str(raw.get("run_id", "unnamed-run"))),
        agent_id=AgentId(str(raw.get("agent_id", "mock-agent-v1"))),
        backend_id=BackendId(backend_id_str),
        max_concurrent_tasks=_as_int(
            raw.get("max_concurrent_tasks", 1), "max_concurrent_tasks"
        ),
        resource_limits=resource_limits,
        model=model,
        deployment=deployment,
        api_version=api_version,
    )

    # ------------------------------------------------------------------
    # benchmark_id
    # ------------------------------------------------------------------
    benchmark_id = str(raw.get("benchmark_id", "toy"))
    supported_benchmarks = _registered("benchmark")
    if benchmark_id not in supported_benchmarks:
        raise ConfigurationError(
            f"Unsupported benchmark_id '{benchmark_id}'. "
            f"Supported values: {sorted(supported_benchmarks)}"
        )

    # ------------------------------------------------------------------
    # evaluators (docs/adr/0006 D13)
    # ------------------------------------------------------------------
    evaluators_raw = raw.get("evaluators", ["deterministic"])
    if not isinstance(evaluators_raw, list) or not all(
        isinstance(name, str) for name in evaluators_raw
    ):
        raise ConfigurationError("evaluators must be a YAML list of names")
    if not evaluators_raw:
        raise ConfigurationError("evaluators must name at least one evaluator")
    if len(set(evaluators_raw)) != len(evaluators_raw):
        raise ConfigurationError(f"evaluators repeats a name: {evaluators_raw}")
    supported_evaluators = _registered("evaluator")
    for name in evaluators_raw:
        if name not in supported_evaluators:
            raise ConfigurationError(
                f"Unsupported evaluator '{name}'. "
                f"Supported values: {sorted(supported_evaluators)}"
            )
    evaluators: tuple[str, ...] = tuple(evaluators_raw)

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
        p_max_cost = _as_float(policy_raw["max_cost_usd"], "policy.max_cost_usd")
        if p_max_cost < 0:
            raise ConfigurationError(
                f"policy.max_cost_usd must be >= 0, got {p_max_cost}"
            )

    p_max_trace: int | None = None
    if "max_trace_events" in policy_raw:
        p_max_trace = _as_int(
            policy_raw["max_trace_events"], "policy.max_trace_events"
        )
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

    # docs/adr/0006 D15 (R25): least privilege. A non-empty list means a
    # recorded call to any tool outside it is a violation.
    allowed_raw = policy_raw.get("allowed_tool_ids", [])
    if not isinstance(allowed_raw, list):
        raise ConfigurationError(
            f"policy.allowed_tool_ids must be a YAML list, "
            f"got {type(allowed_raw).__name__}"
        )
    allowed_tool_ids: tuple[str, ...] = tuple(str(t) for t in allowed_raw)
    both = sorted(set(allowed_tool_ids) & set(deny_tool_ids))
    if both:
        raise ConfigurationError(
            f"policy.allowed_tool_ids and policy.deny_tool_ids both name {both}; "
            "a tool is allowed or denied, not both"
        )

    policy_spec = PolicySpec(
        policy_id=str(policy_raw.get("policy_id", "default")),
        max_cost_usd=p_max_cost,
        max_trace_events=p_max_trace,
        allowed_tool_ids=allowed_tool_ids,
        deny_tool_ids=deny_tool_ids,
    )

    # ------------------------------------------------------------------
    # mock runner parameters (demo/test knobs)
    # ------------------------------------------------------------------
    mock_fail = bool(raw.get("mock_fail", False))

    mock_cost_raw = _as_float(
        raw.get("mock_cost_per_task_usd", 0.0), "mock_cost_per_task_usd"
    )
    if mock_cost_raw < 0:
        raise ConfigurationError(
            f"mock_cost_per_task_usd must be >= 0, got {mock_cost_raw}"
        )

    # ------------------------------------------------------------------
    # benchmark_options (docs/adr/0006 D25)
    # ------------------------------------------------------------------
    # Opaque to the loader: the benchmark validates its own options when the
    # CLI builds it, so the loader knows no benchmark's vocabulary (finding
    # 8). ``benchmark_scenario`` stays as an alias for ``scenario``, so
    # existing toy configs keep working.
    options_raw = raw.get("benchmark_options", {})
    if not isinstance(options_raw, dict):
        raise ConfigurationError(
            f"benchmark_options must be a YAML mapping, "
            f"got {type(options_raw).__name__}"
        )
    benchmark_options: dict[str, Any] = {str(k): v for k, v in options_raw.items()}
    if "benchmark_scenario" in raw:
        alias = str(raw["benchmark_scenario"])
        if "scenario" in benchmark_options and benchmark_options["scenario"] != alias:
            raise ConfigurationError(
                "benchmark_scenario and benchmark_options.scenario disagree "
                f"('{alias}' vs '{benchmark_options['scenario']}'); set one"
            )
        benchmark_options["scenario"] = alias
    benchmark_scenario = str(benchmark_options.get("scenario", ""))

    # ------------------------------------------------------------------
    # runner selection (mock by default; langgraph for the real runner)
    # ------------------------------------------------------------------
    runner_id = str(raw.get("runner_id", "mock"))
    supported_runners = _registered("runner")
    if runner_id not in supported_runners:
        raise ConfigurationError(
            f"Unsupported runner_id '{runner_id}'. "
            f"Supported values: {sorted(supported_runners)}"
        )

    # Model-backed runners (everything except "mock") must specify `model`
    # explicitly in the run config -- no environment-variable or hardcoded
    # fallback is legal here. This is the config-load-time half of closing
    # the model-provenance gap docs/adr/0002 found: a bundle should never be
    # producible from an ambiguous or silently-defaulted model. (The
    # runners' own DEFAULT_MODEL fallback still exists for direct,
    # non-CLI construction -- e.g. tests -- which is a different, lower-
    # stakes context than "a config this loader accepted, that will produce
    # a bundle.") Investigated 2026-08-04: LangGraphRunner silently
    # defaulting to its own DEFAULT_MODEL when `model` was omitted had the
    # identical provenance gap as OpenAIRunner's now-removed OPENAI_MODEL
    # env fallback, just without a second env-var source -- both are closed
    # here, uniformly, rather than fixing only the runner this session
    # happened to start from.
    if runner_id != "mock" and model is None:
        raise ConfigurationError(
            f"runner_id '{runner_id}' requires an explicit 'model' field in "
            "the run config. There is no environment-variable or default "
            "fallback for a config-driven run -- this is what makes the "
            "bundle's recorded config the single source of truth for which "
            "model produced it."
        )

    # ``deployment``/``api_version`` are azure-openai-only fields
    # (docs/adr/0004-cloud-backend-two-axes.md, Axis B). Required for
    # azure-openai; REJECTED (not silently ignored) for every other
    # runner_id. Silently tolerating them elsewhere would let a stale field
    # survive a runner switch undetected -- exactly the shape of bug this
    # project's history keeps finding (docs/adr/0002's "vocabulary exists,
    # never wired" pattern; the 2026-08-03 _SUPPORTED_RUNNERS parity bug
    # above). Fail loudly instead, the same way an omitted 'model' does.
    if runner_id == "azure-openai":
        if deployment is None:
            raise ConfigurationError(
                "runner_id 'azure-openai' requires an explicit 'deployment' "
                "field -- Azure OpenAI keys API requests by deployment "
                "name, not model id (docs/adr/0004-cloud-backend-two-"
                "axes.md, Axis B)."
            )
        if api_version is None:
            raise ConfigurationError(
                "runner_id 'azure-openai' requires an explicit 'api_version' "
                "field -- Azure OpenAI has no OpenAI-API equivalent default "
                "(docs/adr/0004-cloud-backend-two-axes.md, Axis B)."
            )
    elif deployment is not None or api_version is not None:
        raise ConfigurationError(
            "'deployment'/'api_version' are only valid with runner_id: "
            f"azure-openai (got runner_id: '{runner_id}'). Remove them or "
            "set runner_id: azure-openai."
        )

    # ------------------------------------------------------------------
    # mock_trajectory (docs/adr/0006 D11): scripted playback, mock only
    # ------------------------------------------------------------------
    trajectory_raw = raw.get("mock_trajectory")
    mock_trajectory = str(trajectory_raw) if trajectory_raw is not None else None
    if mock_trajectory is not None:
        if mock_trajectory not in ("known_good", "known_bad"):
            raise ConfigurationError(
                f"mock_trajectory must be 'known_good' or 'known_bad', got "
                f"'{mock_trajectory}'"
            )
        if runner_id != "mock":
            raise ConfigurationError(
                "mock_trajectory is only valid with runner_id: mock "
                f"(got runner_id: '{runner_id}')"
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
        benchmark_options=benchmark_options,
        runner_id=runner_id,
        evaluators=evaluators,
        mock_trajectory=mock_trajectory,
    )
