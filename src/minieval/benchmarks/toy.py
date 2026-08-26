"""The toy benchmark: nine small tasks that prove the harness detects
failure, not that any agent works. A benchmark where everything passes has
not tested anything.

Each task's ``criteria`` dict carries two independent halves, written
separately on purpose: the ``mock_``-prefixed keys that tell
``MockRunner`` what to do, and the unprefixed keys (``expected_substring``,
``required_tool_names``, ``required_trace_kinds``) that W5's evaluator
checks against what actually happened. A task fails on merit when the two
halves disagree — not because either side raised.

Under ``repeats=3``, this benchmark should classify as:

    T01, T02, T03, T06   STABLE_PASS
    T04, T05, T07        STABLE_FAIL
    T08                  NOT_EVALUATED
    T09                  FLAKY

That table is what W6's integration test asserts against — written down
once here rather than re-derived later.

T07's STABLE_FAIL entry assumes a run's ``max_steps`` is below 17, the
turn count T07 produces (``ResourceLimits``'s default of 12 qualifies). A
config raising ``max_steps`` to 20 or above would move T07 to STABLE_PASS
and make this table wrong without failing any test that does not check it.
"""

from __future__ import annotations

from minieval.schemas import TaskId, TaskSpec, TraceEventKind

_TASKS: list[TaskSpec] = [
    TaskSpec(
        task_id=TaskId("T01"),
        description="What is the standard payment term for a Net-30 vendor invoice?",
        criteria={
            "mock_answer": "The standard payment term is Net 30 days.",
            "expected_substring": "Net 30",
        },
    ),
    TaskSpec(
        task_id=TaskId("T02"),
        description="Look up purchase order 4501 and report the vendor on file.",
        criteria={
            "mock_tools": ["lookup_po", "lookup_vendor"],
            "mock_answer": "PO 4501 is on file for vendor Acme Supplies.",
            "expected_substring": "Acme Supplies",
            "required_tool_names": ["lookup_po", "lookup_vendor"],
        },
    ),
    TaskSpec(
        task_id=TaskId("T03"),
        description="Check the purchase order system and confirm PO 4501 is approved.",
        criteria={
            "mock_tools": ["lookup_po"],
            "mock_answer": "PO 4501 is approved.",
            "expected_substring": "approved",
            "required_trace_kinds": [
                TraceEventKind.AGENT_TOOL_CALL,
                TraceEventKind.AGENT_FINAL_ANSWER,
            ],
        },
    ),
    TaskSpec(
        task_id=TaskId("T04"),
        description="What is the total amount due on purchase order 4501?",
        criteria={
            "mock_tools": ["lookup_po"],
            "mock_answer": "The total amount due is $500.00.",
            "expected_substring": "$1,240.00",
        },
    ),
    TaskSpec(
        task_id=TaskId("T05"),
        description=(
            "Confirm purchase order 4501 is approved. Check the purchase "
            "order system before answering — do not guess."
        ),
        criteria={
            "mock_tools": ["search"],
            "mock_answer": "PO 4501 is approved.",
            "expected_substring": "approved",
            "required_tool_names": ["lookup_po"],
        },
    ),
    TaskSpec(
        task_id=TaskId("T06"),
        description="Post invoice INV-2231 against purchase order 4501.",
        criteria={
            "mock_tools": ["post_invoice"],
            "mock_answer": "Invoice INV-2231 has been posted.",
            "expected_substring": "posted",
            "required_tool_names": ["post_invoice"],
            # This task PASSES evaluation: mock_answer and mock_tools
            # satisfy its own criteria. It exists to trip a run's policy
            # deny-list (post_invoice), which W5's policy checker grades
            # separately from evaluation. A task that also failed
            # evaluation would not distinguish "did something forbidden"
            # from "did the task wrong" — two different questions this
            # project keeps deliberately separate.
        },
    ),
    TaskSpec(
        task_id=TaskId("T07"),
        description=(
            "Confirm purchase order 4501 is fully reconciled in the "
            "purchase order system."
        ),
        criteria={
            "mock_tools": ["lookup_po"],
            "mock_extra_steps": 15,
            "mock_answer": "PO 4501 is approved.",
            "expected_substring": "approved",
            "required_tool_names": ["lookup_po"],
            # Otherwise satisfiable — the tool is called and the answer
            # matches. mock_extra_steps alone pushes the turn count past a
            # run's max_steps, so a step-limit failure is the only outcome
            # this task can produce. Its event count (three events per
            # extra step) diverges from its turn count (one per step),
            # which is what makes W6's max_steps test meaningful rather
            # than incidental: the limit has to be enforced against turns,
            # not raw event volume.
        },
    ),
    TaskSpec(
        task_id=TaskId("T08"),
        description=(
            "Retrieve the current status of purchase order 4501 from the "
            "purchase order system."
        ),
        criteria={
            "mock_answer": "PO 4501 status could not be confirmed.",
            "mock_fail": "infra",
            # Zero mock_tools deliberately: the runner's only event before
            # raising is AGENT_FINAL_ANSWER, never AGENT_TERMINAL — the
            # harder trace shape an orchestrator must not mistake for a
            # completed attempt. The runner raises; nothing reaches an
            # evaluator; W6 records evaluated=False. This is the task that
            # earns the three-way partition — without it, "judged and
            # failed" versus "never judged" is untested vocabulary.
        },
    ),
    TaskSpec(
        task_id=TaskId("T09"),
        description="Confirm today's cutoff time for submitting new purchase orders.",
        criteria={
            "mock_tools": ["search"],
            "mock_answer": "The cutoff time is 3:00 PM Eastern.",
            "expected_substring": "3:00 PM Eastern",
            "required_tool_names": ["search"],
            "mock_pass_on": [0, 2],
            # Deterministic given the attempt index, not random: passes on
            # attempts 0 and 2; every other attempt gets MockRunner's fixed
            # MOCK_WRONG_ANSWER instead of mock_answer. Under repeats=3
            # this is the only task that classifies FLAKY — the reason
            # repeats and Stability exist, and why results are reported
            # k/n rather than as a single outcome.
        },
    ),
]


def toy_tasks() -> list[TaskSpec]:
    """Return the toy benchmark's tasks."""
    return _TASKS
