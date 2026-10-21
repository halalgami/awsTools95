import json
from datetime import UTC, datetime

from aws95.aws.engines.ctwatch.normalize import normalize_lookup, normalize_record
from aws95.aws.engines.ctwatch.redact import HIDDEN, REDACTED, redact

A = "123456789012"
KEY_ID = "ASIAIOSFODNN7EXAMPLE"
CALLER_KEY = "AKIAIOSFODNN7EXAMPLE"
TOKEN = "FQoGZXIvYXdzEXAMPLESESSIONTOKENVALUE"


def creds():
    return {"credentials": {"accessKeyId": KEY_ID, "sessionToken": TOKEN, "secretAccessKey": "wJalrXUtnFEMIEXAMPLE",
                            "expiration": "Oct 2, 2026 1:00:00 PM"}}


def test_role_and_session_credentials_are_removed():
    for name in ("AssumeRole", "AssumeRoleWithSAML", "GetSessionToken", "GetFederationToken"):
        out = redact({"eventName": name, "responseElements": creds(),
                      "userIdentity": {"type": "IAMUser", "accessKeyId": CALLER_KEY}})
        assert out["responseElements"]["credentials"] == {"secretAccessKey": REDACTED,
                                                          "expiration": "Oct 2, 2026 1:00:00 PM"}, name
        assert out["userIdentity"]["accessKeyId"] == CALLER_KEY        # the caller's key id says who; it stays


def test_container_environment_values_are_masked_and_the_input_is_untouched():
    detail = {"eventName": "RegisterTaskDefinition", "requestParameters": {"containerDefinitions": [
        {"name": "web", "environment": [{"name": "DB_URL", "value": "postgres://app:pw@db/prod"},
                                        {"name": "MODE", "value": "prod"}]}]}}
    env = redact(detail)["requestParameters"]["containerDefinitions"][0]["environment"]
    assert env == [{"name": "DB_URL", "value": REDACTED}, {"name": "MODE", "value": REDACTED}]
    assert detail["requestParameters"]["containerDefinitions"][0]["environment"][0]["value"] == \
        "postgres://app:pw@db/prod"


def test_codebuild_plaintext_environment_values_are_masked():
    """Re-review Minor 6: CodeBuild environmentVariables[].value (PLAINTEXT type) is masked too."""
    d = {"eventSource": "codebuild.amazonaws.com", "eventName": "CreateProject",
         "requestParameters": {"environment": {"environmentVariables": [
             {"name": "DB_PASSWORD_PLAIN", "value": "hunter2", "type": "PLAINTEXT"},
             {"name": "REGION", "value": "us-east-1", "type": "PLAINTEXT"}]}}}
    out = redact(d)["requestParameters"]["environment"]["environmentVariables"]
    assert [v["value"] for v in out] == [REDACTED, REDACTED]
    assert [v["name"] for v in out] == ["DB_PASSWORD_PLAIN", "REGION"]


def test_lambda_environment_variables_are_masked():
    d = {"eventName": "CreateFunction20150331",
         "requestParameters": {"environment": {"variables": {"API_KEY": "k-123", "STAGE": "dev"}}}}
    assert redact(d)["requestParameters"]["environment"] == {"variables": {"API_KEY": REDACTED, "STAGE": REDACTED}}


def test_sensitive_keys_are_masked_but_hidden_markers_and_secret_names_stay():
    d = {"requestParameters": {"password": "hunter2", "newPassword": HIDDEN, "clientSecret": "s3cr3t",
                               "tokenCode": "123456", "privateKey": "-----BEGIN KEY-----", "secretId": "prod/db",
                               "secretArn": "arn:aws:secretsmanager:us-east-1:123456789012:secret:prod/db",
                               "nested": [{"authToken": "abc"}], "policyDocument": "{}", "count": 3}}
    out = redact(d)["requestParameters"]
    assert out["password"] == REDACTED and out["newPassword"] == HIDDEN and out["clientSecret"] == REDACTED
    assert out["tokenCode"] == REDACTED and out["privateKey"] == REDACTED and out["nested"] == [{"authToken": REDACTED}]
    assert out["secretId"] == "prod/db" and out["secretArn"].endswith("secret:prod/db")
    assert out["policyDocument"] == "{}" and out["count"] == 3


def test_rules_see_only_the_redacted_record():
    seen = []

    def evaluate(detail):
        seen.append(json.dumps(detail))
        return 0, None
    detail = {"eventID": "r1", "eventTime": "2026-10-01T10:00:00Z", "eventSource": "sts.amazonaws.com",
              "eventName": "AssumeRole", "userIdentity": {"type": "IAMUser", "userName": "alice"},
              "responseElements": creds()}
    normalize_record(detail, account=A, region="us-east-1", evaluate=evaluate)
    assert TOKEN not in seen[0] and KEY_ID not in seen[0]


def test_secrets_never_reach_the_store_bytes(make_store):
    """Review Focus 2: neither the columns, nor the stored raw JSON, nor the database files hold a secret."""
    store = make_store()
    detail = {"eventID": "e1", "eventTime": "2026-10-01T10:00:00Z", "eventSource": "ecs.amazonaws.com",
              "eventName": "RegisterTaskDefinition", "readOnly": False, "awsRegion": "us-east-1",
              "userIdentity": {"type": "IAMUser", "userName": "alice"}, "responseElements": creds(),
              "requestParameters": {"password": "hunter2", "containerDefinitions": [
                  {"name": "web", "environment": [{"name": "DB_URL", "value": "postgres://app:pw@db/prod"}]}]}}
    raw = {"EventId": "e1", "EventTime": datetime(2026, 10, 1, 10, tzinfo=UTC), "CloudTrailEvent": json.dumps(detail)}
    store.insert_events([normalize_lookup(raw, detail, account=A, region="us-east-1"),
                         normalize_record({**detail, "eventID": "e2"}, account=A, region="us-east-1")])
    stored = store.raw_event("e1") + store.raw_event("e2")
    with store.reader() as db:
        columns = " ".join(str(tuple(r)) for r in db.execute("SELECT * FROM events"))
    store.close()
    files = b"".join(p.read_bytes() for p in store.path.parent.glob(store.path.name + "*"))
    for value in (TOKEN, KEY_ID, "hunter2", "postgres://app:pw@db/prod", "wJalrXUtnFEMIEXAMPLE"):
        assert value not in stored and value not in columns and value.encode() not in files, value
