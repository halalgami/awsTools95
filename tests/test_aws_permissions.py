import json

from botocore.exceptions import EndpointConnectionError

from aws95.aws import gate, permissions
from aws95.contracts.common import Status
from tests.aws_stubs import FakeSession, Raising, stubbed, sts_ok

READ_VERBS = ("Describe", "List", "Get", "Lookup", "View")


def _actions():
    return [a for s in json.loads(permissions.policy_json())["Statement"] for a in s["Action"]]


def test_policy_valid_read_only_sorted():
    doc = json.loads(permissions.policy_json())
    assert doc["Version"] == "2012-10-17"
    read = doc["Statement"][0]
    assert read["Sid"] == "AWS95ToolsReadOnly" and read["Resource"] == "*"
    acts = read["Action"]
    assert acts == sorted(set(acts))
    for a in acts:
        assert a.split(":")[1].startswith(READ_VERBS), a            # the read-only statement is read-only
    for banned in ("athena:", "glue:", "s3:GetObject", "secretsmanager:GetSecretValue", "ssm:GetParameter"):
        assert not any(a.startswith(banned) for a in acts)


def test_every_probe_action_in_policy():
    acts = set(_actions())
    for cap in permissions.CAPABILITIES:
        assert set(cap.actions) <= acts
        if cap.probe is not None:
            assert cap.probe_action in acts


def test_gate_allowlist_is_exactly_the_probes():
    probes = {(c.probe[0], c.probe[1]) for c in permissions.CAPABILITIES if c.probe is not None}
    assert gate.ALLOWED == probes | gate.REVIEWED_EXTRAS


def test_billed_is_per_service():
    for cap in permissions.CAPABILITIES:
        assert cap.billed == (cap.probe is not None and cap.probe[0] in gate.BILLED_SERVICES)


def _session(include_ce=False):
    ec2, s2 = stubbed("ec2")
    s2.add_response("describe_regions", {"Regions": []})
    ct, s3 = stubbed("cloudtrail")
    s3.add_client_error("lookup_events", service_error_code="AccessDeniedException", http_status_code=400)
    clients = {"sts": sts_ok(), "ec2": ec2, "cloudtrail": ct}
    if include_ce:
        ce, s4 = stubbed("ce")
        s4.add_response("get_cost_and_usage", {"ResultsByTime": []})
        clients["ce"] = ce
    return FakeSession(clients)


def test_check_without_billing_never_calls_ce():
    s = _session()
    rows = {r.capability: r for r in permissions.check(s, "me-south-1", False, None)}
    assert rows["identity"].status is Status.OK
    assert rows["resources"].status is Status.OK and rows["resources"].message == "Verified."
    assert rows["activity"].status is Status.DENIED
    assert rows["activity"].missing_actions == ("cloudtrail:LookupEvents",)
    assert rows["activity"].message == "Permission denied for cloudtrail:LookupEvents."
    assert rows["billing"].status is Status.NOT_CONNECTED and "$0.01" in rows["billing"].message
    assert rows["insights"].available is True              # 2d part 2 shipped; its probe is free
    assert all(svc != "ce" for svc, _ in s.requested)
    assert ("sts", "us-east-1") in s.requested and ("ec2", "me-south-1") in s.requested


def test_only_billing_runs_when_asked():
    ce, stub = stubbed("ce")
    stub.add_response("get_cost_and_usage", {"ResultsByTime": []})
    s = FakeSession({"ce": ce})
    rows = permissions.check(s, "me-south-1", True, None, only={"billing"})
    assert [r.capability for r in rows] == ["billing"] and rows[0].status is Status.OK
    assert s.requested == [("ce", "us-east-1")]


def test_unreachable_probe_is_classified():
    boom = Raising(EndpointConnectionError(endpoint_url="https://x"))
    rows = {r.capability: r for r in permissions.check(FakeSession({"sts": boom, "ec2": boom, "cloudtrail": boom}),
                                                       "us-east-1", False, None)}
    assert rows["identity"].status is Status.UNREACHABLE


def test_every_allowed_pair_has_an_iam_action_in_the_policy():
    assert set(permissions.IAM_ACTIONS) == gate.ALLOWED
    assert permissions.IMPORT_ONLY == {("s3", "list_objects_v2"), ("s3", "get_object"),
                                       ("organizations", "describe_organization")}
    acts = set(_actions())
    for pair, action in permissions.IAM_ACTIONS.items():
        if pair in permissions.IMPORT_ONLY:
            assert action not in acts, (pair, action)     # never Resource "*": import_policy() scopes these
            continue
        assert action in acts, (pair, action)


def test_every_capability_is_shipped():
    assert permissions.SHIPPED_PARTS == frozenset({"2a", "2b", "2c", "2d"})
    assert all(c.shipped for c in permissions.CAPABILITIES)


def test_billing_capability_lists_every_billed_call():
    billing = next(c for c in permissions.CAPABILITIES if c.id == "billing")
    assert set(billing.actions) == {"ce:GetCostAndUsage", "ce:GetCostForecast", "ce:ListCostAllocationTags"}


def test_import_policy_lines_are_scoped():
    key = "arn:aws:kms:us-east-1:123456789012:key/abcd"
    doc = json.loads(permissions.import_policy("example-cloudtrail-bucket", "logs", key,
                                               needs={*permissions.IMPORT_NEEDS, permissions.ORG_NEED}))
    by = {s["Action"]: s for s in doc}
    assert set(by) == {"s3:ListBucket", "s3:GetObject", "kms:Decrypt", "organizations:DescribeOrganization"}
    assert by["s3:ListBucket"]["Resource"] == "arn:aws:s3:::example-cloudtrail-bucket"
    assert by["s3:ListBucket"]["Condition"] == {"StringLike": {"s3:prefix": ["logs/AWSLogs/*"]}}
    assert by["s3:GetObject"]["Resource"] == "arn:aws:s3:::example-cloudtrail-bucket/logs/AWSLogs/*"
    assert by["kms:Decrypt"]["Resource"] == key
    assert by["organizations:DescribeOrganization"]["Resource"] == "*"      # the action has no resource level
    default = json.loads(permissions.import_policy("example-cloudtrail-bucket", "logs", key))
    assert {s["Action"] for s in default} == {"s3:ListBucket", "s3:GetObject", "kms:Decrypt"}
    only = json.loads(permissions.import_policy("example-cloudtrail-bucket", "", None,
                                                needs={"s3:GetObject", "kms:Decrypt"}))
    assert [s["Action"] for s in only] == ["s3:GetObject"]                    # no key, no KMS line
    assert only[0]["Resource"] == "arn:aws:s3:::example-cloudtrail-bucket/AWSLogs/*"


def test_activity_capability_is_shipped():
    assert permissions.SHIPPED_PARTS == {"2a", "2b", "2c", "2d"}
    assert next(c for c in permissions.CAPABILITIES if c.id == "activity").shipped


def test_insights_invoke_is_its_own_scoped_statement():
    """Security review (2026-10-20): InvokeModel is billed, so it never sits in the "*" read-only statement; it is
    limited to Anthropic foundation models and inference profiles."""
    statements = json.loads(permissions.policy_json())["Statement"]
    assert [s["Sid"] for s in statements] == ["AWS95ToolsReadOnly", "AWS95ToolsInsightsInvoke"]
    invoke = statements[1]
    assert invoke["Action"] == ["bedrock:InvokeModel"] and "*" not in invoke["Resource"]
    assert all(":foundation-model/anthropic." in r or ":inference-profile/" in r for r in invoke["Resource"])
    assert "bedrock:InvokeModel" not in statements[0]["Action"]
