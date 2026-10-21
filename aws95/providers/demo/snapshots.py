from __future__ import annotations

from aws95.contracts.common import Result, Scope
from aws95.contracts.snapshots import AccountStage, DiffEntry, MetricPoint, Snapshot, SnapshotSection
from aws95.core.cancel import CancelToken, raise_if_cancelled
from aws95.providers.demo.builder import build
from aws95.providers.demo.store import DEMO_FRESHNESS, load


def _ok(data):
    return Result.ok(data, freshness=DEMO_FRESHNESS, is_demo=True)


class DemoSnapshots:
    def list_snapshots(self, scope: Scope, *, cancel: CancelToken | None = None):
        raise_if_cancelled(cancel)
        snaps = [build(Snapshot, s) for s in load("snapshots.json")["snapshots"]]
        snaps = [s for s in snaps if not scope.accounts or s.account in scope.accounts]
        snaps.sort(key=lambda s: (s.account, s.taken_at), reverse=True)
        snaps.sort(key=lambda s: s.account)                     # grouped by account, newest first within
        return _ok(tuple(snaps))

    def diff(self, scope: Scope, a_id: int = 0, b_id: int = 0, *, cancel: CancelToken | None = None):
        raise_if_cancelled(cancel)
        rows = load("snapshots.json")["diffs"].get(f"{a_id}-{b_id}", [])
        return _ok(tuple(build(DiffEntry, r) for r in rows))

    def metric_names(self, scope: Scope, *, cancel: CancelToken | None = None):
        raise_if_cancelled(cancel)
        names: list[str] = []
        for m in load("snapshots.json")["metrics"]:
            if m["name"] not in names:
                names.append(m["name"])
        return _ok(tuple(names))

    def metric_series(self, scope: Scope, name: str = "", *, cancel: CancelToken | None = None):
        raise_if_cancelled(cancel)
        points = [build(MetricPoint, m) for m in load("snapshots.json")["metrics"] if m["name"] == name]
        if scope.accounts:
            points = [p for p in points if p.dims.get("account") in scope.accounts]
        points.sort(key=lambda p: (p.observed_at, p.dims.get("account", "")))
        return _ok(tuple(points))

    def ages(self, scope: Scope, *, cancel: CancelToken | None = None):
        return Result.not_connected("Snapshots aren't taken in demo mode.", is_demo=True)

    def take_snapshot(self, scope: Scope, request=None, *, progress=None, stop=None,
                      cancel: CancelToken | None = None):
        return Result.not_connected("Snapshots aren't taken in demo mode.", is_demo=True)

    def sections(self, scope: Scope, snapshot_id=None, *, cancel: CancelToken | None = None):
        raise_if_cancelled(cancel)
        snaps = self.list_snapshots(scope).data or ()
        latest: dict[str, Snapshot] = {}
        for s in snaps:
            if snapshot_id is not None and s.id != snapshot_id:
                continue
            if s.account not in latest or s.taken_at > latest[s.account].taken_at:
                latest[s.account] = s
        out = []
        for s in latest.values():
            for h in s.collectors:
                for region in h.coverage.regions_requested:
                    ok = region in h.coverage.regions_covered
                    out.append(SnapshotSection(s.account, s.id, h.collector, region, "done" if ok else "failed",
                                               error=None if ok else (h.error or "not covered")))
        return _ok(tuple(sorted(out, key=lambda x: (x.account, x.collector, x.region))))

    def stages(self, scope: Scope, *, cancel: CancelToken | None = None):
        raise_if_cancelled(cancel)
        secs = self.sections(scope).data
        by: dict[str, list[SnapshotSection]] = {}
        for s in secs:
            by.setdefault(s.account, []).append(s)
        snaps = {s.id: s for s in (self.list_snapshots(scope).data or ())}
        return _ok(tuple(AccountStage(a, rows[0].snapshot_id, snaps[rows[0].snapshot_id].taken_at,
                                      sum(r.state in ("done", "partial") for r in rows), len(rows),
                                      sum(r.resumable() for r in rows), sum(r.state == "denied" for r in rows))
                         for a, rows in sorted(by.items())))

    def resume_snapshot(self, scope: Scope, request=None, *, progress=None, stop=None,
                        cancel: CancelToken | None = None):
        return Result.not_connected("Snapshots aren't taken in demo mode.", is_demo=True)
