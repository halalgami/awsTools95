"""A deterministic local activity history (no AWS) for provider tests and the live Activity goldens.

dev  (123456789012): every lookup pass covered in us-east-1 and eu-west-1 from 7 days to 1 hour before PINNED;
                     one imported range in us-east-1 (2026-09-07 → 2026-09-17) holding a denied read.
prod (210987654321): every pass covered in us-east-1 for 3 of its 7 chosen days, so its load is unfinished.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aws95.aws.engines.ctwatch.scope import LOOKUP_PASSES
from aws95.aws.engines.ctwatch.service import days_key, regions_key
from aws95.contracts.accounts import RosterEntry
from aws95.store import EventRow, Store

PINNED = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
DEV, PROD = "123456789012", "210987654321"
ROSTER = (RosterEntry("dev", DEV), RosterEntry("prod", PROD))
IMPORT_START = datetime(2026, 9, 7, tzinfo=timezone.utc)
IMPORT_END = datetime(2026, 9, 17, tzinfo=timezone.utc)


def _row(n, account, region, hours_ago, ptype, name, human, source, action, *, risk=0, rule=None, error=None,
         arns=(), read_only=False, origin="lookup") -> EventRow:
    at = PINNED - timedelta(hours=hours_ago)
    detail = {"eventID": f"ev-{n:04d}", "eventTime": at.strftime("%Y-%m-%dT%H:%M:%SZ"), "eventSource": source,
              "eventName": action, "awsRegion": region, "readOnly": read_only,
              "userIdentity": {"type": ptype, "userName": name}, "recipientAccountId": account}
    if error:
        detail["errorCode"] = error
    return EventRow(f"ev-{n:04d}", account, region, at, ptype, name, human, source, action, read_only, error,
                    "203.0.113.10" if human else None, "console.amazonaws.com" if human else "AWS Internal",
                    tuple(arns), risk, rule, origin, json.dumps(detail, sort_keys=True))


EVENTS = (
    _row(1, DEV, "us-east-1", 2, "Root", "root", True, "signin.amazonaws.com", "ConsoleLogin"),
    _row(2, DEV, "us-east-1", 3, "IAMUser", "alice", True, "iam.amazonaws.com", "CreateAccessKey", risk=3,
         rule="iam-escalation", arns=(f"arn:aws:iam::{DEV}:user/example_user",)),
    _row(3, DEV, "us-east-1", 5, "IAMUser", "alice", True, "s3.amazonaws.com", "PutBucketPolicy", risk=2,
         rule="exposure", arns=("arn:aws:s3:::example_bucket",)),
    _row(4, DEV, "us-east-1", 6, "AssumedRole", "ci-deploy-role", False, "lambda.amazonaws.com", "UpdateFunctionCode"),
    _row(5, DEV, "us-east-1", 8, "AssumedRole", "alice@example.com (SSO)", True, "sts.amazonaws.com", "AssumeRole",
         risk=1, rule="role-assumption", read_only=True),
    _row(6, DEV, "us-east-1", 20, "IAMUser", "bob", True, "cloudtrail.amazonaws.com", "StopLogging", risk=1,
         rule="security-config"),
    _row(7, DEV, "eu-west-1", 26, "AssumedRole", "ci-deploy-role", False, "ec2.amazonaws.com", "RunInstances"),
    _row(8, DEV, "us-east-1", 30, "IAMUser", "bob", True, "s3.amazonaws.com", "DeleteBucket", risk=2,
         rule="destructive", arns=("arn:aws:s3:::exampleXbucket",)),
    _row(9, DEV, "eu-west-1", 40, "AWSService", "ec2.amazonaws.com", False, "ec2.amazonaws.com",
         "CreateNetworkInterface"),
    _row(10, DEV, "us-east-1", 50, "IAMUser", "bob", True, "iam.amazonaws.com", "PutRolePolicy", risk=2,
         rule="exposure", error="AccessDenied"),
    _row(11, DEV, "eu-west-1", 70, "AssumedRole", "ci-deploy-role", False, "lambda.amazonaws.com",
         "UpdateFunctionCode"),
    _row(12, DEV, "us-east-1", 100, "IAMUser", "alice", True, "iam.amazonaws.com", "CreateUser", risk=3,
         rule="iam-escalation"),
    _row(13, DEV, "eu-west-1", 150, "AssumedRole", "ci-deploy-role", False, "ec2.amazonaws.com", "RunInstances"),
    _row(14, PROD, "us-east-1", 4, "IAMUser", "carol", True, "signin.amazonaws.com", "ConsoleLogin"),
    _row(15, PROD, "us-east-1", 10, "AssumedRole", "app-role", False, "ssm.amazonaws.com", "PutParameter"),
    _row(16, PROD, "us-east-1", 30, "IAMUser", "carol", True, "iam.amazonaws.com", "AttachRolePolicy", risk=3,
         rule="iam-escalation"),
    _row(17, PROD, "us-east-1", 60, "AWSService", "autoscaling.amazonaws.com", False, "ec2.amazonaws.com",
         "CreateTags"),
    _row(18, DEV, "us-east-1", 480, "IAMUser", "bob", True, "s3.amazonaws.com", "GetObject", error="AccessDenied",
         read_only=True, origin="import"),
)


def seed_activity(path_or_store: Path | str | Store) -> Store:
    store = path_or_store if isinstance(path_or_store, Store) else Store(path_or_store)
    store.insert_events(EVENTS)
    for account, regions, days_covered in ((DEV, ("us-east-1", "eu-west-1"), 7), (PROD, ("us-east-1",), 3)):
        store.set_meta(days_key(account), "7")
        store.set_meta(regions_key(account), ",".join(regions))
        for region in regions:
            for rule in LOOKUP_PASSES:
                store.record_coverage(account, region, rule.key, PINNED - timedelta(days=days_covered),
                                      PINNED - timedelta(hours=1))
    store.record_coverage(DEV, "us-east-1", "import", IMPORT_START, IMPORT_END)
    return store
