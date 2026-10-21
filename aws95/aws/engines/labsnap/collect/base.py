# Upstream: labsnap/labsnap/collect/base.py @ 1b1d437 — adapted for aws95; owned here from now on.
"""The collector registry. A collector module registers itself on import (collectors/__init__.py imports them all)."""
from __future__ import annotations

import warnings
from typing import Literal, Protocol, runtime_checkable

from aws95.aws.engines.labsnap.model import CollectorResult

_REGISTRY: dict[str, type] = {}


class UnknownCollector(Exception):
    pass


@runtime_checkable
class Collector(Protocol):
    name: str
    scope: Literal["global", "regional"]
    timeout_s: int
    volatile_keys: frozenset      # attrs excluded from diffing
    # Optional class attribute `billed = True`: the collector makes billed calls and runs only for accounts whose
    # owner opted in to Billing (spec F2).

    def collect(self, session, region: str | None, cancel=None) -> CollectorResult: ...


def register(cls):
    """Class decorator. A class without `name` is not registered, and says so (it would otherwise vanish)."""
    if not hasattr(cls, "name"):
        warnings.warn(f"collector {cls.__name__} has no `name`; not registered", stacklevel=2)
        return cls
    _REGISTRY[cls.name] = cls
    return cls


def all_collectors() -> dict[str, type]:
    return dict(_REGISTRY)


def select(names) -> list:
    if not names:
        return [c() for c in _REGISTRY.values()]
    out = []
    for n in names:
        if n not in _REGISTRY:
            raise UnknownCollector(f"unknown collector: {n} (have: {', '.join(sorted(_REGISTRY))})")
        out.append(_REGISTRY[n]())
    return out
