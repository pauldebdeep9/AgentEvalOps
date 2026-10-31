"""Entry-point discovery and benchmark options (docs/adr/0006 D24, D25;
R24 slice 2)."""

from __future__ import annotations

import ast
import json
import logging
import pathlib
from typing import Any

import pytest
from typer.testing import CliRunner

from agentevalops import registry as registry_module
from agentevalops.benchmarks.toy.adapter import ToyBenchmarkAdapter
from agentevalops.cli import app
from agentevalops.config.loader import load_run_config
from agentevalops.core.errors import ConfigurationError
from agentevalops.registry import (
    DOMAIN_PACK_ENTRY_POINT_GROUP,
    ENTRY_POINT_GROUP,
    KIND_BENCHMARK,
    KIND_DOMAIN_PACK,
    Registry,
)

cli = CliRunner()


class _EntryPoint:
    def __init__(self, name: str, obj: object, *, boom: bool = False) -> None:
        self.name = name
        self._obj = obj
        self._boom = boom

    def load(self) -> object:
        if self._boom:
            raise ImportError("plugin is broken")
        return self._obj


def _fake_groups(
    monkeypatch: pytest.MonkeyPatch, groups: dict[str, list[_EntryPoint]]
) -> None:
    monkeypatch.setattr(
        "agentevalops.registry.importlib_metadata.entry_points",
        lambda *, group: groups.get(group, []),
    )


class _Pack:
    pass


# ---------------------------------------------------------------------------
# D24: discovery
# ---------------------------------------------------------------------------


def test_domain_packs_register_under_their_own_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_groups(
        monkeypatch,
        {
            DOMAIN_PACK_ENTRY_POINT_GROUP: [_EntryPoint("seed", _Pack)],
            ENTRY_POINT_GROUP: [_EntryPoint("plugbench", ToyBenchmarkAdapter)],
        },
    )
    reg = Registry()
    loaded = reg.discover_all()
    assert loaded == {
        ENTRY_POINT_GROUP: ("plugbench",),
        DOMAIN_PACK_ENTRY_POINT_GROUP: ("seed",),
    }
    assert reg.get(KIND_DOMAIN_PACK, "seed") is _Pack
    assert reg.get(KIND_BENCHMARK, "plugbench") is ToyBenchmarkAdapter


def test_a_plugin_that_fails_to_import_is_skipped_and_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Fail-first: before D24 the skip was silent."""
    _fake_groups(
        monkeypatch,
        {DOMAIN_PACK_ENTRY_POINT_GROUP: [_EntryPoint("bad", _Pack, boom=True)]},
    )
    with caplog.at_level(logging.WARNING, logger="agentevalops.registry"):
        loaded = Registry().discover_entry_points(DOMAIN_PACK_ENTRY_POINT_GROUP)
    assert loaded == ()
    (record,) = caplog.records
    assert "Skipped plugin 'bad'" in record.message
    assert "failed to import" in record.message


def test_a_plugin_shadowed_by_a_registered_name_is_skipped_and_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Fail-first: before D24 the skip was silent."""
    reg = Registry()
    reg.register(KIND_BENCHMARK, "toy", ToyBenchmarkAdapter)
    _fake_groups(monkeypatch, {ENTRY_POINT_GROUP: [_EntryPoint("toy", object)]})
    with caplog.at_level(logging.WARNING, logger="agentevalops.registry"):
        assert reg.discover_entry_points() == ()
    (record,) = caplog.records
    assert "already registered" in record.message
    assert reg.get(KIND_BENCHMARK, "toy") is ToyBenchmarkAdapter


def test_rediscovering_a_built_in_is_quiet(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """pyproject.toml publishes the built-in toy benchmark as an entry
    point; finding the very object already registered is not a conflict,
    and must not warn on every CLI run."""
    reg = Registry()
    reg.register(KIND_BENCHMARK, "toy", ToyBenchmarkAdapter)
    same = _EntryPoint("toy", ToyBenchmarkAdapter)
    _fake_groups(monkeypatch, {ENTRY_POINT_GROUP: [same]})
    with caplog.at_level(logging.WARNING, logger="agentevalops.registry"):
        assert reg.discover_entry_points() == ()
    assert caplog.records == []


def test_cli_discovers_plugins_before_loading_the_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """A benchmark only a plugin provides is selectable from YAML."""
    fresh = registry_module.build_default_registry()
    monkeypatch.setattr(registry_module, "default_registry", fresh)
    plugin = _EntryPoint("plugbench", ToyBenchmarkAdapter)
    _fake_groups(monkeypatch, {ENTRY_POINT_GROUP: [plugin]})
    config = tmp_path / "c.yaml"
    config.write_text("run_id: r1\nbenchmark_id: plugbench\n", encoding="utf-8")
    result = cli.invoke(app, ["run", "--config", str(config)])
    assert result.exit_code == 0, result.output
    assert fresh.has(KIND_BENCHMARK, "plugbench")


# ---------------------------------------------------------------------------
# D25: benchmark options
# ---------------------------------------------------------------------------


def _write(tmp_path: pathlib.Path, text: str) -> pathlib.Path:
    path = tmp_path / "c.yaml"
    path.write_text("run_id: r1\nbenchmark_id: toy\n" + text, encoding="utf-8")
    return path


def test_options_are_read_and_the_scenario_alias_still_works(
    tmp_path: pathlib.Path,
) -> None:
    config = _write(tmp_path, "benchmark_options:\n  scenario: mixed\n")
    loaded = load_run_config(config)
    assert (loaded.benchmark_options, loaded.benchmark_scenario) == (
        {"scenario": "mixed"},
        "mixed",
    )
    aliased = load_run_config(_write(tmp_path, "benchmark_scenario: failure\n"))
    assert aliased.benchmark_options == {"scenario": "failure"}


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("benchmark_options: [1]\n", "mapping"),
        (
            "benchmark_scenario: smoke\nbenchmark_options:\n  scenario: mixed\n",
            "disagree",
        ),
    ],
)
def test_malformed_options_are_rejected(
    tmp_path: pathlib.Path, text: str, match: str
) -> None:
    with pytest.raises(ConfigurationError, match=match):
        load_run_config(_write(tmp_path, text))


def test_loader_knows_no_benchmark_vocabulary(tmp_path: pathlib.Path) -> None:
    """Finding 8: the loader no longer imports the toy benchmark, and an
    unknown scenario is the benchmark's to refuse, not the loader's."""
    from agentevalops.config import loader

    tree = ast.parse(pathlib.Path(loader.__file__).read_text(encoding="utf-8"))
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    assert not any(m and m.startswith("agentevalops.benchmarks") for m in imported)
    loaded = load_run_config(_write(tmp_path, "benchmark_scenario: no-such\n"))
    assert loaded.benchmark_options == {"scenario": "no-such"}


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("benchmark_options:\n  colour: red\n", "does not accept"),
        ("benchmark_scenario: no-such\n", "Unknown toy scenario"),
    ],
)
def test_the_benchmark_refuses_options_it_does_not_take(
    tmp_path: pathlib.Path, text: str, message: str
) -> None:
    result = cli.invoke(app, ["run", "--config", str(_write(tmp_path, text))])
    assert result.exit_code == 1
    assert "Configuration error" in result.output
    assert message in result.output


def test_options_reach_the_benchmark_and_the_bundle(tmp_path: pathlib.Path) -> None:
    out = tmp_path / "bundle"
    config = _write(tmp_path, "benchmark_options:\n  scenario: mixed\n")
    result = cli.invoke(app, ["run", "--config", str(config), "--output", str(out)])
    assert result.exit_code == 0, result.output
    recorded: dict[str, Any] = json.loads((out / "config.json").read_text())
    assert recorded["benchmark_options"] == {"scenario": "mixed"}
    summary = json.loads((out / "summary.json").read_text())
    assert summary["total_tasks"] == 3  # the mixed scenario's three tasks
