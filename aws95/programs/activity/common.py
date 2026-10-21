"""Activity helpers shared by the tabs: ranges, event rows, paged fetch."""
from __future__ import annotations

from datetime import timedelta
from functools import partial

from aws95.contracts.activity import ActivityEvent, ActivityQuery
from aws95.contracts.common import Result, Scope
from aws95.core.cancel import CancelToken
from aws95.kit.widgets.datagrid import Column
from aws95.programs.common.paging import fetch_all_pages

RANGES = (("24h", "Last 24 hours", timedelta(hours=24)), ("7d", "Last 7 days", timedelta(days=7)),
          ("30d", "Last 30 days", timedelta(days=30)), ("90d", "Last 90 days", timedelta(days=90)),
          ("custom", "Custom…", None))
EVENT_COLUMNS = [Column("time", "Time (UTC)", "date", -1), Column("risk", "Risk", "number"), Column("rule", "Rule"),
                 Column("principal", "Principal"), Column("human", "Human"), Column("action", "Action"),
                 Column("service", "Service"), Column("account", "Account"), Column("region", "Region"),
                 Column("ip", "Source IP"), Column("event_id", "Event ID")]


def event_row(e: ActivityEvent) -> dict:
    return {"time": e.time_utc, "risk": e.risk_level, "rule": e.risk_rule or "", "principal": e.principal_name,
            "human": "Yes" if e.is_human else "", "action": e.name, "service": e.source, "account": e.account,
            "region": e.region, "ip": e.source_ip or "", "event_id": e.event_id, "event": e}


def fetch_all_events(provider, scope: Scope, query: ActivityQuery | None = None, *,
                     cancel: CancelToken | None = None, limit: int = 50_000) -> Result:
    """Follow the provider's cursors through the shared pager (M3-R9)."""
    return fetch_all_pages(partial(provider.events, scope, query, cancel=cancel), cancel=cancel, limit=limit)
