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

from dataclasses import dataclass
from importlib import metadata as importlib_metadata
from typing import Any, Callable

from agentevalops.core.errors import ConfigurationError

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

#: Entry-point group external packages publish benchmark adapters under.
ENTRY_POINT_GROUP: str = "agentevalops.adapters"

_KNOWN_KINDS: frozenset[str] = frozenset(
    {KIND_BENCHMARK, KIND_RUNNER, KIND_EVALUATOR, KIND_POLICY}
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
            raise ConfigurationError(
                f"Component already registered: {kind}:{name}"
            )

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
        return tuple(
            sorted(n for (k, n) in self._entries if k == kind)
        )

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
        """Load benchmark adapters published under *group* via entry points.

        Each entry point's ``name`` becomes the registry name; its loaded
        object becomes the factory and is registered under
        :data:`KIND_BENCHMARK`.

        Parameters
        ----------
        group:
            Entry-point group name.  Defaults to ``"agentevalops.adapters"``.
        replace:
            Forwarded to :meth:`register`.

        Returns
        -------
        tuple[str, ...]
            Names of entry points actually loaded (skipping any that fail to
            import).  Failures are silently skipped so a broken third-party
            plugin can't crash the CLI; callers wanting strict behaviour
            should iterate the entry points themselves.
        """
        loaded: list[str] = []
        for ep in importlib_metadata.entry_points(group=group):
            try:
                factory = ep.load()
            except Exception:  # pragma: no cover - defensive
                continue
            try:
                self.register(
                    KIND_BENCHMARK, ep.name, factory, replace=replace
                )
            except ConfigurationError:
                # Already registered (built-in shadows plugin) — skip silently.
                continue
            loaded.append(ep.name)
        return tuple(loaded)


# ---------------------------------------------------------------------------
# Built-in registrations
# ---------------------------------------------------------------------------


def _register_builtins(reg: Registry) -> None:
    """Register the components that ship inside this package."""
    # Imports are local to avoid import cycles and to keep module import cheap
    # for callers that don't actually run anything.
    from agentevalops.agents.mock_runner import MockAgentRunner
    from agentevalops.benchmarks.toy.adapter import ToyBenchmarkAdapter
    from agentevalops.evaluators.deterministic import DeterministicEvaluator
    from agentevalops.policy.basic_checker import BasicPolicyChecker

    reg.register(KIND_BENCHMARK, "toy", ToyBenchmarkAdapter)
    reg.register(KIND_RUNNER, "mock", MockAgentRunner)
    reg.register(KIND_EVALUATOR, "deterministic", DeterministicEvaluator)
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


__all__ = [
    "ENTRY_POINT_GROUP",
    "KIND_BENCHMARK",
    "KIND_EVALUATOR",
    "KIND_POLICY",
    "KIND_RUNNER",
    "Registry",
    "RegistryEntry",
    "UnknownComponentError",
    "build_default_registry",
    "default_registry",
    "discover_entry_points",
    "get",
    "names",
]
