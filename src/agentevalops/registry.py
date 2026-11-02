"""Component registry with entry-point discovery (ROADMAP W6).

The registry is the single lookup table the CLI and config loader consult when
resolving names like ``benchmark_id: "toy"`` to concrete classes.  It supports
two registration paths:

1. **Built-in components.**  First-party adapters/runners/evaluators/policy
   checkers shipped inside this package register themselves at import time via
   :func:`_register_builtins`.

2. **Third-party plugins.**  External packages can publish benchmark adapters
   under the ``agentevalops.adapters`` entry-point group.  Calling
   :meth:`Registry.discover_entry_points` (also exposed as the module-level
   :func:`discover_entry_points`) loads them via
   :func:`importlib.metadata.entry_points`.

Design notes
------------
* Factories are stored as the *callable* (typically the class itself).  The
  caller instantiates with whatever kwargs it needs — the registry takes no
  position on construction signatures.
* The registry is keyed by ``(kind, name)``.  Kinds are simple strings; the
  built-in kinds are exposed as ``KIND_*`` constants.
* :class:`UnknownComponentError` is a ``ConfigurationError`` so existing
  error-handling paths in the CLI still apply.
* The module-level :data:`default_registry` is the singleton used by the rest
  of the package; tests can pass their own :class:`Registry` for isolation.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import metadata as importlib_metadata
from typing import Any, Callable

from agentevalops.core.errors import ConfigurationError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

#: Benchmark adapters (e.g. ``"toy"``).
KIND_BENCHMARK: str = "benchmark"

#: Agent runners (e.g. ``"mock"``).
KIND_RUNNER: str = "runner"

#: Evaluators (e.g. ``"deterministic"``).
KIND_EVALUATOR: str = "evaluator"

#: Policy checkers (e.g. ``"basic"``).
KIND_POLICY: str = "policy"

#: Domain packs (docs/adr/0006 D23, D24), e.g. the seed domain.
KIND_DOMAIN_PACK: str = "domain_pack"

#: Entry-point group external packages publish benchmark adapters under.
ENTRY_POINT_GROUP: str = "agentevalops.adapters"

#: Entry-point group for domain packs (docs/adr/0006 D24).
DOMAIN_PACK_ENTRY_POINT_GROUP: str = "agentevalops.domain_packs"

#: Which kind each entry-point group registers under.
_GROUP_KINDS: dict[str, str] = {
    ENTRY_POINT_GROUP: KIND_BENCHMARK,
    DOMAIN_PACK_ENTRY_POINT_GROUP: KIND_DOMAIN_PACK,
}

_KNOWN_KINDS: frozenset[str] = frozenset(
    {KIND_BENCHMARK, KIND_RUNNER, KIND_EVALUATOR, KIND_POLICY, KIND_DOMAIN_PACK}
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class UnknownComponentError(ConfigurationError):
    """Raised when the requested ``(kind, name)`` is not registered."""


# ---------------------------------------------------------------------------
# Entry type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegistryEntry:
    """One registered component.

    Attributes
    ----------
    kind:
        Component kind, e.g. :data:`KIND_BENCHMARK`.
    name:
        Short identifier referenced from config files.
    factory:
        Callable invoked to produce an instance.  Usually the class itself.
    """

    kind: str
    name: str
    factory: Callable[..., Any]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class Registry:
    """Process-wide table of pluggable components keyed by ``(kind, name)``."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], RegistryEntry] = {}

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def register(
        self,
        kind: str,
        name: str,
        factory: Callable[..., Any],
        *,
        replace: bool = False,
    ) -> RegistryEntry:
        """Register *factory* under ``(kind, name)``.

        Parameters
        ----------
        kind:
            Must be one of the ``KIND_*`` constants.
        name:
            Non-empty identifier; the value users put in YAML.
        factory:
            Any callable (usually the class).  The registry never invokes it
            on its own.
        replace:
            If ``False`` (default), re-registering the same key raises
            :class:`ConfigurationError`.  Set to ``True`` in tests that
            intentionally override.

        Returns
        -------
        RegistryEntry
            The stored entry.
        """
        if kind not in _KNOWN_KINDS:
            raise ConfigurationError(
                f"Unknown registry kind '{kind}'. "
                f"Supported kinds: {sorted(_KNOWN_KINDS)}"
            )
        if not name:
            raise ConfigurationError("Registry name must be a non-empty string")
        if not callable(factory):
            raise ConfigurationError(
                f"Registry factory for {kind}:{name} must be callable"
            )

        key = (kind, name)
        if key in self._entries and not replace:
            raise ConfigurationError(f"Component already registered: {kind}:{name}")

        entry = RegistryEntry(kind=kind, name=name, factory=factory)
        self._entries[key] = entry
        return entry

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, kind: str, name: str) -> Callable[..., Any]:
        """Return the factory registered under ``(kind, name)``.

        Raises
        ------
        UnknownComponentError
            If no entry matches.
        """
        try:
            return self._entries[(kind, name)].factory
        except KeyError:
            known = self.names(kind)
            raise UnknownComponentError(
                f"No {kind} registered under name '{name}'. "
                f"Known: {list(known) or '[]'}"
            ) from None

    def has(self, kind: str, name: str) -> bool:
        """Return ``True`` if ``(kind, name)`` is registered."""
        return (kind, name) in self._entries

    def names(self, kind: str) -> tuple[str, ...]:
        """Return the registered names for *kind*, sorted alphabetically."""
        return tuple(sorted(n for (k, n) in self._entries if k == kind))

    def entries(self, kind: str | None = None) -> tuple[RegistryEntry, ...]:
        """Return all entries, optionally filtered to a single *kind*."""
        if kind is None:
            return tuple(self._entries.values())
        return tuple(e for e in self._entries.values() if e.kind == kind)

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover_entry_points(
        self,
        group: str = ENTRY_POINT_GROUP,
        *,
        replace: bool = False,
    ) -> tuple[str, ...]:
        """Load components published under *group* via entry points.

        Each entry point's ``name`` becomes the registry name; its loaded
        object becomes the factory, registered under the group's kind --
        :data:`KIND_BENCHMARK` for :data:`ENTRY_POINT_GROUP`,
        :data:`KIND_DOMAIN_PACK` for :data:`DOMAIN_PACK_ENTRY_POINT_GROUP`.

        Parameters
        ----------
        group:
            Entry-point group name.  Defaults to ``"agentevalops.adapters"``.
        replace:
            Forwarded to :meth:`register`.

        Returns
        -------
        tuple[str, ...]
            Names of entry points actually loaded. A plugin that fails to
            import, or whose name is already registered (a built-in shadows
            it), is skipped and logged as a warning (docs/adr/0006 D24) -- a
            broken third-party plugin can't crash the CLI, and can't vanish
            unnoticed either.
        """
        kind = _GROUP_KINDS.get(group, KIND_BENCHMARK)
        loaded: list[str] = []
        for ep in importlib_metadata.entry_points(group=group):
            try:
                factory = ep.load()
            except Exception as exc:  # noqa: BLE001 - a plugin's own failure
                logger.warning(
                    "Skipped plugin '%s' (entry-point group %s): it failed "
                    "to import: %s",
                    ep.name,
                    group,
                    exc,
                )
                continue
            if (
                not replace
                and self.has(kind, ep.name)
                and self.get(kind, ep.name) is factory
            ):
                # The very object already registered -- e.g. pyproject.toml
                # publishing a built-in as an entry point. Not a conflict.
                continue
            try:
                self.register(kind, ep.name, factory, replace=replace)
            except ConfigurationError:
                logger.warning(
                    "Skipped plugin '%s' (entry-point group %s): a %s of "
                    "that name is already registered",
                    ep.name,
                    group,
                    kind,
                )
                continue
            loaded.append(ep.name)
        return tuple(loaded)

    def discover_all(self) -> dict[str, tuple[str, ...]]:
        """Discover every entry-point group this platform knows; returns the
        names loaded per group. The CLI calls it once, before resolving any
        name from a config (docs/adr/0006 D24)."""
        return {group: self.discover_entry_points(group) for group in _GROUP_KINDS}


# ---------------------------------------------------------------------------
# Built-in registrations
# ---------------------------------------------------------------------------


def _agentic_baseline_factory(reg: Registry) -> Callable[..., Any]:
    """Build ``agentic-baseline`` from YAML options: ``pack`` names a
    registered domain pack, resolved here (docs/adr/0006 D23, D24) so the
    benchmark itself never imports the registry."""
    from agentevalops.benchmarks.agentic_baseline import AgenticBaselineBenchmark
    from agentevalops.core.protocols import DomainPack

    def build(pack: str = "", scenarios: list[str] | None = None) -> Any:
        if not pack:
            raise ConfigurationError(
                "agentic-baseline needs benchmark_options.pack: the name of "
                f"a registered domain pack ({sorted(reg.names(KIND_DOMAIN_PACK))})"
            )
        resolved = reg.get(KIND_DOMAIN_PACK, str(pack))()
        if not isinstance(resolved, DomainPack):
            raise ConfigurationError(
                f"domain pack '{pack}' does not satisfy the DomainPack protocol"
            )
        return AgenticBaselineBenchmark(resolved, scenarios=scenarios)

    return build


def _register_builtins(reg: Registry) -> None:
    """Register the components that ship inside this package."""
    # Imports are local to avoid import cycles and to keep module import cheap
    # for callers that don't actually run anything.
    from agentevalops.agents.azure_openai_runner import AzureOpenAIRunner
    from agentevalops.agents.langgraph_runner import LangGraphRunner
    from agentevalops.agents.mock_runner import MockAgentRunner
    from agentevalops.agents.openai_runner import OpenAIRunner
    from agentevalops.benchmarks.toy.adapter import ToyBenchmarkAdapter
    from agentevalops.domain_packs.seed import SeedDomainPack
    from agentevalops.evaluators.deterministic import DeterministicEvaluator
    from agentevalops.evaluators.policy_compliance import PolicyComplianceEvaluator
    from agentevalops.evaluators.state_based import StateBasedEvaluator
    from agentevalops.evaluators.tool_use import ToolUseEvaluator
    from agentevalops.policy.basic_checker import BasicPolicyChecker

    reg.register(KIND_BENCHMARK, "toy", ToyBenchmarkAdapter)
    reg.register(KIND_BENCHMARK, "agentic-baseline", _agentic_baseline_factory(reg))
    # The seed domain (docs/adr/0005 D9) is a built-in and is also published
    # under the domain_packs entry-point group, as a third-party pack would be;
    # discovery finds the same object and skips it quietly (docs/adr/0006 D24).
    reg.register(KIND_DOMAIN_PACK, "seed", SeedDomainPack)
    reg.register(KIND_RUNNER, "mock", MockAgentRunner)
    # Registering the langgraph/openai/azure-openai runners does NOT import
    # anthropic/langgraph/openai — those deps are imported lazily inside the
    # runner methods, so the registry stays importable with only the core
    # dependencies present.
    reg.register(KIND_RUNNER, "langgraph", LangGraphRunner)
    reg.register(KIND_RUNNER, "openai", OpenAIRunner)
    # Axis B of docs/adr/0004-cloud-backend-two-axes.md -- a model-provider
    # concern only; unrelated to the (out-of-scope) CloudBackend Azure axis.
    reg.register(KIND_RUNNER, "azure-openai", AzureOpenAIRunner)
    reg.register(KIND_EVALUATOR, "deterministic", DeterministicEvaluator)
    # docs/adr/0006 D13: a run configures any set of these under
    # ``evaluators:``; a scenario declares which apply.
    reg.register(KIND_EVALUATOR, "tool_use", ToolUseEvaluator)
    reg.register(KIND_EVALUATOR, "state_based", StateBasedEvaluator)
    reg.register(KIND_EVALUATOR, "policy", PolicyComplianceEvaluator)
    reg.register(KIND_POLICY, "basic", BasicPolicyChecker)


def build_default_registry() -> Registry:
    """Construct a fresh registry with built-ins registered.

    Used by tests that need an isolated registry instance.  Production code
    should import :data:`default_registry` instead.
    """
    reg = Registry()
    _register_builtins(reg)
    return reg


#: Process-wide default registry pre-loaded with built-in components.
default_registry: Registry = build_default_registry()


# ---------------------------------------------------------------------------
# Module-level convenience proxies
# ---------------------------------------------------------------------------


def get(kind: str, name: str) -> Callable[..., Any]:
    """Look up *name* of *kind* in :data:`default_registry`."""
    return default_registry.get(kind, name)


def build_benchmark(benchmark_id: str, options: dict[str, Any]) -> Any:
    """The benchmark *benchmark_id* built with *options*, as a run builds it
    and as ``rescore`` rebuilds it from ``config.json`` (docs/adr/0006 D25:
    the benchmark validates its own options). Raises
    ``UnknownComponentError`` when it is not installed and
    ``ConfigurationError`` when it refuses the options."""
    benchmark_cls = default_registry.get(KIND_BENCHMARK, benchmark_id)
    try:
        return benchmark_cls(**options)
    except TypeError as exc:
        raise ConfigurationError(
            f"benchmark '{benchmark_id}' does not accept these benchmark_options: {exc}"
        ) from None


#: The policy checker every run uses: no config names another, and no
#: bundle records one, so the run and ``rescore`` both build this one.
POLICY_CHECKER = "basic"


def build_evaluators(kinds: Sequence[str]) -> list[Any]:
    """The evaluators *kinds* -- a run's configured evaluator kinds, in
    order (docs/adr/0006 D13) -- as a run builds them and as ``rescore``
    rebuilds them from ``config.json``. Raises ``UnknownComponentError``
    for one that is not installed."""
    return [default_registry.get(KIND_EVALUATOR, kind)() for kind in kinds]


def build_policy_checker() -> Any:
    """The run-level policy checker, as a run builds it and as ``rescore``
    rebuilds it."""
    return default_registry.get(KIND_POLICY, POLICY_CHECKER)()


def names(kind: str) -> tuple[str, ...]:
    """Return the names of *kind* registered in :data:`default_registry`."""
    return default_registry.names(kind)


def discover_entry_points(
    group: str = ENTRY_POINT_GROUP,
    *,
    replace: bool = False,
) -> tuple[str, ...]:
    """Discover plugin adapters into :data:`default_registry`."""
    return default_registry.discover_entry_points(group, replace=replace)


def discover_all() -> dict[str, tuple[str, ...]]:
    """Discover every known entry-point group on the default registry."""
    return default_registry.discover_all()


__all__ = [
    "DOMAIN_PACK_ENTRY_POINT_GROUP",
    "ENTRY_POINT_GROUP",
    "KIND_BENCHMARK",
    "KIND_DOMAIN_PACK",
    "KIND_EVALUATOR",
    "KIND_POLICY",
    "KIND_RUNNER",
    "POLICY_CHECKER",
    "Registry",
    "RegistryEntry",
    "UnknownComponentError",
    "build_benchmark",
    "build_default_registry",
    "build_evaluators",
    "build_policy_checker",
    "default_registry",
    "discover_all",
    "discover_entry_points",
    "get",
    "names",
]
