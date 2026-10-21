"""LiveBilling: Cost Explorer figures from each account's latest snapshot WITH Billing (spec F2). No live Cost
Explorer call. Amounts are Decimal; a multi-account figure is an exact Decimal sum of AWS's own figures."""
from __future__ import annotations

import json
from decimal import Decimal

from aws95.contracts.billing import BILLING_NOT_ENABLED, CostPoint, CostSummary, FreeTierUsage
from aws95.contracts.common import Result, Scope, Status, exact_add, exact_sum
from aws95.contracts.service_map import uncovered_reason
from aws95.core.cancel import CancelToken
from aws95.providers.live.common import (NO_ACCOUNTS, Clock, Roster, freshness_of, join_messages, scope_accounts,
                                         utcnow)
from aws95.providers.live.guard import guarded
from aws95.store import Store, parse_ts


def _previous_month(month: str) -> str:
    year, mon = (int(x) for x in month.split("-"))
    return f"{year - 1}-12" if mon == 1 else f"{year}-{mon - 1:02d}"


DIFFERENT_MONTHS = "These Billing snapshots are from different months. Take a new snapshot."
NOT_COLLECTED = "Not collected in the latest snapshot for {ids} — take a new snapshot with Billing on."
_PER_ACCOUNT = {"region": "cost.region.mtd", "usage_type": "cost.usage_type.mtd"}


class LiveBilling:
    def __init__(self, store: Store, roster: Roster, *, clock: Clock = utcnow) -> None:
        self._store = store
        self._roster = roster
        self._clock = clock

    def _latest_with(self, db, accounts, collector: str) -> dict:
        out = {}
        for acct in accounts:
            row = db.execute("SELECT s.* FROM snapshots s JOIN collector_runs c ON c.snapshot_id = s.id "
                             "AND c.collector=? AND c.status='ok' WHERE s.account=? "
                             "ORDER BY s.taken_at DESC, s.id DESC LIMIT 1", (collector, acct)).fetchone()
            if row is not None:
                out[acct] = row
        return out

    @staticmethod
    def _metric(db, snapshot_id: int, name: str):
        return db.execute("SELECT dims, period, value, unit FROM metrics WHERE snapshot_id=? AND name=? "
                          "ORDER BY period", (snapshot_id, name)).fetchall()

    def _finish(self, data, accounts, snaps, extra: str | None = None, partial: bool = False):
        fresh, stale = freshness_of([parse_ts(r["taken_at"]) for r in snaps.values()], self._clock())
        missing = [a for a in accounts if a not in snaps]
        gap = f"Billing isn't enabled for: {', '.join(missing)}." if missing else None
        status = Status.STALE if stale else Status.PARTIAL if (missing or partial) else Status.OK
        return Result(status, data, None, fresh, join_messages(stale, gap, extra))

    @guarded()
    def summary(self, scope: Scope, *, cancel: CancelToken | None = None):
        accounts = scope_accounts(scope, self._roster())
        if not accounts:
            return Result.not_connected(NO_ACCOUNTS)
        with self._store.reader() as db:
            snaps = self._latest_with(db, accounts, "cost")
            if not snaps:
                return Result.not_connected(BILLING_NOT_ENABLED)
            per = {acct: ({r["period"]: (Decimal(r["value"]), r["unit"]) for r in self._metric(db, s["id"], "cost.total.monthly")},
                          [(Decimal(r["value"]), r["unit"]) for r in self._metric(db, s["id"], "cost.total.daily")],
                          {r["period"]: (Decimal(r["value"]), r["unit"]) for r in self._metric(db, s["id"], "cost.forecast.remaining")})
                   for acct, s in snaps.items()}
        months = {s["taken_at"][:7] for s in snaps.values()}
        if len(months) > 1:
            return Result.error(f"These Billing snapshots are from different months ({', '.join(sorted(months))}). "
                                "Take a new snapshot to compare them.")
        month = months.pop()
        prev = _previous_month(month)
        units: set[str] = set()
        mtd, last, rest = Decimal(0), Decimal(0), Decimal(0)
        last_known = rest_known = True
        for monthly, daily, remaining in per.values():
            if month in monthly:
                mtd = exact_add(mtd, monthly[month][0])
                units.add(monthly[month][1])
            else:                                       # no monthly row: the exact sum of AWS's daily figures
                mtd = exact_add(mtd, exact_sum(v for v, _ in daily))
                units |= {u for _, u in daily}
            if prev in monthly:
                last = exact_add(last, monthly[prev][0])
                units.add(monthly[prev][1])
            else:
                last_known = False
            if month in remaining:
                rest = exact_add(rest, remaining[month][0])
                units.add(remaining[month][1])
            else:
                rest_known = False
        if len(units) > 1:
            return Result.error("These accounts report costs in different currencies.")
        as_of = min(parse_ts(s["taken_at"]) for s in snaps.values())
        summary = CostSummary(mtd, exact_add(mtd, rest) if rest_known else None, last if last_known else None,
                              units.pop() if units else "USD", as_of)
        return self._finish(summary, accounts, snaps)

    @guarded()
    def breakdown(self, scope: Scope, dimension: str = "service", *, cancel: CancelToken | None = None):
        if dimension not in ("service", "account", *_PER_ACCOUNT):
            return Result.error(f"Unknown breakdown {dimension!r}.")
        accounts = scope_accounts(scope, self._roster())
        if not accounts:
            return Result.not_connected(NO_ACCOUNTS)
        with self._store.reader() as db:
            snaps = self._latest_with(db, accounts, "cost")
            if not snaps:
                return Result.not_connected(BILLING_NOT_ENABLED)
            months = {s["taken_at"][:7] for s in snaps.values()}
            if len(months) > 1:
                return Result.error(DIFFERENT_MONTHS)
            month = next(iter(months))
            if dimension == "service":
                return self._by_service(db, accounts, snaps, month)
            if dimension == "account":
                return self._by_account(db, accounts, snaps, month)
            return self._per_account(db, accounts, snaps, month, dimension)

    def _by_service(self, db, accounts, snaps, month):
        totals: dict[tuple[str, str], Decimal] = {}
        for s in snaps.values():
            for r in self._metric(db, s["id"], "cost.service.monthly"):
                if r["period"] != month:
                    continue
                key = (json.loads(r["dims"]).get("service", ""), r["unit"])
                totals[key] = exact_add(totals.get(key, Decimal(0)), Decimal(r["value"]))
        points = sorted((CostPoint(month, amount, unit, {"service": service})
                         for (service, unit), amount in totals.items()),
                        key=lambda p: (-p.amount, p.dims["service"]))
        return self._finish(tuple(points), accounts, snaps)

    def _by_account(self, db, accounts, snaps, month):
        points = []
        for acct, s in snaps.items():
            monthly = [r for r in self._metric(db, s["id"], "cost.total.monthly") if r["period"] == month]
            if monthly:
                points.append(CostPoint(month, Decimal(monthly[0]["value"]), monthly[0]["unit"], {"account": acct}))
                continue
            daily = self._metric(db, s["id"], "cost.total.daily")      # no monthly row: AWS's daily figures, summed exactly
            units = {r["unit"] for r in daily}
            if daily and len(units) == 1:
                points.append(CostPoint(month, exact_sum(Decimal(r["value"]) for r in daily), units.pop(),
                                        {"account": acct}))
        points.sort(key=lambda p: (-p.amount, p.dims["account"]))
        return self._finish(tuple(points), accounts, snaps)

    def _per_account(self, db, accounts, snaps, month, dimension):
        points, missing = [], []
        for acct, s in snaps.items():
            rows = [r for r in self._metric(db, s["id"], _PER_ACCOUNT[dimension]) if r["period"] == month]
            if not rows:
                missing.append(acct)
            for r in rows:
                points.append(CostPoint(month, Decimal(r["value"]), r["unit"],
                                        {"account": acct, dimension: json.loads(r["dims"]).get(dimension, "")}))
        note = NOT_COLLECTED.format(ids=", ".join(missing)) if missing else None
        if not points:
            return Result.not_connected(note or BILLING_NOT_ENABLED)
        points.sort(key=lambda p: (-p.amount, p.dims[dimension], p.dims["account"]))
        return self._finish(tuple(points), accounts, snaps, note, partial=bool(missing))

    @guarded()
    def trend(self, scope: Scope, granularity: str = "monthly", *, cancel: CancelToken | None = None):
        accounts = scope_accounts(scope, self._roster())
        if not accounts:
            return Result.not_connected(NO_ACCOUNTS)
        name = "cost.total.daily" if granularity == "daily" else "cost.total.monthly"
        with self._store.reader() as db:
            snaps = self._latest_with(db, accounts, "cost")
            if not snaps:
                return Result.not_connected(BILLING_NOT_ENABLED)
            totals: dict[tuple[str, str], Decimal] = {}
            for s in snaps.values():
                for r in self._metric(db, s["id"], name):
                    key = (r["period"], r["unit"])
                    totals[key] = exact_add(totals.get(key, Decimal(0)), Decimal(r["value"]))
        points = tuple(CostPoint(period, amount, unit) for (period, unit), amount in sorted(totals.items()))
        return self._finish(points, accounts, snaps)

    @guarded()
    def free_tier(self, scope: Scope, *, cancel: CancelToken | None = None):
        accounts = scope_accounts(scope, self._roster())
        if not accounts:
            return Result.not_connected(NO_ACCOUNTS)
        with self._store.reader() as db:
            snaps = self._latest_with(db, accounts, "freetier")
            if not snaps:
                return Result.not_connected("Free tier usage wasn't collected in the latest snapshot.")
            rows: dict[tuple, dict] = {}
            for acct, s in snaps.items():
                for name in ("freetier.actual", "freetier.limit", "freetier.forecast"):
                    for r in self._metric(db, s["id"], name):
                        dims = json.loads(r["dims"])
                        key = (acct, dims.get("service", ""), dims.get("usage_type", ""), dims.get("region", ""))
                        entry = rows.setdefault(key, {"period": r["period"], "unit": r["unit"]})
                        entry[name.split(".")[1]] = Decimal(r["value"])
        items = tuple(FreeTierUsage(service, f"{usage} ({region})" if region else usage, e["actual"], e["limit"],
                                    e.get("forecast"), e["unit"] or "", e["period"] or "")
                      for (acct, service, usage, region), e in sorted(rows.items())
                      if "actual" in e and "limit" in e)
        fresh, stale = freshness_of([parse_ts(r["taken_at"]) for r in snaps.values()], self._clock())
        return Result(Status.STALE if stale else Status.OK, items, None, fresh, stale)

    @guarded()
    def uncovered_spend(self, scope: Scope, *, cancel: CancelToken | None = None):
        accounts = scope_accounts(scope, self._roster())
        if not accounts:
            return Result.not_connected(NO_ACCOUNTS)
        with self._store.reader() as db:
            snaps = self._latest_with(db, accounts, "cost")
            if not snaps:
                return Result.not_connected(BILLING_NOT_ENABLED)
            months = {s["taken_at"][:7] for s in snaps.values()}
            if len(months) > 1:
                return Result.error(DIFFERENT_MONTHS)
            month = next(iter(months))
            points = []
            for acct, s in snaps.items():
                for r in self._metric(db, s["id"], "cost.service.monthly"):
                    service = json.loads(r["dims"]).get("service", "")
                    reason = uncovered_reason(service)
                    if r["period"] == month and reason is not None:
                        points.append(CostPoint(month, Decimal(r["value"]), r["unit"],
                                                {"account": acct, "service": service, "reason": reason}))
        points.sort(key=lambda p: (-p.amount, p.dims["service"], p.dims["account"]))
        return self._finish(tuple(points), accounts, snaps)
