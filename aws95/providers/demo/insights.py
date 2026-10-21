from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

from aws95.contracts.activity import ActivityQuery
from aws95.contracts.common import Result, Scope
from aws95.contracts.insights import InsightKind, InsightReport, InsightSection, ModelChoice
from aws95.core.cancel import CancelToken, raise_if_cancelled
from aws95.providers.demo.store import DEMO_FRESHNESS, load

SEVERITIES = ("critical", "high", "medium", "low")


def _ok(data):
    return Result.ok(data, freshness=DEMO_FRESHNESS, is_demo=True)


class DemoInsights:
    """Aggregates only: the preview payload is counted from the other demo providers, never from ids or ARNs."""

    def __init__(self, resources=None, billing=None, findings=None, activity=None) -> None:
        self._resources, self._billing, self._findings, self._activity = resources, billing, findings, activity

    def _aggregates(self, scope: Scope) -> dict[str, Any]:
        types = self._resources.resource_types(scope).data
        counts = {t.rtype: t.count for t in types if t.count}
        regions = {r.region for t in types if t.count
                   for r in self._resources.list_resources(scope, t.rtype, page_size=10_000).data.items
                   if r.region != "global"}
        summary = self._billing.summary(scope).data
        open_ = self._findings.list_findings(scope, "open").data.items
        by_sev = Counter(f.severity for f in open_)
        return {"accounts_in_scope": len(self._billing.breakdown(scope, "account").data), "regions_in_scope": len(regions), "resource_counts": counts,
                "cost_scope": "accounts (all regions)",   # cost is account-wide; the counts are region-filtered
                "cost_mtd_usd": str(summary.mtd),
                "cost_last_month_usd": None if summary.last_month is None else str(summary.last_month),
                "open_findings_by_severity": {s: by_sev.get(s, 0) for s in SEVERITIES},
                "activity_events_30d": self._activity.events(scope).data.total,
                "risky_events_30d": self._activity.events(scope, ActivityQuery(risk_min=1)).data.total}

    def preview_payload(self, scope: Scope, kind: InsightKind = "insights", *, cancel: CancelToken | None = None):
        raise_if_cancelled(cancel)
        summary = self._billing.summary(scope)
        if summary.data is None:
            return Result.error(summary.message or "No demo billing for this scope.", is_demo=True)
        return _ok({"analysis": kind, **self._aggregates(scope)})

    def analyze(self, scope: Scope, kind: InsightKind = "insights", payload: Mapping[str, Any] | None = None, *,
                cancel: CancelToken | None = None):
        raise_if_cancelled(cancel)
        if payload is None:
            return Result.error("No reviewed payload was supplied.", is_demo=True)
        sections = tuple(InsightSection(s["title"], tuple(s["bullets"])) for s in load("insights.json")["reports"][kind])
        return _ok(InsightReport(kind=kind, model="demo (no model called)", sections=sections))

    def models(self, scope: Scope, account_id: str = "", *, cancel: CancelToken | None = None):
        raise_if_cancelled(cancel)
        return _ok((ModelChoice("demo", "Demo model (no call)"),))
