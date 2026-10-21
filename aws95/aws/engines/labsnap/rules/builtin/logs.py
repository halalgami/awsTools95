# Upstream: labsnap/labsnap/rules/builtin/logs.py @ 1b1d437 — adapted for aws95; owned here from now on.
from aws95.aws.engines.labsnap.model import Finding
from aws95.aws.engines.labsnap.rules.base import register_rule


@register_rule
class NoRetention:
    id = "logs.no_retention"
    severity = "medium"
    title = "CloudWatch log group has no retention policy"
    description = ("Log events never expire, so the group grows and costs money "
                   "forever unless someone deletes it by hand.")
    params: dict = {}

    def evaluate(self, snap, params):
        for r in snap.resources("logs.group"):
            if "retention_days" not in r.attrs:
                continue
            if r.attrs["retention_days"] is None:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity)
