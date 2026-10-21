"""Bounded fan-out for per-item reads (resumable-snapshots spec §5.3): Lambda's per-function and S3's per-bucket
calls run FANOUT at a time instead of one after another. Every call still goes through the same gate binding."""
from __future__ import annotations

import concurrent.futures as cf
from collections.abc import Callable, Sequence
from typing import Any

from aws95.core.cancel import raise_if_cancelled

FANOUT = 4


def map_ordered(fn: Callable[[Any], Any], items: Sequence[Any], *, workers: int = FANOUT, cancel=None) -> list:
    """fn over items, FANOUT at a time, results in input order. An exception from fn is returned in its slot so the
    caller keeps its own per-item handling (a denied bucket stays one partial row, never a lost run)."""
    raise_if_cancelled(cancel)

    def guarded(item):
        raise_if_cancelled(cancel)
        try:
            return fn(item)
        except Exception as exc:  # noqa: BLE001 - handed back to the caller, in order
            return exc

    with cf.ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="aws95-fanout") as pool:
        return list(pool.map(guarded, items))
