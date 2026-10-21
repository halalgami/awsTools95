"""Fixture JSON → contract dataclasses, with strict validation (unknown keys, float money, naive datetimes)."""
from __future__ import annotations

import collections.abc
import dataclasses
import functools
import types
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints


class FixtureError(ValueError):
    pass


@functools.lru_cache(maxsize=None)
def _hints(tp: type) -> dict[str, Any]:
    """get_type_hints evaluates every annotation string; a class's hints never change, so do it once."""
    return get_type_hints(tp)


def build(tp: Any, data: Any) -> Any:
    origin = get_origin(tp)
    if tp is Any:
        return data
    if origin in (Union, types.UnionType):
        args = get_args(tp)
        if data is None and type(None) in args:
            return None
        errors = []
        for arg in args:
            if arg is type(None):
                continue
            try:
                return build(arg, data)
            except (FixtureError, TypeError, ValueError) as exc:
                errors.append(str(exc))
        raise FixtureError(f"{data!r} matches none of {args}: {errors}")
    if origin is Literal:
        if data not in get_args(tp):
            raise FixtureError(f"{data!r} not one of {get_args(tp)}")
        return data
    if origin is tuple:
        args = get_args(tp)
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(build(args[0], x) for x in data)
        return tuple(build(a, x) for a, x in zip(args, data, strict=True))
    if origin in (dict, collections.abc.Mapping) or tp in (dict, collections.abc.Mapping):
        if not isinstance(data, dict):
            raise FixtureError(f"expected an object, got {type(data).__name__}")
        return dict(data)
    if tp is Decimal:
        if isinstance(data, float):
            raise FixtureError("money must be written as a string in fixtures, not a float")
        try:
            return Decimal(str(data))
        except InvalidOperation:
            raise FixtureError(f"{data!r} is not a decimal") from None
    if tp is datetime:
        value = datetime.fromisoformat(data)
        if value.tzinfo is None:
            raise FixtureError(f"datetime {data!r} must include a timezone")
        return value
    if isinstance(tp, type) and issubclass(tp, Enum):
        return tp(data)
    if dataclasses.is_dataclass(tp):
        if not isinstance(data, dict):
            raise FixtureError(f"{tp.__name__} needs an object")
        hints = _hints(tp)
        names = {f.name for f in dataclasses.fields(tp)}
        unknown = set(data) - names
        if unknown:
            raise FixtureError(f"{tp.__name__}: unknown fields {sorted(unknown)}")
        try:
            return tp(**{k: build(hints[k], v) for k, v in data.items()})
        except TypeError as exc:
            raise FixtureError(f"{tp.__name__}: {exc}") from None
    if tp in (str, int, bool, float):
        if tp is int and isinstance(data, bool) or not isinstance(data, tp):
            raise FixtureError(f"expected {tp.__name__}, got {data!r}")
        return data
    raise FixtureError(f"unsupported fixture type {tp!r}")
