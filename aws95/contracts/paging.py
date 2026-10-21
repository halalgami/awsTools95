"""fetch_all_pages: follow a provider's page cursors and merge them into one honest Result (M3-R5, M3-R9)."""
from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

from aws95.contracts.common import Coverage, Page, Result, Status
from aws95.core.cancel import CancelToken, raise_if_cancelled

_USABLE = (Status.OK, Status.PARTIAL, Status.STALE)
_DEGRADED = (Status.OK, Status.STALE, Status.PARTIAL)


def merge_coverage(a: Coverage | None, b: Coverage | None) -> Coverage | None:
    if a is None or b is None:
        return a or b
    return Coverage(tuple(dict.fromkeys(a.regions_requested + b.regions_requested)),
                    tuple(dict.fromkeys(a.regions_covered + b.regions_covered)), a.denials + b.denials)


def fetch_all_pages(call: Callable[[str | None], Result[Page[Any]]], *, cancel: CancelToken | None = None,
                    limit: int | None = None) -> Result[tuple[Any, ...]]:
    """call(cursor) returns a Result[Page]. Result.data is the merged tuple of items."""
    items: list[Any] = []
    cursor: str | None = None
    seen: set[str] = set()
    first: Result | None = None
    status = Status.OK
    coverage: Coverage | None = None
    pages = 0
    while True:
        raise_if_cancelled(cancel)
        page = call(cursor)
        if page.status not in _USABLE or page.data is None:
            if first is None:
                return page
            failed_cov = page.coverage or Coverage()
            if page.status is Status.DENIED:
                failed_cov = Coverage(failed_cov.regions_requested, failed_cov.regions_covered, failed_cov.denials + 1)
            return dataclasses.replace(first, status=Status.PARTIAL, coverage=merge_coverage(coverage, failed_cov),
                                       data=tuple(items),
                                       message=f"Showing {len(items)} rows. Page {pages + 1} failed: "
                                               f"{page.message or page.status.value}")
        pages += 1
        first = first or page
        status = max(status, page.status, key=_DEGRADED.index)
        coverage = merge_coverage(coverage, page.coverage)
        items.extend(page.data.items)
        cursor = page.data.next_cursor
        if limit is not None and cursor is not None and len(items) >= limit:
            return dataclasses.replace(first, status=status, coverage=coverage, data=tuple(items[:limit]),
                                       message=f"Showing the first {limit} items.")
        if cursor is None:
            return dataclasses.replace(first, status=status, coverage=coverage, data=tuple(items))
        if cursor in seen:
            return Result.error("The provider repeated a page cursor.", is_demo=first.is_demo)
        seen.add(cursor)
