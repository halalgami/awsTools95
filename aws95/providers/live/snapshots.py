"""LiveSnapshots: history, diff, trend, ages and sections read from the store; take_snapshot and resume_snapshot are
the paths to AWS."""
from __future__ import annotations

import json
import threading
from collections.abc import Callable
from decimal import Decimal

from aws95.contracts.common import Freshness, Result, Scope, Status
from aws95.contracts.snapshots import (RESUME_WINDOW, AccountAge, AccountStage, DiffEntry, MetricPoint, Snapshot,
                                       SnapshotOutcome, SnapshotSection)
from aws95.core.cancel import AnyToken, CancelToken
from aws95.providers.live.common import (NO_ACCOUNTS, Clock, Roster, collector_health, latest_snapshots, marks,
                                         runs_by_snapshot, scope_accounts, utcnow)
from aws95.providers.live.guard import guarded
from aws95.store import COVERED_STATES, RESUMABLE_STATES, Store, parse_ts


def outcome_text(out: SnapshotOutcome, labels) -> str:
    """One line per account: the engine's section summary, or the bare status when there is none."""
    parts = []
    for run in out.runs:
        text = run.message or (f"{run.status} ({run.denials} denied)" if run.denials else run.status)
        parts.append(f"{labels.get(run.account_id, run.account_id)}: {text}")
    return ("Snapshot cancelled. " if out.cancelled else "") + " · ".join(parts)


class LiveSnapshots:
    def __init__(self, store: Store, roster: Roster, *, clock: Clock = utcnow,
                 service_factory: Callable[[Store], object] | None = None, jobs=None) -> None:
        self._store = store
        self._roster = roster
        self._clock = clock
        self._service_factory = service_factory
        self._service = None
        self._service_lock = threading.Lock()
        self._jobs = jobs

    def _service_once(self):
        with self._service_lock:
            if self._service is None:
                if self._service_factory is not None:
                    self._service = self._service_factory(self._store)
                else:
                    from aws95.aws.engines.labsnap.service import SnapshotService
                    self._service = SnapshotService(self._store, jobs=self._jobs)
            return self._service

    def _accounts(self, scope: Scope) -> tuple[str, ...]:
        return scope_accounts(scope, self._roster())

    @guarded()
    def list_snapshots(self, scope: Scope, *, cancel: CancelToken | None = None):
        accounts = self._accounts(scope)
        if not accounts:
            return Result.not_connected(NO_ACCOUNTS)
        with self._store.reader() as db:
            rows = db.execute(f"SELECT * FROM snapshots WHERE account IN ({marks(len(accounts))}) "
                              "ORDER BY account, taken_at DESC, id DESC", accounts).fetchall()
            runs = runs_by_snapshot(db, [r["id"] for r in rows])
            counts = {r[0]: (r[1], r[2]) for r in db.execute(
                f"SELECT snapshot_id, SUM(state IN ('done','partial')), COUNT(*) FROM snapshot_sections "
                f"WHERE snapshot_id IN ({marks(len(rows))}) GROUP BY snapshot_id", [r["id"] for r in rows])} \
                if rows else {}
        snaps = tuple(Snapshot(r["id"], r["account"], r["caller_arn"], parse_ts(r["taken_at"]), r["tool_version"],
                               r["status"],
                               tuple(collector_health(x) for _, x in sorted(runs.get(r["id"], {}).items())),
                               tuple(json.loads(r["regions"])), *counts.get(r["id"], (0, 0))) for r in rows)
        if not snaps:
            return Result.ok((), message="No snapshots yet. Take Snapshot to make the first one.")
        return Result.ok(snaps, freshness=Freshness(max(s.taken_at for s in snaps), "snapshot"))

    @guarded()
    def diff(self, scope: Scope, a_id: int = 0, b_id: int = 0, *, cancel: CancelToken | None = None):
        from aws95.aws.engines.labsnap.analyze.diff import CrossAccountDiff, diff_snapshots
        try:
            d = diff_snapshots(self._store, a_id, b_id)
        except CrossAccountDiff:
            return Result.error("These snapshots belong to different accounts.")
        except ValueError:
            return Result.error("That snapshot no longer exists.")
        entries = [*(DiffEntry("added", c.rtype, c.region, c.rid, partial=c.partial) for c in d.added),
                   *(DiffEntry("removed", c.rtype, c.region, c.rid, partial=c.partial) for c in d.removed),
                   *(DiffEntry("changed", c.rtype, c.region, c.rid, deltas=dict(c.deltas), partial=c.partial)
                     for c in d.changed),
                   *(DiffEntry("excluded", e.rtype, e.region, e.rid, reason=e.reason) for e in d.excluded)]
        return Result.ok(tuple(entries))

    @guarded()
    def metric_names(self, scope: Scope, *, cancel: CancelToken | None = None):
        accounts = self._accounts(scope)
        if not accounts:
            return Result.not_connected(NO_ACCOUNTS)
        with self._store.reader() as db:
            names = [r[0] for r in db.execute(
                f"SELECT DISTINCT m.name FROM metrics m JOIN snapshots s ON s.id = m.snapshot_id "
                f"WHERE s.account IN ({marks(len(accounts))}) ORDER BY m.name", accounts)]
        return Result.ok(tuple(names))

    @guarded()
    def metric_series(self, scope: Scope, name: str = "", *, cancel: CancelToken | None = None):
        accounts = self._accounts(scope)
        if not accounts:
            return Result.not_connected(NO_ACCOUNTS)
        with self._store.reader() as db:
            rows = db.execute(
                f"SELECT s.account, m.dims, m.period, m.value, m.unit, m.observed_at FROM metrics m "
                f"JOIN snapshots s ON s.id = m.snapshot_id WHERE m.name=? AND s.account IN ({marks(len(accounts))}) "
                "ORDER BY m.observed_at, s.id", (name, *accounts)).fetchall()
        last: dict[tuple, Decimal] = {}
        points = []
        for r in rows:
            dims = {k: str(v) for k, v in json.loads(r["dims"]).items()}
            dims["account"] = r["account"]
            value = Decimal(r["value"])
            restated = False
            if r["period"]:                             # the same period observed again with another value ("" = none)
                key = (r["account"], r["dims"], r["period"])
                restated = key in last and last[key] != value
                last[key] = value
            points.append(MetricPoint(name, dims, r["period"] or None, value, r["unit"], parse_ts(r["observed_at"]),
                                      restated))
        fresh = Freshness(max(p.observed_at for p in points), "snapshot") if points else None
        return Result.ok(tuple(points), freshness=fresh)

    @guarded()
    def ages(self, scope: Scope, *, cancel: CancelToken | None = None):
        accounts = self._accounts(scope)
        with self._store.reader() as db:
            latest = latest_snapshots(db, accounts)
        return Result.ok(tuple(AccountAge(a, parse_ts(latest[a]["taken_at"]) if a in latest else None)
                               for a in accounts))

    @guarded()
    def take_snapshot(self, scope: Scope, request=None, *, progress=None, stop: CancelToken | None = None,
                      cancel: CancelToken | None = None):
        if request is None or not request.accounts:
            return Result.error("Choose at least one account to snapshot.")
        from aws95.aws.engines.labsnap.service import SnapshotBusy
        try:
            out = self._service_once().take(request.accounts, request.include_billing_for, request.regions_for,
                                            cancel=AnyToken(stop, cancel), progress=progress,
                                            labels=request.labels)
        except SnapshotBusy as exc:
            return Result.error(str(exc))
        return self._outcome_result(out, request.labels)

    def _outcome_result(self, out: SnapshotOutcome, labels) -> Result:
        if out.runs and all(r.status == "failed" for r in out.runs):
            status = Status.ERROR
        elif all(r.status == "complete" for r in out.runs) and not out.cancelled:
            status = Status.OK
        else:
            status = Status.PARTIAL
        return Result(status, out, None, Freshness(self._clock(), "snapshot"), outcome_text(out, labels))

    @guarded()
    def sections(self, scope: Scope, snapshot_id: int | None = None, *, cancel: CancelToken | None = None):
        accounts = self._accounts(scope)
        if not accounts:
            return Result.not_connected(NO_ACCOUNTS)
        with self._store.reader() as db:
            if snapshot_id is not None:
                ids = [snapshot_id]
            else:
                ids = [r["id"] for r in latest_snapshots(db, accounts).values()]
            if not ids:
                return Result.ok((), message="No snapshots yet. Take Snapshot to make the first one.")
            rows = db.execute(
                f"SELECT s.*, n.account FROM snapshot_sections s JOIN snapshots n ON n.id = s.snapshot_id "
                f"WHERE s.snapshot_id IN ({marks(len(ids))}) ORDER BY n.account, s.collector, s.region", ids).fetchall()
        return Result.ok(tuple(SnapshotSection(r["account"], r["snapshot_id"], r["collector"], r["region"], r["state"],
                                               parse_ts(r["started_at"]) if r["started_at"] else None,
                                               parse_ts(r["ended_at"]) if r["ended_at"] else None,
                                               r["rows"], r["denials"], r["error"]) for r in rows))

    @guarded()
    def stages(self, scope: Scope, *, cancel: CancelToken | None = None):
        accounts = self._accounts(scope)
        if not accounts:
            return Result.not_connected(NO_ACCOUNTS)
        with self._store.reader() as db:
            latest = latest_snapshots(db, accounts)
            out = []
            for a in accounts:
                snap = latest.get(a)
                if snap is None:
                    out.append(AccountStage(a, None, None, 0, 0, 0, 0))
                    continue
                rows = db.execute("SELECT collector, region, state FROM snapshot_sections WHERE snapshot_id=?",
                                  (snap["id"],)).fetchall()
                taken = parse_ts(snap["taken_at"])
                resumable = sum(r["state"] in RESUMABLE_STATES for r in rows)
                out.append(AccountStage(
                    a, snap["id"], taken, sum(r["state"] in COVERED_STATES for r in rows), len(rows), resumable,
                    sum(r["state"] == "denied" for r in rows),
                    tuple(f"{r['collector']} {r['region']}" for r in rows if r["state"] == "running"),
                    any(r["collector"] == "cost" and r["state"] in RESUMABLE_STATES for r in rows),
                    taken + RESUME_WINDOW if resumable else None))
        return Result.ok(tuple(out))

    @guarded()
    def resume_snapshot(self, scope: Scope, request=None, *, progress=None, stop: CancelToken | None = None,
                        cancel: CancelToken | None = None):
        if request is None or not request.accounts:
            return Result.error("Choose at least one account to resume.")
        from aws95.aws.engines.labsnap.service import SnapshotBusy
        try:
            out = self._service_once().resume(request.accounts, request.include_billing_for,
                                              cancel=AnyToken(stop, cancel), progress=progress,
                                              labels=request.labels, retry_denied=request.retry_denied)
        except SnapshotBusy as exc:
            return Result.error(str(exc))
        return self._outcome_result(out, request.labels)
