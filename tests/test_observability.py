"""Tests for the W9 OpenTelemetry warmup scaffold (observability/otel_setup.py).

These assert the *seam* only: the module is import-safe without the optional
`otel` extra, the span-name/attribute constants match ARCHITECTURE §8, and the
no-op tracer is usable at call sites. Real span emission + JSONL export land in
the dedicated W9 session.
"""

from __future__ import annotations

import pytest

from agentevalops.observability import otel_setup as otel

# ---------------------------------------------------------------------------
# Import safety + availability
# ---------------------------------------------------------------------------


def test_module_imports_without_otel_installed() -> None:
    # Importing at module top (above) must not require opentelemetry.
    assert otel is not None


def test_otel_available_returns_bool() -> None:
    assert isinstance(otel.otel_available(), bool)


# ---------------------------------------------------------------------------
# Constants match the ARCHITECTURE §8 span hierarchy / attributes
# ---------------------------------------------------------------------------


def test_span_name_constants() -> None:
    assert otel.SPAN_EVAL_RUN == "eval.run"
    assert otel.SPAN_EVAL_TASK == "eval.task"
    assert otel.SPAN_BENCHMARK_PREPARE == "benchmark.prepare"
    assert otel.SPAN_AGENT_EXECUTE == "agent.execute"
    assert otel.SPAN_AGENT_PLAN == "agent.plan"
    assert otel.SPAN_AGENT_TOOL_CALL == "agent.tool_call"
    assert otel.SPAN_AGENT_FINAL_ANSWER == "agent.final_answer"
    assert otel.SPAN_BENCHMARK_GRADE == "benchmark.grade"
    assert otel.SPAN_POLICY_CHECK == "policy.check"
    assert otel.SPAN_BENCHMARK_TEARDOWN == "benchmark.teardown"
    assert otel.SPAN_SCORER_SUMMARIZE == "scorer.summarize"
    assert otel.SPAN_REPORT_RENDER == "report.render"


def test_evaluator_span_prefix_composes() -> None:
    assert f"{otel.SPAN_EVALUATOR_PREFIX}.deterministic" == "evaluator.deterministic"


def test_attribute_key_constants() -> None:
    assert otel.ATTR_RUN_ID == "run_id"
    assert otel.ATTR_TASK_ID == "task_id"
    assert otel.ATTR_AGENT_ID == "agent_id"
    assert otel.ATTR_MODEL_ID == "model_id"
    assert otel.ATTR_COST_USD == "cost_usd"
    assert otel.ATTR_TOKENS_IN == "tokens_in"
    assert otel.ATTR_TOKENS_OUT == "tokens_out"
    assert otel.ATTR_LATENCY_MS == "latency_ms"


# ---------------------------------------------------------------------------
# No-op tracer seam
# ---------------------------------------------------------------------------


def test_get_tracer_returns_usable_noop() -> None:
    tracer = otel.get_tracer()
    with tracer.start_as_current_span(otel.SPAN_EVAL_RUN) as span:
        # These are no-ops but must be callable so instrumented sites are safe.
        span.set_attribute(otel.ATTR_RUN_ID, "r1")
        span.set_attributes({otel.ATTR_COST_USD: 0.0})
        span.end()


def test_configure_tracer_is_a_warmup_stub() -> None:
    with pytest.raises(NotImplementedError, match="W9 warmup stub"):
        otel.configure_tracer()
