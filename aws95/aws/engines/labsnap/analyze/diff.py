# Upstream: labsnap/labsnap/analyze/diff.py @ 1b1d437 — adapted for aws95 (Decimal metrics, "excluded"); owned here.
"""Compare two snapshots of one account. Only collectors that succeeded in BOTH are compared, only within regions
both covered. A resource outside that is reported as EXCLUDED with the reason, never as removed or added."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal

import aws95.aws.engines.labsnap.collect.collectors  # noqa: F401 - registers collectors (their volatile keys)
from aws95.aws.engines.labsnap.collect.base import all_collectors

REL_THRESHOLD = Decimal("0.10")
# Periodic metrics (cost, free tier) restate by small amounts as AWS reconciles; a move of a cent or less is noise.
PERIODIC_ABS_FLOOR = Decimal("0.01")


@dataclass
class ChangedResource:
    rtype: str
    region: str
    rid: str
    deltas: dict            # attr -> (before, after)
    partial: bool = False


@dataclass
class ExcludedResource:
    rtype: str
    region: str
    rid: str
    reason: str


@dataclass
class MetricChange:
    name: str
    dims: dict
    period: str | None
    before: Decimal
    after: Decimal
    restatement: bool


@dataclass
class DiffResult:
    added: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    changed: list = field(default_factory=list)
    metric_changes: list = field(default_factory=list)
    not_compared: list = field(default_factory=list)
    excluded: list = field(default_factory=list)


class CrossAccountDiff(Exception):
    """The two snapshots belong to different accounts: every resource would read as removed/added."""


def _account(db, sid):
    row = db.execute("SELECT account FROM snapshots WHERE id=?", (sid,)).fetchone()
    if row is None:
        raise ValueError(f"no snapshot with id {sid}")
    return row["account"]


def _volatile(collector_name: str) -> set:
    cls = all_collectors().get(collector_name)
    return set(getattr(cls, "volatile_keys", frozenset()) or frozenset())


def diff_snapshots(store, a_id: int, b_id: int) -> DiffResult:
    with store.reader() as db:
        acct_a, acct_b = _account(db, a_id), _account(db, b_id)
        if acct_a != acct_b:
            raise CrossAccountDiff(f"snapshot {a_id} and snapshot {b_id} belong to different accounts")
        runs_a = {r["collector"]: dict(r) for r in db.execute("SELECT * FROM collector_runs WHERE snapshot_id=?", (a_id,))}
        runs_b = {r["collector"]: dict(r) for r in db.execute("SELECT * FROM collector_runs WHERE snapshot_id=?", (b_id,))}
        res_a = {(r["rtype"], r["region"], r["rid"]): dict(r)
                 for r in db.execute("SELECT * FROM resource_state WHERE snapshot_id=?", (a_id,))}
        res_b = {(r["rtype"], r["region"], r["rid"]): dict(r)
                 for r in db.execute("SELECT * FROM resource_state WHERE snapshot_id=?", (b_id,))}
        met_a = {(r["name"], r["dims"], r["period"]): dict(r)
                 for r in db.execute("SELECT * FROM metrics WHERE snapshot_id=?", (a_id,))}
        met_b = {(r["name"], r["dims"], r["period"]): dict(r)
                 for r in db.execute("SELECT * FROM metrics WHERE snapshot_id=?", (b_id,))}

    out = DiffResult()
    comparable: dict[str, tuple[set, set, set]] = {}
    why: dict[str, str] = {}
    for name in sorted(set(runs_a) | set(runs_b)):
        ra, rb = runs_a.get(name), runs_b.get(name)
        if not ra or not rb:
            why[name] = f"{name} did not run in snapshot #{a_id if not ra else b_id}"
            out.not_compared.append(f"{name}: absent from one snapshot")
            continue
        if ra["status"] != "ok" or rb["status"] != "ok":
            why[name] = f"{name} failed in snapshot #{a_id if ra['status'] != 'ok' else b_id}"
            out.not_compared.append(f"{name}: collector failed ({ra['status']} / {rb['status']})")
            continue
        cov_a, cov_b = set(json.loads(ra["regions_covered"])), set(json.loads(rb["regions_covered"]))
        missed = cov_a ^ cov_b
        if missed:
            out.not_compared.append(f"{name}: regions not covered by both snapshots: {', '.join(sorted(missed))}")
        comparable[name] = (cov_a & cov_b, cov_a, cov_b)

    def reason(row) -> str | None:
        name, region = row["collector"], row["region"]
        if name not in comparable:
            return f"Not looked at: {why.get(name, name + ' was not compared')}."
        both, cov_a, _cov_b = comparable[name]
        if region in both:
            return None
        side = a_id if region not in cov_a else b_id
        return f"Not looked at: {name} did not cover {region} in snapshot #{side}."

    keys_a = {k for k, v in res_a.items() if reason(v) is None}
    keys_b = {k for k, v in res_b.items() if reason(v) is None}
    excluded: dict[tuple, ExcludedResource] = {}
    for key, row in [*res_a.items(), *res_b.items()]:
        text = reason(row)
        if text is not None and key not in excluded:
            excluded[key] = ExcludedResource(*key, text)
    out.excluded = [excluded[k] for k in sorted(excluded)]

    # A one-sided partial resource was still LISTED (only a follow-up call failed): it really was added/removed.
    for key in sorted(keys_b - keys_a):
        out.added.append(ChangedResource(*key, {}, bool(res_b[key]["partial"])))
    for key in sorted(keys_a - keys_b):
        out.removed.append(ChangedResource(*key, {}, bool(res_a[key]["partial"])))
    for key in sorted(keys_a & keys_b):
        ra, rb = res_a[key], res_b[key]
        if ra["partial"] or rb["partial"]:
            out.changed.append(ChangedResource(*key, {}, True))
            continue
        volatile = _volatile(rb["collector"])
        attrs_a = {k: v for k, v in json.loads(ra["attrs"]).items() if k not in volatile}
        attrs_b = {k: v for k, v in json.loads(rb["attrs"]).items() if k not in volatile}
        deltas = {k: (attrs_a.get(k), attrs_b.get(k)) for k in set(attrs_a) | set(attrs_b)
                  if attrs_a.get(k) != attrs_b.get(k)}
        if deltas:
            out.changed.append(ChangedResource(*key, deltas, False))

    for key in sorted(set(met_a) & set(met_b)):
        ra, rb = met_a[key], met_b[key]
        if ra["collector"] not in comparable:
            continue
        name, dims, period = key
        before, after = Decimal(ra["value"]), Decimal(rb["value"])
        if before == after:
            continue
        if period:                                # '' = a point-in-time metric (store schema: never NULL)
            if abs(after - before) <= PERIODIC_ABS_FLOOR:
                continue
        elif before and abs(after - before) / abs(before) < REL_THRESHOLD:
            continue
        out.metric_changes.append(MetricChange(name, json.loads(dims), period or None, before, after,
                                               restatement=bool(period)))
    out.metric_changes.sort(key=lambda c: c.name)
    return out
