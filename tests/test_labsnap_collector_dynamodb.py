# Upstream: labsnap/tests/test_collector_dynamodb.py @ 1b1d437 — adapted for aws95; owned here from now on.
from botocore.exceptions import ClientError

from aws95.aws.engines.labsnap.collect.collectors.dynamodb import DynamoDbCollector


class FakeDynamoDb:
    def __init__(self, deny_backups=False):
        self.deny_backups = deny_backups
        self.read_calls = []

    def get_paginator(self, op):
        class P:
            def paginate(self_inner, **kw):
                return iter([{"TableNames": ["orders", "sessions"]}])
        return P()

    def describe_table(self, TableName):
        self.read_calls.append(("describe_table", TableName))
        tables = {
            "orders": {
                "TableName": "orders", "TableStatus": "ACTIVE",
                "ItemCount": 128_400, "TableSizeBytes": 88_000_000,
                "BillingModeSummary": {"BillingMode": "PAY_PER_REQUEST"},
                "SSEDescription": {"Status": "ENABLED", "SSEType": "KMS",
                                   "KMSMasterKeyArn": "arn:aws:kms:eu-west-1:"
                                                      "123456789012:key/cmk-1"},
                "DeletionProtectionEnabled": True,
                "StreamSpecification": {"StreamEnabled": True},
                "GlobalSecondaryIndexes": [{"IndexName": "by-customer"}]},
            # No SSEDescription at all: AWS-owned key. Encrypted, just not
            # with a key the account manages.
            "sessions": {
                "TableName": "sessions", "TableStatus": "ACTIVE",
                "ItemCount": 12, "TableSizeBytes": 4096,
                "BillingModeSummary": {"BillingMode": "PROVISIONED"},
                "DeletionProtectionEnabled": False},
        }
        return {"Table": tables[TableName]}

    def describe_continuous_backups(self, TableName):
        if self.deny_backups:
            raise ClientError({"Error": {"Code": "AccessDeniedException"}},
                              "DescribeContinuousBackups")
        status = "ENABLED" if TableName == "orders" else "DISABLED"
        return {"ContinuousBackupsDescription": {
            "PointInTimeRecoveryDescription":
                {"PointInTimeRecoveryStatus": status}}}


class Session:
    def __init__(self, **kw):
        self.ddb = FakeDynamoDb(**kw)

    def client(self, name, **kw):
        assert name == "dynamodb"
        return self.ddb


def test_table_configuration_is_recorded():
    res = DynamoDbCollector().collect(Session(), "eu-west-1")
    tables = {r.rid: r.attrs for r in res.resources}
    orders = tables["orders"]
    assert orders["billing_mode"] == "PAY_PER_REQUEST"
    assert orders["sse_type"] == "KMS"
    assert orders["has_custom_kms_key"] is True
    assert orders["pitr_enabled"] is True
    assert orders["deletion_protection"] is True
    assert orders["stream_enabled"] is True
    assert orders["gsi_count"] == 1


def test_absent_sse_description_records_the_aws_owned_key_not_unencrypted():
    # DynamoDB encrypts every table at rest. A missing SSEDescription means
    # the AWS-owned default key, NOT an unencrypted table - recording it as
    # False would manufacture a finding for a state that cannot exist.
    res = DynamoDbCollector().collect(Session(), "eu-west-1")
    sessions = [r for r in res.resources if r.rid == "sessions"][0]
    assert sessions.attrs["sse_type"] == "AWS_OWNED"
    assert sessions.attrs["has_custom_kms_key"] is False
    assert sessions.partial is False


def test_no_table_contents_are_ever_read():
    s = Session()
    DynamoDbCollector().collect(s, "eu-west-1")
    assert all(call[0] == "describe_table" for call in s.ddb.read_calls)
    assert not hasattr(s.ddb, "scan")
    assert not hasattr(s.ddb, "query")


def test_denied_backup_read_omits_pitr_and_marks_partial():
    res = DynamoDbCollector().collect(Session(deny_backups=True), "eu-west-1")
    orders = [r for r in res.resources if r.rid == "orders"][0]
    assert orders.partial is True
    assert "pitr_enabled" not in orders.attrs
    # Everything from describe_table was readable and must survive.
    assert orders.attrs["billing_mode"] == "PAY_PER_REQUEST"
    assert res.denials >= 1


def test_size_and_item_metrics_are_emitted_per_table():
    res = DynamoDbCollector().collect(Session(), "eu-west-1")
    items = {tuple(sorted(m.dims.items())): m.value
             for m in res.metrics if m.name == "dynamodb.item_count"}
    assert items[(("region", "eu-west-1"), ("table", "orders"))] == 128400.0
    counts = {m.name: m.value for m in res.metrics
              if m.name == "dynamodb.table_count"}
    assert counts["dynamodb.table_count"] == 2.0
