"""The system declaration (``system.yaml``) and the YAML path for repetitions
and seed (ROADMAP R27 slice 1; docs/adr/0005 D6, docs/adr/0006 D18).

Every guard has a fail-first test: each rejection below was written before
the rule, and failed, before the rule made it pass.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import fast_yaml
import pytest
from typer.testing import CliRunner

from agentevalops.cli import app
from agentevalops.config.loader import load_run_config
from agentevalops.config.system import load_system_declaration
from agentevalops.core.errors import ConfigurationError
from agentevalops.core.schemas import (
    INVOLVEMENT_LEVELS,
    MGF_CATEGORIES,
    SystemDeclaration,
)

SHIPPED = Path("configs/seed_system.yaml")
RUN_CONFIG = Path("configs/agentic_baseline_mock.yaml")


def _shipped() -> dict[str, Any]:
    doc = fast_yaml.safe_load(SHIPPED.read_text(encoding="utf-8"))
    assert isinstance(doc, dict)
    return doc


def _write(tmp_path: Path, doc: Any, name: str = "system.yaml") -> Path:
    path = tmp_path / name
    path.write_text(fast_yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


def _edited(tmp_path: Path, mutate: Any) -> Path:
    doc = _shipped()
    mutate(doc)
    return _write(tmp_path, doc)


def _rejects(tmp_path: Path, mutate: Any, match: str) -> None:
    path = _edited(tmp_path, mutate)
    with pytest.raises(ConfigurationError, match=match):
        load_system_declaration(path)


# --------------------------------------------------------------------------
# The shipped declaration
# --------------------------------------------------------------------------


def test_shipped_declaration_loads() -> None:
    decl = load_system_declaration(SHIPPED)
    assert isinstance(decl, SystemDeclaration)
    assert decl.pack_id == "seed"
    assert decl.involvement == "human_approves"
    assert {t.name for t in decl.tools} == {
        "case_lookup",
        "status_update",
        "send_notice",
        "records_export",
        "policy_search",
        "refer_to_officer",
    }
    assert decl.intended_use
    assert decl.business_metrics and decl.business_metrics[0].definition
    assert decl.repetitions >= 1
    assert len(decl.sha256) == 64


def test_declaration_records_its_source_hash(tmp_path: Path) -> None:
    import hashlib

    path = _edited(tmp_path, lambda d: None)
    decl = load_system_declaration(path)
    assert decl.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_vocabularies() -> None:
    assert INVOLVEMENT_LEVELS == ("human_approves", "human_monitors", "autonomous")
    assert MGF_CATEGORIES == (
        "task_execution",
        "tool_calling",
        "policy_compliance",
        "robustness",
    )


# --------------------------------------------------------------------------
# Rejections (fail-first)
# --------------------------------------------------------------------------


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not found"):
        load_system_declaration(tmp_path / "nope.yaml")


def test_not_a_mapping(tmp_path: Path) -> None:
    path = tmp_path / "system.yaml"
    path.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="mapping"):
        load_system_declaration(path)


def test_malformed_yaml(tmp_path: Path) -> None:
    path = tmp_path / "system.yaml"
    path.write_text("tools: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="Malformed"):
        load_system_declaration(path)


@pytest.mark.parametrize("key", ["system_id", "pack_id", "intended_use"])
def test_required_text_missing(tmp_path: Path, key: str) -> None:
    _rejects(tmp_path, lambda d: d.pop(key), f"{key}")


@pytest.mark.parametrize("key", ["system_id", "pack_id", "intended_use"])
def test_required_text_blank(tmp_path: Path, key: str) -> None:
    _rejects(tmp_path, lambda d: d.__setitem__(key, "  "), f"{key}")


def test_unknown_involvement(tmp_path: Path) -> None:
    _rejects(
        tmp_path, lambda d: d.__setitem__("involvement", "sometimes"), "involvement"
    )


def test_involvement_missing(tmp_path: Path) -> None:
    _rejects(tmp_path, lambda d: d.pop("involvement"), "involvement")


def test_tools_empty(tmp_path: Path) -> None:
    _rejects(tmp_path, lambda d: d.__setitem__("tools", []), "tools")


def test_tools_not_a_list(tmp_path: Path) -> None:
    _rejects(tmp_path, lambda d: d.__setitem__("tools", {"name": "x"}), "tools")


def test_duplicate_tool(tmp_path: Path) -> None:
    _rejects(tmp_path, lambda d: d["tools"].append(dict(d["tools"][0])), "twice")


def test_tool_without_name(tmp_path: Path) -> None:
    _rejects(tmp_path, lambda d: d["tools"][0].pop("name"), "name")


def test_tool_bad_permission(tmp_path: Path) -> None:
    _rejects(
        tmp_path,
        lambda d: d["tools"][0].__setitem__("permission", "admin"),
        "permission",
    )


def test_tool_bad_data_class(tmp_path: Path) -> None:
    _rejects(
        tmp_path,
        lambda d: d["tools"][0].__setitem__("data_class", "secret"),
        "data_class",
    )


def test_tool_external_not_bool(tmp_path: Path) -> None:
    _rejects(
        tmp_path, lambda d: d["tools"][0].__setitem__("external", "yes"), "external"
    )


def test_tool_reversible_not_bool(tmp_path: Path) -> None:
    _rejects(
        tmp_path, lambda d: d["tools"][0].__setitem__("reversible", 1), "reversible"
    )


def _tool(doc: dict[str, Any], name: str) -> dict[str, Any]:
    for tool in doc["tools"]:
        if tool["name"] == name:
            return dict(tool)
    raise AssertionError(name)


def test_irreversible_tool_declared_reversible(tmp_path: Path) -> None:
    def mutate(d: dict[str, Any]) -> None:
        for tool in d["tools"]:
            if tool["name"] == "send_notice":
                tool["reversible"] = True

    _rejects(tmp_path, mutate, "reversible")


def test_write_tool_declared_irreversible(tmp_path: Path) -> None:
    def mutate(d: dict[str, Any]) -> None:
        for tool in d["tools"]:
            if tool["name"] == "status_update":
                tool["reversible"] = False

    _rejects(tmp_path, mutate, "reversible")


def test_tool_unknown_key(tmp_path: Path) -> None:
    _rejects(tmp_path, lambda d: d["tools"][0].__setitem__("colour", "red"), "colour")


def test_out_of_scope_must_be_strings(tmp_path: Path) -> None:
    _rejects(tmp_path, lambda d: d.__setitem__("out_of_scope", [1]), "out_of_scope")


def test_business_metric_without_definition(tmp_path: Path) -> None:
    _rejects(
        tmp_path,
        lambda d: d["business_metrics"][0].pop("definition"),
        "definition",
    )


def test_business_metric_not_a_mapping(tmp_path: Path) -> None:
    _rejects(
        tmp_path, lambda d: d.__setitem__("business_metrics", ["x"]), "business_metrics"
    )


def test_repetitions_zero(tmp_path: Path) -> None:
    _rejects(tmp_path, lambda d: d.__setitem__("repetitions", 0), "repetitions")


def test_repetitions_not_int(tmp_path: Path) -> None:
    _rejects(tmp_path, lambda d: d.__setitem__("repetitions", "five"), "repetitions")


def test_seed_not_int(tmp_path: Path) -> None:
    _rejects(tmp_path, lambda d: d.__setitem__("seed", 1.5), "seed")


def test_threshold_unknown_category(tmp_path: Path) -> None:
    _rejects(
        tmp_path,
        lambda d: d.__setitem__("thresholds", {"speed": {"min_pass_k": 1.0}}),
        "speed",
    )


def test_threshold_out_of_range(tmp_path: Path) -> None:
    _rejects(
        tmp_path,
        lambda d: d.__setitem__("thresholds", {"robustness": {"min_pass_k": 1.5}}),
        "min_pass_k",
    )


def test_threshold_unknown_gate(tmp_path: Path) -> None:
    _rejects(
        tmp_path,
        lambda d: d.__setitem__("thresholds", {"robustness": {"gate": "soft"}}),
        "gate",
    )


def test_threshold_empty_entry(tmp_path: Path) -> None:
    _rejects(
        tmp_path,
        lambda d: d.__setitem__("thresholds", {"robustness": {}}),
        "min_pass_k",
    )


def test_threshold_unknown_key(tmp_path: Path) -> None:
    _rejects(
        tmp_path,
        lambda d: d.__setitem__("thresholds", {"robustness": {"max_fail": 0}}),
        "max_fail",
    )


def test_top_level_unknown_key(tmp_path: Path) -> None:
    _rejects(tmp_path, lambda d: d.__setitem__("owner", "me"), "owner")


def test_defaults_when_optional_blocks_absent(tmp_path: Path) -> None:
    def mutate(d: dict[str, Any]) -> None:
        for key in (
            "out_of_scope",
            "business_metrics",
            "thresholds",
            "repetitions",
            "seed",
        ):
            d.pop(key, None)

    decl = load_system_declaration(_edited(tmp_path, mutate))
    assert decl.out_of_scope == ()
    assert decl.business_metrics == ()
    assert decl.thresholds == ()
    assert decl.repetitions == 1
    assert decl.seed == 0


def test_thresholds_parsed_with_their_source_left_to_the_plan(tmp_path: Path) -> None:
    doc = _edited(
        tmp_path,
        lambda d: d.__setitem__(
            "thresholds",
            {"robustness": {"min_pass_k": 0.9}, "policy_compliance": {"gate": "hard"}},
        ),
    )
    decl = load_system_declaration(doc)
    by_category = {t.category: t for t in decl.thresholds}
    assert by_category["robustness"].min_pass_k == 0.9
    assert by_category["robustness"].gate is None
    assert by_category["policy_compliance"].min_pass_k is None
    assert by_category["policy_compliance"].gate == "hard"


# --------------------------------------------------------------------------
# The run config: `system:`, `repetitions`, `seed`
# --------------------------------------------------------------------------


def _run_doc() -> dict[str, Any]:
    doc = fast_yaml.safe_load(RUN_CONFIG.read_text(encoding="utf-8"))
    assert isinstance(doc, dict)
    return doc


def _run_config(
    tmp_path: Path, mutate: Any, system: dict[str, Any] | None = None
) -> Path:
    """The shipped run config, edited, next to a copy of the shipped system
    declaration (so a relative ``system:`` resolves the way it ships)."""
    _write(tmp_path, system if system is not None else _shipped(), "seed_system.yaml")
    doc = _run_doc()
    mutate(doc)
    return _write(tmp_path, doc, "run.yaml")


def test_shipped_run_config_names_the_declaration() -> None:
    loaded = load_run_config(RUN_CONFIG)
    assert loaded.system is not None
    assert loaded.system_path == SHIPPED.resolve()
    assert loaded.run_config.repetitions == loaded.system.repetitions
    assert loaded.run_config.seed == loaded.system.seed


def test_system_path_resolves_relative_to_the_config_file(tmp_path: Path) -> None:
    sub = tmp_path / "nested"
    sub.mkdir()
    path = _run_config(sub, lambda d: d.__setitem__("system", "seed_system.yaml"))
    loaded = load_run_config(path)
    assert loaded.system_path == (sub / "seed_system.yaml").resolve()


def test_agentic_baseline_without_a_declaration_refuses(tmp_path: Path) -> None:
    """Owner's condition (R27): the seed pack never runs without a plan."""
    path = _run_config(tmp_path, lambda d: d.pop("system"))
    with pytest.raises(ConfigurationError, match="system"):
        load_run_config(path)


def test_agentic_baseline_without_a_declaration_does_not_start(tmp_path: Path) -> None:
    path = _run_config(tmp_path, lambda d: d.pop("system"))
    out = tmp_path / "bundle"
    result = CliRunner().invoke(
        app, ["run", "--config", str(path), "--output", str(out)]
    )
    assert result.exit_code == 1
    assert "system" in result.output
    assert not out.exists()


def test_declaration_pack_must_match_the_run_config(tmp_path: Path) -> None:
    system = _shipped()
    system["pack_id"] = "other"
    path = _run_config(tmp_path, lambda d: None, system)
    with pytest.raises(ConfigurationError, match="pack"):
        load_run_config(path)


def test_declaration_only_with_the_agentic_baseline(tmp_path: Path) -> None:
    _write(tmp_path, _shipped(), "seed_system.yaml")
    doc = fast_yaml.safe_load(
        Path("configs/toy_smoke.yaml").read_text(encoding="utf-8")
    )
    doc["system"] = "seed_system.yaml"
    path = _write(tmp_path, doc, "toy.yaml")
    with pytest.raises(ConfigurationError, match="system"):
        load_run_config(path)


def test_declaration_errors_name_the_file(tmp_path: Path) -> None:
    system = _shipped()
    system["involvement"] = "never"
    path = _run_config(tmp_path, lambda d: None, system)
    with pytest.raises(ConfigurationError, match="seed_system.yaml"):
        load_run_config(path)


def test_system_must_be_a_path(tmp_path: Path) -> None:
    path = _run_config(tmp_path, lambda d: d.__setitem__("system", {"pack_id": "seed"}))
    with pytest.raises(ConfigurationError, match="system"):
        load_run_config(path)


@pytest.mark.parametrize("key", ["repetitions", "seed"])
def test_repetitions_and_seed_live_in_one_place(tmp_path: Path, key: str) -> None:
    path = _run_config(tmp_path, lambda d: d.__setitem__(key, 3))
    with pytest.raises(ConfigurationError, match=key):
        load_run_config(path)


def test_repetitions_from_the_declaration_reach_the_run_config(tmp_path: Path) -> None:
    system = _shipped()
    system["repetitions"] = 4
    system["seed"] = 99
    path = _run_config(tmp_path, lambda d: None, system)
    loaded = load_run_config(path)
    assert loaded.run_config.repetitions == 4
    assert loaded.run_config.seed == 99


def _toy(tmp_path: Path, extra: str) -> Path:
    text = Path("configs/toy_smoke.yaml").read_text(encoding="utf-8")
    path = tmp_path / "toy.yaml"
    path.write_text(text + "\n" + extra + "\n", encoding="utf-8")
    return path


def test_toy_run_may_set_repetitions_and_seed_at_top_level(tmp_path: Path) -> None:
    loaded = load_run_config(_toy(tmp_path, "repetitions: 2\nseed: 7"))
    assert loaded.system is None
    assert loaded.run_config.repetitions == 2
    assert loaded.run_config.seed == 7


def test_toy_run_defaults(tmp_path: Path) -> None:
    loaded = load_run_config(_toy(tmp_path, ""))
    assert loaded.run_config.repetitions == 1
    assert loaded.run_config.seed == 0


def test_top_level_repetitions_zero_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="repetitions"):
        load_run_config(_toy(tmp_path, "repetitions: 0"))


def test_top_level_seed_must_be_int(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="seed"):
        load_run_config(_toy(tmp_path, "seed: abc"))


def test_repetitions_from_yaml_reach_the_orchestrator(tmp_path: Path) -> None:
    """docs/adr/0006 D18: k task runs per task, each with its repetition."""
    import json

    path = _toy(tmp_path, "repetitions: 3")
    out = tmp_path / "bundle"
    result = CliRunner().invoke(
        app, ["run", "--config", str(path), "--output", str(out)]
    )
    assert result.exit_code == 0, result.output
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    results = summary["task_results"]
    task_ids = {r["task_id"] for r in results}
    assert len(results) == 3 * len(task_ids)
    assert {r["repetition"] for r in results} == {0, 1, 2}
