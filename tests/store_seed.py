"""A deterministic seeded aws95.db for provider tests and live goldens (no AWS).

dev  (123456789012): two snapshots (8 days and 2 hours before PINNED); every collector OK; Billing on; s3 has 1 denial.
prod (210987654321): one snapshot 5 hours before PINNED; ec2 did not reach eu-west-1; iam was denied; no Billing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from aws95.contracts.accounts import RosterEntry
from aws95.store import Store, ts

PINNED = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
DEV, PROD = "123456789012", "210987654321"
ROSTER = (RosterEntry("dev", DEV), RosterEntry("prod", PROD))
REGIONS = ("us-east-1", "eu-west-1")
ALL = ("account", "budgets", "cloudtrail", "cognito", "cost", "dynamodb", "ec2", "ecr", "ecs", "elb", "freetier",
       "guardrails", "iam", "kms", "lambda", "logs", "rds", "s3", "secrets")
GLOBAL = {"account", "budgets", "cost", "freetier", "iam", "s3"}


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
    value: Decimal
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


def _runs(*, skip=(), failed=None, uncovered=None, denials=None):
    failed, uncovered, denials = failed or {}, uncovered or {}, denials or {}
    out = {}
    for c in ALL:
        if c in skip:
            continue
        requested = ("global",) if c in GLOBAL else REGIONS
        if c in failed:
            out[c] = ("failed", requested, (), 0, failed[c])
            continue
        covered = tuple(r for r in requested if r not in uncovered.get(c, ()))
        missing = sorted(set(requested) - set(covered))
        error = f"{', '.join(missing)}: Can't reach AWS ({c}). Check your network or VPN." if missing else None
        out[c] = ("ok", requested, covered, denials.get(c, 0), error)
    return out


def _sections(s: Store, sid: int, runs: dict, taken_at: datetime) -> None:
    """Sections matching the seeded collector runs (resumable-snapshots spec §4): done where covered, failed with the
    run's error elsewhere. Written directly so the seeded collector summaries stay exactly as declared."""
    s.plan_sections(sid, [(c, r) for c, (_, requested, *_rest) in runs.items() for r in requested])
    started, ended = ts(taken_at), ts(taken_at + timedelta(seconds=1))
    with s._tx() as db:
        for c, (_, requested, covered, _denials, error) in runs.items():
            for r in requested:
                ok = r in covered
                rows = db.execute("SELECT COUNT(*) FROM resource_state WHERE snapshot_id=? AND collector=? AND region=?",
                                  (sid, c, r)).fetchone()[0]
                db.execute("UPDATE snapshot_sections SET state=?, started_at=?, ended_at=?, rows=?, error=? "
                           "WHERE snapshot_id=? AND collector=? AND region=?",
                           ("done" if ok else "failed", started, ended, rows, None if ok else (error or "not covered"),
                            sid, c, r))


def _snapshot(s: Store, account: str, taken_at: datetime, runs: dict, resources: dict, metrics: dict, findings: list):
    sid = s.begin_snapshot(account, f"arn:aws:iam::{account}:user/auditor", "3.0.0", REGIONS, taken_at=taken_at)
    for collector, rows in resources.items():
        s.write_resources(sid, collector, rows)
    for collector, rows in metrics.items():
        s.write_metrics(sid, collector, rows, taken_at)
    for collector, (status, requested, covered, denials, error) in runs.items():
        s.record_collector(sid, collector, status, error, 1200, requested, covered, denials)
    _sections(s, sid, runs, taken_at)
    s.write_findings(sid, findings, {f.rule for f in findings})
    s.finish_snapshot(sid)
    return sid


def _cost(period_now: str, period_prev: str, prev_total: str) -> list[M]:
    usd = "USD"
    return [M("cost.total.monthly", Decimal(prev_total), period=period_prev, unit=usd),
            M("cost.total.monthly", Decimal("45.6789"), period=period_now, unit=usd),
            M("cost.service.monthly", Decimal("30.1234"), {"service": "Amazon Elastic Compute Cloud - Compute"},
              period_now, usd),
            M("cost.service.monthly", Decimal("15.5555"), {"service": "Amazon Simple Storage Service"},
              period_now, usd),
            M("cost.total.daily", Decimal("20.0000"), period=f"{period_now}-01", unit=usd),
            M("cost.total.daily", Decimal("25.6789"), period=f"{period_now}-02", unit=usd),
            M("cost.forecast.remaining", Decimal("1300.00"), period=period_now, unit=usd)]


FREETIER_DIMS = {"service": "AmazonEC2", "usage_type": "BoxUsage:t2.micro", "region": "us-east-1", "day_of_month": 2}


def _freetier() -> list[M]:
    return [M("freetier.usage", Decimal("50.0"), dict(FREETIER_DIMS), "2026-10", "%"),
            M("freetier.actual", Decimal("375.0"), dict(FREETIER_DIMS), "2026-10", "Hrs"),
            M("freetier.limit", Decimal("750.0"), dict(FREETIER_DIMS), "2026-10", "Hrs"),
            M("freetier.forecast", Decimal("700.0"), dict(FREETIER_DIMS), "2026-10", "Hrs")]


def seed_store(path) -> Store:
    """The caller owns the returned Store and closes it (the test fixtures do, at teardown)."""
    s = Store(path)
    # dev, older snapshot (8 days ago): i-0ccc exists then; carol has no MFA then.
    _snapshot(s, DEV, PINNED - timedelta(days=8), _runs(),
              {"ec2": [R("ec2.instance", "us-east-1", "i-0aaa", {"state": "running", "instance_type": "t3.micro"}),
                       R("ec2.instance", "us-east-1", "i-0ccc", {"state": "stopped", "instance_type": "t3.small"})],
               "s3": [R("s3.bucket", "global", "example-logs", {"encrypted": True, "has_lifecycle": False})]},
              {"cost": [M("cost.total.monthly", Decimal("1234.50"), period="2026-09", unit="USD")]},
              [F("s3.no_lifecycle", "s3.bucket", "global", "example-logs", "medium"),
               F("iam.user_no_mfa", "iam.user", "global", "alice", "high"),
               F("iam.user_no_mfa", "iam.user", "global", "carol", "high")])
    # dev, newest snapshot (2 hours ago).
    _snapshot(s, DEV, PINNED - timedelta(hours=2), _runs(denials={"s3": 1}),
              {"ec2": [R("ec2.instance", "us-east-1", "i-0aaa", {"state": "running", "instance_type": "t3.micro"}),
                       R("ec2.instance", "eu-west-1", "i-0bbb", {"state": "stopped", "instance_type": "t3.small"}),
                       R("ec2.security_group", "us-east-1", "sg-0123", {"open_ports": [22], "all_ports_open": False})],
               "s3": [R("s3.bucket", "global", "example-logs", {"encrypted": True, "has_lifecycle": False}),
                      R("s3.bucket", "global", "example-site", {"encrypted": True}, partial=True)],
               "iam": [R("iam.user", "global", "alice", {"has_mfa": False}),
                       R("iam.user", "global", "bob", {"has_mfa": True})]},
              {"cost": _cost("2026-10", "2026-09", "1234.56"), "freetier": _freetier(),
               "s3": [M("s3.bucket_count", Decimal(2))]},
              [F("s3.no_lifecycle", "s3.bucket", "global", "example-logs", "medium"),
               F("iam.user_no_mfa", "iam.user", "global", "alice", "high"),
               F("ec2.sg_open_admin_port", "ec2.security_group", "us-east-1", "sg-0123", "high", {"ports": [22]})])
    # prod, one snapshot (5 hours ago): eu-west-1 unreachable for ec2, iam denied, no Billing.
    _snapshot(s, PROD, PINNED - timedelta(hours=5),
              _runs(skip={"cost"}, failed={"iam": "global: Permission denied for iam:ListUsers."},
                    uncovered={"ec2": ("eu-west-1",)}),
              {"ec2": [R("ec2.instance", "us-east-1", "i-0ddd", {"state": "running", "instance_type": "m5.large"})],
               "s3": [R("s3.bucket", "global", "prod-data", {"encrypted": True, "has_lifecycle": False})]},
              {"freetier": _freetier()},
              [F("s3.no_lifecycle", "s3.bucket", "global", "prod-data", "medium")])
    s.add_mute(DEV, "ec2.sg_open_admin_port", "sg-0123", "Bastion host; the NACL limits SSH to the office.", None,
               now=PINNED - timedelta(days=1))
    return s
