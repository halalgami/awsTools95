"""Deterministic demo fixtures for every domain. Fake data only; demo mode watermarks it everywhere.

Run: uv run python -m tools.make_demo_fixtures   → aws95/providers/demo/fixtures/*.json
Rules: accounts 111111111111/222222222222 only; RFC 5737 IPs; no e-mail addresses; money as strings.
"""
from __future__ import annotations

import json
import random
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "aws95" / "providers" / "demo" / "fixtures"
ANCHOR = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
A, B = "111111111111", "222222222222"
CENT = Decimal("0.01")
REGIONS2 = ["us-east-1", "eu-west-1"]


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _cov(requested: list[str], covered: list[str], denials: int = 0) -> dict:
    return {"regions_requested": requested, "regions_covered": covered, "denials": denials}


# ----- accounts: collector health -----
COLLECTORS = ["ec2", "s3", "lambda", "rds", "dynamodb", "iam", "vpc", "cloudtrail", "cost"]


def _health(acct: str, i: int, name: str) -> dict:
    h = {"collector": name, "status": "ok", "error": None, "duration_ms": 120 + 37 * i,
         "coverage": _cov(["global"] if name in ("iam", "cost") else REGIONS2,
                          ["global"] if name in ("iam", "cost") else REGIONS2)}
    if acct == B and name == "iam":
        h.update(status="denied", error="AccessDenied: iam:ListUsers", coverage=_cov(["global"], [], 1))
    if acct == B and name == "rds":
        h.update(status="partial", coverage=_cov(REGIONS2, ["us-east-1"]))
    return h


def health() -> dict:
    return {"collectors": [{"account": acct, "health": _health(acct, i, c)}
                           for acct in (A, B) for i, c in enumerate(COLLECTORS)]}


# ----- billing -----
SERVICES = [
    ("Amazon Elastic Compute Cloud - Compute", "142.18"), ("Amazon Relational Database Service", "96.40"),
    ("Amazon Simple Storage Service", "38.912"), ("Amazon Virtual Private Cloud", "31.20"),
    ("Amazon DynamoDB", "12.55"), ("AmazonCloudWatch", "9.6125"), ("AWS Lambda", "4.07"),
    ("AWS Key Management Service", "3.00"), ("Amazon Route 53", "1.50"), ("Tax", "72.917"),
]
UNCOVERED = ("AmazonCloudWatch", "AWS Key Management Service", "Amazon Route 53")


def _split(total: Decimal, keys: list[str], shares: list[str]) -> list[tuple[str, Decimal]]:
    """Fixed shares for all but the last key; the last key takes the exact remainder."""
    out = [(k, Decimal(s)) for k, s in zip(keys[:-1], shares)]
    out.append((keys[-1], total - sum((v for _, v in out), Decimal(0))))
    return out


# Account A's share of each breakdown; the last key takes A's exact remainder, and B is always total - A.
A_MTD, A_FORECAST, A_LAST_MONTH = Decimal("301.15"), Decimal("478.30"), Decimal("436.92")
A_SERVICE_SHARES = ["110.00", "70.00", "25.00", "20.00", "8.00", "5.00", "2.50", "2.00", "1.00"]
A_REGION_SHARES = ["130.00", "40.00", "30.00"]
A_USAGE_SHARES = ["55.00", "45.00", "8.00"]
A_TREND_SHARE = Decimal("0.73")      # A's share of every earlier month and day, rounded to the cent


def _by_account(total: list[tuple[str, Decimal]], a_shares: list[str], a_mtd: Decimal) -> tuple[dict, dict]:
    """Split one breakdown into per-account lists that each sum exactly to that account's MTD."""
    a_parts = _split(a_mtd, [k for k, _ in total], a_shares)
    b_parts = [(k, t - a) for (k, t), (_, a) in zip(total, a_parts)]
    assert all(v >= 0 for _, v in a_parts + b_parts), "an account share exceeds its total"
    assert sum((v for _, v in a_parts), Decimal(0)) == a_mtd
    return {A: a_parts, B: b_parts}


def _trend_split(points: list[dict], a_current: Decimal | None = None) -> tuple[list[dict], list[dict]]:
    """Per-account trend points: A takes a fixed share (or the given current figure), B the exact remainder."""
    a_pts, b_pts = [], []
    for i, p in enumerate(points):
        amount = Decimal(p["amount"])
        a_amount = a_current if (a_current is not None and i == len(points) - 1) else \
            (amount * A_TREND_SHARE).quantize(CENT)
        a_pts.append({**p, "amount": str(a_amount)})
        b_pts.append({**p, "amount": str(amount - a_amount)})
    return a_pts, b_pts


def billing() -> dict:
    mtd = sum((Decimal(a) for _, a in SERVICES), Decimal(0))
    rng = random.Random(9501)
    point = lambda period, amount, dim=None, key=None: {  # noqa: E731 - generator-local helper
        "period": period, "amount": str(amount), "unit": "USD", "dims": {dim: key} if dim else {}}
    months = [f"{2025 + (9 + i) // 12}-{(9 + i) % 12 + 1:02d}" for i in range(13)]
    monthly = [point(m, Decimal(rng.randint(45000, 70000)) * CENT) for m in months[:-1]]
    monthly.append(point(months[-1], mtd))
    start = (ANCHOR - timedelta(days=29)).date()
    daily = [point((start + timedelta(days=i)).isoformat(), Decimal(rng.randint(1200, 2800)) * CENT) for i in range(30)]
    forecast, last_month = Decimal("655.10"), Decimal("598.42")
    b_mtd = mtd - A_MTD
    service_total = [(s, Decimal(a)) for s, a in SERVICES]
    region_total = _split(mtd, ["us-east-1", "eu-west-1", "me-south-1", "global"], ["198.40", "61.0625", "47.19"])
    usage_total = _split(mtd, ["USE1-BoxUsage:t3.medium", "USE1-RDS:db.t3.small", "EUW1-TimedStorage-ByteHrs",
                               "Other"], ["88.32", "71.04", "12.905"])
    services = _by_account(service_total, A_SERVICE_SHARES, A_MTD)
    regions = _by_account(region_total, A_REGION_SHARES, A_MTD)
    usages = _by_account(usage_total, A_USAGE_SHARES, A_MTD)
    am, bm = _trend_split(monthly, A_MTD)
    ad, bd = _trend_split(daily)
    assert Decimal(bm[-1]["amount"]) == b_mtd
    summaries = {
        A: {"mtd": str(A_MTD), "forecast": str(A_FORECAST), "last_month": str(A_LAST_MONTH)},
        B: {"mtd": str(b_mtd), "forecast": str(forecast - A_FORECAST), "last_month": str(last_month - A_LAST_MONTH)},
    }
    by_account = {}
    for acct, m, d in ((A, am, ad), (B, bm, bd)):
        by_account[acct] = {
            "summary": {**summaries[acct], "currency": "USD", "as_of": _iso(ANCHOR)},
            "breakdown": {
                "service": [point("2026-10", v, "service", k) for k, v in services[acct]],
                "region": [point("2026-10", v, "region", k) for k, v in regions[acct]],
                "usage_type": [point("2026-10", v, "usage_type", k) for k, v in usages[acct]],
            },
            "uncovered": [point("2026-10", v, "service", k) for k, v in services[acct] if k in UNCOVERED],
            "monthly": m, "daily": d,
        }
    return {
        "summary": {"mtd": str(mtd), "forecast": str(forecast), "last_month": str(last_month), "currency": "USD",
                    "as_of": _iso(ANCHOR)},
        "by_account": by_account,
        "breakdown": {
            "service": [point("2026-10", v, "service", s) for s, v in service_total],
            "account": [point("2026-10", Decimal(summaries[k]["mtd"]), "account", k) for k in (A, B)],
            "region": [point("2026-10", v, "region", k) for k, v in region_total],
            "usage_type": [point("2026-10", v, "usage_type", k) for k, v in usage_total],
        },
        "monthly": monthly,
        "daily": daily,
        "free_tier": [
            {"service": "AWS Lambda", "metric": "Requests", "actual": "412000", "limit": "1000000",
             "forecast": "960000", "unit": "Requests", "period": "2026-10"},
            {"service": "Amazon Simple Storage Service", "metric": "Standard storage", "actual": "4.2",
             "limit": "5", "forecast": "5.6", "unit": "GB-Mo", "period": "2026-10"},
            {"service": "Amazon DynamoDB", "metric": "Storage", "actual": "0.8", "limit": "25",
             "forecast": None, "unit": "GB-Mo", "period": "2026-10"},
        ],
        "uncovered": [point("2026-10", Decimal(a), "service", s) for s, a in SERVICES if s in UNCOVERED],
    }


# ----- findings -----
RULES = [
    ("ec2-public-ip", "EC2 instance has a public IP", "Instances reachable from the internet.", "medium", {}),
    ("s3-public-access", "S3 bucket allows public access", "Block Public Access is off.", "critical", {}),
    ("iam-user-no-mfa", "IAM user without MFA", "Console users must use MFA.", "high", {}),
    ("iam-access-key-old", "Access key older than the limit", "Rotate long-lived keys.", "medium", {"max_age_days": 90}),
    ("rds-not-encrypted", "RDS storage not encrypted", "Storage encryption is off.", "high", {}),
    ("sg-open-ssh", "Security group opens SSH to the world", "0.0.0.0/0 on port 22.", "critical", {"port": 22}),
    ("lambda-old-runtime", "Lambda runtime is deprecated", "Runtime past end of support.", "low", {}),
    ("cloudtrail-off", "CloudTrail not enabled in region", "No trail records this region.", "high", {}),
    ("ebs-unencrypted", "EBS volume not encrypted", "Volume encryption is off.", "medium", {}),
    ("budget-missing", "No AWS Budget configured", "No budget alarms exist.", "low", {}),
]
_SEV = {r[0]: r[3] for r in RULES}
# (account, rule, rtype, region, rid, state, age_days, seeded, summary)
FINDINGS = [
    (A, "ec2-public-ip", "ec2.instance", "us-east-1", "i-0a1b2c3d4e5f60001", "open", 34, False, "Public IP 203.0.113.10"),
    (A, "s3-public-access", "s3.bucket", "us-east-1", "demo-static-site", "open", 3, False, "BlockPublicAcls=false"),
    (A, "iam-user-no-mfa", "iam.user", "global", "demo-ci-bot", "open", 120, True, "Console password, no MFA device"),
    (A, "iam-access-key-old", "iam.user", "global", "demo-ci-bot", "open", 95, False, "Key age 95 days"),
    (A, "rds-not-encrypted", "rds.instance", "us-east-1", "demo-orders-db", "open", 61, False, "StorageEncrypted=false"),
    (A, "sg-open-ssh", "ec2.securitygroup", "eu-west-1", "sg-0bastion0001", "open", 1, False, "22/tcp from 0.0.0.0/0"),
    (B, "lambda-old-runtime", "lambda.function", "us-east-1", "image-resizer", "open", 200, False, "Runtime python3.8"),
    (B, "sg-open-ssh", "ec2.securitygroup", "me-south-1", "sg-0etl00000002", "open", 12, False, "22/tcp from 0.0.0.0/0"),
    (B, "ec2-public-ip", "ec2.instance", "me-south-1", "i-0a1b2c3d4e5f60004", "open", 12, False, "Public IP 198.51.100.24"),
    (B, "ebs-unencrypted", "ec2.volume", "us-east-1", "vol-0demo00000001", "open", 45, False, "Encrypted=false"),
    (B, "cloudtrail-off", "account", "eu-west-1", "eu-west-1", "open", 300, True, "No trail covers eu-west-1"),
    (A, "ec2-public-ip", "ec2.instance", "eu-west-1", "i-0a1b2c3d4e5f60003", "resolved", 20, False, "Public IP removed"),
    (B, "s3-public-access", "s3.bucket", "eu-west-1", "demo-tmp-scratch", "resolved", 9, False, "Block Public Access on"),
    (A, "lambda-old-runtime", "lambda.function", "us-east-1", "report-builder", "accepted", 150, False, "Runtime nodejs16.x"),
    (B, "iam-user-no-mfa", "iam.user", "global", "break-glass", "accepted", 400, True, "Break-glass user"),
]
MUTES = {
    (A, "lambda-old-runtime", "report-builder"): {"id": 1, "account": A, "rule": "lambda-old-runtime", "rid": "report-builder",
                                                  "reason": "Migration scheduled for Q4", "muted_at": _iso(ANCHOR - timedelta(days=30)),
                                                  "expires_at": _iso(ANCHOR + timedelta(days=60))},
    (B, "iam-user-no-mfa", "break-glass"): {"id": 2, "account": None, "rule": "iam-user-no-mfa", "rid": "break-glass",
                                            "reason": "Break-glass user; MFA device kept in the safe",
                                            "muted_at": _iso(ANCHOR - timedelta(days=200)), "expires_at": None},
}


def findings() -> dict:
    rows = []
    for acct, rule, rtype, region, rid, state, age, seeded, summary in FINDINGS:
        rows.append({"account": acct, "rule": rule, "rtype": rtype, "region": region, "rid": rid,
                     "severity": _SEV[rule], "first_seen": _iso(ANCHOR - timedelta(days=age)), "seeded": seeded,
                     "state": state, "age_days": age, "detail": {"summary": summary},
                     "mute": MUTES.get((acct, rule, rid))})
    rules = [{"id": i, "title": t, "description": d, "severity": s, "params": p, "source": "labsnap",
              "enabled": i != "budget-missing"} for i, t, d, s, p in RULES]
    return {"rules": rules, "findings": rows,
            "uncollected": [[B, "iam-user-no-mfa"], [B, "iam-access-key-old"]]}


# ----- snapshots -----
def snapshots() -> dict:
    h = health()["collectors"]
    snaps = []
    for acct, base in ((A, 100), (B, 200)):
        for n, days in ((1, 14), (2, 7), (3, 0)):
            collectors = [row["health"] for row in h if row["account"] == acct]
            partial = acct == B and n == 3
            if not partial:   # older snapshots of B had a working IAM collector
                collectors = [dict(c, status="ok", error=None, coverage=_cov(c["coverage"]["regions_requested"],
                                                                             c["coverage"]["regions_requested"]))
                              for c in collectors]
            snaps.append({"id": base + n, "account": acct,
                          "caller_arn": f"arn:aws:iam::{acct}:role/labsnap-reader",
                          "taken_at": _iso(ANCHOR - timedelta(days=days)), "tool_version": "labsnap 0.9.0",
                          "status": "partial" if partial else "complete", "collectors": collectors})
    d = lambda kind, rtype, region, rid, deltas=None, reason=None, partial=False: {  # noqa: E731
        "kind": kind, "rtype": rtype, "region": region, "rid": rid, "deltas": deltas or {}, "reason": reason,
        "partial": partial}
    diffs = {
        "101-102": [d("added", "ec2.instance", "us-east-1", "i-0a1b2c3d4e5f60002"),
                    d("changed", "ec2.instance", "us-east-1", "i-0a1b2c3d4e5f60001",
                      {"state": ["stopped", "running"]})],
        "102-103": [d("changed", "ec2.instance", "us-east-1", "i-0a1b2c3d4e5f60002", {"state": ["running", "stopped"]}),
                    d("added", "s3.bucket", "us-east-1", "demo-static-site"),
                    d("removed", "lambda.function", "us-east-1", "old-cron-job")],
        "101-103": [d("added", "ec2.instance", "us-east-1", "i-0a1b2c3d4e5f60002"),
                    d("added", "s3.bucket", "us-east-1", "demo-static-site"),
                    d("removed", "lambda.function", "us-east-1", "old-cron-job")],
        "201-202": [d("added", "dynamodb.table", "us-east-1", "demo-sessions")],
        "202-203": [d("changed", "lambda.function", "us-east-1", "image-resizer", {"memory_mb": [512, 1024]}),
                    d("excluded", "iam.user", "global", "*", reason="collector failed: iam (AccessDenied)"),
                    d("excluded", "rds.instance", "eu-west-1", "*", reason="region not covered: eu-west-1"),
                    d("excluded", "rds.instance", "us-east-1", "*", reason="partial data: attributes denied",
                      partial=True)],
    }
    metrics = []
    for name, unit, base_value in (("resources.total", "count", 40), ("ec2.instances.running", "count", 3),
                                   ("s3.buckets", "count", 3), ("cost.mtd", "USD", None)):
        for acct in (A, B):
            for week in range(8):
                observed = ANCHOR - timedelta(days=7 * (7 - week))
                if base_value is None:
                    value = str(Decimal(150 + 31 * week + (17 if acct == B else 0)) + Decimal("0.25"))
                else:
                    value = str(base_value + week // 2 + (2 if acct == B else 0))
                metrics.append({"name": name, "dims": {"account": acct}, "period": observed.date().isoformat(),
                                "value": value, "unit": unit, "observed_at": _iso(observed),
                                "restated": week == 5})
    return {"snapshots": snaps, "diffs": diffs, "metrics": metrics}


# ----- activity -----
HUMANS = [("IAMUser", "ops-alice"), ("IAMUser", "dev-bob"), ("AssumedRole", "admin-sso/carol")]
MACHINES = [("AssumedRole", "ci-deploy-role"), ("AssumedRole", "lambda-exec-role"),
            ("AWSService", "ec2.amazonaws.com"), ("AWSService", "autoscaling.amazonaws.com")]
# (source, name, read_only, risk_level, risk_rule, weight, humans_only)
ACTIONS = [
    ("ec2.amazonaws.com", "DescribeInstances", True, 0, None, 30, False),
    ("ec2.amazonaws.com", "RunInstances", False, 1, "ec2-launch", 4, False),
    ("ec2.amazonaws.com", "AuthorizeSecurityGroupIngress", False, 3, "sg-open-world", 1, True),
    ("s3.amazonaws.com", "GetObject", True, 0, None, 25, False),
    ("s3.amazonaws.com", "PutBucketPolicy", False, 2, "s3-policy-change", 2, True),
    ("iam.amazonaws.com", "CreateAccessKey", False, 2, "iam-key-created", 1, True),
    ("iam.amazonaws.com", "AttachUserPolicy", False, 2, "iam-policy-change", 1, True),
    ("signin.amazonaws.com", "ConsoleLogin", False, 1, "console-login", 6, True),
    ("lambda.amazonaws.com", "UpdateFunctionCode", False, 1, "lambda-code-change", 3, False),
    ("sts.amazonaws.com", "AssumeRole", True, 0, None, 20, False),
    ("cloudtrail.amazonaws.com", "StopLogging", False, 3, "cloudtrail-stopped", 1, True),
    ("kms.amazonaws.com", "Decrypt", True, 0, None, 15, False),
]


def _arn(source: str, acct: str, region: str, n: int) -> list[str]:
    if source == "s3.amazonaws.com":
        return [f"arn:aws:s3:::{['demo-logs-archive', 'demo-static-site'][n % 2]}"]
    if source == "ec2.amazonaws.com":
        return [f"arn:aws:ec2:{region}:{acct}:instance/i-0a1b2c3d4e5f6000{1 + n % 4}"]
    if source == "iam.amazonaws.com":
        return [f"arn:aws:iam::{acct}:user/demo-ci-bot"]
    if source == "lambda.amazonaws.com":
        return [f"arn:aws:lambda:{region}:{acct}:function:report-builder"]
    return []


def activity() -> dict:
    rng = random.Random(1995)
    weights = [a[5] for a in ACTIONS]
    events = []
    for i in range(259):
        source, name, read_only, risk, rule, _w, humans_only = rng.choices(ACTIONS, weights)[0]
        ptype, pname = rng.choice(HUMANS if humans_only else HUMANS + MACHINES * 3)
        acct = rng.choice([A, A, B])
        region = "us-east-1" if source in ("iam.amazonaws.com", "signin.amazonaws.com") else \
            rng.choice(["us-east-1", "us-east-1", "eu-west-1", "me-south-1"])
        human = ptype == "IAMUser" or pname.startswith("admin-sso/")
        events.append({
            "event_id": f"evt-{i + 1:05d}", "account": acct,
            "time_utc": _iso(ANCHOR - timedelta(minutes=rng.randint(1, 30 * 24 * 60))),
            "principal_type": ptype, "principal_name": pname, "is_human": human, "source": source, "name": name,
            "read_only": read_only, "source_ip": f"203.0.113.{rng.randint(2, 250)}" if human else None,
            "user_agent": rng.choice(["aws-cli/2.15.0", "console.amazonaws.com"]) if human else "AWS Internal",
            "resource_arns": _arn(source, acct, region, i), "region": region,
            "risk_level": risk, "risk_rule": rule})
    events.append({  # the newest event: root signs in (always risk 3)
        "event_id": "evt-00260", "account": A, "time_utc": _iso(ANCHOR), "principal_type": "Root",
        "principal_name": "root", "is_human": True, "source": "signin.amazonaws.com", "name": "ConsoleLogin",
        "read_only": False, "source_ip": "198.51.100.7", "user_agent": "console.amazonaws.com",
        "resource_arns": [], "region": "us-east-1", "risk_level": 3, "risk_rule": "root-activity"})
    events.sort(key=lambda e: (e["time_utc"], e["event_id"]), reverse=True)
    span = {"start": _iso(ANCHOR - timedelta(days=30)), "end": _iso(ANCHOR)}
    imported = {"start": _iso(ANCHOR - timedelta(days=60)), "end": _iso(ANCHOR - timedelta(days=30))}
    history = {
        "accounts": [
            {"account": A, "days_requested": 30, "unfinished": False,
             "regions": [{"region": r, "looked_up": [span], "imported": [imported] if r == "us-east-1" else []}
                         for r in ("eu-west-1", "me-south-1", "us-east-1")]},
            {"account": B, "days_requested": 30, "unfinished": False,
             "regions": [{"region": r, "looked_up": [span], "imported": []} for r in ("eu-west-1", "us-east-1")]}],
        "denied_changes_since": _iso(ANCHOR - timedelta(days=30)),
        "denied_reads_since": _iso(ANCHOR - timedelta(days=60)),
        "running": None, "progress": None}
    return {"anchor": _iso(ANCHOR), "events": events, "history": history}


# ----- insights -----
def insights() -> dict:
    sections = {
        "insights": [("Spend", ("Compute and RDS are the two largest services this month.",
                                "Tax is shown as its own line, as Cost Explorer reports it.")),
                     ("Inventory", ("Most resources live in us-east-1.",))],
        "oddities": [("Unusual", ("A root console sign-in happened today.",
                                  "CloudTrail logging was stopped once this month."))],
        "improvements": [("Security", ("Two security groups allow SSH from anywhere.",
                                       "One RDS instance stores data unencrypted.")),
                         ("Cost", ("A stopped EC2 instance still has volumes attached.",))],
        "advice": [("Next steps", ("Enable MFA for every console user.", "Turn on CloudTrail in eu-west-1.",
                                   "Review the deprecated Lambda runtime."))],
    }
    return {"reports": {k: [{"title": t, "bullets": list(b)} for t, b in v] for k, v in sections.items()}}


def fixtures() -> dict[str, dict]:
    return {"health.json": health(), "billing.json": billing(), "findings.json": findings(),
            "snapshots.json": snapshots(), "activity.json": activity(), "insights.json": insights()}


def write_all(out_dir: Path = OUT) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, obj in fixtures().items():
        path = out_dir / name
        path.write_text(json.dumps(obj, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        written.append(path)
    return written


def main() -> int:
    for path in write_all():
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
