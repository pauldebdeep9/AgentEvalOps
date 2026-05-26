"""JSON serialization helpers for AgentEvalOps dataclasses.

All public surface: ``to_jsonable(obj)`` — recursively converts an object
graph to a structure that ``json.dump`` can serialize without custom encoder.
Standard library only; no Pydantic or attrs.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any


def to_jsonable(obj: Any) -> Any:
    """Recursively convert *obj* to a JSON-serializable value.

    Conversion rules (checked in order):

    - ``None``, ``bool``, ``int``, ``float``, ``str`` → returned as-is
    - ``Enum``          → ``.value``
    - ``datetime``      → ``.isoformat()``
    - ``Path``          → ``str(obj)``
    - ``list``/``tuple``→ list with elements recursively converted
    - ``dict``          → dict with str keys, values recursively converted
    - dataclass instance→ ``{field: to_jsonable(value), …}``
    - anything else     → ``str(obj)``
    """
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(item) for item in obj]
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {
            f.name: to_jsonable(getattr(obj, f.name))
            for f in dataclasses.fields(obj)
        }
    return str(obj)
