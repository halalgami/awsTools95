import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from aws95.store import SCHEMA_VERSION, Store

T0 = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)
A = "123456789012"


@dataclass
class R:
    rtype: str
    region: str
    rid: str
    attrs: dict = field(default_factory=dict)
    partial: bool = False


@dataclass
class M:
    name: str
    value: object
    dims: dict = field(default_factory=dict)
    period: str | None = None
    unit: str | None = None


def _snap(s):
    return s.begin_snapshot(A, f"arn:aws:iam::{A}:user/auditor", "3.0.0", ("us-east-1", "eu-west-1"), taken_at=T0)


def _runs(s, sid):
    with s.reader() as db:
        return {r["collector"]: dict(r) for r in db.execute("SELECT * FROM collector_runs WHERE snapshot_id=?", (sid,))}


def test_schema_is_v2_with_sections(make_store):
    s = make_store()
    with s.reader() as db:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        version = db.execute("PRAGMA user_version").fetchone()[0]
    assert "snapshot_sections" in names and version == SCHEMA_VERSION >= 2


def test_v1_database_is_migrated_with_sections_from_collector_runs(tmp_path):
    from aws95.store.schema import MIGRATIONS
    p = tmp_path / "old.db"
    conn = sqlite3.connect(p)
    conn.executescript(f"BEGIN;\n{MIGRATIONS[0]}\nPRAGMA user_version = 1;\nCOMMIT;")
    conn.execute("INSERT INTO snapshots(id, account, caller_arn, taken_at, tool_version, status, regions) "
                 "VALUES (1, ?, 'arn', '2026-10-01T00:00:00Z', '3.0.0', 'partial', '[\"eu-west-1\",\"us-east-1\"]')",
                 (A,))
    conn.execute("INSERT INTO collector_runs VALUES (1, 'lambda', 'failed', 'timed out after 180s', 1, "
                 "'[\"eu-west-1\",\"us-east-1\"]', '[\"us-east-1\"]', 0)")
    conn.execute("INSERT INTO resource_state VALUES (1, 'lambda', 'lambda.function', 'us-east-1', 'f1', '{}', 0)")
    conn.commit()
    conn.close()
    s = Store(p)
    try:
        rows = {(r["region"], r["state"], r["rows"], r["error"]) for r in s.sections(1)}
        assert rows == {("eu-west-1", "failed", 0, "timed out after 180s"), ("us-east-1", "done", 1, None)}
    finally:
        s.close()


def test_end_section_writes_data_state_and_collector_summary_together(make_store):
    s = make_store()
    sid = _snap(s)
    s.plan_sections(sid, [("lambda", "us-east-1"), ("lambda", "eu-west-1"), ("iam", "global")])
    s.start_section(sid, "lambda", "us-east-1", T0)
    s.end_section(sid, "lambda", "us-east-1", "done", ended_at=T0 + timedelta(seconds=3),
                  resources=[R("lambda.function", "us-east-1", "f1")],
                  metrics=[M("lambda.function_count", Decimal(1), {"region": "us-east-1"})], observed_at=T0)
    s.end_section(sid, "lambda", "eu-west-1", "timed_out", ended_at=T0 + timedelta(seconds=300),
                  error="timed out after 300s")
    r = _runs(s, sid)["lambda"]
    assert r["status"] == "ok" and r["regions_covered"] == '["us-east-1"]'
    assert r["regions_requested"] == '["eu-west-1", "us-east-1"]' and r["error"] == "eu-west-1: timed out after 300s"
    by = {x["region"]: x for x in s.sections(sid)}
    assert by["us-east-1"]["rows"] == 1 and by["eu-west-1"]["state"] == "timed_out"
    assert by["global"]["state"] == "waiting"           # iam global was planned, never run


def test_partial_section_counts_as_covered_and_sums_denials(make_store):
    s = make_store()
    sid = _snap(s)
    s.plan_sections(sid, [("s3", "global")])
    s.end_section(sid, "s3", "global", "partial", ended_at=T0, denials=9)
    r = _runs(s, sid)["s3"]
    assert r["status"] == "ok" and r["denials"] == 9 and r["regions_covered"] == '["global"]' and r["error"] is None


def test_denied_section_is_not_covered(make_store):
    s = make_store()
    sid = _snap(s)
    s.plan_sections(sid, [("kms", "eu-west-1")])
    s.end_section(sid, "kms", "eu-west-1", "denied", ended_at=T0, error="Permission denied for this request.")
    r = _runs(s, sid)["kms"]
    assert r["status"] == "failed" and r["regions_covered"] == "[]"
    assert r["error"] == "eu-west-1: Permission denied for this request."


def test_finish_is_complete_only_when_every_section_is_covered(make_store):
    s = make_store()
    sid = _snap(s)
    s.plan_sections(sid, [("ec2", "us-east-1"), ("ec2", "eu-west-1")])
    s.end_section(sid, "ec2", "us-east-1", "done", ended_at=T0)
    assert s.finish_snapshot(sid) == "partial"
    s.end_section(sid, "ec2", "eu-west-1", "partial", ended_at=T0, denials=1)
    assert s.finish_snapshot(sid) == "complete"


def test_resumable_sections_skip_finished_and_denied_unless_asked(make_store):
    s = make_store()
    sid = _snap(s)
    plan = [("a", "x"), ("b", "x"), ("c", "x"), ("d", "x"), ("e", "x"), ("f", "x"), ("g", "x")]
    s.plan_sections(sid, plan)
    for (c, r), state in zip(plan[1:], ("done", "partial", "denied", "timed_out", "failed", "stopped")):
        s.end_section(sid, c, r, state, ended_at=T0)
    assert s.resumable_sections(sid) == [("a", "x"), ("e", "x"), ("f", "x"), ("g", "x")]
    assert ("d", "x") in s.resumable_sections(sid, retry_denied=True)


def test_plan_sections_is_idempotent(make_store):
    s = make_store()
    sid = _snap(s)
    s.plan_sections(sid, [("ec2", "us-east-1")])
    s.end_section(sid, "ec2", "us-east-1", "done", ended_at=T0)
    s.plan_sections(sid, [("ec2", "us-east-1")])
    assert [r["state"] for r in s.sections(sid)] == ["done"]


def test_running_sections_read_as_stopped_after_reopen(tmp_path):
    """Review Focus 1: a crash mid-run never leaves a section 'running' forever."""
    p = tmp_path / "a.db"
    s = Store(p)
    sid = _snap(s)
    s.plan_sections(sid, [("lambda", "us-east-1")])
    s.start_section(sid, "lambda", "us-east-1", T0)
    s.close()
    s2 = Store(p)
    try:
        [row] = s2.sections(sid)
        assert row["state"] == "stopped" and ("lambda", "us-east-1") in s2.resumable_sections(sid)
    finally:
        s2.close()


def test_end_section_rejects_a_non_end_state(make_store):
    s = make_store()
    sid = _snap(s)
    s.plan_sections(sid, [("ec2", "us-east-1")])
    with pytest.raises(ValueError):
        s.end_section(sid, "ec2", "us-east-1", "running", ended_at=T0)
