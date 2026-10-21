# Upstream: labsnap/labsnap/analyze/findings.py @ 1b1d437 — adapted for aws95; owned here from now on.
"""Findings lifecycle, derived from stored snapshots (never a separate state table).

A finding is open while it is present in its account's latest snapshot. It is resolved only when it is absent AND the
rule's collectors looked there (RULE_COLLECTORS + collector_runs). Absent where nobody looked stays open, marked
`unverified`: "not looked at" must never read as "fixed"."""
from __future__ import annotations

import json
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

import aws95.aws.engines.labsnap.collect.collectors  # noqa: F401 - registers collectors (their scopes)
from aws95.aws.engines.labsnap.collect.base import all_collectors
from aws95.aws.engines.labsnap.rules.sources import RULE_COLLECTORS
from aws95.aws.engines.labsnap.rules.view import SnapshotView
from aws95.core.cancel import CancelledError, raise_if_cancelled
from aws95.store import parse_ts

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def evaluate_snapshot(store, snapshot_id: int, rules: Sequence) -> int:
    """Run every rule over one stored snapshot and persist the findings. Safe to re-run."""
    view = SnapshotView(store, snapshot_id)
    findings = []
    for rule in rules:
        findings.extend(rule.evaluate(view, rule.params))
    store.write_findings(snapshot_id, findings, {r.id for r in rules})
    return len(findings)


def recheck(store, snapshot_ids: Sequence[int], *, cancel=None,
            progress: Callable[[int, int], None] | None = None) -> int:
    """Re-evaluate stored snapshots with today's tuned rules (findings-tools spec §4). Every known rule id is passed
    as evaluated, so a disabled rule's stored findings are cleared. Returns how many snapshots finished."""
    from aws95.aws.engines.labsnap.rules.base import RuleOverrides, load_rules, shipped_rules
    rules = load_rules(RuleOverrides.from_store(store))
    known = set(shipped_rules())
    done = 0
    for sid in snapshot_ids:
        try:
            raise_if_cancelled(cancel)
        except CancelledError:
            break
        view = SnapshotView(store, sid)
        store.write_findings(sid, [f for r in rules for f in r.evaluate(view, r.params)], known)
        done += 1
        if progress is not None:
            progress(done, len(snapshot_ids))
    return done


@dataclass
class FindingState:
    rule: str
    rtype: str
    region: str
    rid: str
    severity: str
    account: str
    first_seen: str
    last_seen: str
    age_days: int
    seeded: bool = False
    detail: dict | None = None
    unverified: bool = False          # absent from the latest snapshot, but its collectors did not look there
    mute: dict | None = None          # the active mute row that accepts it

    @property
    def muted(self) -> bool:
        return self.mute is not None


def _in_clause(values: Collection[str]) -> str:
    return ",".join("?" * len(values))


def _history(db, accounts, anchor):
    sql = ("SELECT s.account, f.rule, f.rtype, f.region, f.rid, f.severity, MIN(s.taken_at) AS first_seen, "
           "MAX(s.taken_at) AS last_seen FROM findings f JOIN snapshots s ON s.id = f.snapshot_id ")
    conditions, args = [], []
    if accounts:
        conditions.append(f"s.account IN ({_in_clause(accounts)})")
        args.extend(accounts)
    if anchor is not None:
        conditions.append("(s.taken_at < ? OR (s.taken_at = ? AND s.id <= ?))")
        args.extend([anchor["taken_at"], anchor["taken_at"], anchor["id"]])
    if conditions:
        sql += "WHERE " + " AND ".join(conditions) + " "
    sql += "GROUP BY s.account, f.rule, f.rtype, f.region, f.rid"
    return db.execute(sql, args).fetchall()


def _rechecked(covered_by: dict[str, set[str]], rule: str, region: str) -> bool:
    """Did every collector this rule reads look at `region` in the reference snapshot?"""
    sources = RULE_COLLECTORS.get(rule)
    if not sources:
        return True
    registry = all_collectors()
    for name in sources:
        covered = covered_by.get(name)
        if covered is None:
            return False
        scope = getattr(registry.get(name), "scope", "regional")
        if scope == "global":
            if "global" not in covered:
                return False
        elif region not in covered:
            return False
    return True


def _states(store, accounts, want_open: bool, as_of: int | None, now: datetime | None) -> list[FindingState]:
    accounts = tuple(accounts or ())
    with store.reader() as db:
        anchor = None
        if as_of is not None:
            anchor = db.execute("SELECT id, account, taken_at FROM snapshots WHERE id=?", (as_of,)).fetchone()
            if anchor is None:
                raise ValueError(f"no snapshot with id {as_of}")
            if accounts and anchor["account"] not in accounts:
                raise ValueError(f"snapshot {as_of} belongs to another account")
            accounts = (anchor["account"],)
        history = _history(db, accounts, anchor)
        seeds = {(r["account"], r["rule"], r["rid"]): r["first_seen"]
                 for r in db.execute("SELECT * FROM seeded_findings")}
        # Findings-tools spec §4: a disabled rule's rows are hidden, never shown as resolved.
        disabled = {r["rule"] for r in db.execute("SELECT rule FROM rule_overrides WHERE disabled=1")}
        if anchor is not None:
            reference = {anchor["account"]: anchor["id"]}
            clock = parse_ts(anchor["taken_at"])
        else:
            reference = {}
            for acct in sorted({row["account"] for row in history}):
                latest = db.execute("SELECT id FROM snapshots WHERE account=? ORDER BY taken_at DESC, id DESC LIMIT 1",
                                    (acct,)).fetchone()
                if latest is not None:
                    reference[acct] = latest["id"]
            clock = now or datetime.now(timezone.utc)
        present: dict[str, dict[tuple, str | None]] = {}
        covered: dict[str, dict[str, set[str]]] = {}
        for acct, sid in reference.items():
            present[acct] = {(r["rule"], r["rtype"], r["region"], r["rid"]): r["detail"] for r in db.execute(
                "SELECT rule, rtype, region, rid, detail FROM findings WHERE snapshot_id=?", (sid,))}
            covered[acct] = {r["collector"]: set(json.loads(r["regions_covered"])) for r in db.execute(
                "SELECT collector, regions_covered FROM collector_runs WHERE snapshot_id=? AND status='ok'", (sid,))}

    out = []
    for row in history:
        if row["rule"] in disabled:
            continue
        key = (row["rule"], row["rtype"], row["region"], row["rid"])
        here = present.get(row["account"], {})
        unverified = False
        if key in here:
            is_open = True
        elif not _rechecked(covered.get(row["account"], {}), row["rule"], row["region"]):
            is_open, unverified = True, True
        else:
            is_open = False
        if is_open != want_open:
            continue
        first_seen = row["first_seen"]
        seeded_at = seeds.get((row["account"], row["rule"], row["rid"]))
        seeded = bool(seeded_at and seeded_at < first_seen)
        if seeded:
            first_seen = seeded_at
        detail = here.get(key)
        out.append(FindingState(row["rule"], row["rtype"], row["region"], row["rid"], row["severity"],
                                row["account"], first_seen, row["last_seen"], (clock - parse_ts(first_seen)).days,
                                seeded, json.loads(detail) if detail else None, unverified))
    out.sort(key=lambda s: (_SEVERITY_ORDER.get(s.severity, 9), -s.age_days, s.account, s.rule, s.rid))
    return out


def open_findings(store, accounts: Collection[str] | None = None, as_of: int | None = None, *,
                  now: datetime | None = None) -> list[FindingState]:
    return _states(store, accounts, True, as_of, now)


def resolved_findings(store, accounts: Collection[str] | None = None, as_of: int | None = None, *,
                      now: datetime | None = None) -> list[FindingState]:
    return _states(store, accounts, False, as_of, now)


def apply_mutes(store, states: list[FindingState], *, now: datetime | None = None) -> list[FindingState]:
    """Annotate with the matching active mute. Never removes anything: accepted findings stay visible."""
    mutes = [dict(m) for m in store.active_mutes(now)]
    for st in states:
        st.mute = None
        for m in mutes:
            if m["rule"] != st.rule:
                continue
            if m["account"] is not None and m["account"] != st.account:
                continue
            if m["rid"] is not None and m["rid"] != st.rid:
                continue
            st.mute = m
            break
    return states
