"""Toy benchmark task definitions.

All tasks are hard-coded, fully deterministic, and require no external I/O.
They exist to exercise the orchestration loop and demonstrate how the
BenchmarkAdapter, MockAgentRunner, and DeterministicEvaluator interact.

Task metadata keys used by MockAgentRunner and DeterministicEvaluator:

``expected_output``
    String the agent is expected to return.  When ``match_mode`` is
    ``"exact"`` the evaluator requires a strict equality match; the default
    ``"substring"`` mode requires the value to appear anywhere in the answer.

``mock_answer``
    Overrides ``expected_output`` as the answer the MockAgentRunner actually
    returns.  Use this to create tasks that always fail evaluation (the
    runner returns a wrong answer the evaluator cannot accept).

``match_mode``
    ``"exact"`` or ``"substring"`` (default).  Controls how
    DeterministicEvaluator compares ``expected_output`` against the agent's
    final answer.

``expected_substring``
    If set, the evaluator also checks that the agent's answer contains this
    substring (independent of ``expected_output``).

``required_trace_kinds``
    List of :class:`~agentevalops.core.schemas.TraceEventKind` value strings
    that must appear in the task's trace.  Fails if any kind is absent.

``required_tool_names``
    List of tool-name strings.  Each must appear in an ``AGENT_TOOL_CALL``
    event's ``payload["tool_name"]`` field.  Fails if any name is absent.

``emit_fake_tool_call``
    When truthy, MockAgentRunner emits an ``AGENT_TOOL_CALL`` event before
    the final answer.  The tool name is taken from ``fake_tool_name``
    (defaults to ``"mock-tool"``).

``fake_tool_name``
    Tool name used in the fake ``AGENT_TOOL_CALL`` event emitted when
    ``emit_fake_tool_call`` is truthy.

``difficulty``
    Informational only.  Not used for evaluation.

``scenario``
    Name of the scenario this task belongs to.  Informational.
"""

from __future__ import annotations

from agentevalops.core.schemas import TaskSpec
from agentevalops.core.types import TaskId

# ---------------------------------------------------------------------------
# Smoke scenario tasks — all pass with the default happy MockAgentRunner
# ---------------------------------------------------------------------------

SMOKE_TASKS: list[TaskSpec] = [
    TaskSpec(
        task_id=TaskId("toy-001"),
        benchmark_id="toy",
        description="Return the string 'hello world'.",
        metadata={
            "expected_output": "hello world",
            "match_mode": "exact",
            "difficulty": "trivial",
            "scenario": "smoke",
        },
    ),
    TaskSpec(
        task_id=TaskId("toy-002"),
        benchmark_id="toy",
        description="Return the integer 42 as a string.",
        metadata={
            "expected_output": "42",
            "match_mode": "exact",
            "difficulty": "trivial",
            "scenario": "smoke",
        },
    ),
]

# ---------------------------------------------------------------------------
# Failure scenario tasks — always fail evaluation even in happy runner mode
#
# MockAgentRunner returns ``mock_answer`` which does not match the evaluation
# criterion, so DeterministicEvaluator marks every task as failed without
# the agent itself crashing or emitting AGENT_ERROR.
# ---------------------------------------------------------------------------

FAILURE_TASKS: list[TaskSpec] = [
    TaskSpec(
        task_id=TaskId("toy-fail-001"),
        benchmark_id="toy",
        description="Return the string 'correct answer'.",
        metadata={
            "expected_output": "correct answer",
            "mock_answer": "wrong answer",
            "match_mode": "exact",
            "difficulty": "trivial",
            "scenario": "failure",
        },
    ),
    TaskSpec(
        task_id=TaskId("toy-fail-002"),
        benchmark_id="toy",
        description="Return a sentence that contains the word 'magic_word'.",
        metadata={
            "expected_substring": "magic_word",
            "mock_answer": "no magic here",
            "difficulty": "trivial",
            "scenario": "failure",
        },
    ),
]

# ---------------------------------------------------------------------------
# Mixed scenario tasks — 2 pass + 1 fail; useful for scorer tests
# ---------------------------------------------------------------------------

MIXED_TASKS: list[TaskSpec] = [
    # Task 1: exact-match, passes (same as smoke toy-001)
    TaskSpec(
        task_id=TaskId("toy-001"),
        benchmark_id="toy",
        description="Return the string 'hello world'.",
        metadata={
            "expected_output": "hello world",
            "match_mode": "exact",
            "difficulty": "trivial",
            "scenario": "mixed",
        },
    ),
    # Task 2: wrong mock_answer → always fails evaluation
    TaskSpec(
        task_id=TaskId("toy-fail-001"),
        benchmark_id="toy",
        description="Return the string 'correct answer'.",
        metadata={
            "expected_output": "correct answer",
            "mock_answer": "wrong answer",
            "match_mode": "exact",
            "difficulty": "trivial",
            "scenario": "mixed",
        },
    ),
    # Task 3: substring match, passes
    TaskSpec(
        task_id=TaskId("toy-substr-001"),
        benchmark_id="toy",
        description="Return a sentence that contains the word 'planet'.",
        metadata={
            "expected_substring": "planet",
            "mock_answer": "Earth is the third planet from the sun.",
            "difficulty": "trivial",
            "scenario": "mixed",
        },
    ),
]
