# Upstream: ctwatch/ctwatch/ingest/fetcher.py @ 254ff31 — adapted for aws95; owned here from now on.
"""LookupEvents in windows, newest first, resumable (first-drop spec §5.2).

Upstream swept oldest → newest behind one watermark. Here every (account, region, pass) keeps merged coverage ranges
in the store (activity_coverage), so a load resumes exactly at its gaps, an update fetches only what is newer than
the coverage, and the newest events land first. Coverage is recorded only up to now - SETTLE, so events
CloudTrail publishes late are fetched again by the next update (review C3; de-duplicated by event_id). LookupEvents allows about 2 requests a second per region: callers
share one RateLimiter per job."""
from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Any

from aws95.aws import gate
from aws95.aws.engines.ctwatch.normalize import normalize_lookup
from aws95.aws.engines.ctwatch.scope import ScopeRule
from aws95.core.cancel import CancelToken, raise_if_cancelled
from aws95.store import EventRow

EVENTS_PER_CALL = 50
MIN_GAP = timedelta(minutes=1)
SETTLE = timedelta(minutes=15)    # CloudTrail publishes 5-15 min late: coverage never claims the last 15 min
Span = tuple[datetime, datetime]


def windows(start: datetime, end: datetime, hours: int = 4) -> list[Span]:
    """Upstream helper: [start, end) cut into `hours` windows, oldest first."""
    out, cur, step = [], start, timedelta(hours=hours)
    while cur < end:
        nxt = min(cur + step, end)
        out.append((cur, nxt))
        cur = nxt
    return out


def gaps(covered: Sequence[Span], start: datetime, end: datetime, *, min_span: timedelta = MIN_GAP) -> list[Span]:
    """The parts of [start, end) no covered range touches, oldest first; pieces shorter than min_span are dropped."""
    out: list[Span] = []
    cursor = start
    for lo, hi in sorted(covered):
        if hi <= cursor:
            continue
        if lo >= end:
            break
        if lo > cursor:
            out.append((cursor, min(lo, end)))
        cursor = max(cursor, hi)
        if cursor >= end:
            break
    if cursor < end:
        out.append((cursor, end))
    return [(lo, hi) for lo, hi in out if hi - lo >= min_span]


def newest_first(spans: Sequence[Span], hours: int) -> list[Span]:
    """Cut every span into `hours` windows from its end backwards; newest windows first overall."""
    step, out = timedelta(hours=hours), []
    for lo, hi in sorted(spans, key=lambda s: s[1], reverse=True):
        cur = hi
        while cur > lo:
            nxt = max(lo, cur - step)
            out.append((nxt, cur))
            cur = nxt
    return out


def day_slices(start: datetime, end: datetime) -> list[Span]:
    """[start, end) in one-day slices from `end` backwards (progress counts these as days)."""
    out, cur = [], end
    while cur > start:
        nxt = max(start, cur - timedelta(days=1))
        out.append((nxt, cur))
        cur = nxt
    return out


class RateLimiter:
    """At most `per_second` calls a second; thread-safe. Clock and sleep are injected so tests never sleep."""

    def __init__(self, per_second: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Any] = time.sleep) -> None:
        self._gap = 1.0 / per_second
        self._clock = clock
        self._sleep = sleep
        self._next: float | None = None
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            if self._next is not None and now < self._next:
                self._sleep(self._next - now)
                now = self._next
            self._next = now + self._gap


class RepeatedToken(Exception):
    """AWS returned a continuation token it had already returned: stop instead of looping forever."""


def lookup(session: Any, region: str, rule: ScopeRule, start: datetime, end: datetime, *, account: str,
           limiter: RateLimiter, cancel: CancelToken | None = None) -> list[EventRow]:
    """One window of one pass: every page, filtered by the rule's keep test, de-duplicated within the window."""
    if rule.lookup is None:
        raise ValueError(f"{rule.key} has no lookup pass")
    cloudtrail = gate.bind(session, "cloudtrail", region, cancel=cancel)
    key, value = rule.lookup
    params: dict[str, Any] = {"StartTime": start, "EndTime": end, "MaxResults": EVENTS_PER_CALL,
                              "LookupAttributes": [{"AttributeKey": key, "AttributeValue": value}]}
    rows: list[EventRow] = []
    seen_ids: set[str] = set()
    seen_tokens: set[str] = set()
    while True:
        limiter.wait()
        page = cloudtrail.lookup_events(**params)
        for raw in page.get("Events") or ():
            event_id = raw.get("EventId")
            if not event_id or event_id in seen_ids:
                continue
            try:
                detail = json.loads(raw["CloudTrailEvent"])
            except (KeyError, TypeError, ValueError):
                continue
            if not isinstance(detail, dict) or not rule.keep(detail):
                continue
            seen_ids.add(event_id)
            rows.append(normalize_lookup(raw, detail, account=account, region=region))
        token = page.get("NextToken")
        if not token:
            return rows
        if token in seen_tokens:
            raise RepeatedToken(f"{rule.key}: repeated NextToken")
        seen_tokens.add(token)
        params["NextToken"] = token
        raise_if_cancelled(cancel)


def sample(session: Any, region: str, now: datetime, *, cancel: CancelToken | None = None) -> tuple[int, bool]:
    """Spec §5.1: ONE call over the last hour of change events. Returns (events counted, more exist)."""
    cloudtrail = gate.bind(session, "cloudtrail", region, cancel=cancel)
    page = cloudtrail.lookup_events(StartTime=now - timedelta(hours=1), EndTime=now, MaxResults=EVENTS_PER_CALL,
                                    LookupAttributes=[{"AttributeKey": "ReadOnly", "AttributeValue": "false"}])
    return len(page.get("Events") or ()), bool(page.get("NextToken"))
