from datetime import datetime, timedelta, timezone
from decimal import Decimal

from aws95.aws.engines.labsnap.analyze.findings import (apply_mutes, evaluate_snapshot, open_findings,
                                                        resolved_findings)
from aws95.aws.engines.labsnap.model import Finding, Resource
from aws95.store import Store, ts
from tests.engine_helpers import build_snapshot

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
A, B = "123456789012", "111111111111"


def f(rid, rule="s3.no_lifecycle", region="global", sev="medium", detail=None):
    return Finding(rule, "s3.bucket", region, rid, sev, detail)


def test_open_then_resolved_with_first_seen_and_age(tmp_path):
    s = Store(tmp_path / "x.db")
    build_snapshot(s, T0, findings=[f("b1", detail={"why": "no rules"}), f("b2")])
    build_snapshot(s, T0 + timedelta(days=5), findings=[f("b1", detail={"why": "no rules"})])
    now = T0 + timedelta(days=10)
    [b1] = open_findings(s, now=now)
    assert (b1.rid, b1.first_seen, b1.last_seen, b1.age_days, b1.detail) == \
        ("b1", ts(T0), ts(T0 + timedelta(days=5)), 10, {"why": "no rules"})
    assert [x.rid for x in resolved_findings(s, now=now)] == ["b2"]


def test_absence_is_not_resolution_when_the_collector_did_not_look(tmp_path):
    s = Store(tmp_path / "x.db")
    build_snapshot(s, T0, findings=[f("b1")])
    build_snapshot(s, T0 + timedelta(days=1), status="failed", covered=(), findings=[], rules={"s3.no_lifecycle"})
    now = T0 + timedelta(days=2)
    assert [(x.rid, x.unverified) for x in open_findings(s, now=now)] == [("b1", True)]
    assert resolved_findings(s, now=now) == []


def test_absence_is_not_resolution_in_a_region_the_snapshot_skipped(tmp_path):
    s = Store(tmp_path / "x.db")
    rule = "logs.no_retention"
    build_snapshot(s, T0, collector="logs", requested=("eu-west-1",), covered=("eu-west-1",),
                   findings=[Finding(rule, "logs.group", "eu-west-1", "g1", "medium")])
    build_snapshot(s, T0 + timedelta(days=1), collector="logs", requested=("eu-west-1",), covered=(),
                   findings=[], rules={rule})
    assert [x.unverified for x in open_findings(s, now=T0 + timedelta(days=2))] == [True]


def test_one_accounts_fresh_snapshot_never_resolves_another(tmp_path):
    s = Store(tmp_path / "x.db")
    build_snapshot(s, T0, account=A, findings=[f("b1")])
    build_snapshot(s, T0 + timedelta(days=1), account=B, findings=[])
    now = T0 + timedelta(days=2)
    assert [x.account for x in open_findings(s, now=now)] == [A]
    assert open_findings(s, accounts=[B], now=now) == []


def test_seeded_first_seen_wins_when_earlier(tmp_path):
    s = Store(tmp_path / "x.db")
    build_snapshot(s, T0, findings=[f("b1")])
    with s._tx() as db:      # seeding has no UI in this release; the table is written directly here
        db.execute("INSERT INTO seeded_findings VALUES (?, ?, ?, ?, ?)",
                   (A, "s3.no_lifecycle", "b1", ts(T0 - timedelta(days=30)), "audit"))
    [st] = open_findings(s, now=T0)
    assert st.seeded and st.first_seen == ts(T0 - timedelta(days=30)) and st.age_days == 30


def test_as_of_ages_against_that_snapshot(tmp_path):
    s = Store(tmp_path / "x.db")
    a = build_snapshot(s, T0, findings=[f("b1")])
    build_snapshot(s, T0 + timedelta(days=5), findings=[])
    [st] = open_findings(s, as_of=a, now=T0 + timedelta(days=100))
    assert st.age_days == 0


def test_mutes_annotate_and_never_remove(tmp_path):
    s = Store(tmp_path / "x.db")
    build_snapshot(s, T0, findings=[f("b1"), f("b2")])
    s.add_mute(A, "s3.no_lifecycle", "b1", "accepted risk", None, now=T0)
    states = apply_mutes(s, open_findings(s, now=T0), now=T0)
    assert {x.rid: (x.muted, x.mute["reason"] if x.mute else None) for x in states} == \
        {"b1": (True, "accepted risk"), "b2": (False, None)}


def test_expired_and_rule_wide_mutes(tmp_path):
    s = Store(tmp_path / "x.db")
    build_snapshot(s, T0, findings=[f("b1"), f("b2")])
    s.add_mute(A, "s3.no_lifecycle", "b1", "for a day", T0 + timedelta(days=1), now=T0)
    later = T0 + timedelta(days=2)
    assert not any(x.muted for x in apply_mutes(s, open_findings(s, now=later), now=later))
    s.add_mute(None, "s3.no_lifecycle", None, "whole rule", None, now=T0)
    assert all(x.muted for x in apply_mutes(s, open_findings(s, now=later), now=later))


class BadBucket:
    id = "t.bad_bucket"
    severity = "low"
    params: dict = {}

    def evaluate(self, snap, params):
        for r in snap.resources("s3.bucket"):
            if r.attrs.get("bad"):
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity, {"pct": Decimal("50.0")})


def test_evaluate_snapshot_writes_and_closes(tmp_path):
    s = Store(tmp_path / "x.db")
    sid = build_snapshot(s, T0, resources=[Resource("s3.bucket", "global", "b1", {"bad": True})])
    assert evaluate_snapshot(s, sid, [BadBucket()]) == 1
    sid2 = build_snapshot(s, T0 + timedelta(days=1), resources=[Resource("s3.bucket", "global", "b1", {"bad": False})])
    assert evaluate_snapshot(s, sid2, [BadBucket()]) == 0
    assert [x.rid for x in resolved_findings(s, now=T0 + timedelta(days=1))] == ["b1"]
