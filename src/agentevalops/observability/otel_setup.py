"""OpenTelemetry setup — span names, attribute keys, and the tracer seam (W9).

WARMUP SCAFFOLD (ROADMAP W9)
---------------------------
This module establishes the *seam* for OpenTelemetry instrumentation and a
no-op fallback so call sites can be wired incrementally. The heavy lifting is
intentionally deferred to a dedicated W9 session:

* ``TODO(W9)``: implement :func:`configure_tracer` — a real ``TracerProvider``
  with a ``BatchSpanProcessor`` + a JSONL ``SpanExporter`` that writes spans to
  ``<bundle_dir>/spans.jsonl`` so they land in the result bundle (ARCHITECTURE
  §8; the ``traces.jsonl`` application trace is a separate, always-present file).
* ``TODO(W9)``: emit spans across the orchestrator / runner / evaluators per the
  span hierarchy below, populating the ``ATTR_*`` attribute keys.
* ``TODO(W21)``: AWS path — export to CloudWatch via ADOT.

Span hierarchy (from ARCHITECTURE §8)::

    eval.run
    └── eval.task
        ├── benchmark.prepare
        ├── agent.execute
        │   ├── agent.plan
        │   ├── agent.tool_call
        │   └── agent.final_answer
        ├── benchmark.grade
        ├── evaluator.<kind>
        ├── policy.check
        └── benchmark.teardown
    ├── scorer.summarize
    └── report.render

Import safety
-------------
``opentelemetry`` is an optional ``otel`` extra imported lazily; this module
MUST import cleanly with only the core deps present. When the extra is absent
(as in CI), :func:`get_tracer` returns a no-op tracer so instrumented call sites
stay safe and free.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

# ---------------------------------------------------------------------------
# Span names — keep in lockstep with ARCHITECTURE §8.
# ---------------------------------------------------------------------------

SPAN_EVAL_RUN = "eval.run"
SPAN_EVAL_TASK = "eval.task"
SPAN_BENCHMARK_PREPARE = "benchmark.prepare"
SPAN_AGENT_EXECUTE = "agent.execute"
SPAN_AGENT_PLAN = "agent.plan"
SPAN_AGENT_TOOL_CALL = "agent.tool_call"
SPAN_AGENT_FINAL_ANSWER = "agent.final_answer"
SPAN_BENCHMARK_GRADE = "benchmark.grade"
#: Evaluator spans are suffixed with the evaluator kind, e.g.
#: ``f"{SPAN_EVALUATOR_PREFIX}.deterministic"``.
SPAN_EVALUATOR_PREFIX = "evaluator"
SPAN_POLICY_CHECK = "policy.check"
SPAN_BENCHMARK_TEARDOWN = "benchmark.teardown"
SPAN_SCORER_SUMMARIZE = "scorer.summarize"
SPAN_REPORT_RENDER = "report.render"

# ---------------------------------------------------------------------------
# Span attribute keys — carried on spans (and later CloudWatch metrics).
# ---------------------------------------------------------------------------

ATTR_RUN_ID = "run_id"
ATTR_TASK_ID = "task_id"
ATTR_AGENT_ID = "agent_id"
ATTR_MODEL_ID = "model_id"
ATTR_COST_USD = "cost_usd"
ATTR_TOKENS_IN = "tokens_in"
ATTR_TOKENS_OUT = "tokens_out"
ATTR_LATENCY_MS = "latency_ms"


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


def otel_available() -> bool:
    """Return ``True`` if the optional ``otel`` extra (opentelemetry) is present."""
    from importlib.util import find_spec

    return find_spec("opentelemetry") is not None


# ---------------------------------------------------------------------------
# No-op tracer — safe stand-in when otel is not installed / not configured.
# Mirrors the subset of the OTel Tracer/Span API the platform will use, so
# instrumented call sites are byte-identical whether or not otel is present.
# ---------------------------------------------------------------------------


class NoOpSpan:
    """A span that records nothing."""

    def set_attribute(self, key: str, value: Any) -> None:
        return None

    def set_attributes(self, attributes: dict[str, Any]) -> None:
        return None

    def end(self) -> None:
        return None


class NoOpTracer:
    """A tracer that yields :class:`NoOpSpan` instances."""

    @contextmanager
    def start_as_current_span(
        self, name: str, **kwargs: Any
    ) -> Iterator[NoOpSpan]:
        yield NoOpSpan()


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def get_tracer(name: str = "agentevalops") -> Any:
    """Return a tracer for instrumentation.

    WARMUP: currently always returns a :class:`NoOpTracer`. ``TODO(W9)``: once
    :func:`configure_tracer` is implemented and otel is available, return the
    real tracer via ``opentelemetry.trace.get_tracer(name)``.
    """
    # TODO(W9): branch to the real tracer when a provider has been configured.
    return NoOpTracer()


def configure_tracer(*, bundle_dir: Any | None = None) -> None:
    """Configure the global ``TracerProvider`` and JSONL span exporter.

    WARMUP STUB — not yet implemented. ``TODO(W9)``: install a
    ``TracerProvider`` with a ``BatchSpanProcessor`` + a JSONL ``SpanExporter``
    writing to ``<bundle_dir>/spans.jsonl`` so spans land in the result bundle.
    """
    raise NotImplementedError(
        "configure_tracer is a W9 warmup stub; span export is not yet wired. "
        "See otel_setup.py TODO(W9) notes and ARCHITECTURE §8."
    )
