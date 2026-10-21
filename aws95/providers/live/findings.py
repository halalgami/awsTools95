"""LiveFindings: open / resolved / accepted findings, Accept and Un-accept (local mutes only), the 38 shipped rules,
and the rule x account fleet matrix - all read from the store. Accepting never writes to AWS (spec F3)."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone

from aws95.contracts.common import Page, Result, Scope, Status
from aws95.contracts.findings import Finding, FleetCell, Mute, RecheckOutcome, RuleInfo
from aws95.core.cancel import AnyToken, CancelToken
from aws95.core.jobs import JobLock
from aws95.core.sanitize import clean_model_text
from aws95.providers.live.common import (NO_ACCOUNTS, NO_SNAPSHOT, Clock, Roster, freshness_of, join_messages,
                                         latest_snapshots, marks, scope_accounts, utcnow)
from aws95.providers.live.guard import guarded
from aws95.store import Store, parse_ts, ts

MAX_REASON = 500
PAGE_SIZE = 500
UNVERIFIED_NOTE = "Not re-checked: the latest snapshot did not look here."


def _mute(row: dict | None) -> Mute | None:
    if row is None:
        return None
    return Mute(int(row["id"]), row["account"], row["rule"], row["rid"], row["reason"], parse_ts(row["muted_at"]),
                parse_ts(row["expires_at"]) if row["expires_at"] else None)


def _in_regions(region: str, scope: Scope) -> bool:
    return not scope.regions or region == "global" or region in scope.regions


class LiveFindings:
    def __init__(self, store: Store, roster: Roster, *, clock: Clock = utcnow, jobs: JobLock | None = None) -> None:
        self._store = store
        self._roster = roster
        self._clock = clock
        self._jobs = jobs or JobLock()

    @staticmethod
    def _finding(s, state: str, tuned: bool = False) -> Finding:
        detail = dict(s.detail or {})
        if s.unverified:
            detail["note"] = UNVERIFIED_NOTE
        return Finding(s.account, s.rule, s.rtype, s.region, s.rid, s.severity, parse_ts(s.first_seen), s.seeded,
                       state, s.age_days, detail, _mute(s.mute), tuned)

    @guarded()
    def list_findings(self, scope: Scope, state: str = "open", cursor: str | None = None, *,
                      cancel: CancelToken | None = None):
        from aws95.aws.engines.labsnap.analyze.findings import apply_mutes, open_findings, resolved_findings
        accounts = scope_accounts(scope, self._roster())
        if not accounts:
            return Result.not_connected(NO_ACCOUNTS)
        with self._store.reader() as db:
            latest = latest_snapshots(db, accounts)
        if not latest:
            return Result.not_connected(NO_SNAPSHOT)
        try:
            offset = int(cursor) if cursor else 0
        except ValueError:
            return Result.error("Bad page cursor.")
        now = self._clock()
        states = (resolved_findings if state == "resolved" else open_findings)(self._store, accounts, now=now)
        states = apply_mutes(self._store, states, now=now)
        if state == "open":
            states = [s for s in states if not s.muted]
        elif state == "accepted":
            states = [s for s in states if s.muted]
        states = [s for s in states if _in_regions(s.region, scope)]
        o = self._overrides()
        tuned = set(o.severity) | set(o.params)
        items = tuple(self._finding(s, state, s.rule in tuned) for s in states[offset:offset + PAGE_SIZE])
        nxt = str(offset + PAGE_SIZE) if offset + PAGE_SIZE < len(states) else None
        fresh, stale = freshness_of([parse_ts(r["taken_at"]) for r in latest.values()], now)
        missing = [a for a in accounts if a not in latest]
        unverified = sum(1 for s in states if s.unverified)
        gap = f"No snapshot yet for {', '.join(missing)}." if missing else None
        note = f"{unverified} not re-checked in the latest snapshot." if unverified else None
        status = Status.STALE if stale else Status.PARTIAL if (missing or unverified) else Status.OK
        return Result(status, Page(items, nxt, len(states)), None, fresh, join_messages(stale, gap, note))

    @guarded()
    def accept(self, scope: Scope, finding: Finding | None = None, target: str = "resource", reason: str = "",
               expires_at: datetime | None = None, *, cancel: CancelToken | None = None):
        if finding is None:
            return Result.error("Select a finding to accept.")
        text = clean_model_text(reason or "").strip()
        if not text:
            return Result.error("Give a reason for accepting this finding.")
        if len(text) > MAX_REASON:
            return Result.error(f"Keep the reason under {MAX_REASON} characters.")
        now = parse_ts(ts(self._clock()))
        if expires_at is not None and (expires_at.tzinfo is None or expires_at <= now):
            return Result.error("The expiry date must be in the future.")
        if target == "resource":
            account, rid = finding.account, finding.rid
        elif target == "account":
            account, rid = finding.account, None
        elif target == "rule":
            account, rid = None, None
        else:
            return Result.error(f"Unknown accept target {target!r}.")
        mute_id = self._store.add_mute(account, finding.rule, rid, text, expires_at, now=now)
        return Result.ok(Mute(mute_id, account, finding.rule, rid, text, now, expires_at))

    @guarded()
    def unaccept(self, scope: Scope, mute_id: int = 0, *, cancel: CancelToken | None = None):
        if not self._store.remove_mute(int(mute_id)):
            return Result.error("That acceptance no longer exists.")
        return Result.ok(None)

    @guarded()
    def rules(self, scope: Scope, *, cancel: CancelToken | None = None):
        from aws95.aws.engines.labsnap.rules.base import shipped_rules
        o = self._overrides()
        out = []
        for rule_id, cls in sorted(shipped_rules().items()):
            shipped = dict(getattr(cls, "params", {}) or {})
            params = {**shipped, **o.params.get(rule_id, {})}
            tuned = rule_id in o.disabled or rule_id in o.severity or rule_id in o.params
            out.append(RuleInfo(rule_id, cls.title, cls.description, o.severity.get(rule_id, cls.severity), params,
                                "built-in", rule_id not in o.disabled, tuned, cls.severity, shipped))
        return Result.ok(tuple(out))

    @guarded()
    def fleet(self, scope: Scope, *, cancel: CancelToken | None = None):
        from aws95.aws.engines.labsnap.analyze.findings import apply_mutes, open_findings
        from aws95.aws.engines.labsnap.rules.base import load_rules
        from aws95.aws.engines.labsnap.rules.sources import RULE_COLLECTORS
        accounts = scope_accounts(scope, self._roster())
        if not accounts:
            return Result.not_connected(NO_ACCOUNTS)
        now = self._clock()
        with self._store.reader() as db:
            latest = latest_snapshots(db, accounts)
            ok_runs = {acct: {r[0] for r in db.execute(
                "SELECT collector FROM collector_runs WHERE snapshot_id=? AND status='ok'", (row["id"],))}
                for acct, row in latest.items()}
        states = [s for s in apply_mutes(self._store, open_findings(self._store, accounts, now=now), now=now)
                  if not s.muted and not s.unverified and _in_regions(s.region, scope)]
        counts = Counter((s.rule, s.account) for s in states)
        cells = []
        for rule in load_rules(self._overrides()):
            sources = RULE_COLLECTORS.get(rule.id, ())
            for acct in accounts:
                collected = acct in latest and all(c in ok_runs[acct] for c in sources)
                cells.append(FleetCell(rule.id, acct, counts.get((rule.id, acct), 0) if collected else 0, collected))
        fresh, stale = freshness_of([parse_ts(r["taken_at"]) for r in latest.values()], now)
        return Result(Status.STALE if stale else Status.OK, tuple(cells), None, fresh, stale)

    def busy_text(self) -> str | None:
        """For Clear Local Data: a running backfill must finish before the store is deleted."""
        return self._jobs.busy_message() if self._jobs.holder == "backfill" else None

    def _overrides(self):
        from aws95.aws.engines.labsnap.rules.base import RuleOverrides
        return RuleOverrides.from_store(self._store)

    def _open_count(self, accounts) -> int:
        from aws95.aws.engines.labsnap.analyze.findings import apply_mutes, open_findings
        return sum(1 for s in apply_mutes(self._store, open_findings(self._store, accounts, now=self._clock()),
                                          now=self._clock()) if not s.muted)

    def _recheck_latest(self, accounts) -> Result:
        from aws95.aws.engines.labsnap.analyze.findings import recheck
        with self._store.reader() as db:
            ids = [r["id"] for r in latest_snapshots(db, accounts).values()]
        done = recheck(self._store, ids)
        out = RecheckOutcome(done, len(ids), self._open_count(accounts))
        return Result.ok(out, message=out.text())

    @guarded()
    def set_rule(self, scope: Scope, rule_id: str = "", *, enabled: bool = True, severity: str | None = None,
                 params=None, cancel: CancelToken | None = None):
        from aws95.aws.engines.labsnap.rules.base import shipped_rules, validate_params
        cls = shipped_rules().get(rule_id)
        if cls is None:
            return Result.error(f"Unknown rule {rule_id!r}.")
        if severity is not None and severity not in ("critical", "high", "medium", "low"):
            return Result.error("Severity must be critical, high, medium or low.")
        try:
            checked = validate_params(rule_id, params or {})
        except ValueError as exc:
            return Result.error(str(exc))
        shipped = dict(getattr(cls, "params", {}) or {})
        changed = {k: v for k, v in checked.items() if shipped.get(k) != v}
        sev = severity if severity and severity != cls.severity else None
        if not self._jobs.try_acquire("recheck"):
            return Result.error(self._jobs.busy_message())
        try:
            if enabled and sev is None and not changed:
                self._store.clear_rule_override(rule_id)
            else:
                self._store.set_rule_override(rule_id, disabled=not enabled, severity=sev, params=changed or None,
                                              changed_at=self._clock())
            return self._recheck_latest(scope_accounts(scope, self._roster()))
        finally:
            self._jobs.release()

    @guarded()
    def reset_rule(self, scope: Scope, rule_id: str = "", *, cancel: CancelToken | None = None):
        if not self._jobs.try_acquire("recheck"):
            return Result.error(self._jobs.busy_message())
        try:
            self._store.clear_rule_override(rule_id)
            return self._recheck_latest(scope_accounts(scope, self._roster()))
        finally:
            self._jobs.release()

    @guarded()
    def seed(self, scope: Scope, finding: Finding | None = None, first_seen: date | None = None, note: str = "", *,
             cancel: CancelToken | None = None):
        if finding is None or first_seen is None:
            return Result.error("Select a finding and a date.")
        if not finding.seeded and first_seen >= finding.first_seen.date():
            return Result.error(f"The date must be before {finding.first_seen.date().isoformat()}, "
                                "when AWS 95 first saw this finding.")
        text = clean_model_text(note or "").strip()
        if len(text) > MAX_REASON:
            return Result.error(f"Keep the note under {MAX_REASON} characters.")
        when = datetime(first_seen.year, first_seen.month, first_seen.day, tzinfo=timezone.utc)
        self._store.seed_finding(finding.account, finding.rule, finding.rid, when, text)
        return Result.ok(None)

    @guarded()
    def unseed(self, scope: Scope, finding: Finding | None = None, *, cancel: CancelToken | None = None):
        if finding is None or not self._store.unseed_finding(finding.account, finding.rule, finding.rid):
            return Result.error("This finding isn't seeded.")
        return Result.ok(None)

    @guarded()
    def backfill(self, scope: Scope, since: date | None = None, *, progress=None, stop: CancelToken | None = None,
                 cancel: CancelToken | None = None):
        from aws95.aws.engines.labsnap.analyze.findings import recheck
        if since is None:
            return Result.error("Choose a start date.")
        accounts = scope_accounts(scope, self._roster())
        if not accounts:
            return Result.not_connected(NO_ACCOUNTS)
        if not self._jobs.try_acquire("backfill"):
            return Result.error(self._jobs.busy_message())
        try:
            start = ts(datetime(since.year, since.month, since.day, tzinfo=timezone.utc))
            with self._store.reader() as db:
                ids = [r["id"] for r in db.execute(
                    f"SELECT id FROM snapshots WHERE account IN ({marks(len(accounts))}) AND taken_at >= ? "
                    "ORDER BY taken_at, id", (*accounts, start))]
            done = recheck(self._store, ids, cancel=AnyToken(stop, cancel), progress=progress) if ids else 0
            out = RecheckOutcome(done, len(ids), self._open_count(accounts), cancelled=done < len(ids))
            return Result.ok(out, message=out.text(since) if ids else
                             f"No stored snapshots since {since.isoformat()}.")
        finally:
            self._jobs.release()
