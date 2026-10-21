# Upstream: labsnap/labsnap/collect/collectors/logs.py @ 1b1d437 — adapted for aws95; owned here from now on.
from aws95.aws import gate
from decimal import Decimal
from aws95.aws.engines.labsnap.collect.base import register
from aws95.aws.engines.labsnap.model import CollectorResult, Metric, Resource


@register
class LogsCollector:
    name = "logs"
    scope = "regional"
    timeout_s = 120
    volatile_keys = frozenset({"stored_bytes"})

    def collect(self, session, region, cancel=None):
        logs = gate.bind(session, "logs", region, cancel=cancel)
        resources = []
        without_retention = 0

        # describe_log_groups is the only call this collector makes - there is
        # no per-group follow-up to guard with probe(), so (unlike s3/iam/rds)
        # it has no partial-denial path: a denial here is whole-collector
        # failure, same as cost.py's single get_cost_and_usage call.
        for page in logs.pages("describe_log_groups"):
            for g in page["logGroups"]:
                # retentionInDays is simply absent from the API response when
                # unset - None here is a real "never expires" fact, not a
                # missing/denied attribute, so it is always stored explicitly.
                retention = g.get("retentionInDays")
                if retention is None:
                    without_retention += 1
                resources.append(Resource("logs.group", region, g["logGroupName"], {
                    "retention_days": retention,
                    "stored_bytes": g.get("storedBytes"),
                }))

        metrics = [Metric("logs.groups_without_retention", Decimal(without_retention),
                          dims={"region": region})]
        return CollectorResult(resources, metrics, [region])
