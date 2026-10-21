# Upstream: labsnap/labsnap/rules/builtin/ec2.py @ 1b1d437 — adapted for aws95; owned here from now on.
from aws95.aws.engines.labsnap.model import Finding
from aws95.aws.engines.labsnap.rules.base import register_rule


@register_rule
class SgOpenAdminPort:
    id = "ec2.sg_open_admin_port"
    severity = "high"
    title = "Security group exposes an administrative port to the internet"
    description = ("The group permits ingress from 0.0.0.0/0 on a port commonly "
                   "used for direct administrative or database access.")
    params = {"ports": [22, 3389, 5432, 3306, 6379, 27017]}

    def evaluate(self, snap, params):
        ports = set(params.get("ports", []))
        for r in snap.resources("ec2.security_group"):
            if "open_ports" not in r.attrs or "all_ports_open" not in r.attrs:
                continue
            hit = r.attrs["all_ports_open"] or (ports & set(r.attrs["open_ports"]))
            if not hit:
                continue
            yield Finding(self.id, r.rtype, r.region, r.rid, self.severity,
                          {"open_ports": r.attrs["open_ports"],
                           "all_ports_open": r.attrs["all_ports_open"]})


@register_rule
class IdleResource:
    id = "waste.idle_resource"
    severity = "low"
    title = "Resource is idle but still incurring cost"
    description = ("A stopped instance still billed for its attached EBS volumes, "
                   "or an EBS volume with nothing attached to it - capacity paid "
                   "for and not in use.")
    params: dict = {}

    def evaluate(self, snap, params):
        for r in snap.resources("ec2.instance"):
            if "state" not in r.attrs or "attached_volume_gb" not in r.attrs:
                continue
            if r.attrs["state"] == "stopped" and r.attrs["attached_volume_gb"] > 0:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity,
                              {"reason": "stopped_instance_with_ebs",
                               "attached_volume_gb": r.attrs["attached_volume_gb"]})

        for r in snap.resources("ec2.volume"):
            if "attached_to" not in r.attrs:
                continue
            if r.attrs["attached_to"] is None:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity,
                              {"reason": "unattached_volume",
                               "size_gb": r.attrs.get("size_gb")})
