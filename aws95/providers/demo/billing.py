from __future__ import annotations

from aws95.contracts.billing import CostDimension, CostPoint, CostSummary, FreeTierUsage, Granularity
from aws95.contracts.common import Result, Scope
from aws95.contracts.service_map import uncovered_reason
from aws95.core.cancel import CancelToken, raise_if_cancelled
from aws95.providers.demo.builder import build
from aws95.providers.demo.store import DEMO_FRESHNESS, load

DEMO_ACCOUNTS = ("111111111111", "222222222222")


def _ok(data):
    return Result.ok(data, freshness=DEMO_FRESHNESS, is_demo=True)


def _points(rows) -> tuple[CostPoint, ...]:
    return tuple(build(CostPoint, r) for r in rows)


def _selected(scope: Scope) -> tuple[str, ...]:
    """The demo accounts the scope names (all of them for an empty scope); unknown ids are ignored."""
    if not scope.accounts:
        return DEMO_ACCOUNTS
    return tuple(a for a in DEMO_ACCOUNTS if a in scope.accounts)


def _slice(scope: Scope, whole, per_account):
    """The pre-summed whole when every demo account is in scope, else that one account's generator-made slice.

    Slices are never added here: the generator wrote every figure and proved the sums (no arithmetic in a view)."""
    chosen = _selected(scope)
    if chosen == DEMO_ACCOUNTS:
        return whole()
    if len(chosen) == 1:
        return per_account(load("billing.json")["by_account"][chosen[0]])
    return None


def _for_account(p: CostPoint, acct: str, **extra: str) -> CostPoint:
    return CostPoint(p.period, p.amount, p.unit, {**p.dims, "account": acct, **extra})


def _per_account(chosen, dimension: str) -> tuple[CostPoint, ...]:
    """Each chosen demo account's own generator-made rows, labelled with the account (never summed here)."""
    rows = []
    for acct in chosen:
        rows += [_for_account(p, acct) for p in _points(load("billing.json")["by_account"][acct]["breakdown"][dimension])]
    return tuple(sorted(rows, key=lambda p: (-p.amount, p.dims.get(dimension, ""), p.dims["account"])))


def _no_such_account(scope: Scope) -> Result | None:
    """The same honest demo error for every method when the scope names accounts but none is a demo account."""
    if not _selected(scope):
        return Result.error("The demo data has no billing for that account.", is_demo=True)
    return None


class DemoBilling:
    def summary(self, scope: Scope, *, cancel: CancelToken | None = None):
        raise_if_cancelled(cancel)
        if (err := _no_such_account(scope)) is not None:
            return err
        data = _slice(scope, lambda: load("billing.json")["summary"], lambda acct: acct["summary"])
        return _ok(build(CostSummary, data))

    def breakdown(self, scope: Scope, dimension: CostDimension = "service", *, cancel: CancelToken | None = None):
        raise_if_cancelled(cancel)
        if (err := _no_such_account(scope)) is not None:
            return err
        chosen = _selected(scope)
        if dimension == "account":
            return _ok(tuple(p for p in _points(load("billing.json")["breakdown"]["account"])
                             if p.dims.get("account") in chosen))
        if dimension in ("region", "usage_type"):
            return _ok(_per_account(chosen, dimension))
        rows = _slice(scope, lambda: load("billing.json")["breakdown"][dimension],
                      lambda acct: acct["breakdown"][dimension])
        return _ok(_points(rows or ()))

    def trend(self, scope: Scope, granularity: Granularity = "monthly", *, cancel: CancelToken | None = None):
        raise_if_cancelled(cancel)
        if (err := _no_such_account(scope)) is not None:
            return err
        key = "monthly" if granularity == "monthly" else "daily"
        rows = _slice(scope, lambda: load("billing.json")[key], lambda acct: acct[key])
        return _ok(_points(rows or ()))

    def free_tier(self, scope: Scope, *, cancel: CancelToken | None = None):
        raise_if_cancelled(cancel)
        if (err := _no_such_account(scope)) is not None:
            return err
        return _ok(tuple(build(FreeTierUsage, r) for r in load("billing.json")["free_tier"]))

    def uncovered_spend(self, scope: Scope, *, cancel: CancelToken | None = None):
        raise_if_cancelled(cancel)
        if (err := _no_such_account(scope)) is not None:
            return err
        rows = []
        for acct in _selected(scope):
            for p in _points(load("billing.json")["by_account"][acct]["breakdown"]["service"]):
                reason = uncovered_reason(p.dims.get("service", ""))
                if reason is not None:
                    rows.append(_for_account(p, acct, reason=reason))
        return _ok(tuple(sorted(rows, key=lambda p: (-p.amount, p.dims["service"], p.dims["account"]))))
