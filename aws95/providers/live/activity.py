"""LiveActivity (first-drop spec §5.5): every view reads the local store, so opening the Activity window never
reaches AWS. Only estimate_history, load_history, find_trails, check_import and import_history reach AWS, and only
when the user asks. Honesty: no coverage is "not loaded" (NOT_CONNECTED), never an empty grid; an uncovered region or
a range that starts before the history is PARTIAL; history more than a day old is STALE."""
from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone

from aws95.contracts.activity import (NO_HISTORY, AccountHistory, ActivityEvent, ActivityQuery, Bucketed,
                                      CoverageSpan, HistoryOutcome, HistoryStatus, PrincipalSummary, RegionHistory)
from aws95.contracts.common import Coverage, Freshness, Page, Result, Scope, Status
from aws95.contracts.snapshots import STALE_AFTER
from aws95.core.cancel import AnyToken, CancelToken, raise_if_cancelled
from aws95.providers.live.common import NO_ACCOUNTS, Clock, Roster, marks, scope_accounts, utcnow
from aws95.providers.live.guard import guarded
from aws95.store import Store, parse_ts, ts

PAGE_SIZE = 500
FRESH_SOURCE = "activity"
CHOOSE = "Choose at least one account."
_GAP = timedelta(minutes=1)
_TOP = {"principal": "principal_name", "action": "event_name", "service": "event_source",
        "actor_kind": "CASE WHEN is_human = 1 THEN 'human' ELSE 'service' END"}
Envelope = tuple[Status, Coverage, Freshness, "str | None"]


def _like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _when(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def outcome_text(out: HistoryOutcome, labels: Mapping[str, str]) -> str:
    verb = "Import" if out.kind == "import" else "Activity"
    parts = []
    for run in out.runs:
        text = f"{labels.get(run.account_id, run.account_id)}: {run.status}"
        if run.events_added:
            text += f" ({run.events_added:,} new events)"
        if run.message and run.status != "complete":
            text += f" ({run.message})"
        parts.append(text)
    return (f"{verb} cancelled. " if out.cancelled else "") + " · ".join(parts)


def _where(accounts: tuple[str, ...], scope: Scope, query: ActivityQuery | None) -> tuple[str, list]:
    clauses, args = [f"account IN ({marks(len(accounts))})"], list(accounts)
    if scope.regions:
        clauses.append(f"region IN ({marks(len(scope.regions))})")
        args += list(scope.regions)
    tr = scope.time_range
    if tr is not None:
        clauses.append("event_time_utc >= ? AND event_time_utc <= ?")
        args += [ts(tr.start), ts(tr.end)]
    q = query or ActivityQuery()
    if q.principal:
        clauses.append("principal_name = ?")
        args.append(q.principal)
    if q.service:
        clauses.append("event_source = ?")
        args.append(q.service)
    if q.action:
        clauses.append("event_name = ?")
        args.append(q.action)
    if q.arn_contains:
        clauses.append("resource_arns LIKE ? ESCAPE '\\'")
        args.append(f"%{_like(q.arn_contains)}%")
    if q.human_only:
        clauses.append("is_human = 1")
    if q.risk_min:
        clauses.append("risk_level >= ?")
        args.append(q.risk_min)
    return " AND ".join(clauses), args


def _event(r: sqlite3.Row) -> ActivityEvent:
    return ActivityEvent(r["event_id"], r["account"], parse_ts(r["event_time_utc"]), r["principal_type"],
                         r["principal_name"], bool(r["is_human"]), r["event_source"], r["event_name"],
                         bool(r["read_only"]), r["source_ip"], r["user_agent"],
                         tuple(json.loads(r["resource_arns"] or "[]")), r["region"],
                         min(3, max(0, int(r["risk_level"]))), r["risk_rule"], r["error_code"])


class LiveActivity:
    def __init__(self, store: Store, roster: Roster, *, clock: Clock = utcnow, retention_days: int = 90,
                 service_factory: Callable[[Store], object] | None = None) -> None:
        self._store = store
        self._roster = roster
        self._clock = clock
        self._retention = max(1, int(retention_days))
        self._service_factory = service_factory
        self._service = None
        self._service_lock = threading.Lock()

    @property
    def retention_days(self) -> int:
        return self._retention

    def set_retention_days(self, days: int) -> None:
        self._retention = max(1, int(days))

    def _service_once(self):
        with self._service_lock:
            if self._service is None:
                if self._service_factory is not None:
                    self._service = self._service_factory(self._store)
                else:
                    from aws95.aws.engines.ctwatch.service import ActivityService
                    self._service = ActivityService(self._store)
            return self._service

    def _accounts(self, scope: Scope) -> tuple[str, ...]:
        return scope_accounts(scope, self._roster())

    # ----- honesty envelope -----
    def _envelope(self, scope: Scope, accounts: tuple[str, ...]) -> Envelope | None:
        spans: dict[str, list[tuple[datetime, datetime]]] = {}
        for account in accounts:
            for c in self._store.coverage(account):
                if c.kind not in ("bulk", "import") or (scope.regions and c.region not in scope.regions):
                    continue
                spans.setdefault(c.region, []).append((c.start, c.end))
        if not spans:
            return None
        tr = scope.time_range
        covered = tuple(sorted(r for r, lst in spans.items()
                               if tr is None or any(lo < tr.end and hi > tr.start for lo, hi in lst)))
        requested = tuple(scope.regions) if scope.regions else tuple(sorted(spans))
        coverage = Coverage(requested, covered)
        earliest = min(lo for lst in spans.values() for lo, _ in lst)
        newest = max(hi for lst in spans.values() for _, hi in lst)
        messages: list[str] = []
        partial = not coverage.complete
        if tr is not None and tr.start < earliest - _GAP:
            partial = True
            messages.append(f"History starts {_when(earliest)}; earlier events aren't loaded.")
        age = self._clock() - newest
        stale = age > STALE_AFTER
        if stale:
            messages.append(f"Activity was last updated {age.days} day{'s' if age.days != 1 else ''} ago. "
                            "Press F5 to update.")
        status = Status.PARTIAL if partial else (Status.STALE if stale else Status.OK)
        return status, coverage, Freshness(newest, FRESH_SOURCE), " · ".join(messages) or None

    def _no_history(self, scope: Scope, accounts: tuple[str, ...]) -> Result:
        if scope.regions and any(self._store.coverage(a) for a in accounts):
            return Result.not_connected(f"Activity history isn't loaded for {', '.join(scope.regions)}. Add the "
                                        "region to the account's regions (Take Snapshot ▸ Change regions…) and "
                                        "use Resume Loading.")
        return Result.not_connected(NO_HISTORY)

    def _prepare(self, scope: Scope) -> tuple[tuple[str, ...], Envelope | None, Result | None]:
        accounts = self._accounts(scope)
        if not accounts:
            return accounts, None, Result.not_connected(NO_ACCOUNTS)
        env = self._envelope(scope, accounts)
        if env is None:
            return accounts, None, self._no_history(scope, accounts)
        return accounts, env, None

    @staticmethod
    def _wrap(data, env: Envelope) -> Result:
        status, coverage, freshness, message = env
        return Result(status, data, coverage, freshness, message)

    # ----- views (store only) -----
    @guarded()
    def events(self, scope: Scope, query: ActivityQuery | None = None, cursor: str | None = None, *,
               cancel: CancelToken | None = None):
        accounts, env, early = self._prepare(scope)
        if early is not None:
            return early
        where, args = _where(accounts, scope, query)
        offset = int(cursor) if cursor else 0
        raise_if_cancelled(cancel)
        with self._store.reader() as db:
            total = db.execute(f"SELECT COUNT(*) FROM events WHERE {where}", args).fetchone()[0]
            rows = db.execute(f"SELECT * FROM events WHERE {where} ORDER BY event_time_utc DESC, event_id DESC "
                              "LIMIT ? OFFSET ?", (*args, PAGE_SIZE, offset)).fetchall()
        nxt = offset + PAGE_SIZE
        return self._wrap(Page(tuple(_event(r) for r in rows), str(nxt) if nxt < total else None, total), env)

    @guarded()
    def principals(self, scope: Scope, *, cancel: CancelToken | None = None):
        accounts, env, early = self._prepare(scope)
        if early is not None:
            return early
        where, args = _where(accounts, scope, None)
        with self._store.reader() as db:
            rows = db.execute(f"SELECT principal_type, principal_name, is_human, COUNT(*) AS c FROM events "
                              f"WHERE {where} GROUP BY principal_type, principal_name, is_human "
                              "ORDER BY c DESC, principal_name", args).fetchall()
        return self._wrap(tuple(PrincipalSummary(r[0], r[1], bool(r[2]), r[3]) for r in rows), env)

    @guarded()
    def time_series(self, scope: Scope, bucket="day", *, cancel: CancelToken | None = None):
        accounts, env, early = self._prepare(scope)
        if early is not None:
            return early
        where, args = _where(accounts, scope, None)
        width, fmt = (13, "%Y-%m-%dT%H") if bucket == "hour" else (10, "%Y-%m-%d")
        with self._store.reader() as db:
            rows = db.execute(f"SELECT substr(event_time_utc, 1, {width}) AS b, COUNT(*) AS c FROM events "
                              f"WHERE {where} GROUP BY b ORDER BY b", args).fetchall()
        series = tuple((datetime.strptime(r[0], fmt).replace(tzinfo=timezone.utc), r[1]) for r in rows)
        return self._wrap(Bucketed(bucket, series), env)

    @guarded()
    def top(self, scope: Scope, dimension="principal", limit: int = 10, *, cancel: CancelToken | None = None):
        accounts, env, early = self._prepare(scope)
        if early is not None:
            return early
        expr = _TOP[dimension]                       # a fixed column expression, never user text
        where, args = _where(accounts, scope, None)
        with self._store.reader() as db:
            rows = db.execute(f"SELECT {expr} AS k, COUNT(*) AS c FROM events WHERE {where} GROUP BY k "
                              "ORDER BY c DESC, k LIMIT ?", (*args, int(limit))).fetchall()
        return self._wrap(tuple((r[0], r[1]) for r in rows), env)

    @guarded()
    def raw_event(self, scope: Scope, event_id: str = "", *, cancel: CancelToken | None = None):
        accounts = self._accounts(scope)
        with self._store.reader() as db:
            row = db.execute("SELECT account FROM events WHERE event_id=?", (event_id,)).fetchone()
        if row is None or row["account"] not in accounts:
            return Result.error("That event isn't in the local activity history.")
        text = self._store.raw_event(event_id)
        try:
            data = json.loads(text) if text is not None else None
        except ValueError:
            data = None
        if not isinstance(data, dict):
            return Result.error("The stored event couldn't be read.")
        return Result.ok(data)

    @guarded()
    def history_status(self, scope: Scope, *, cancel: CancelToken | None = None):
        from aws95.aws.engines.ctwatch.fetcher import gaps
        from aws95.aws.engines.ctwatch.scope import LOOKUP_PASSES
        from aws95.aws.engines.ctwatch.service import days_key, regions_key, saved_days
        accounts = self._accounts(scope)
        if not accounts:
            return Result.not_connected(NO_ACCOUNTS)
        now = self._clock()
        out, bulk_starts, import_starts = [], [], []
        for account in accounts:
            cov = self._store.coverage(account)
            days = saved_days(self._store.get_meta(days_key(account)))
            meta_regions = [r for r in (self._store.get_meta(regions_key(account)) or "").split(",") if r]
            regions, unfinished = [], False
            for region in sorted({c.region for c in cov} | set(meta_regions)):
                looked = tuple(CoverageSpan(c.start, c.end) for c in cov if c.region == region and c.kind == "bulk")
                imported = tuple(CoverageSpan(c.start, c.end) for c in cov
                                 if c.region == region and c.kind == "import")
                regions.append(RegionHistory(region, looked, imported))
                bulk_starts += [s.start for s in looked]
                import_starts += [s.start for s in imported]
                if days is None:
                    continue
                floor = now - timedelta(days=min(days, self._retention))
                for rule in LOOKUP_PASSES:
                    spans = [(c.start, c.end) for c in cov if c.region == region and c.kind == rule.key]
                    top = max((hi for _, hi in spans), default=floor)
                    if not spans or gaps(spans, floor, top):
                        unfinished = True
            out.append(AccountHistory(account, days, tuple(regions), unfinished))
        svc = self._service
        return Result.ok(HistoryStatus(tuple(out), min(bulk_starts, default=None), min(import_starts, default=None),
                                       getattr(svc, "running", None), getattr(svc, "progress_text", None)))

    # ----- jobs (the only paths to AWS; the user starts each one) -----
    def _outcome(self, out: HistoryOutcome, labels: Mapping[str, str]) -> Result:
        if out.runs and all(r.status == "failed" for r in out.runs):
            status = Status.ERROR
        elif all(r.status == "complete" for r in out.runs) and not out.cancelled:
            status = Status.OK
        else:
            status = Status.PARTIAL
        return Result(status, out, None, Freshness(self._clock(), FRESH_SOURCE), outcome_text(out, labels))

    @guarded("cloudtrail", "LookupEvents")
    def estimate_history(self, scope: Scope, request=None, *, cancel: CancelToken | None = None):
        if request is None or not request.accounts:
            return Result.error(CHOOSE)
        return Result.ok(self._service_once().estimate(request, cancel=cancel))

    @guarded("cloudtrail", "LookupEvents")
    def load_history(self, scope: Scope, request=None, *, progress=None, stop: CancelToken | None = None,
                     cancel: CancelToken | None = None):
        if request is None or not request.accounts:
            return Result.error(CHOOSE)
        from aws95.aws.engines.ctwatch.service import ActivityBusy
        try:
            out = self._service_once().load(request, cancel=AnyToken(stop, cancel), progress=progress)
        except ActivityBusy as exc:
            return Result.error(str(exc))
        return self._outcome(out, request.labels)

    @guarded("cloudtrail", "DescribeTrails")
    def find_trails(self, scope: Scope, account=None, regions: tuple[str, ...] = (), *,
                    cancel: CancelToken | None = None):
        accounts = self._accounts(scope)
        entry = account or next((e for e in self._roster() if e.account_id in accounts), None)
        if entry is None:
            return Result.not_connected(NO_ACCOUNTS)
        return Result.ok(self._service_once().find_trails(entry, tuple(regions) or ("us-east-1",), cancel=cancel))

    @guarded("s3", "GetObject")
    def check_import(self, scope: Scope, plan=None, *, cancel: CancelToken | None = None):
        if plan is None:
            return Result.error("Choose an account, a trail and a date range first.")
        return Result.ok(self._service_once().check_import(plan, cancel=cancel))

    @guarded("s3", "GetObject")
    def import_history(self, scope: Scope, plan=None, *, progress=None, stop: CancelToken | None = None,
                       cancel: CancelToken | None = None):
        if plan is None:
            return Result.error("Choose an account, a trail and a date range first.")
        from aws95.aws.engines.ctwatch.service import ActivityBusy
        try:
            out = self._service_once().import_history(plan, cancel=AnyToken(stop, cancel), progress=progress)
        except ActivityBusy as exc:
            return Result.error(str(exc))
        return self._outcome(out, {plan.account.account_id: plan.label or plan.account.profile})
