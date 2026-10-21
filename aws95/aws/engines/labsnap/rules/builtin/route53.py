"""Route 53 rules (edge-dns spec §4)."""
from aws95.aws.engines.labsnap.model import Finding
from aws95.aws.engines.labsnap.rules.base import register_rule


@register_rule
class PublicZoneNoQueryLogging:
    id = "route53.public_zone_no_query_logging"
    severity = "low"
    title = "Public hosted zone has no DNS query logging"
    description = "DNS queries for this public zone aren't logged, so lookups of your names can't be reviewed."
    params: dict = {}

    def evaluate(self, snap, params):
        for r in snap.resources("route53.hosted_zone"):
            if r.attrs.get("private") is False and r.attrs.get("query_logging") is False:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity)
