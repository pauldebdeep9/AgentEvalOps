"""The toy benchmark's tool environment: one tool, ``calculator``.

``finish`` is not here: it is the runners' own control (ADR-0006 D9). The
spec text is today's runner ``_TOOLS`` entry, word for word, so the payload
a runner sends to its provider does not change (R23's golden test).
"""

from __future__ import annotations

from typing import Any

from agentevalops.core.schemas import ToolSpec
from agentevalops.core.types import TaskId
from agentevalops.environments.inprocess import InProcessToolEnvironment

# Characters permitted in a calculator expression.  Anything else is rejected
# rather than passed to ``eval`` — the tool never executes arbitrary code.
_SAFE_EXPR_CHARS = set("0123456789+-*/(). ")

CALCULATOR_TOOL_SPEC = ToolSpec(
    name="calculator",
    description=(
        "Evaluate a simple arithmetic expression using +, -, *, /, "
        "parentheses and numbers. Returns the numeric result as a string."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "e.g. '2 + 2' or '(3 * 4) - 1'",
            }
        },
        "required": ["expression"],
    },
    permission="read",
)


def run_calculator(expression: str) -> str:
    """Evaluate a trivial arithmetic *expression*, safely.

    Only digits, whitespace and the operators ``+ - * / ( ) .`` are allowed,
    and ``**`` is refused. Any other character (or an evaluation error)
    yields an error string that is handed back to the model as the tool
    result — this never raises.
    """
    expr = str(expression)
    if not expr or any(ch not in _SAFE_EXPR_CHARS for ch in expr):
        return "error: expression contains unsupported characters"
    if "**" in expr:
        # Exponentiation is the one allowed operator that can run unbounded
        # ("9**9**9" never returns). ``*`` then ``*`` only tokenises as power
        # when adjacent, so a substring check is exact.
        return "error: exponentiation (**) is not supported"
    try:
        # ``eval`` is bounded to arithmetic by the character allow-list above:
        # no names, calls, attribute access, or subscripts can appear.
        result = eval(expr, {"__builtins__": {}}, {})  # noqa: S307
    except Exception as exc:  # noqa: BLE001 - report any arithmetic failure
        return f"error: {exc}"
    return str(result)


async def _calculator(arguments: dict[str, Any]) -> str:
    return run_calculator(arguments.get("expression", ""))


def toy_environment(
    task_id: TaskId = TaskId("direct"), *, repetition: int = 0
) -> InProcessToolEnvironment:
    """A fresh calculator environment for one task run."""
    return InProcessToolEnvironment(
        task_id,
        (CALCULATOR_TOOL_SPEC,),
        {"calculator": _calculator},
        repetition=repetition,
    )
