# Upstream: labsnap/labsnap/rules/builtin/rds.py @ 1b1d437 — adapted for aws95; owned here from now on.
from aws95.aws.engines.labsnap.model import Finding
from aws95.aws.engines.labsnap.rules.base import register_rule

# Both rules apply identically to an instance and to a cluster: the question
# "is this database reachable from the internet" does not change with the
# shape of the thing answering it.
RDS_TYPES = ("rds.instance", "rds.cluster")


def _rds_resources(snap):
    for rtype in RDS_TYPES:
        yield from snap.resources(rtype)


@register_rule
class RdsPublic:
    id = "rds.public"
    severity = "critical"
    title = "RDS instance is publicly accessible"
    description = ("The instance or cluster has PubliclyAccessible set, so it "
                   "resolves to a public endpoint. Combined with an open "
                   "security group this leaves only the database password "
                   "between it and the internet.")
    params: dict = {}

    def evaluate(self, snap, params):
        for r in _rds_resources(snap):
            # Missing attribute means THIS attribute's own call was denied -
            # asserting a finding from missing data would be a guess, not an
            # observation. `r.partial` is intentionally not checked here: it
            # only means some OTHER follow-up call on this resource was
            # denied, and must not suppress a finding this attribute already
            # answers.
            if "publicly_accessible" not in r.attrs:
                continue
            if r.attrs["publicly_accessible"]:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity,
                              {"instance_class": r.attrs.get("instance_class")})


@register_rule
class RdsSgOpen:
    id = "rds.sg_open"
    severity = "critical"
    title = "RDS security group allows 0.0.0.0/0"
    description = ("A security group attached to this instance or cluster "
                   "permits ingress from any address.")
    params: dict = {}

    def evaluate(self, snap, params):
        for r in _rds_resources(snap):
            if "open_to_world" not in r.attrs:
                continue
            if not r.attrs["open_to_world"]:
                continue
            yield Finding(self.id, r.rtype, r.region, r.rid, self.severity,
                          {"security_groups": r.attrs.get("security_groups", [])})
