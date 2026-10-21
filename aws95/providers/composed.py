"""Reports and Search built from the other providers (2d spec §3, §4). Qt-free; demo and live share it, so a report
always matches what the windows show. No AWS calls: every source reads the local store (live) or fixtures (demo)."""
from __future__ import annotations

import csv
import io
import shlex
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from functools import partial

from aws95.contracts.activity import ActivityQuery
from aws95.contracts.common import Result, Scope, Status
from aws95.contracts.paging import fetch_all_pages
from aws95.contracts.reports import RenderedReport, ReportType
from aws95.contracts.search import SearchHit
from aws95.core.cancel import CancelToken, raise_if_cancelled

REPORT_TYPES = (ReportType("inventory", "Resource inventory", ("md", "csv")),
                ReportType("cost", "Cost summary", ("md", "csv")),
                ReportType("findings", "Open findings", ("md", "csv")),
                ReportType("activity", "Activity watchlist", ("md", "csv")))
COST_NOTE = "Figures are copied from AWS Cost Explorer as stored; nothing is added or rounded."
MISSING = "Some data is missing — see Coverage."
_USABLE = (Status.OK, Status.PARTIAL, Status.STALE)
_PAGE = 1000


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")                 # exact, never scientific
    return str(value)


def _md_cell(value: object) -> str:
    return _text(value).replace("|", "\\|").replace("\n", " ")


def table(title: str, header: Sequence[str], rows: Sequence[Sequence[object]]) -> tuple[str, str]:
    """The table part of a report: Markdown (without the title) and CSV."""
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(_md_cell(v) for v in row) + " |" for row in rows]
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    writer.writerows([[_text(v) for v in row] for row in rows])
    return "\n".join(lines) + "\n", buf.getvalue()


@dataclass
class _Built:
    header: tuple[str, ...]
    rows: list[tuple]
    sources: list[tuple[str, Result]]            # the first is the report's main source
    notes: list[str] = field(default_factory=list)
    stages: bool = True                          # snapshot-based: list each account's stage


def _status_line(label: str, r: Result) -> str:
    return f"{label}: {r.status.value.replace('_', ' ')}" + (f" — {r.message}" if r.message else "")


class ComposedReports:
    def __init__(self, resources, billing, findings, activity, snapshots) -> None:
        self._resources, self._billing, self._findings = resources, billing, findings
        self._activity, self._snapshots = activity, snapshots
        self._builders: dict[str, Callable[[Scope, CancelToken | None], _Built]] = {
            "inventory": self._inventory, "cost": self._cost, "findings": self._open_findings,
            "activity": self._watchlist}

    def report_types(self, scope: Scope, *, cancel: CancelToken | None = None):
        raise_if_cancelled(cancel)
        probe = self._resources.resource_types(scope, cancel=cancel)       # only for demo marking and freshness
        return Result.ok(REPORT_TYPES, freshness=probe.freshness, is_demo=probe.is_demo)

    def render(self, scope: Scope, report_id: str = "", *, cancel: CancelToken | None = None):
        raise_if_cancelled(cancel)
        build = self._builders.get(report_id)
        if build is None:
            return Result.error(f"Unknown report {report_id!r}.")
        built = build(scope, cancel)
        main = built.sources[0][1]
        demo = any(r.is_demo for _, r in built.sources)
        if main.status not in _USABLE or main.data is None:
            return Result(main.status, None, None, None, main.message, demo)
        for label, r in built.sources[1:]:
            if r.status is Status.ERROR:
                return Result(Status.ERROR, None, None, None, f"{label}: {r.message}", demo)
        coverage, degraded = [], False
        if built.stages:
            stages = self._snapshots.stages(scope, cancel=cancel)
            demo = demo or stages.is_demo
            for st in (stages.data or ()) if stages.status is Status.OK else ():
                coverage.append(st.text(st.account))
                degraded |= st.snapshot_id is None or st.done < st.total
        for label, r in built.sources:
            if r.status is not Status.OK:
                coverage.append(_status_line(label, r))
                degraded = True
        coverage += built.notes
        title = next(t.title for t in REPORT_TYPES if t.id == report_id)
        body, csv_text = table(title, built.header, built.rows)
        bullets = "\n".join(f"- {line}" for line in coverage) or "- Everything in scope was collected."
        markdown = f"# {title}\n\n## Coverage\n\n{bullets}\n\n{body}"
        return Result(Status.PARTIAL if degraded else Status.OK, RenderedReport(title, markdown, csv_text), None,
                      main.freshness, MISSING if degraded else None, demo)

    # ----- the four reports -----
    def _inventory(self, scope: Scope, cancel) -> _Built:
        types = self._resources.resource_types(scope, cancel=cancel)
        sources: list[tuple[str, Result]] = [("Resources", types)]
        rows: list[tuple] = []
        if types.status in _USABLE and types.data is not None:
            for t in types.data:
                raise_if_cancelled(cancel)
                page = fetch_all_pages(partial(self._resources.list_resources, scope, t.rtype, page_size=_PAGE,
                                               cancel=cancel), cancel=cancel)
                if page.status is not Status.OK:
                    sources.append((f"Resources ({t.label})", page))
                rows += [(r.account, r.rtype, r.region, r.name, r.rid, r.state) for r in (page.data or ())]
        return _Built(("account", "type", "region", "name", "id", "state"), rows, sources)

    def _cost(self, scope: Scope, cancel) -> _Built:
        breakdown = self._billing.breakdown(scope, "service", cancel=cancel)
        summary = self._billing.summary(scope, cancel=cancel)
        rows = [(p.dims.get("service", ""), p.period, p.amount, p.unit) for p in (breakdown.data or ())]
        s = summary.data if summary.status in _USABLE else None
        if s is not None:
            rows.append(("Month to date (provider total)", s.as_of.date().isoformat(), s.mtd, s.currency))
            if s.forecast is not None:
                rows.append(("Forecast (provider)", "", s.forecast, s.currency))
            if s.last_month is not None:
                rows.append(("Last month (provider)", "", s.last_month, s.currency))
        return _Built(("service", "period", "amount", "unit"), rows, [("Billing", breakdown), ("Billing summary", summary)],
                      [COST_NOTE])

    def _open_findings(self, scope: Scope, cancel) -> _Built:
        found = fetch_all_pages(partial(self._findings.list_findings, scope, "open", cancel=cancel), cancel=cancel)
        rows = [(f.severity, f.rule, f.account, f.region, f.rid, f.age_days) for f in (found.data or ())]
        return _Built(("severity", "rule", "account", "region", "resource", "age_days"), rows, [("Findings", found)])

    def _watchlist(self, scope: Scope, cancel) -> _Built:
        events = fetch_all_pages(partial(self._activity.events, scope, ActivityQuery(risk_min=1), cancel=cancel),
                                 cancel=cancel)
        rows = [(e.time_utc.isoformat(), e.risk_level, e.risk_rule, e.principal_name, e.name, e.account, e.region)
                for e in (events.data or ())]
        return _Built(("time", "risk", "rule", "principal", "action", "account", "region"), rows,
                      [("Activity", events)], stages=False)


# ----- Search (2d spec §4) -----
PER_KIND = 8
MIN_CHARS = 2
ResourceHits = Callable[[Scope, str, CancelToken | None], list[SearchHit]]


def resource_hit(account: str, rtype: str, region: str, rid: str, name: str | None = None) -> SearchHit:
    return SearchHit("resource", name or rid, f"{rtype} · {region} · {account}", "resources",
                     {"rtype": rtype, "filter": f"rid:{shlex.quote(rid)}"})


class ComposedSearch:
    def __init__(self, resources, findings, activity, billing, *, resource_hits: ResourceHits | None = None) -> None:
        self._resources, self._findings, self._activity, self._billing = resources, findings, activity, billing
        self._resource_hits = resource_hits or self._scan_resources

    def search(self, scope: Scope, text: str = "", limit: int = 50, *, cancel: CancelToken | None = None):
        q = (text or "").strip()
        demo = bool(self._resources.resource_types(scope, cancel=cancel).is_demo)     # marks demo hits [DEMO]
        if len(q) < MIN_CHARS:
            return Result.ok((), is_demo=demo)
        out: list[SearchHit] = []
        for find in (self._resource_hits, self._finding_hits, self._principal_hits, self._service_hits):
            raise_if_cancelled(cancel)
            out += find(scope, q, cancel)[:PER_KIND]
        return Result.ok(tuple(out[:max(0, limit)]), is_demo=demo)

    def _scan_resources(self, scope: Scope, q: str, cancel) -> list[SearchHit]:
        types = self._resources.resource_types(scope, cancel=cancel)
        if types.status not in _USABLE or types.data is None:
            return []
        needle, found = q.lower(), []
        for t in types.data:
            page = fetch_all_pages(partial(self._resources.list_resources, scope, t.rtype, page_size=_PAGE,
                                           cancel=cancel), cancel=cancel)
            for r in page.data or ():
                if needle in r.rid.lower() or needle in (r.name or "").lower():
                    found.append(resource_hit(r.account, r.rtype, r.region, r.rid, r.name))
                    if len(found) >= PER_KIND:
                        return found
        return found

    def _finding_hits(self, scope: Scope, q: str, cancel) -> list[SearchHit]:
        page = fetch_all_pages(partial(self._findings.list_findings, scope, "open", cancel=cancel), cancel=cancel)
        needle = q.lower()
        return [SearchHit("finding", f.rule, f"{f.rid} · {f.account}", "findings",
                          {"rule": f.rule, "account": f.account, "target": f"{f.rtype}:{f.rid}"})
                for f in (page.data or ()) if needle in f.rule.lower() or needle in f.rid.lower()][:PER_KIND]

    def _principal_hits(self, scope: Scope, q: str, cancel) -> list[SearchHit]:
        r = self._activity.principals(scope, cancel=cancel)
        if r.status not in _USABLE or r.data is None:
            return []
        needle = q.lower()
        return [SearchHit("principal", p.principal_name, f"{p.principal_type} · {p.count} events", "activity",
                          {"principal": p.principal_name})
                for p in r.data if needle in p.principal_name.lower()][:PER_KIND]

    def _service_hits(self, scope: Scope, q: str, cancel) -> list[SearchHit]:
        r = self._billing.breakdown(scope, "service", cancel=cancel)
        if r.status not in _USABLE or r.data is None:
            return []
        needle, seen, out = q.lower(), set(), []
        for p in r.data:
            service = p.dims.get("service", "")
            if service and needle in service.lower() and service not in seen:
                seen.add(service)
                out.append(SearchHit("service", service, f"Billing · {p.period}", "billing", {"service": service}))
        return out[:PER_KIND]
