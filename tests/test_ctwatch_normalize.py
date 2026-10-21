# Upstream: ctwatch/tests/test_normalize.py @ 254ff31 — adapted for aws95; owned here from now on.
import json
from datetime import UTC, datetime

from aws95.aws.engines.ctwatch.normalize import (classify_principal, extract_arns, normalize, normalize_lookup,
                                                 normalize_record, record_arns)

A = "123456789012"


def test_classify_iam_user():
    ui = {"type": "IAMUser", "userName": "alice", "arn": "arn:aws:iam::1:user/alice"}
    assert classify_principal(ui) == ("IAMUser", "alice", True)


def test_classify_assumed_role_uses_role_name():
    ui = {"type": "AssumedRole", "arn": "arn:aws:sts::1:assumed-role/MyRole/sess",
          "sessionContext": {"sessionIssuer": {"userName": "MyRole"}}}
    assert classify_principal(ui) == ("AssumedRole", "MyRole", False)


def test_classify_sso_session_is_a_person():
    ui = {"type": "AssumedRole", "arn": "arn:aws:sts::1:assumed-role/AWSReservedSSO_Admin_0a1b2c/alice@example.com",
          "sessionContext": {"sessionIssuer": {"userName": "AWSReservedSSO_Admin_0a1b2c"}}}
    assert classify_principal(ui) == ("AssumedRole", "alice@example.com (SSO)", True)


def test_classify_aws_service():
    ui = {"type": "AWSService", "invokedBy": "lambda.amazonaws.com"}
    assert classify_principal(ui) == ("AWSService", "lambda.amazonaws.com", False)


def test_extract_arns_from_resources():
    res = [{"ResourceName": "arn:aws:iam::1:user/x"}, {"ResourceName": None}, {"ResourceName": "y"}]
    assert extract_arns({}, res) == ("arn:aws:iam::1:user/x", "y")


def test_normalize_builds_record():
    raw = {"EventId": "e1", "EventName": "CreateAccessKey", "EventSource": "iam.amazonaws.com",
           "EventTime": datetime(2026, 8, 2, 11, 2, tzinfo=UTC),
           "Resources": [{"ResourceName": "example-user"}],
           "CloudTrailEvent": json.dumps({"userIdentity": {"type": "IAMUser", "userName": "bob"},
                                          "readOnly": False, "sourceIPAddress": "203.0.113.5",
                                          "userAgent": "cli", "awsRegion": "us-east-1"})}
    rec = normalize(raw, evaluate=lambda d: (3, "iam-escalation"), account=A, region="us-east-1")
    assert rec.event_id == "e1" and rec.principal_name == "bob" and rec.is_human
    assert rec.name == "CreateAccessKey" and rec.risk_level == 3 and rec.origin == "lookup"
    assert rec.resource_arns == ("example-user",) and rec.time_utc == datetime(2026, 8, 2, 11, 2, tzinfo=UTC)
    assert json.loads(rec.raw_json) == json.loads(raw["CloudTrailEvent"]) and rec.account == A


def test_classify_federated_principals():
    assert classify_principal({"type": "SAMLUser", "userName": "alice@example.com", "principalId": "x"}) == (
        "SAMLUser", "alice@example.com", True)
    assert classify_principal({"type": "WebIdentityUser", "userName": "app-client", "principalId": "y"}) == (
        "WebIdentityUser", "app-client", False)


def test_classify_anonymous_cognito_traffic():
    ui = {"type": "Unknown", "principalId": "Anonymous"}
    assert classify_principal(ui) == ("Anonymous", "anonymous", False)


def test_classify_service_without_type_key():
    ui = {"accountId": "1", "invokedBy": "ecs-tasks.amazonaws.com"}
    assert classify_principal(ui) == ("AWSService", "ecs-tasks.amazonaws.com", False)


def test_classify_unknown_falls_back_to_arn_then_principal_id():
    ui = {"type": "Unknown", "principalId": "AIDAEXAMPLE", "arn": "arn:aws:iam::1:user/z"}
    assert classify_principal(ui) == ("Unknown", "arn:aws:iam::1:user/z", False)
    assert classify_principal({"type": "Unknown", "principalId": "AIDAEXAMPLE"}) == ("Unknown", "AIDAEXAMPLE", False)


def test_classify_never_returns_empty_name():
    assert classify_principal({})[1] != ""
    assert classify_principal({"type": "IAMUser"})[1] != ""


def test_lookup_uses_the_lookup_region_when_the_record_has_none():
    detail = {"userIdentity": {"type": "Root"}, "readOnly": True, "eventName": "GetBucketAcl"}
    raw = {"EventId": "e2", "EventTime": datetime(2026, 8, 2, tzinfo=UTC), "CloudTrailEvent": json.dumps(detail)}
    rec = normalize_lookup(raw, detail, account=A, region="eu-west-1")
    assert rec.region == "eu-west-1" and rec.principal_name == "root" and rec.read_only
    assert rec.source == "" and rec.name == "GetBucketAcl" and rec.source_ip is None


def test_normalize_s3_record_keeps_error_code_and_arns():
    detail = {"eventID": "s1", "eventTime": "2026-10-01T23:59:58Z", "eventSource": "s3.amazonaws.com",
              "eventName": "GetObject", "awsRegion": "us-east-1", "readOnly": True, "errorCode": "AccessDenied",
              "userIdentity": {"type": "IAMUser", "userName": "alice"},
              "resources": [{"ARN": "arn:aws:s3:::example-bucket/a.txt"}, {"type": "x"}]}
    rec = normalize_record(detail, account=A, region="us-east-1")
    assert rec.origin == "import" and rec.error_code == "AccessDenied" and rec.event_id == "s1"
    assert rec.time_utc == datetime(2026, 10, 1, 23, 59, 58, tzinfo=UTC)
    assert record_arns(detail) == ("arn:aws:s3:::example-bucket/a.txt",)
    assert json.loads(rec.raw_json) == detail


def test_insight_records_are_labelled_and_not_risk_scored():
    detail = {"eventID": "i1", "eventTime": "2026-10-01T10:00:00Z", "eventType": "AwsCloudTrailInsight",
              "eventSource": "iam.amazonaws.com", "eventName": "AttachUserPolicy", "insightDetails": {"state": "Start"}}
    rec = normalize_record(detail, account=A, region="us-east-1")
    assert (rec.principal_type, rec.principal_name, rec.is_human) == ("Insight", "CloudTrail Insights", False)
    assert (rec.risk_level, rec.risk_rule) == (0, None)
