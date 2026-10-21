"""Helpers shared by the store-backed live providers (spec §4). Reads only: nothing here reaches AWS."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timezone

from aws95.contracts.accounts import CollectorHealth, RosterEntry
from aws95.contracts.common import Coverage, Freshness, Scope, Status
from aws95.contracts.snapshots import STALE_AFTER

NO_ACCOUNTS = "No accounts yet. Add one with Start ▸ Settings ▸ AWS Setup Wizard…"
NO_SNAPSHOT = "No snapshot yet. Take Snapshot to collect this data."
Roster = Callable[[], tuple[RosterEntry, ...]]
Clock = Callable[[], datetime]
_CHUNK = 500


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def scope_accounts(scope: Scope | None, roster: Sequence[RosterEntry]) -> tuple[str, ...]:
    """The scope bar's accounts, else every roster account."""
    if scope is not None and scope.accounts:
        return tuple(scope.accounts)
    return tuple(e.account_id for e in roster)


def marks(n: int) -> str:
    return ",".join("?" * n)


def latest_snapshots(db: sqlite3.Connection, accounts: Iterable[str]) -> dict[str, sqlite3.Row]:
    out: dict[str, sqlite3.Row] = {}
    for acct in accounts:
        row = db.execute("SELECT * FROM snapshots WHERE account=? ORDER BY taken_at DESC, id DESC LIMIT 1",
                         (acct,)).fetchone()
        if row is not None:
            out[acct] = row
    return out


def runs_by_snapshot(db: sqlite3.Connection, ids: Sequence[int]) -> dict[int, dict[str, sqlite3.Row]]:
    out: dict[int, dict[str, sqlite3.Row]] = {}
    ids = list(ids)
    for start in range(0, len(ids), _CHUNK):
        chunk = ids[start:start + _CHUNK]
        for r in db.execute(f"SELECT * FROM collector_runs WHERE snapshot_id IN ({marks(len(chunk))})", chunk):
            out.setdefault(r["snapshot_id"], {})[r["collector"]] = r
    return out


def freshness_of(taken: Sequence[datetime], now: datetime) -> tuple[Freshness | None, str | None]:
    """Freshness of the OLDEST data shown, and the stale message when it is more than a day old."""
    if not taken:
        return None, None
    oldest = min(taken)
    fresh = Freshness(oldest, "snapshot")
    if now - oldest > STALE_AFTER:
        days = (now - oldest).days
        return fresh, f"Snapshot is {days} day{'s' if days != 1 else ''} old. Take Snapshot to update."
    return fresh, None


def join_messages(*parts: str | None) -> str | None:
    kept = [p for p in parts if p]
    return " · ".join(kept) if kept else None


def health_status(run: sqlite3.Row) -> Status:
    if run["status"] == "ok":
        complete = (json.loads(run["regions_requested"]) == json.loads(run["regions_covered"])
                    and int(run["denials"]) == 0)
        return Status.OK if complete else Status.PARTIAL
    error = run["error"] or ""
    if "Permission denied" in error:
        return Status.DENIED
    if "Can't reach AWS" in error:
        return Status.UNREACHABLE
    return Status.ERROR


def collector_health(run: sqlite3.Row) -> CollectorHealth:
    return CollectorHealth(run["collector"], health_status(run), run["error"], run["duration_ms"],
                           Coverage(tuple(json.loads(run["regions_requested"])),
                                    tuple(json.loads(run["regions_covered"])), int(run["denials"])))
