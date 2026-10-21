# Upstream: labsnap/labsnap/collect/collectors/dynamodb.py @ 1b1d437 — adapted for aws95; owned here from now on.
from aws95.aws import gate
from decimal import Decimal
from aws95.aws.engines.labsnap.collect.base import register
from aws95.aws.engines.labsnap.collect.probe import probe
from aws95.aws.engines.labsnap.model import CollectorResult, Metric, Resource


@register
class DynamoDbCollector:
    name = "dynamodb"
    scope = "regional"
    timeout_s = 300
    # Both are live measurements that move on their own; they are emitted as
    # metrics, where movement is the point, and excluded from diffing, where
    # it would be noise.
    volatile_keys = frozenset({"item_count", "size_bytes"})

    def collect(self, session, region, cancel=None):
        # Table CONTENTS are never read: no Scan, no Query, no GetItem. This
        # collector records configuration and size only.
        ddb = gate.bind(session, "dynamodb", region, cancel=cancel)
        resources, metrics, denials = [], [], 0
        count = 0

        for page in ddb.pages("list_tables"):
            for name in page["TableNames"]:
                count += 1
                detail, unreadable = probe(ddb.describe_table, TableName=name)
                denials += unreadable
                if unreadable:
                    resources.append(
                        Resource("dynamodb.table", region, name, {}, True))
                    continue

                t = (detail or {}).get("Table", {})
                sse = t.get("SSEDescription") or {}
                attrs = {
                    "table_status": t.get("TableStatus"),
                    "billing_mode": (t.get("BillingModeSummary") or {}).get(
                        "BillingMode"),
                    "item_count": t.get("ItemCount"),
                    "size_bytes": t.get("TableSizeBytes"),
                    # DynamoDB encrypts every table at rest. An absent
                    # SSEDescription means the AWS-owned default key - NOT an
                    # unencrypted table. Recording False here would invent a
                    # state that does not exist, which is why there is no
                    # "no encryption" rule for DynamoDB.
                    "sse_type": sse.get("SSEType", "AWS_OWNED"),
                    "has_custom_kms_key": bool(sse.get("KMSMasterKeyArn")),
                    "deletion_protection": bool(t.get("DeletionProtectionEnabled")),
                    "stream_enabled": bool(
                        (t.get("StreamSpecification") or {}).get("StreamEnabled")),
                    "gsi_count": len(t.get("GlobalSecondaryIndexes") or []),
                }

                backups, unreadable_b = probe(ddb.describe_continuous_backups,
                                              TableName=name)
                denials += unreadable_b
                if not unreadable_b:
                    pitr = ((backups or {}).get("ContinuousBackupsDescription", {})
                            .get("PointInTimeRecoveryDescription", {})
                            .get("PointInTimeRecoveryStatus"))
                    attrs["pitr_enabled"] = pitr == "ENABLED"

                resources.append(Resource("dynamodb.table", region, name,
                                          attrs, unreadable_b))
                dims = {"region": region, "table": name}
                if attrs["item_count"] is not None:
                    metrics.append(Metric("dynamodb.item_count",
                                          Decimal(attrs["item_count"]), dims=dims))
                if attrs["size_bytes"] is not None:
                    metrics.append(Metric("dynamodb.size_bytes",
                                          Decimal(attrs["size_bytes"]),
                                          dims=dims, unit="Bytes"))

        metrics.append(Metric("dynamodb.table_count", Decimal(count),
                              dims={"region": region}))
        return CollectorResult(resources, metrics, [region], denials)
