"""Amazon Route 53 hosted zones (edge-dns spec §3.1). Global: the API lives in us-east-1. Zones only, no record sets.
DNSSEC and query logging are read for public zones only (they don't apply to private zones)."""
from decimal import Decimal

from aws95.aws import gate
from aws95.aws.engines.labsnap.collect.base import register
from aws95.aws.engines.labsnap.collect.probe import probe
from aws95.aws.engines.labsnap.model import CollectorResult, Metric, Resource


@register
class Route53Collector:
    name = "route53"
    scope = "global"
    timeout_s = 180
    volatile_keys = frozenset({"record_count"})

    def collect(self, session, region, cancel=None):
        r53 = gate.bind(session, "route53", "us-east-1", cancel=cancel)
        resources, denials, zones = [], 0, 0
        for page in r53.pages("list_hosted_zones"):
            for z in page["HostedZones"]:
                zones += 1
                zone_id = z["Id"].rsplit("/", 1)[-1]
                private = bool(z.get("Config", {}).get("PrivateZone"))
                attrs = {"name": z["Name"], "private": private, "record_count": z.get("ResourceRecordSetCount")}
                partial = False
                if not private:
                    dnssec, unreadable = probe(r53.get_dnssec, HostedZoneId=zone_id)
                    denials += unreadable
                    partial |= unreadable
                    if not unreadable and dnssec is not None:
                        attrs["dnssec"] = dnssec.get("Status", {}).get("ServeSignature")
                    logs, unreadable = probe(_logging_configs, r53, zone_id)
                    denials += unreadable
                    partial |= unreadable
                    if not unreadable and logs is not None:
                        attrs["query_logging"] = bool(logs)
                resources.append(Resource("route53.hosted_zone", "global", zone_id, attrs, partial))
        return CollectorResult(resources, [Metric("route53.zone_count", Decimal(zones))], ["global"], denials)


def _logging_configs(r53, zone_id: str) -> list:
    return [c for page in r53.pages("list_query_logging_configs", HostedZoneId=zone_id)
            for c in page.get("QueryLoggingConfigs", [])]
