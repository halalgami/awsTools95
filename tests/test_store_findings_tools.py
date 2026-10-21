from datetime import datetime, timezone

from aws95.store import SCHEMA_VERSION

T = datetime(2026, 10, 20, 9, tzinfo=timezone.utc)


def test_schema_v3_has_rule_overrides(make_store):
    s = make_store()
    with s.reader() as db:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert SCHEMA_VERSION == 3 and "rule_overrides" in names


def test_rule_override_round_trip(make_store):
    s = make_store()
    s.set_rule_override("ec2.sg_open_admin_port", disabled=False, severity="critical", params={"ports": [22]},
                        changed_at=T)
    s.set_rule_override("s3.no_lifecycle", disabled=True, severity=None, params=None, changed_at=T)
    o = s.rule_overrides()
    assert o["ec2.sg_open_admin_port"] == {"disabled": False, "severity": "critical", "params": {"ports": [22]}}
    assert o["s3.no_lifecycle"] == {"disabled": True, "severity": None, "params": None}
    s.clear_rule_override("s3.no_lifecycle")
    assert "s3.no_lifecycle" not in s.rule_overrides()


def test_seed_round_trip(make_store):
    s = make_store()
    s.seed_finding("123456789012", "iam.user_no_mfa", "alice", T, "Q2 audit")
    with s.reader() as db:
        row = db.execute("SELECT * FROM seeded_findings").fetchone()
    assert (row["first_seen"], row["source"]) == ("2026-10-20T09:00:00Z", "Q2 audit")
    s.seed_finding("123456789012", "iam.user_no_mfa", "alice", T, "")
    with s.reader() as db:
        assert db.execute("SELECT source FROM seeded_findings").fetchone()[0] == "manual"
    assert s.unseed_finding("123456789012", "iam.user_no_mfa", "alice") is True
    assert s.unseed_finding("123456789012", "iam.user_no_mfa", "alice") is False
