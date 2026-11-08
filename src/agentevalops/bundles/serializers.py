"""JSON serialization helpers for AgentEvalOps dataclasses.

``to_jsonable(obj)`` recursively converts an object graph to a structure
that ``json.dump`` can serialize without custom encoder; ``from_jsonable(cls,
data)`` is its inverse for the dataclasses a bundle holds (docs/adr/0008 R1,
R29 (b1)), driven by each dataclass's own fields and type hints, so a field
added to a schema is loaded as soon as it is written, and a key the
dataclass does not know is an error rather than silently dropped. The named
loaders below cover what ``rescore`` reads back. Standard library only; no
Pydantic or attrs.
"""

from __future__ import annotations

import dataclasses
import functools
import types
import typing
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, TypeVar

from agentevalops.core.schemas import (
    AgentOutput,
    EndStateRecord,
    EvaluationResult,
    PassRateInterval,
    RunConfig,
    ToolCallRecord,
    TraceEvent,
)

T = TypeVar("T")


def to_jsonable(obj: Any) -> Any:
    """Recursively convert *obj* to a JSON-serializable value.

    Conversion rules (checked in order):

    - ``None``, ``bool``, ``int``, ``float``, ``str`` → returned as-is
    - ``Enum``          → ``.value``
    - ``datetime``      → ``.isoformat()``
    - ``Path``          → ``str(obj)``
    - ``list``/``tuple``→ list with elements recursively converted
    - ``dict``          → dict with str keys, values recursively converted
    - dataclass instance→ ``{field: to_jsonable(value), …}``, leaving out a
      field marked ``OMIT_WHEN_NONE`` while it is None (R35)
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
            if not (f.metadata.get("omit_when_none") and getattr(obj, f.name) is None)
        }
    return str(obj)


class LoadError(ValueError):
    """A bundle's JSON does not fit the dataclass it is loaded into."""


@functools.cache
def _hints(cls: Any) -> dict[str, Any]:
    return typing.get_type_hints(cls)


def _load(tp: Any, value: Any, where: str) -> Any:
    """*value*, as JSON gives it, converted to the type *tp* -- the inverse of
    ``to_jsonable`` for every type a bundle's dataclasses use."""
    if tp is Any:
        return value
    supertype = getattr(tp, "__supertype__", None)  # a NewType: TaskId, RunId
    if supertype is not None:
        return _load(supertype, value, where)
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if origin in (typing.Union, types.UnionType):
        if value is None and type(None) in args:
            return None
        options = [a for a in args if a is not type(None)]
        if len(options) != 1:
            raise LoadError(f"{where}: cannot choose among {options}")
        return _load(options[0], value, where)
    if origin is typing.Literal:
        if value not in args:
            raise LoadError(f"{where}: {value!r} is not one of {list(args)}")
        return value
    if origin in (list, tuple, dict):
        if origin is dict:
            if not isinstance(value, dict):
                raise LoadError(f"{where}: expected an object, got {value!r}")
            return {k: _load(args[1], v, f"{where}.{k}") for k, v in value.items()}
        if not isinstance(value, list):
            raise LoadError(f"{where}: expected an array, got {value!r}")
        if origin is list:
            return [_load(args[0], v, f"{where}[{n}]") for n, v in enumerate(value)]
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(
                _load(args[0], v, f"{where}[{n}]") for n, v in enumerate(value)
            )
        return tuple(
            _load(a, v, f"{where}[{n}]") for n, (a, v) in enumerate(zip(args, value))
        )
    if isinstance(tp, type) and issubclass(tp, Enum):
        return tp(value)
    if tp is datetime:
        return datetime.fromisoformat(value)
    if tp is Path:
        return Path(value)
    if dataclasses.is_dataclass(tp) and isinstance(tp, type):
        return from_jsonable(tp, value, where=where)
    if tp is float and isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    if isinstance(tp, type) and not isinstance(value, tp):
        raise LoadError(f"{where}: expected {tp.__name__}, got {value!r}")
    return value


def from_jsonable(cls: type[T], data: Any, *, where: str = "") -> T:
    """The dataclass *cls* rebuilt from *data*, as ``to_jsonable`` wrote it.
    A key *cls* has no field for is an error; a field *data* lacks takes its
    default (an older bundle), and one without a default is an error."""
    where = where or cls.__name__
    if not isinstance(data, dict):
        raise LoadError(f"{where}: expected an object, got {data!r}")
    fields = {f.name: f for f in dataclasses.fields(cls)}  # type: ignore[arg-type]
    unknown = sorted(set(data) - set(fields))
    if unknown:
        raise LoadError(f"{where}: no field for {unknown}")
    hints = _hints(typing.cast(Any, cls))
    kwargs = {
        name: _load(hints[name], data[name], f"{where}.{name}")
        for name in fields
        if name in data
    }
    try:
        return cls(**kwargs)
    except TypeError as exc:
        raise LoadError(f"{where}: {exc}") from None


def load_tool_call(data: Any) -> ToolCallRecord:
    """One line of ``tool_calls.jsonl``."""
    return from_jsonable(ToolCallRecord, data)


def load_end_state(data: Any) -> EndStateRecord:
    """One line of ``end_states.jsonl``."""
    return from_jsonable(EndStateRecord, data)


def load_trace_event(data: Any) -> TraceEvent:
    """One line of ``traces.jsonl``."""
    return from_jsonable(TraceEvent, data)


def load_agent_output(data: Any) -> AgentOutput:
    """A task run's ``output`` in ``summary.json``."""
    return from_jsonable(AgentOutput, data)


def load_run_config(data: Any) -> RunConfig:
    """``config.json``'s ``run_config``."""
    return from_jsonable(RunConfig, data)


def load_pass_rate_interval(data: Any) -> PassRateInterval:
    """A pass^k rate's bootstrap interval (ROADMAP R36)."""
    return from_jsonable(PassRateInterval, data)


#: The keys the writer adds to each ``evaluations.json`` entry, beside the
#: ``EvaluationResult``'s own fields (``BundleWriter._write_evaluations``).
EVALUATION_RUN_KEYS: tuple[str, ...] = ("task_id", "repetition")


def load_evaluation(data: Any) -> tuple[str, int, EvaluationResult]:
    """One ``evaluations.json`` entry: its task run and its result."""
    if not isinstance(data, dict):
        raise LoadError(f"evaluations.json: expected an object, got {data!r}")
    result = {k: v for k, v in data.items() if k not in EVALUATION_RUN_KEYS}
    return (
        str(data.get("task_id")),
        int(data.get("repetition", 0)),
        from_jsonable(EvaluationResult, result, where="evaluations.json"),
    )
