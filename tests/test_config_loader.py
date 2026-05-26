"""Tests for config/loader.py — load_run_config and LoadedRunConfig."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentevalops.config.loader import LoadedRunConfig, load_run_config
from agentevalops.core.errors import ConfigurationError
from agentevalops.core.types import RunId

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SMOKE = Path("configs/toy_smoke.yaml")


def _write(tmp_path: Path, content: str, name: str = "cfg.yaml") -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Happy-path: toy_smoke.yaml
# ---------------------------------------------------------------------------


def test_load_smoke_returns_loaded_run_config() -> None:
    result = load_run_config(_SMOKE)
    assert isinstance(result, LoadedRunConfig)


def test_load_smoke_run_config_run_id() -> None:
    result = load_run_config(_SMOKE)
    assert result.run_config.run_id == RunId("toy-smoke-001")


def test_load_smoke_run_config_agent_id() -> None:
    result = load_run_config(_SMOKE)
    assert result.run_config.agent_id == "mock-agent-v1"


def test_load_smoke_benchmark_id() -> None:
    result = load_run_config(_SMOKE)
    assert result.benchmark_id == "toy"


def test_load_smoke_policy_spec_has_id() -> None:
    result = load_run_config(_SMOKE)
    assert result.policy_spec.policy_id == "default"


def test_load_smoke_policy_max_cost_set() -> None:
    result = load_run_config(_SMOKE)
    assert result.policy_spec.max_cost_usd == 1.0


def test_load_smoke_mock_fail_defaults_false() -> None:
    result = load_run_config(_SMOKE)
    assert result.mock_fail is False


def test_load_smoke_mock_cost_defaults_zero() -> None:
    result = load_run_config(_SMOKE)
    assert result.mock_cost_per_task_usd == 0.0


def test_load_smoke_path_stored(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "run_id: r1\nbenchmark_id: toy\n",
    )
    result = load_run_config(p)
    assert result.path == p


def test_load_smoke_raw_is_dict() -> None:
    result = load_run_config(_SMOKE)
    assert isinstance(result.raw, dict)


# ---------------------------------------------------------------------------
# mock_fail and mock_cost_per_task_usd
# ---------------------------------------------------------------------------


def test_mock_fail_true_loaded(tmp_path: Path) -> None:
    p = _write(tmp_path, "run_id: r1\nbenchmark_id: toy\nmock_fail: true\n")
    result = load_run_config(p)
    assert result.mock_fail is True


def test_mock_cost_per_task_loaded(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "run_id: r1\nbenchmark_id: toy\nmock_cost_per_task_usd: 0.5\n",
    )
    result = load_run_config(p)
    assert result.mock_cost_per_task_usd == pytest.approx(0.5)


def test_negative_mock_cost_raises(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "run_id: r1\nbenchmark_id: toy\nmock_cost_per_task_usd: -1.0\n",
    )
    with pytest.raises(ConfigurationError, match="mock_cost_per_task_usd"):
        load_run_config(p)


# ---------------------------------------------------------------------------
# policy fields
# ---------------------------------------------------------------------------


def test_policy_max_trace_events_loaded(tmp_path: Path) -> None:
    yaml_text = (
        "run_id: r1\nbenchmark_id: toy\n"
        "policy:\n  policy_id: p1\n  max_trace_events: 10\n"
    )
    p = _write(tmp_path, yaml_text)
    result = load_run_config(p)
    assert result.policy_spec.max_trace_events == 10


def test_policy_deny_tool_ids_loaded(tmp_path: Path) -> None:
    yaml_text = (
        "run_id: r1\nbenchmark_id: toy\n"
        "policy:\n  policy_id: p1\n"
        "  deny_tool_ids:\n    - bash\n    - curl\n"
    )
    p = _write(tmp_path, yaml_text)
    result = load_run_config(p)
    assert result.policy_spec.deny_tool_ids == ("bash", "curl")


# ---------------------------------------------------------------------------
# File-system error cases
# ---------------------------------------------------------------------------


def test_missing_file_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not found"):
        load_run_config(tmp_path / "no_such.yaml")


def test_directory_path_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not a file"):
        load_run_config(tmp_path)


# ---------------------------------------------------------------------------
# YAML errors
# ---------------------------------------------------------------------------


def test_malformed_yaml_raises_config_error(tmp_path: Path) -> None:
    p = _write(tmp_path, "key: [unclosed")
    with pytest.raises(ConfigurationError, match="Malformed YAML"):
        load_run_config(p)


def test_yaml_list_raises_config_error(tmp_path: Path) -> None:
    p = _write(tmp_path, "- item1\n- item2\n")
    with pytest.raises(ConfigurationError, match="mapping"):
        load_run_config(p)


def test_yaml_string_raises_config_error(tmp_path: Path) -> None:
    p = _write(tmp_path, "just a plain string\n")
    with pytest.raises(ConfigurationError, match="mapping"):
        load_run_config(p)


def test_yaml_null_raises_config_error(tmp_path: Path) -> None:
    p = _write(tmp_path, "~\n")
    with pytest.raises(ConfigurationError, match="mapping"):
        load_run_config(p)


# ---------------------------------------------------------------------------
# Validation errors — resource_limits
# ---------------------------------------------------------------------------


def test_negative_resource_max_cost_raises(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "run_id: r1\nbenchmark_id: toy\nresource_limits:\n  max_cost_usd: -5.0\n",
    )
    with pytest.raises(ConfigurationError, match="max_cost_usd"):
        load_run_config(p)


def test_zero_max_tokens_raises(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "run_id: r1\nbenchmark_id: toy\nresource_limits:\n  max_tokens: 0\n",
    )
    with pytest.raises(ConfigurationError, match="max_tokens"):
        load_run_config(p)


def test_negative_max_wall_seconds_raises(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "run_id: r1\nbenchmark_id: toy\nresource_limits:\n  max_wall_seconds: -1.0\n",
    )
    with pytest.raises(ConfigurationError, match="max_wall_seconds"):
        load_run_config(p)


# ---------------------------------------------------------------------------
# Validation errors — policy
# ---------------------------------------------------------------------------


def test_negative_policy_max_cost_raises(tmp_path: Path) -> None:
    yaml_text = (
        "run_id: r1\nbenchmark_id: toy\n"
        "policy:\n  policy_id: p1\n  max_cost_usd: -0.1\n"
    )
    p = _write(tmp_path, yaml_text)
    with pytest.raises(ConfigurationError, match="policy.max_cost_usd"):
        load_run_config(p)


def test_zero_max_trace_events_raises(tmp_path: Path) -> None:
    yaml_text = (
        "run_id: r1\nbenchmark_id: toy\n"
        "policy:\n  policy_id: p1\n  max_trace_events: 0\n"
    )
    p = _write(tmp_path, yaml_text)
    with pytest.raises(ConfigurationError, match="max_trace_events"):
        load_run_config(p)


def test_negative_max_trace_events_raises(tmp_path: Path) -> None:
    yaml_text = (
        "run_id: r1\nbenchmark_id: toy\n"
        "policy:\n  policy_id: p1\n  max_trace_events: -3\n"
    )
    p = _write(tmp_path, yaml_text)
    with pytest.raises(ConfigurationError, match="max_trace_events"):
        load_run_config(p)


# ---------------------------------------------------------------------------
# Validation errors — backend and benchmark
# ---------------------------------------------------------------------------


def test_unsupported_backend_raises(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "run_id: r1\nbenchmark_id: toy\nbackend_id: gcp\n",
    )
    with pytest.raises(ConfigurationError, match="backend_id"):
        load_run_config(p)


def test_unsupported_benchmark_raises(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "run_id: r1\nbenchmark_id: swebench\n",
    )
    with pytest.raises(ConfigurationError, match="benchmark_id"):
        load_run_config(p)


def test_aws_backend_accepted(tmp_path: Path) -> None:
    """'aws' is a declared future backend — loader must accept it."""
    p = _write(
        tmp_path,
        "run_id: r1\nbenchmark_id: toy\nbackend_id: aws\n",
    )
    result = load_run_config(p)
    assert result.run_config.backend_id == "aws"
