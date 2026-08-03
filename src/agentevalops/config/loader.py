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
#: Agent runners selectable from a config's ``runner_id``.  ``"mock"`` is the
#: default and the only runner required for the v0.1 demo path; ``"langgraph"``
#: is the real Claude-backed runner (ROADMAP W8), ``"openai"`` is the real
#: OpenAI-backed runner (docs/adr/0001-openai-runner-chat-completions.md),
#: and ``"azure-openai"`` is the Azure OpenAI-backed runner (docs/adr/0004-
#: cloud-backend-two-axes.md, Axis B) -- all registered separately.
#:
#: This frozenset duplicates the registry's set of registered runner names
#: (``registry.names(registry.KIND_RUNNER)``) instead of querying it
#: directly. Investigated 2026-08-03, after ``"openai"`` was registered in
#: ``registry.py`` but left out here, making the runner unreachable from any
#: YAML config despite being fully wired up everywhere else: **no deliberate
#: design reason was found for keeping these two lists separate.** It looks
#: like accidental duplication from when this module predated the registry
#: having more than two runners, not an intentional boundary. The actual
#: guardrail against future drift is
#: ``test_supported_runners_matches_registry`` in
#: ``tests/test_config_loader.py``, which asserts this set equals the
#: registry's registered runner names and fails loudly if they diverge --
#: this comment records the "why not merged" investigation, not a reason to
#: leave it duplicated forever. Collapsing this into a direct
#: ``registry.names(...)`` call is a legitimate future refactor; it was not
#: done here to keep this fix scoped to the parity bug, and would need
#: checking against import-linter's layers contract (config/ and registry.py
#: are already in the same middle tier, so this is expected to be
#: uncontroversial, just unverified until someone actually does it).
_SUPPORTED_RUNNERS: frozenset[str] = frozenset(
    {"mock", "langgraph", "openai", "azure-openai"}
)

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
    runner_id:
        Which registered agent runner to use (``"mock"`` by default, or
        ``"langgraph"`` for the real Claude-backed runner).  The CLI resolves
        this against the component registry.
    """

    path: Path
    raw: dict[str, Any]
    run_config: RunConfig
    policy_spec: PolicySpec
    benchmark_id: str
    mock_fail: bool = False
    mock_cost_per_task_usd: float = 0.0
    benchmark_scenario: str = "smoke"
    runner_id: str = "mock"

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
        max_concurrent_tasks=int(raw.get("max_concurrent_tasks", 1)),
        resource_limits=resource_limits,
        model=model,
        deployment=deployment,
        api_version=api_version,
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

    # ------------------------------------------------------------------
    # runner selection (mock by default; langgraph for the real runner)
    # ------------------------------------------------------------------
    runner_id = str(raw.get("runner_id", "mock"))
    if runner_id not in _SUPPORTED_RUNNERS:
        raise ConfigurationError(
            f"Unsupported runner_id '{runner_id}'. "
            f"Supported values: {sorted(_SUPPORTED_RUNNERS)}"
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

    return LoadedRunConfig(
        path=path,
        raw=raw,
        run_config=run_config,
        policy_spec=policy_spec,
        benchmark_id=benchmark_id,
        mock_fail=mock_fail,
        mock_cost_per_task_usd=mock_cost_raw,
        benchmark_scenario=benchmark_scenario,
        runner_id=runner_id,
    )
