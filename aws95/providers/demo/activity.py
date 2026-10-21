from __future__ import annotations

import dataclasses
from collections import Counter
from datetime import datetime
from typing import Any

from aws95.contracts.activity import (AccountHistory, ActivityEvent, ActivityQuery, Bucket, Bucketed, CoverageSpan,
                                      HistoryStatus, PrincipalSummary, TopDimension)
from aws95.contracts.common import Page, Result, Scope
from aws95.core.cancel import CancelToken, raise_if_cancelled
from aws95.providers.demo import store
from aws95.providers.demo.builder import build

PAGE_SIZE = 500
DEMO_NO_AWS = "Loading activity isn't available in demo mode."


def _plus(when: datetime | None, shift) -> datetime | None:
    return None if when is None else when + shift


def _shift_account(a: AccountHistory, shift) -> AccountHistory:
    def moved(spans):
        return tuple(CoverageSpan(s.start + shift, s.end + shift) for s in spans)
    return dataclasses.replace(a, regions=tuple(dataclasses.replace(r, looked_up=moved(r.looked_up),
                                                                    imported=moved(r.imported)) for r in a.regions))


def _ok(data):
    return Result.ok(data, freshness=store.DEMO_FRESHNESS, is_demo=True)


def _matches(e: ActivityEvent, scope: Scope, q: ActivityQuery) -> bool:
    if not store.in_scope(e.account, e.region, scope):
        return False
    tr = scope.time_range
    if tr is not None and not tr.start <= e.time_utc <= tr.end:
        return False
    if q.principal and e.principal_name != q.principal:
        return False
    if q.service and e.source != q.service:
        return False
    if q.action and e.name != q.action:
        return False
    if q.arn_contains and not any(q.arn_contains in arn for arn in e.resource_arns):
        return False
    if q.human_only and not e.is_human:
        return False
    return e.risk_level >= q.risk_min


class DemoActivity:
    def __init__(self) -> None:
        # Rebased once per instance, so one event has one time on every call (list, raw_event, pages).
        self._shift = store.now() - datetime.fromisoformat(store.load("activity.json")["anchor"])
        self._built: tuple[ActivityEvent, ...] | None = None

    def _events(self) -> tuple[ActivityEvent, ...]:
        # Built once per instance (every call used to re-validate the whole fixture); the events are frozen.
        if self._built is None:
            data = store.load("activity.json")
            shift = self._shift
            self._built = tuple(dataclasses.replace(ev, time_utc=ev.time_utc + shift)
                                for ev in (build(ActivityEvent, e) for e in data["events"]))
        return self._built

    def _select(self, scope: Scope, query: ActivityQuery | None = None) -> list[ActivityEvent]:
        q = query or ActivityQuery()
        return [e for e in self._events() if _matches(e, scope, q)]

    def events(self, scope: Scope, query: ActivityQuery | None = None, cursor: str | None = None, *,
               cancel: CancelToken | None = None):
        raise_if_cancelled(cancel)
        rows = self._select(scope, query)
        start = int(cursor) if cursor else 0
        nxt = start + PAGE_SIZE
        return _ok(Page(items=tuple(rows[start:nxt]), next_cursor=str(nxt) if nxt < len(rows) else None,
                        total=len(rows)))

    def principals(self, scope: Scope, *, cancel: CancelToken | None = None):
        raise_if_cancelled(cancel)
        counts = Counter((e.principal_type, e.principal_name, e.is_human) for e in self._select(scope))
        return _ok(tuple(PrincipalSummary(t, n, h, c) for (t, n, h), c in counts.most_common()))

    def time_series(self, scope: Scope, bucket: Bucket = "day", *, cancel: CancelToken | None = None):
        raise_if_cancelled(cancel)
        def floor(t: datetime) -> datetime:
            t = t.replace(minute=0, second=0, microsecond=0)
            return t.replace(hour=0) if bucket == "day" else t
        counts = Counter(floor(e.time_utc) for e in self._select(scope))
        return _ok(Bucketed(bucket=bucket, series=tuple(sorted(counts.items()))))

    def top(self, scope: Scope, dimension: TopDimension = "principal", limit: int = 10, *,
            cancel: CancelToken | None = None):
        raise_if_cancelled(cancel)
        key = {"principal": lambda e: e.principal_name, "action": lambda e: e.name,
               "service": lambda e: e.source,
               "actor_kind": lambda e: "human" if e.is_human else "service"}[dimension]
        counts = Counter(key(e) for e in self._select(scope))
        return _ok(tuple(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]))

    def raw_event(self, scope: Scope, event_id: str = "", *, cancel: CancelToken | None = None):
        raise_if_cancelled(cancel)
        for e in self._events():
            if e.event_id == event_id:
                return _ok(self._raw(e))
        return Result.error(f"Event {event_id!r} is not in the demo data.", is_demo=True)

    @staticmethod
    def _raw(e: ActivityEvent) -> dict[str, Any]:
        return {"eventVersion": "1.09", "eventID": e.event_id, "eventTime": e.time_utc.isoformat(),
                "eventSource": e.source, "eventName": e.name, "awsRegion": e.region,
                "recipientAccountId": e.account, "readOnly": e.read_only,
                "sourceIPAddress": e.source_ip or e.source, "userAgent": e.user_agent,
                "userIdentity": {"type": e.principal_type, "principalName": e.principal_name,
                                 "accountId": e.account},
                "resources": [{"ARN": arn} for arn in e.resource_arns]}

    def history_status(self, scope: Scope, *, cancel: CancelToken | None = None):
        raise_if_cancelled(cancel)
        status = build(HistoryStatus, store.load("activity.json")["history"])
        accounts = tuple(_shift_account(a, self._shift) for a in status.accounts
                         if not scope.accounts or a.account in scope.accounts)
        return _ok(dataclasses.replace(status, accounts=accounts,
                                       denied_changes_since=_plus(status.denied_changes_since, self._shift),
                                       denied_reads_since=_plus(status.denied_reads_since, self._shift)))

    # Demo mode never reaches AWS and never writes the store.
    def estimate_history(self, scope: Scope, request=None, *, cancel: CancelToken | None = None):
        return Result.not_connected(DEMO_NO_AWS, is_demo=True)

    def load_history(self, scope: Scope, request=None, *, progress=None, stop=None,
                     cancel: CancelToken | None = None):
        return Result.not_connected(DEMO_NO_AWS, is_demo=True)

    def find_trails(self, scope: Scope, account=None, regions=(), *, cancel: CancelToken | None = None):
        return Result.not_connected(DEMO_NO_AWS, is_demo=True)

    def check_import(self, scope: Scope, plan=None, *, cancel: CancelToken | None = None):
        return Result.not_connected(DEMO_NO_AWS, is_demo=True)

    def import_history(self, scope: Scope, plan=None, *, progress=None, stop=None,
                       cancel: CancelToken | None = None):
        return Result.not_connected(DEMO_NO_AWS, is_demo=True)
