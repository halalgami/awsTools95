"""@guarded: a live provider method never raises into the UI (spec §5.3)."""
from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from aws95.contracts.common import Freshness, Result, Status

log = logging.getLogger("aws95.live")


def guarded(service: str = "", operation: str = "") -> Callable:
    def wrap(fn: Callable[..., Result[Any]]) -> Callable[..., Result[Any]]:
        @functools.wraps(fn)
        def inner(*args: Any, **kwargs: Any) -> Result[Any]:
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - every failure becomes a Result; CancelledError re-raised
                from aws95.aws.discover import DiscoveryError
                from aws95.aws.errors import classify
                if isinstance(exc, DiscoveryError):
                    return Result.error(str(exc))
                import sqlite3

                from aws95.store import StoreError
                if isinstance(exc, StoreError):
                    return Result.error(str(exc))
                if isinstance(exc, sqlite3.Error):
                    log.warning("local store error: %s", type(exc).__name__)
                    return Result.error("The local data could not be read. Try again, or clear it with "
                                        "Start ▸ Settings ▸ Clear Local Data….")
                c = classify(exc, service, operation)
                log.warning("aws call failed: %s %s %s", service, operation, c.code)
                when = Freshness(datetime.now(timezone.utc), "attempt")
                if c.status is Status.NOT_CONNECTED:
                    return Result.not_connected(c.message, freshness=when)
                return Result(c.status, None, None, when, c.message)
        return inner
    return wrap
