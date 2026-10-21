# Upstream: labsnap/labsnap/collect/collectors/elb.py @ 1b1d437 — adapted for aws95; owned here from now on.
from aws95.aws import gate
from decimal import Decimal
from aws95.aws.engines.labsnap.collect.base import register
from aws95.aws.engines.labsnap.collect.probe import probe
from aws95.aws.engines.labsnap.model import CollectorResult, Metric, Resource


@register
class ElbCollector:
    name = "elb"
    scope = "regional"
    timeout_s = 180
    # Target health is a live reading, not configuration: it flips as
    # instances come and go and would mark every load balancer as "changed"
    # on every run, drowning real drift.
    volatile_keys = frozenset({"healthy_target_count", "total_target_count"})

    def collect(self, session, region, cancel=None):
        # elbv2 covers Application, Network and Gateway load balancers.
        # Classic ELB is a separate API with a different response shape and
        # is deliberately not collected.
        elb = gate.bind(session, "elbv2", region, cancel=cancel)
        resources, denials = [], 0

        lb_count = 0
        for page in elb.pages("describe_load_balancers"):
            for lb in page["LoadBalancers"]:
                lb_count += 1
                arn = lb["LoadBalancerArn"]
                attrs = {
                    "scheme": lb.get("Scheme"),
                    "lb_type": lb.get("Type"),
                    "vpc_id": lb.get("VpcId"),
                    "state": lb.get("State", {}).get("Code"),
                    "az_count": len(lb.get("AvailabilityZones", [])),
                }
                # DNSName is intentionally not stored: it embeds the load
                # balancer name and no rule reads it.
                listeners, unreadable = probe(elb.describe_listeners,
                                              LoadBalancerArn=arn)
                denials += unreadable
                if not unreadable:
                    attrs["listeners"] = [
                        {"port": ln.get("Port"),
                         "protocol": ln.get("Protocol"),
                         "ssl_policy": ln.get("SslPolicy")}
                        for ln in (listeners or {}).get("Listeners", [])]
                resources.append(Resource("elb.load_balancer", region,
                                          lb["LoadBalancerName"], attrs,
                                          unreadable))

        for page in elb.pages("describe_target_groups"):
            for tg in page["TargetGroups"]:
                attrs = {
                    "protocol": tg.get("Protocol"),
                    "port": tg.get("Port"),
                    "target_type": tg.get("TargetType"),
                    "attached_lb_count": len(tg.get("LoadBalancerArns", [])),
                }
                health, unreadable = probe(
                    elb.describe_target_health,
                    TargetGroupArn=tg["TargetGroupArn"])
                denials += unreadable
                if not unreadable:
                    descs = (health or {}).get("TargetHealthDescriptions", [])
                    attrs["total_target_count"] = len(descs)
                    attrs["healthy_target_count"] = sum(
                        1 for d in descs
                        if d.get("TargetHealth", {}).get("State") == "healthy")
                resources.append(Resource("elb.target_group", region,
                                          tg["TargetGroupName"], attrs,
                                          unreadable))

        metrics = [Metric("elb.load_balancer_count", Decimal(lb_count),
                          dims={"region": region})]
        return CollectorResult(resources, metrics, [region], denials)
