"""LiveResources: the type tree and rows of each account's latest snapshot. A region the snapshot did not cover
reads "Not collected in <region>", never an empty grid (spec §4)."""
from __future__ import annotations

import json

from aws95.contracts.common import Coverage, Page, Result, Scope, Status
from aws95.contracts.resources import ResourceRow, ResourceType
from aws95.core.cancel import CancelToken
from aws95.providers.live.catalog import BY_RTYPE, CATALOG, GLOBAL_COLLECTORS, collector_of
from aws95.providers.live.common import (NO_ACCOUNTS, NO_SNAPSHOT, Clock, Roster, freshness_of, join_messages,
                                         latest_snapshots, marks, scope_accounts, utcnow)
from aws95.providers.live.guard import guarded
from aws95.store import Store, parse_ts

_STATE_KEYS = ("state", "status", "key_state", "table_status")


def _state(attrs: dict) -> str | None:
    for key in _STATE_KEYS:
        if attrs.get(key) is not None:
            return str(attrs[key])
    return None


class LiveResources:
    def __init__(self, store: Store, roster: Roster, *, clock: Clock = utcnow) -> None:
        self._store = store
        self._roster = roster
        self._clock = clock

    @guarded()
    def resource_types(self, scope: Scope, *, cancel: CancelToken | None = None):
        accounts = scope_accounts(scope, self._roster())
        if not accounts:
            return Result.not_connected(NO_ACCOUNTS)
        with self._store.reader() as db:
            latest = latest_snapshots(db, accounts)
            if not latest:
                return Result.not_connected(NO_SNAPSHOT)
            ids = [r["id"] for r in latest.values()]
            sql = f"SELECT rtype, region, COUNT(*) AS n FROM resource_state WHERE snapshot_id IN ({marks(len(ids))}) "
            counts: dict[str, int] = {}
            for r in db.execute(sql + "GROUP BY rtype, region", ids):
                if scope.regions and r["region"] != "global" and r["region"] not in scope.regions:
                    continue
                counts[r["rtype"]] = counts.get(r["rtype"], 0) + int(r["n"])
            ran = {r[0] for r in db.execute(f"SELECT DISTINCT collector FROM collector_runs WHERE snapshot_id IN "
                                            f"({marks(len(ids))}) AND status='ok'", ids)}
        types = [ResourceType(rtype, domain, label, counts.get(rtype, 0))
                 for rtype, collector, domain, label in CATALOG if rtype in counts or collector in ran]
        types += [ResourceType(rt, "Other", rt, n) for rt, n in counts.items() if rt not in BY_RTYPE]
        types.sort(key=lambda t: (t.domain, t.label))
        fresh, stale = freshness_of([parse_ts(r["taken_at"]) for r in latest.values()], self._clock())
        missing = [a for a in accounts if a not in latest]
        gap = f"No snapshot yet for {', '.join(missing)}." if missing else None
        status = Status.STALE if stale else Status.PARTIAL if missing else Status.OK
        return Result(status, tuple(types), None, fresh, join_messages(stale, gap))

    @guarded()
    def list_resources(self, scope: Scope, rtype: str = "", cursor: str | None = None, *, page_size: int = 500,
                       cancel: CancelToken | None = None):
        accounts = scope_accounts(scope, self._roster())
        if not accounts:
            return Result.not_connected(NO_ACCOUNTS)
        try:
            offset = int(cursor) if cursor else 0
        except ValueError:
            return Result.error("Bad page cursor.")
        collector = collector_of(rtype)
        regional = collector not in GLOBAL_COLLECTORS
        wanted = tuple(scope.regions) if regional and scope.regions else ()
        label = BY_RTYPE[rtype][3] if rtype in BY_RTYPE else rtype
        with self._store.reader() as db:
            latest = latest_snapshots(db, accounts)
            if not latest:
                return Result.not_connected(NO_SNAPSHOT)
            ids = [r["id"] for r in latest.values()]
            runs = {acct: db.execute("SELECT * FROM collector_runs WHERE snapshot_id=? AND collector=?",
                                     (row["id"], collector)).fetchone() for acct, row in latest.items()}
            region_sql = f" AND r.region IN ({marks(len(wanted))})" if wanted else ""
            base = (f"FROM resource_state r JOIN snapshots s ON s.id = r.snapshot_id "
                    f"WHERE r.snapshot_id IN ({marks(len(ids))}) AND r.rtype=?{region_sql}")
            args = [*ids, rtype, *wanted]
            total = int(db.execute("SELECT COUNT(*) " + base, args).fetchone()[0])
            rows = db.execute("SELECT s.account, r.* " + base + " ORDER BY s.account, r.region, r.rid LIMIT ? OFFSET ?",
                              [*args, page_size, offset]).fetchall()

        requested: set[str] = set()
        covered: set[str] = set()
        denials = 0
        gaps: set[str] = set()
        for acct, snap in latest.items():
            run = runs.get(acct)
            ok = run is not None and run["status"] == "ok"
            cov = set(json.loads(run["regions_covered"])) if ok else set()
            asked = (set(wanted) if wanted else set(json.loads(snap["regions"]))) if regional else {"global"}
            requested |= asked
            covered |= cov & asked
            denials += int(run["denials"]) if run is not None else 0
            gaps |= asked - cov
        missing = [a for a in accounts if a not in latest]
        if gaps == {"global"}:
            gap_text = f"{label} weren't collected in the latest snapshot."
        elif gaps:
            gap_text = f"Not collected in {', '.join(sorted(gaps - {'global'}))}."
        else:
            gap_text = None
        missing_text = f"No snapshot yet for {', '.join(missing)}." if missing else None
        if total == 0 and (gaps or missing):
            return Result.not_connected(join_messages(gap_text, missing_text, "Take a snapshot that covers it."))
        coverage = Coverage(tuple(sorted(requested)), tuple(sorted(covered)), denials)
        fresh, stale = freshness_of([parse_ts(r["taken_at"]) for r in latest.values()], self._clock())
        if stale:
            status = Status.STALE
        elif gaps or missing or not coverage.complete:
            status = Status.PARTIAL
        else:
            status = Status.OK
        items = tuple(ResourceRow(r["account"], r["rtype"], r["region"], r["rid"], r["rid"],
                                  _state(json.loads(r["attrs"])), json.loads(r["attrs"]), {}, bool(r["partial"]))
                      for r in rows)
        nxt = str(offset + page_size) if offset + page_size < total else None
        return Result(status, Page(items, nxt, total), coverage, fresh, join_messages(stale, gap_text, missing_text))
