from __future__ import annotations

from dataclasses import replace

from aws95.contracts.common import Page, Result, Scope
from aws95.contracts.findings import Finding, FindingState, FleetCell, RuleInfo
from aws95.core.cancel import CancelToken, raise_if_cancelled
from aws95.providers.demo.builder import build
from aws95.providers.demo.store import DEMO_FRESHNESS, in_scope, load

DEMO_ACCOUNTS = ("111111111111", "222222222222")


def _ok(data):
    return Result.ok(data, freshness=DEMO_FRESHNESS, is_demo=True)


class DemoFindings:
    def _all(self) -> tuple[Finding, ...]:
        return tuple(build(Finding, f) for f in load("findings.json")["findings"])

    def list_findings(self, scope: Scope, state: FindingState = "open", cursor: str | None = None, *,
                      cancel: CancelToken | None = None):
        raise_if_cancelled(cancel)
        items = tuple(f for f in self._all() if f.state == state and in_scope(f.account, f.region, scope))
        return _ok(Page(items=items, next_cursor=None, total=len(items)))

    def rules(self, scope: Scope, *, cancel: CancelToken | None = None):
        raise_if_cancelled(cancel)
        rules = (build(RuleInfo, r) for r in load("findings.json")["rules"])
        # Demo rules run as shipped: their defaults are their own values (the Rules editor shows "Shipped").
        return _ok(tuple(replace(r, default_severity=r.severity, default_params=dict(r.params)) for r in rules))

    def fleet(self, scope: Scope, *, cancel: CancelToken | None = None):
        raise_if_cancelled(cancel)
        data = load("findings.json")
        uncollected = {(a, r) for a, r in data["uncollected"]}
        accounts = [a for a in DEMO_ACCOUNTS if not scope.accounts or a in scope.accounts]
        open_ = [f for f in self._all() if f.state == "open" and in_scope(f.account, f.region, scope)]
        cells = []
        for rule in data["rules"]:
            for acct in accounts:
                count = sum(1 for f in open_ if f.rule == rule["id"] and f.account == acct)
                cells.append(FleetCell(rule=rule["id"], account=acct, open_count=count,
                                       collected=(acct, rule["id"]) not in uncollected))
        return _ok(tuple(cells))

    def accept(self, scope: Scope, finding=None, target="resource", reason: str = "", expires_at=None, *,
               cancel: CancelToken | None = None):
        return Result.not_connected("Accepting findings isn't available in demo mode.", is_demo=True)

    def unaccept(self, scope: Scope, mute_id: int = 0, *, cancel: CancelToken | None = None):
        return Result.not_connected("Accepting findings isn't available in demo mode.", is_demo=True)

    # Findings tools (findings-tools spec §2): demo never writes.
    def set_rule(self, scope: Scope, rule_id: str = "", *, enabled=True, severity=None, params=None, cancel=None):
        return Result.not_connected("Changing rules isn't available in demo mode.", is_demo=True)

    def reset_rule(self, scope: Scope, rule_id: str = "", *, cancel=None):
        return Result.not_connected("Changing rules isn't available in demo mode.", is_demo=True)

    def seed(self, scope: Scope, finding=None, first_seen=None, note: str = "", *, cancel=None):
        return Result.not_connected("Seeding findings isn't available in demo mode.", is_demo=True)

    def unseed(self, scope: Scope, finding=None, *, cancel=None):
        return Result.not_connected("Seeding findings isn't available in demo mode.", is_demo=True)

    def backfill(self, scope: Scope, since=None, *, progress=None, stop=None, cancel=None):
        return Result.not_connected("Backfill isn't available in demo mode.", is_demo=True)
