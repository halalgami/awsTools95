"""LiveInsights (ai-insights spec §4–§6): a per-account aggregate preview from the local store, and on Analyze one
Bedrock Converse call per account in that account, with exactly the reviewed entry."""
from __future__ import annotations

from collections import Counter
from datetime import timedelta
from functools import partial

from aws95.contracts.activity import ActivityQuery
from aws95.contracts.common import Result, Scope, Status, TimeRange
from aws95.contracts.insights import AccountInsight, InsightReport, ModelChoice
from aws95.contracts.paging import fetch_all_pages
from aws95.core.cancel import CancelToken, raise_if_cancelled
from aws95.providers.insights_payload import TOP_SERVICES, build_payload, scrub_check
from aws95.providers.live.common import NO_ACCOUNTS, Clock, Roster, scope_accounts, utcnow
from aws95.providers.live.guard import guarded

SEVERITIES = ("critical", "high", "medium", "low")
CHANGED = "The account list changed. Review the payload again."
_USABLE = (Status.OK, Status.PARTIAL, Status.STALE)


class LiveInsights:
    def __init__(self, resources, findings, billing, activity, snapshots, roster: Roster, *, model_for, region_for,
                 session_factory=None, clock: Clock = utcnow) -> None:
        self._resources, self._findings, self._billing = resources, findings, billing
        self._activity, self._snapshots, self._roster = activity, snapshots, roster
        self._model_for, self._region_for, self._clock = model_for, region_for, clock
        self._session_factory = session_factory

    def _session(self, account: str):
        profile = next(e.profile for e in self._roster() if e.account_id == account)
        if self._session_factory is not None:
            return self._session_factory(profile)
        from aws95.aws.session import session_for
        return session_for(profile)

    def _entry(self, account: str, cancel) -> dict:
        one = Scope(accounts=(account,))
        notes: list[str] = []
        types = self._resources.resource_types(one, cancel=cancel)
        counts = {t.rtype: t.count for t in (types.data or ()) if t.count} if types.status in _USABLE else {}
        if types.status not in _USABLE:
            notes.append("No snapshot yet for this account.")
        snaps = self._snapshots.list_snapshots(one, cancel=cancel)
        regions = len(snaps.data[0].regions) if snaps.status in _USABLE and snaps.data else 0
        found = fetch_all_pages(partial(self._findings.list_findings, one, "open", cancel=cancel), cancel=cancel)
        items = found.data or ()
        by_sev = Counter(f.severity for f in items)
        summary = self._billing.summary(one, cancel=cancel)
        services = self._billing.breakdown(one, "service", cancel=cancel)
        s = summary.data if summary.status in _USABLE else None
        if s is None:
            notes.append("Billing is off for this account.")
        now = self._clock()
        month = Scope(accounts=(account,), time_range=TimeRange(now - timedelta(days=30), now))
        events = self._activity.events(month, ActivityQuery(), cancel=cancel)
        risky = fetch_all_pages(partial(self._activity.events, month, ActivityQuery(risk_min=1), cancel=cancel),
                                cancel=cancel, limit=5000)
        if events.status not in _USABLE:
            notes.append("No Activity history is loaded for this account.")
        return {
            "regions_in_scope": regions, "resource_counts": counts,
            "open_findings_by_severity": {k: by_sev.get(k, 0) for k in SEVERITIES},
            "open_findings_by_rule": dict(sorted(Counter(f.rule for f in items).items())),
            "cost_currency": s.currency if s else None,
            "cost_mtd": format(s.mtd, "f") if s else None,
            "cost_last_month": format(s.last_month, "f") if s and s.last_month is not None else None,
            "top_services_mtd": [[p.dims.get("service", ""), format(p.amount, "f")]
                                 for p in (services.data or ())[:TOP_SERVICES]] if services.status in _USABLE else [],
            "activity_events_30d": events.data.total if events.status in _USABLE and events.data else None,
            "risky_events_30d": len(risky.data or ()) if risky.status in _USABLE else None,
            "risky_events_by_rule": dict(sorted(Counter(e.risk_rule or "unknown" for e in (risky.data or ())).items())),
            "notes": notes}

    def _forbidden(self) -> set[str]:
        return {w for e in self._roster() for w in (e.account_id, e.profile)}

    @guarded()
    def preview_payload(self, scope: Scope, kind: str = "insights", *, cancel: CancelToken | None = None):
        accounts = scope_accounts(scope, self._roster())
        if not accounts:
            return Result.not_connected(NO_ACCOUNTS)
        payload = build_payload(kind, [self._entry(a, cancel) for a in accounts])
        problem = scrub_check(payload, self._forbidden())
        if problem:
            return Result.error(problem)
        return Result.ok(payload)

    @guarded()
    def models(self, scope: Scope, account_id: str = "", *, cancel: CancelToken | None = None):
        from aws95.aws.insights import bedrock
        try:
            found = bedrock.list_models(self._session(account_id), self._region_for(account_id), cancel=cancel)
        except Exception as exc:  # noqa: BLE001 - explained, never raised
            return Result.error(bedrock.explain_error(exc, "Bedrock"))
        return Result.ok(tuple(ModelChoice(m, n) for m, n in found))

    @guarded()
    def analyze(self, scope: Scope, kind: str = "insights", payload=None, *, cancel: CancelToken | None = None):
        from aws95.aws.insights import bedrock
        from aws95.aws.insights.prompts import SYSTEM_PROMPTS
        if payload is None or "accounts" not in payload:
            return Result.error("No reviewed payload was supplied.")
        accounts = scope_accounts(scope, self._roster())
        entries = list(payload["accounts"])
        if len(entries) != len(accounts):
            return Result.error(CHANGED)
        out, failed = [], 0
        for account, entry in zip(accounts, entries):
            raise_if_cancelled(cancel)
            model = self._model_for(account)
            if not model:
                out.append(AccountInsight(entry["label"], "", error=f"Choose a model for {entry['label']}."))
                failed += 1
                continue
            try:
                answer = bedrock.converse(self._session(account), self._region_for(account), model,
                                          SYSTEM_PROMPTS[kind], entry, cancel=cancel)
            except Exception as exc:  # noqa: BLE001 - explained per account; the others carry on
                out.append(AccountInsight(entry["label"], model, error=bedrock.explain_error(exc, model)))
                failed += 1
                continue
            out.append(AccountInsight(entry["label"], model, bedrock.parse_sections(answer["text"]),
                                      answer["input_tokens"], answer["output_tokens"]))
        report = InsightReport(kind=kind, model=", ".join(sorted({a.model for a in out if a.model})) or "none",
                               sections=(), accounts=tuple(out))
        if failed == len(out):
            return Result(Status.ERROR, report, None, None, "No account was analysed. See each account's note.")
        return Result(Status.PARTIAL if failed else Status.OK, report, None, None,
                      "Some accounts weren't analysed. See each account's note." if failed else None)
