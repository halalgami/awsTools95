import ast
import os
import sqlite3
import sys
import threading
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from aws95.store import (DB_NAME, SCHEMA_VERSION, Store, StoreError, StoreTooNew, dec_text, default_path,
                         parse_ts, ts)

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1] / "aws95"


@dataclass
class R:
    rtype: str
    region: str
    rid: str
    attrs: dict
    partial: bool = False


@dataclass
class M:
    name: str
    value: object
    dims: dict = field(default_factory=dict)
    period: str | None = None
    unit: str | None = None


@dataclass
class F:
    rule: str
    rtype: str
    region: str
    rid: str
    severity: str
    detail: dict | None = None


def _snap(s, account="123456789012", taken_at=T0, regions=("us-east-1",)):
    return s.begin_snapshot(account, f"arn:aws:iam::{account}:user/auditor", "3.0.0", regions, taken_at=taken_at)


def test_default_path_is_in_app_dir():
    assert default_path().name == DB_NAME
    assert default_path().parent == Path(os.environ["AWS95_HOME"])


def test_lazy_until_first_use(tmp_path):
    s = Store(tmp_path / "a.db")
    assert not (tmp_path / "a.db").exists()
    _snap(s)
    assert (tmp_path / "a.db").exists()


def test_migration_creates_every_table_and_sets_user_version(tmp_path):
    s = Store(tmp_path / "a.db")
    with s.reader() as db:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        version = db.execute("PRAGMA user_version").fetchone()[0]
        mode = db.execute("PRAGMA journal_mode").fetchone()[0]
    assert {"meta", "snapshots", "collector_runs", "resource_state", "metrics", "findings", "mutes",
            "seeded_findings", "events", "activity_coverage", "snapshot_sections"} <= names
    assert version == SCHEMA_VERSION == 3
    assert mode == "wal"


def test_reopen_is_idempotent(tmp_path):
    _snap(Store(tmp_path / "a.db"))
    s2 = Store(tmp_path / "a.db")
    assert s2.latest_snapshot("123456789012") is not None


def test_newer_schema_is_refused(tmp_path):
    p = tmp_path / "a.db"
    conn = sqlite3.connect(p)
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    with pytest.raises(StoreTooNew):
        with Store(p).reader():
            pass


def test_reopen_after_abandoned_transaction(tmp_path):
    """Review Focus 2: an interrupted write leaves the store usable and the half-written row absent."""
    p = tmp_path / "a.db"
    s = Store(p)
    sid = _snap(s)
    conn = s._ensure()
    with s._write_lock:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO meta VALUES ('half', 'written')")
        with s._open_lock:
            s._drop_writer()               # the process "died" mid-transaction
    s2 = Store(p)
    assert s2.get_meta("half") is None
    assert s2.latest_snapshot("123456789012")["id"] == sid


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_file_is_0600_and_rechecked_at_open(tmp_path):
    p = tmp_path / "a.db"
    _snap(Store(p))
    assert (p.stat().st_mode & 0o777) == 0o600
    os.chmod(p, 0o644)
    Store(p).get_meta("x")
    assert (p.stat().st_mode & 0o777) == 0o600


def test_decimal_round_trip_exact(tmp_path):
    """Review Focus 5: exponent forms and long amounts come back digit for digit."""
    s = Store(tmp_path / "a.db")
    sid = _snap(s)
    values = [Decimal("0.0000001234"), Decimal("1.2E-7"), Decimal("12345678901234567890.123456789"), Decimal("839.72"), 7]
    s.write_metrics(sid, "cost", [M(f"m{i}", v, period="2026-09", unit="USD") for i, v in enumerate(values)], T0)
    with s.reader() as db:
        got = {r["name"]: r["value"] for r in db.execute("SELECT name, value FROM metrics")}
    assert [Decimal(got[f"m{i}"]) for i in range(len(values))] == [Decimal(v) for v in values]
    assert all(isinstance(v, str) for v in got.values())


def test_float_money_is_refused(tmp_path):
    s = Store(tmp_path / "a.db")
    sid = _snap(s)
    with pytest.raises(TypeError):
        s.write_metrics(sid, "cost", [M("m", 839.72)], T0)
    with pytest.raises(TypeError):
        dec_text(True)
    with pytest.raises(ValueError):
        dec_text(Decimal("NaN"))


def test_metrics_value_column_is_text(tmp_path):
    s = Store(tmp_path / "a.db")
    _snap(s)
    with s.reader() as db:
        cols = {r["name"]: r["type"] for r in db.execute("PRAGMA table_info(metrics)")}
    assert cols["value"] == "TEXT"


def test_no_float_in_store_source():
    for path in (ROOT / "store").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            assert not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "float"), \
                f"float( in {path}"


def test_finish_snapshot_complete_only_when_every_collector_covered_everything(tmp_path):
    s = Store(tmp_path / "a.db")
    a = _snap(s)
    s.record_collector(a, "ec2", "ok", None, 5, ["us-east-1", "eu-west-1"], ["us-east-1", "eu-west-1"], 0)
    assert s.finish_snapshot(a) == "complete"
    b = _snap(s)
    s.record_collector(b, "ec2", "ok", None, 5, ["us-east-1", "eu-west-1"], ["us-east-1"], 0)
    assert s.finish_snapshot(b) == "partial"
    c = _snap(s)
    assert s.finish_snapshot(c) == "partial"          # nothing ran is never complete


def test_resources_findings_and_latest(tmp_path):
    s = Store(tmp_path / "a.db")
    old = _snap(s, taken_at=T0 - timedelta(days=2))
    new = _snap(s, taken_at=T0)
    s.write_resources(new, "s3", [R("s3.bucket", "global", "b1", {"encrypted": True, "size": Decimal("1.5")})])
    s.write_findings(new, [F("s3.no_lifecycle", "s3.bucket", "global", "b1", "medium", {"pct": Decimal("50.0")})],
                     {"s3.no_lifecycle"})
    assert s.latest_snapshot("123456789012")["id"] == new != old
    with s.reader() as db:
        attrs = db.execute("SELECT attrs FROM resource_state").fetchone()[0]
        detail = db.execute("SELECT detail FROM findings").fetchone()[0]
    assert '"size": "1.5"' in attrs and '"pct": "50.0"' in detail


def test_write_findings_replaces_only_evaluated_rules(tmp_path):
    s = Store(tmp_path / "a.db")
    sid = _snap(s)
    s.write_findings(sid, [F("a", "x", "global", "1", "low"), F("b", "x", "global", "1", "low")], {"a", "b"})
    s.write_findings(sid, [], {"a"})
    with s.reader() as db:
        assert [r[0] for r in db.execute("SELECT rule FROM findings")] == ["b"]


def test_mutes_null_scoped_replace_and_expiry(tmp_path):
    s = Store(tmp_path / "a.db")
    s.add_mute(None, "iam.user_no_mfa", None, "accepted", None, now=T0)
    second = s.add_mute(None, "iam.user_no_mfa", None, "still accepted", T0 + timedelta(days=1), now=T0)
    rows = s.active_mutes(now=T0)          # the NULL-scoped mute was replaced, not duplicated
    assert [(r["reason"], r["expires_at"]) for r in rows] == [("still accepted", ts(T0 + timedelta(days=1)))]
    assert s.active_mutes(now=T0 + timedelta(days=2)) == []
    assert s.remove_mute(second) is True
    assert s.remove_mute(second) is False


def test_ts_round_trip_and_rejects_naive():
    assert parse_ts(ts(T0)) == T0
    with pytest.raises(ValueError):
        ts(datetime(2026, 1, 1))


def test_reader_not_blocked_by_open_write(tmp_path):
    s = Store(tmp_path / "a.db")
    sid = _snap(s)
    conn = s._ensure()
    with s._write_lock:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO meta VALUES ('k', 'v')")
        with s.reader() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM snapshots")]
        conn.execute("ROLLBACK")
    assert ids == [sid]


def test_reader_is_read_only(tmp_path):
    s = Store(tmp_path / "a.db")
    _snap(s)
    with s.reader() as db, pytest.raises(sqlite3.OperationalError):
        db.execute("DELETE FROM snapshots")


def test_dropped_store_closes_its_connection(tmp_path):
    """C4: a Store that is never close()d must not leak a ResourceWarning (which fails -W error elsewhere)."""
    import gc
    s = Store(tmp_path / "a.db")
    _snap(s)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        del s
        gc.collect()
    assert [w for w in caught if issubclass(w.category, ResourceWarning)] == []


def test_close_is_idempotent_and_reopens_on_use(tmp_path):
    s = Store(tmp_path / "a.db")
    _snap(s)
    s.close()
    s.close()
    assert s.latest_snapshot("123456789012") is not None


def test_point_in_time_metrics_have_an_empty_period_and_never_duplicate(tmp_path):
    s = Store(tmp_path / "a.db")
    sid = _snap(s)
    s.write_metrics(sid, "s3", [M("s3.bucket_count", Decimal(1))], T0)
    s.write_metrics(sid, "s3", [M("s3.bucket_count", Decimal(2))], T0)
    with s.reader() as db:
        rows = db.execute("SELECT period, value FROM metrics").fetchall()
    assert [tuple(r) for r in rows] == [("", "2")]


def test_clear_deletes_files_and_next_use_recreates(tmp_path):
    p = tmp_path / "a.db"
    s = Store(p)
    _snap(s)
    s.clear()
    assert not any(Path(str(p) + suf).exists() for suf in ("", "-wal", "-shm", "-journal"))
    assert s.latest_snapshot("123456789012") is None
    assert p.exists()


def test_clear_waits_for_readers(tmp_path):
    """Review Focus 4: a reader in progress finishes before the file goes; no crash."""
    s = Store(tmp_path / "a.db")
    _snap(s)
    entered, release, done = threading.Event(), threading.Event(), []

    def read():
        with s.reader() as db:
            entered.set()
            release.wait(5)
            done.append(db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0])

    t = threading.Thread(target=read)
    t.start()
    entered.wait(5)
    clearer = threading.Thread(target=s.clear)
    clearer.start()
    clearer.join(0.2)
    assert clearer.is_alive()                       # blocked on the reader
    release.set()
    t.join(5)
    clearer.join(5)
    assert done == [1] and not clearer.is_alive()


def test_clear_times_out_with_store_error(tmp_path):
    s = Store(tmp_path / "a.db")
    _snap(s)
    outcome = []

    def try_clear():
        try:
            s.clear(timeout=0.1)
            outcome.append("cleared")
        except StoreError:
            outcome.append("busy")

    with s.reader():
        t = threading.Thread(target=try_clear)
        t.start()
        t.join(5)
    assert outcome == ["busy"]
    assert s.latest_snapshot("123456789012") is not None      # nothing was deleted
