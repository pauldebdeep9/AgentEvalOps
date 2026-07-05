"""Tests for registry.py — component registry and entry-point discovery (W6)."""

from __future__ import annotations

import pytest

from agentevalops.core.errors import ConfigurationError
from agentevalops.registry import (
    ENTRY_POINT_GROUP,
    KIND_BENCHMARK,
    KIND_EVALUATOR,
    KIND_POLICY,
    KIND_RUNNER,
    Registry,
    RegistryEntry,
    UnknownComponentError,
    build_default_registry,
    default_registry,
    discover_entry_points,
    get,
    names,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _factory(**kwargs: object) -> dict[str, object]:
    """A trivial callable standing in for a component class."""
    return dict(kwargs)


# ---------------------------------------------------------------------------
# register / get
# ---------------------------------------------------------------------------


def test_register_returns_entry() -> None:
    reg = Registry()
    entry = reg.register(KIND_RUNNER, "mine", _factory)
    assert isinstance(entry, RegistryEntry)
    assert entry.kind == KIND_RUNNER
    assert entry.name == "mine"
    assert entry.factory is _factory


def test_get_returns_registered_factory() -> None:
    reg = Registry()
    reg.register(KIND_BENCHMARK, "toy", _factory)
    assert reg.get(KIND_BENCHMARK, "toy") is _factory


def test_register_rejects_unknown_kind() -> None:
    reg = Registry()
    with pytest.raises(ConfigurationError, match="Unknown registry kind"):
        reg.register("nonsense", "x", _factory)


def test_register_rejects_empty_name() -> None:
    reg = Registry()
    with pytest.raises(ConfigurationError, match="non-empty"):
        reg.register(KIND_RUNNER, "", _factory)


def test_register_rejects_non_callable_factory() -> None:
    reg = Registry()
    with pytest.raises(ConfigurationError, match="must be callable"):
        reg.register(KIND_RUNNER, "x", "not-callable")  # type: ignore[arg-type]


def test_register_duplicate_raises_without_replace() -> None:
    reg = Registry()
    reg.register(KIND_RUNNER, "dup", _factory)
    with pytest.raises(ConfigurationError, match="already registered"):
        reg.register(KIND_RUNNER, "dup", _factory)


def test_register_duplicate_allowed_with_replace() -> None:
    reg = Registry()
    reg.register(KIND_RUNNER, "dup", _factory)

    def other(**kwargs: object) -> str:
        return "other"

    reg.register(KIND_RUNNER, "dup", other, replace=True)
    assert reg.get(KIND_RUNNER, "dup") is other


def test_get_unknown_raises_unknown_component_error() -> None:
    reg = Registry()
    reg.register(KIND_BENCHMARK, "toy", _factory)
    with pytest.raises(UnknownComponentError, match="No benchmark registered"):
        reg.get(KIND_BENCHMARK, "missing")


def test_unknown_component_error_is_configuration_error() -> None:
    assert issubclass(UnknownComponentError, ConfigurationError)


# ---------------------------------------------------------------------------
# has / names / entries
# ---------------------------------------------------------------------------


def test_has_reflects_registration() -> None:
    reg = Registry()
    assert not reg.has(KIND_RUNNER, "x")
    reg.register(KIND_RUNNER, "x", _factory)
    assert reg.has(KIND_RUNNER, "x")


def test_names_sorted_and_scoped_to_kind() -> None:
    reg = Registry()
    reg.register(KIND_RUNNER, "b", _factory)
    reg.register(KIND_RUNNER, "a", _factory)
    reg.register(KIND_BENCHMARK, "z", _factory)
    assert reg.names(KIND_RUNNER) == ("a", "b")
    assert reg.names(KIND_BENCHMARK) == ("z",)
    assert reg.names(KIND_EVALUATOR) == ()


def test_entries_all_and_filtered() -> None:
    reg = Registry()
    reg.register(KIND_RUNNER, "r", _factory)
    reg.register(KIND_BENCHMARK, "b", _factory)
    assert len(reg.entries()) == 2
    only_runners = reg.entries(KIND_RUNNER)
    assert len(only_runners) == 1
    assert only_runners[0].kind == KIND_RUNNER


# ---------------------------------------------------------------------------
# Built-in registry
# ---------------------------------------------------------------------------


def test_build_default_registry_has_builtins() -> None:
    reg = build_default_registry()
    assert reg.has(KIND_BENCHMARK, "toy")
    assert reg.has(KIND_RUNNER, "mock")
    assert reg.has(KIND_EVALUATOR, "deterministic")
    assert reg.has(KIND_POLICY, "basic")


def test_builtin_factories_are_the_real_classes() -> None:
    from agentevalops.agents.mock_runner import MockAgentRunner
    from agentevalops.benchmarks.toy.adapter import ToyBenchmarkAdapter
    from agentevalops.evaluators.deterministic import DeterministicEvaluator
    from agentevalops.policy.basic_checker import BasicPolicyChecker

    reg = build_default_registry()
    assert reg.get(KIND_BENCHMARK, "toy") is ToyBenchmarkAdapter
    assert reg.get(KIND_RUNNER, "mock") is MockAgentRunner
    assert reg.get(KIND_EVALUATOR, "deterministic") is DeterministicEvaluator
    assert reg.get(KIND_POLICY, "basic") is BasicPolicyChecker


def test_default_registry_is_prewired() -> None:
    assert default_registry.has(KIND_BENCHMARK, "toy")


# ---------------------------------------------------------------------------
# Entry-point discovery
# ---------------------------------------------------------------------------


class _FakeEntryPoint:
    def __init__(self, name: str, obj: object, *, boom: bool = False) -> None:
        self.name = name
        self._obj = obj
        self._boom = boom

    def load(self) -> object:
        if self._boom:
            raise ImportError("broken plugin")
        return self._obj


def test_discover_entry_points_registers_plugins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reg = Registry()
    fake = [_FakeEntryPoint("plugbench", _factory)]

    def fake_entry_points(*, group: str) -> list[_FakeEntryPoint]:
        assert group == ENTRY_POINT_GROUP
        return fake

    monkeypatch.setattr(
        "agentevalops.registry.importlib_metadata.entry_points",
        fake_entry_points,
    )
    loaded = reg.discover_entry_points()
    assert loaded == ("plugbench",)
    assert reg.get(KIND_BENCHMARK, "plugbench") is _factory


def test_discover_entry_points_skips_import_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reg = Registry()
    fake = [
        _FakeEntryPoint("good", _factory),
        _FakeEntryPoint("broken", _factory, boom=True),
    ]
    monkeypatch.setattr(
        "agentevalops.registry.importlib_metadata.entry_points",
        lambda *, group: fake,
    )
    loaded = reg.discover_entry_points()
    assert loaded == ("good",)
    assert not reg.has(KIND_BENCHMARK, "broken")


def test_discover_entry_points_skips_already_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reg = Registry()
    reg.register(KIND_BENCHMARK, "toy", _factory)  # built-in shadows plugin
    fake = [_FakeEntryPoint("toy", object)]
    monkeypatch.setattr(
        "agentevalops.registry.importlib_metadata.entry_points",
        lambda *, group: fake,
    )
    loaded = reg.discover_entry_points()
    assert loaded == ()
    assert reg.get(KIND_BENCHMARK, "toy") is _factory


def test_discover_entry_points_replace_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reg = Registry()
    reg.register(KIND_BENCHMARK, "toy", _factory)
    fake = [_FakeEntryPoint("toy", object)]
    monkeypatch.setattr(
        "agentevalops.registry.importlib_metadata.entry_points",
        lambda *, group: fake,
    )
    loaded = reg.discover_entry_points(replace=True)
    assert loaded == ("toy",)
    assert reg.get(KIND_BENCHMARK, "toy") is object


# ---------------------------------------------------------------------------
# Module-level convenience proxies
# ---------------------------------------------------------------------------


def test_module_get_proxy() -> None:
    assert get(KIND_BENCHMARK, "toy") is default_registry.get(KIND_BENCHMARK, "toy")


def test_module_names_proxy() -> None:
    assert names(KIND_RUNNER) == default_registry.names(KIND_RUNNER)


def test_module_discover_proxy_returns_tuple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agentevalops.registry.importlib_metadata.entry_points",
        lambda *, group: [],
    )
    assert discover_entry_points() == ()
