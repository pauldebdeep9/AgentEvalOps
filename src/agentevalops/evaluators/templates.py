"""Template matching shared by the ``tool_use`` and ``state_based``
evaluators: an expected value is a template over an observed one.

Every key a template mapping names must be present and match; keys it does
not name are unconstrained. A template list must match in length and item
by item, in order. Anything else must be equal. The result is the list of
paths that disagree, so a verdict can say where.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def mismatches(expected: Any, actual: Any, path: str = "") -> list[str]:
    """Every path at which *actual* departs from the template *expected*.
    Empty means a match."""
    where = path or "<root>"
    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            return [f"{where}: expected a mapping, got {_show(actual)}"]
        found: list[str] = []
        for key, value in expected.items():
            child = f"{path}.{key}" if path else str(key)
            if key not in actual:
                found.append(f"{child}: missing")
            else:
                found.extend(mismatches(value, actual[key], child))
        return found
    if isinstance(expected, Sequence) and not isinstance(expected, str):
        if not isinstance(actual, Sequence) or isinstance(actual, str):
            return [f"{where}: expected a list, got {_show(actual)}"]
        if len(actual) != len(expected):
            return [f"{where}: expected {len(expected)} item(s), got {len(actual)}"]
        found = []
        for index, (want, got) in enumerate(zip(expected, actual)):
            found.extend(mismatches(want, got, f"{path}[{index}]"))
        return found
    if (type(expected) is bool) != (type(actual) is bool) or expected != actual:
        return [f"{where}: expected {_show(expected)}, got {_show(actual)}"]
    return []


def matches(expected: Any, actual: Any) -> bool:
    return not mismatches(expected, actual)


def _show(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."
