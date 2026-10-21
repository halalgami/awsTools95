# Upstream: labsnap/labsnap/collect/collectors/rds.py @ 1b1d437 — adapted for aws95; owned here from now on.
from aws95.aws import gate
from decimal import Decimal
from aws95.aws.engines.labsnap.collect.base import register
from aws95.aws.engines.labsnap.collect.probe import probe
from aws95.aws.engines.labsnap.model import CollectorResult, Metric, Resource


def _open_to_world(ec2, group_ids):
    if not group_ids:
        return False
    groups, denied = probe(ec2.describe_security_groups, GroupIds=list(group_ids))
    if denied or not groups:
        return None                      # unknown, not False
    for g in groups["SecurityGroups"]:
        for perm in g.get("IpPermissions", []):
            # ::/0 in Ipv6Ranges is IPv6's "open to the whole internet",
            # exactly as 0.0.0.0/0 is for IPv4.
            if (any(r.get("CidrIp") == "0.0.0.0/0" for r in perm.get("IpRanges", []))
                    or any(r.get("CidrIpv6") == "::/0"
                          for r in perm.get("Ipv6Ranges", []))):
                return True
    return False


@register
class RdsCollector:
    name = "rds"
    scope = "regional"
    timeout_s = 120
    volatile_keys = frozenset({"latest_restorable_time"})

    def collect(self, session, region, cancel=None):
        rds = gate.bind(session, "rds", region, cancel=cancel)
        ec2 = gate.bind(session, "ec2", region, cancel=cancel)
        resources, denials = [], 0
        # instance id -> whether it is publicly accessible. Built while
        # collecting instances, then read when deriving cluster reachability:
        # a cluster has no PubliclyAccessible field of its own, so the only
        # honest answer comes from its members.
        instance_public = {}
        for page in rds.pages("describe_db_instances"):
            for db in page["DBInstances"]:
                sgs = [s["VpcSecurityGroupId"] for s in db.get("VpcSecurityGroups", [])]
                open_world = _open_to_world(ec2, sgs)
                public = bool(db.get("PubliclyAccessible"))
                instance_public[db["DBInstanceIdentifier"]] = public
                attrs = {
                    "instance_class": db["DBInstanceClass"],
                    "engine": db["Engine"],
                    "engine_version": db["EngineVersion"],
                    "publicly_accessible": public,
                    "allocated_storage_gb": db.get("AllocatedStorage"),
                    "multi_az": bool(db.get("MultiAZ")),
                    "security_groups": sgs}
                partial = open_world is None
                if not partial:
                    attrs["open_to_world"] = open_world
                denials += partial
                resources.append(Resource("rds.instance", region,
                                          db["DBInstanceIdentifier"], attrs, partial))
        instance_count = len(resources)

        clusters = 0
        for page in rds.pages("describe_db_clusters"):
            for cl in page["DBClusters"]:
                clusters += 1
                sgs = [s["VpcSecurityGroupId"] for s in cl.get("VpcSecurityGroups", [])]
                open_world = _open_to_world(ec2, sgs)
                members = [m["DBInstanceIdentifier"]
                           for m in cl.get("DBClusterMembers", [])]
                attrs = {
                    "engine": cl["Engine"],
                    "engine_version": cl["EngineVersion"],
                    "engine_mode": cl.get("EngineMode"),
                    "multi_az": bool(cl.get("MultiAZ")),
                    "storage_encrypted": bool(cl.get("StorageEncrypted")),
                    "backup_retention_days": cl.get("BackupRetentionPeriod"),
                    "member_count": len(members),
                    "security_groups": sgs}
                # A cluster is reachable from the internet if ANY member is.
                # If a member was not in the instance list we could not read
                # its reachability, so we say nothing rather than guess: an
                # omitted attribute is what stops a rule asserting "private"
                # about something we never saw.
                known = [instance_public[m] for m in members if m in instance_public]
                partial = open_world is None
                if not members:
                    # Zero members (e.g. Aurora Serverless v1) means there is
                    # no instance-derived reachability to know - that is a
                    # known fact about the cluster, not a denial. Omit
                    # publicly_accessible but do NOT mark the cluster partial
                    # on that basis alone, or it would report as partial on
                    # every diff forever and never compare cleanly.
                    pass
                elif len(known) == len(members):
                    attrs["publicly_accessible"] = any(known)
                else:
                    # Some members present but not all readable - this stays
                    # partial, unlike the zero-member case above.
                    partial = True
                if open_world is not None:
                    attrs["open_to_world"] = open_world
                denials += (open_world is None)
                resources.append(Resource("rds.cluster", region,
                                          cl["DBClusterIdentifier"], attrs, partial))

        metrics = [
            Metric("rds.instance_count", Decimal(instance_count),
                   dims={"region": region}),
            Metric("rds.cluster_count", Decimal(clusters), dims={"region": region})]
        return CollectorResult(resources, metrics, [region], denials)
