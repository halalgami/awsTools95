# Upstream: labsnap/labsnap/store/db.py @ 1b1d437 and ctwatch/ctwatch/store/cache.py @ 254ff31 — adapted for aws95; owned here from now on.
"""The one local store (spec §3.3): aws95.db in the app directory, 0600, WAL, versioned by PRAGMA user_version.

Threading: one writer connection, every write serialised by `_write_lock`; every read opens its own short-lived
connection through `reader()`, so readers never wait on a writer (WAL). `clear()` takes the write lock, waits until
no reader is active, then deletes the files; the next use recreates an empty store.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import weakref
import zlib
from collections.abc import Collection, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from aws95.core.paths import app_dir
from aws95.store.schema import MIGRATIONS, SCHEMA_VERSION

DB_NAME = "aws95.db"
_TS = "%Y-%m-%dT%H:%M:%SZ"
_SUFFIXES = ("", "-wal", "-shm", "-journal")
SECTION_STATES = ("waiting", "running", "done", "partial", "denied", "timed_out", "failed", "stopped")
END_STATES = ("done", "partial", "denied", "timed_out", "failed", "stopped")
COVERED_STATES = ("done", "partial")
RESUMABLE_STATES = ("waiting", "stopped", "timed_out", "failed")


class StoreError(Exception):
    """The local store can't be used right now."""


class StoreTooNew(StoreError):
    """The database was written by a newer AWS 95 Tools."""


def default_path() -> Path:
    return app_dir() / DB_NAME


def ts(dt: datetime) -> str:
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("store times must be timezone-aware")
    return dt.astimezone(timezone.utc).strftime(_TS)


def parse_ts(text: str) -> datetime:
    return datetime.strptime(text, _TS).replace(tzinfo=timezone.utc)


def dec_text(value: Any) -> str:
    """The exact decimal text of a Decimal or int. A float is refused: it has already lost the AWS digits."""
    if isinstance(value, bool) or not isinstance(value, (Decimal, int)):
        raise TypeError(f"money/metric values must be Decimal or int, got {type(value).__name__}")
    d = Decimal(value)
    if not d.is_finite():
        raise ValueError(f"not a finite number: {value}")
    return str(d)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return ts(obj)
    if isinstance(obj, (set, frozenset, tuple)):
        return sorted(obj) if isinstance(obj, (set, frozenset)) else list(obj)
    raise TypeError(f"not JSON serialisable: {type(obj).__name__}")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=_json_default)


@dataclass(frozen=True)
class EventRow:
    """One CloudTrail event as 2c stores it. `raw_json` is the event's own JSON text; it is stored zlib-compressed."""
    event_id: str
    account: str
    region: str
    time_utc: datetime
    principal_type: str
    principal_name: str
    is_human: bool
    source: str
    name: str
    read_only: bool
    error_code: str | None
    source_ip: str | None
    user_agent: str | None
    resource_arns: tuple[str, ...]
    risk_level: int
    risk_rule: str | None
    origin: Literal["lookup", "import"]
    raw_json: str


@dataclass(frozen=True)
class CoverageRange:
    account: str
    region: str
    kind: str
    start: datetime
    end: datetime


class Store:
    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._write_lock = threading.RLock()
        self._open_lock = threading.Lock()
        self._state = threading.Condition(threading.Lock())
        self._readers = 0
        self._clearing = False
        self._writer: sqlite3.Connection | None = None
        self._finalizer: weakref.finalize | None = None

    @property
    def path(self) -> Path:
        if self._path is None:
            self._path = default_path()
        return self._path

    # ----- opening -----
    def _open(self) -> sqlite3.Connection:
        path = self.path
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        os.close(fd)
        if os.name == "posix":
            os.chmod(path, 0o600)                   # created 0600, and re-tightened on every open
        conn = sqlite3.connect(str(path), timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise StoreTooNew(f"{path.name} was written by a newer AWS 95 Tools (schema v{version}); "
                                  f"this version reads v{SCHEMA_VERSION}. Update the app rather than downgrade data.")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=NORMAL")
            for index in range(version, SCHEMA_VERSION):
                conn.executescript(f"BEGIN;\n{MIGRATIONS[index]}\nPRAGMA user_version = {index + 1};\nCOMMIT;")
            # A run that was killed leaves sections 'running': they read as stopped, so Resume can finish them.
            conn.execute("UPDATE snapshot_sections SET state='stopped' WHERE state='running'")
            conn.commit()
        except BaseException:
            conn.close()
            raise
        if os.name == "posix":
            for suffix in ("-wal", "-shm"):
                side = Path(str(path) + suffix)
                if side.exists():
                    os.chmod(side, 0o600)
        return conn

    def _ensure(self) -> sqlite3.Connection:
        with self._open_lock:
            if self._writer is None:
                self._writer = self._open()
                # A Store dropped without close() still closes its connection (no ResourceWarning under -W error).
                self._finalizer = weakref.finalize(self, self._writer.close)
            return self._writer

    def _drop_writer(self) -> None:
        """Close the writer connection. Caller holds _open_lock."""
        if self._finalizer is not None:
            self._finalizer.detach()
            self._finalizer = None
        if self._writer is not None:
            self._writer.close()
            self._writer = None

    @contextmanager
    def reader(self) -> Iterator[sqlite3.Connection]:
        with self._state:
            while self._clearing:
                self._state.wait()
            self._readers += 1
        try:
            self._ensure()                          # the file exists and is migrated before any read
            conn = sqlite3.connect(str(self.path), timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA query_only=ON")
                yield conn
            finally:
                conn.close()
        finally:
            with self._state:
                self._readers -= 1
                self._state.notify_all()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._write_lock:
            conn = self._ensure()
            with conn:
                yield conn

    # ----- snapshot writes -----
    def begin_snapshot(self, account: str, caller_arn: str, tool_version: str, regions: Sequence[str], *,
                       taken_at: datetime | None = None) -> int:
        when = ts(taken_at or datetime.now(timezone.utc))
        with self._tx() as db:
            cur = db.execute("INSERT INTO snapshots(account, caller_arn, taken_at, tool_version, status, regions) "
                             "VALUES (?, ?, ?, ?, 'partial', ?)",
                             (account, caller_arn, when, tool_version, json.dumps(list(regions))))
        return int(cur.lastrowid)

    def record_collector(self, snapshot_id: int, collector: str, status: str, error: str | None,
                         duration_ms: int | None, regions_requested: Sequence[str], regions_covered: Sequence[str],
                         denials: int) -> None:
        with self._tx() as db:
            db.execute("INSERT OR REPLACE INTO collector_runs(snapshot_id, collector, status, error, duration_ms, "
                       "regions_requested, regions_covered, denials) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (snapshot_id, collector, status, error, duration_ms, json.dumps(sorted(set(regions_requested))),
                        json.dumps(sorted(set(regions_covered))), int(denials)))

    @staticmethod
    def _resource_rows(snapshot_id: int, collector: str, resources: Sequence[Any]) -> list[tuple]:
        return [(snapshot_id, collector, r.rtype, r.region, r.rid, _json(r.attrs), int(bool(r.partial)))
                for r in resources]

    @staticmethod
    def _metric_rows(snapshot_id: int, collector: str, metrics: Sequence[Any], observed_at: datetime) -> list[tuple]:
        when = ts(observed_at)
        return [(snapshot_id, collector, m.name, _json(m.dims or {}), m.period or "", dec_text(m.value), m.unit, when)
                for m in metrics]

    _RESOURCE_SQL = ("INSERT OR REPLACE INTO resource_state(snapshot_id, collector, rtype, region, rid, attrs, partial) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)")
    _METRIC_SQL = ("INSERT OR REPLACE INTO metrics(snapshot_id, collector, name, dims, period, value, unit, observed_at) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?)")

    def write_resources(self, snapshot_id: int, collector: str, resources: Sequence[Any]) -> None:
        rows = self._resource_rows(snapshot_id, collector, resources)
        with self._tx() as db:
            db.executemany(self._RESOURCE_SQL, rows)

    def write_metrics(self, snapshot_id: int, collector: str, metrics: Sequence[Any], observed_at: datetime) -> None:
        rows = self._metric_rows(snapshot_id, collector, metrics, observed_at)
        with self._tx() as db:
            db.executemany(self._METRIC_SQL, rows)

    # ----- sections (resumable-snapshots spec §4) -----
    def plan_sections(self, snapshot_id: int, sections: Sequence[tuple[str, str]]) -> None:
        with self._tx() as db:
            db.executemany("INSERT OR IGNORE INTO snapshot_sections(snapshot_id, collector, region, state) "
                           "VALUES (?, ?, ?, 'waiting')", [(snapshot_id, c, r) for c, r in sections])

    def start_section(self, snapshot_id: int, collector: str, region: str, started_at: datetime) -> None:
        with self._tx() as db:
            db.execute("UPDATE snapshot_sections SET state='running', started_at=?, ended_at=NULL, error=NULL "
                       "WHERE snapshot_id=? AND collector=? AND region=?", (ts(started_at), snapshot_id, collector, region))

    def end_section(self, snapshot_id: int, collector: str, region: str, state: str, *, ended_at: datetime,
                    resources: Sequence[Any] = (), metrics: Sequence[Any] = (), observed_at: datetime | None = None,
                    denials: int = 0, error: str | None = None) -> None:
        """A section's data, its end state and its collector's summary, in one transaction: a section is never done
        without its data, and its data never lands while it still reads as waiting."""
        if state not in END_STATES:
            raise ValueError(f"not an end state: {state!r}")
        res_rows = self._resource_rows(snapshot_id, collector, resources)
        met_rows = self._metric_rows(snapshot_id, collector, metrics, observed_at or ended_at) if metrics else []
        with self._tx() as db:
            if res_rows:
                db.executemany(self._RESOURCE_SQL, res_rows)
            if met_rows:
                db.executemany(self._METRIC_SQL, met_rows)
            db.execute("UPDATE snapshot_sections SET state=?, ended_at=?, rows=?, denials=?, error=? "
                       "WHERE snapshot_id=? AND collector=? AND region=?",
                       (state, ts(ended_at), len(res_rows), int(denials), error, snapshot_id, collector, region))
            self._summarise_collector(db, snapshot_id, collector)

    @staticmethod
    def _summarise_collector(db: sqlite3.Connection, snapshot_id: int, collector: str) -> None:
        rows = db.execute("SELECT * FROM snapshot_sections WHERE snapshot_id=? AND collector=? ORDER BY region",
                          (snapshot_id, collector)).fetchall()
        requested = [r["region"] for r in rows]
        covered = [r["region"] for r in rows if r["state"] in COVERED_STATES]
        errors = [f"{r['region']}: {r['error']}" for r in rows if r["error"] and r["state"] not in COVERED_STATES]
        seconds = sum(int((parse_ts(r["ended_at"]) - parse_ts(r["started_at"])).total_seconds())
                      for r in rows if r["started_at"] and r["ended_at"])
        db.execute("INSERT OR REPLACE INTO collector_runs(snapshot_id, collector, status, error, duration_ms, "
                   "regions_requested, regions_covered, denials) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (snapshot_id, collector, "ok" if covered else "failed", "; ".join(errors) or None, seconds * 1000,
                    json.dumps(sorted(requested)), json.dumps(sorted(covered)), sum(r["denials"] for r in rows)))

    def sections(self, snapshot_id: int) -> list[sqlite3.Row]:
        with self.reader() as db:
            return db.execute("SELECT * FROM snapshot_sections WHERE snapshot_id=? ORDER BY collector, region",
                              (snapshot_id,)).fetchall()

    def resumable_sections(self, snapshot_id: int, *, retry_denied: bool = False) -> list[tuple[str, str]]:
        states = RESUMABLE_STATES + (("denied",) if retry_denied else ())
        with self.reader() as db:
            rows = db.execute(f"SELECT collector, region FROM snapshot_sections WHERE snapshot_id=? "
                              f"AND state IN ({', '.join('?' * len(states))}) ORDER BY collector, region",
                              (snapshot_id, *states)).fetchall()
        return [(r["collector"], r["region"]) for r in rows]

    def write_findings(self, snapshot_id: int, findings: Sequence[Any], rules_evaluated: Collection[str]) -> None:
        """Replace this snapshot's findings for every rule that ran (a rule that now finds nothing must close)."""
        rows = [(snapshot_id, f.rule, f.rtype, f.region, f.rid, f.severity, _json(f.detail) if f.detail else None)
                for f in findings]
        with self._tx() as db:
            for rule in set(rules_evaluated):
                db.execute("DELETE FROM findings WHERE snapshot_id=? AND rule=?", (snapshot_id, rule))
            db.executemany("INSERT OR REPLACE INTO findings(snapshot_id, rule, rtype, region, rid, severity, detail) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?)", rows)

    def finish_snapshot(self, snapshot_id: int) -> str:
        with self._tx() as db:
            sections = db.execute("SELECT state FROM snapshot_sections WHERE snapshot_id=?", (snapshot_id,)).fetchall()
            if sections:
                complete = all(r["state"] in COVERED_STATES for r in sections)
            else:
                rows = db.execute("SELECT status, regions_requested, regions_covered FROM collector_runs "
                                  "WHERE snapshot_id=?", (snapshot_id,)).fetchall()
                # Zero rows is "nothing was collected", never complete.
                complete = bool(rows) and all(
                    r["status"] == "ok" and json.loads(r["regions_requested"]) == json.loads(r["regions_covered"])
                    for r in rows)
            status = "complete" if complete else "partial"
            db.execute("UPDATE snapshots SET status=? WHERE id=?", (status, snapshot_id))
        return status

    def latest_snapshot(self, account: str) -> sqlite3.Row | None:
        with self.reader() as db:
            return db.execute("SELECT * FROM snapshots WHERE account=? ORDER BY taken_at DESC, id DESC LIMIT 1",
                              (account,)).fetchone()

    # ----- mutes ("accepted" findings) -----
    def add_mute(self, account: str | None, rule: str, rid: str | None, reason: str, expires_at: datetime | None,
                 *, now: datetime | None = None) -> int:
        # SQLite treats NULL as distinct in UNIQUE, so replace with NULL-safe IS comparisons (labsnap note).
        with self._tx() as db:
            db.execute("DELETE FROM mutes WHERE account IS ? AND rule=? AND rid IS ?", (account, rule, rid))
            cur = db.execute("INSERT INTO mutes(account, rule, rid, reason, muted_at, expires_at) "
                             "VALUES (?, ?, ?, ?, ?, ?)",
                             (account, rule, rid, reason, ts(now or datetime.now(timezone.utc)),
                              ts(expires_at) if expires_at is not None else None))
        return int(cur.lastrowid)

    def remove_mute(self, mute_id: int) -> bool:
        with self._tx() as db:
            return db.execute("DELETE FROM mutes WHERE id=?", (mute_id,)).rowcount > 0

    def active_mutes(self, now: datetime | None = None) -> list[sqlite3.Row]:
        with self.reader() as db:
            return db.execute("SELECT * FROM mutes WHERE expires_at IS NULL OR expires_at > ? ORDER BY id",
                              (ts(now or datetime.now(timezone.utc)),)).fetchall()

    # ----- meta -----
    # ----- rule tuning and seeds (findings-tools spec §3) -----
    def rule_overrides(self) -> dict[str, dict]:
        with self.reader() as db:
            rows = db.execute("SELECT rule, disabled, severity, params FROM rule_overrides").fetchall()
        return {r["rule"]: {"disabled": bool(r["disabled"]), "severity": r["severity"],
                            "params": json.loads(r["params"]) if r["params"] else None} for r in rows}

    def set_rule_override(self, rule: str, *, disabled: bool, severity: str | None, params: dict | None,
                          changed_at: datetime) -> None:
        with self._tx() as db:
            db.execute("INSERT OR REPLACE INTO rule_overrides(rule, disabled, severity, params, changed_at) "
                       "VALUES (?, ?, ?, ?, ?)", (rule, int(disabled), severity,
                                                  json.dumps(params, sort_keys=True) if params else None,
                                                  ts(changed_at)))

    def clear_rule_override(self, rule: str) -> None:
        with self._tx() as db:
            db.execute("DELETE FROM rule_overrides WHERE rule=?", (rule,))

    def seed_finding(self, account: str, rule: str, rid: str, first_seen: datetime, note: str) -> None:
        with self._tx() as db:
            db.execute("INSERT OR REPLACE INTO seeded_findings(account, rule, rid, first_seen, source) "
                       "VALUES (?, ?, ?, ?, ?)", (account, rule, rid, ts(first_seen), note.strip() or "manual"))

    def unseed_finding(self, account: str, rule: str, rid: str) -> bool:
        with self._tx() as db:
            cur = db.execute("DELETE FROM seeded_findings WHERE account=? AND rule=? AND rid=?", (account, rule, rid))
        return cur.rowcount > 0

    def set_meta(self, key: str, value: str) -> None:
        with self._tx() as db:
            db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))

    def get_meta(self, key: str) -> str | None:
        with self.reader() as db:
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    # ----- activity (2c) -----
    def insert_events(self, events: Sequence[EventRow]) -> int:
        rows = [(e.event_id, e.account, e.region, ts(e.time_utc), e.principal_type, e.principal_name, int(e.is_human),
                 e.source, e.name, int(e.read_only), e.error_code, e.source_ip, e.user_agent,
                 json.dumps(list(e.resource_arns)), int(e.risk_level), e.risk_rule, e.origin,
                 zlib.compress(e.raw_json.encode("utf-8"))) for e in events]
        with self._tx() as db:
            before = db.total_changes
            db.executemany("INSERT OR IGNORE INTO events(event_id, account, region, event_time_utc, principal_type, "
                           "principal_name, is_human, event_source, event_name, read_only, error_code, source_ip, "
                           "user_agent, resource_arns, risk_level, risk_rule, origin, raw) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
            return db.total_changes - before

    def raw_event(self, event_id: str) -> str | None:
        with self.reader() as db:
            row = db.execute("SELECT raw FROM events WHERE event_id=?", (event_id,)).fetchone()
        return zlib.decompress(row[0]).decode("utf-8") if row else None

    def mark_imported(self, event_ids: Sequence[str]) -> int:
        """2c: an event first looked up and later read from an imported log file is imported history, so
        retention (prune_events) keeps it. Returns how many rows changed."""
        ids = list(dict.fromkeys(event_ids))
        changed = 0
        with self._tx() as db:
            for start in range(0, len(ids), 500):
                chunk = ids[start:start + 500]
                changed += db.execute(f"UPDATE events SET origin='import' WHERE origin='lookup' AND event_id IN "
                                      f"({','.join('?' * len(chunk))})", chunk).rowcount
        return changed

    def record_coverage(self, account: str, region: str, kind: str, start: datetime, end: datetime) -> None:
        if end < start:
            raise ValueError("coverage end is before its start")
        with self._tx() as db:
            ranges = [(parse_ts(r[0]), parse_ts(r[1])) for r in db.execute(
                "SELECT range_from, range_to FROM activity_coverage WHERE account=? AND region=? AND kind=?",
                (account, region, kind))]
            ranges.append((start, end))
            merged: list[list[datetime]] = []
            for lo, hi in sorted(ranges):
                if merged and lo <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], hi)
                else:
                    merged.append([lo, hi])
            db.execute("DELETE FROM activity_coverage WHERE account=? AND region=? AND kind=?", (account, region, kind))
            db.executemany("INSERT INTO activity_coverage(account, region, kind, range_from, range_to) "
                           "VALUES (?, ?, ?, ?, ?)", [(account, region, kind, ts(lo), ts(hi)) for lo, hi in merged])

    def coverage(self, account: str, region: str | None = None, kind: str | None = None) -> list[CoverageRange]:
        sql, args = "SELECT * FROM activity_coverage WHERE account=?", [account]
        if region is not None:
            sql += " AND region=?"
            args.append(region)
        if kind is not None:
            sql += " AND kind=?"
            args.append(kind)
        with self.reader() as db:
            rows = db.execute(sql + " ORDER BY region, kind, range_from", args).fetchall()
        return [CoverageRange(r["account"], r["region"], r["kind"], parse_ts(r["range_from"]), parse_ts(r["range_to"]))
                for r in rows]

    def prune_events(self, before: datetime) -> int:
        """Retention (spec §3.3): drop looked-up events older than `before`. Imported history stays until cleared."""
        cut = ts(before)
        with self._tx() as db:
            deleted = db.execute("DELETE FROM events WHERE origin='lookup' AND event_time_utc < ?", (cut,)).rowcount
            db.execute("DELETE FROM activity_coverage WHERE kind != 'import' AND range_to <= ?", (cut,))
            db.execute("UPDATE activity_coverage SET range_from=? WHERE kind != 'import' AND range_from < ?",
                       (cut, cut))
        return deleted

    # ----- lifecycle -----
    def clear(self, timeout: float = 10.0) -> None:
        """Settings ▸ Clear Local Data…: delete aws95.db. Waits for active readers; writers wait on the lock."""
        with self._write_lock:
            with self._state:
                self._clearing = True
                try:
                    if not self._state.wait_for(lambda: self._readers == 0, timeout):
                        raise StoreError("The local data is in use. Close the open windows and try again.")
                    with self._open_lock:
                        self._drop_writer()
                        for suffix in _SUFFIXES:
                            Path(str(self.path) + suffix).unlink(missing_ok=True)
                finally:
                    self._clearing = False
                    self._state.notify_all()

    def close(self) -> None:
        with self._write_lock, self._open_lock:
            self._drop_writer()
