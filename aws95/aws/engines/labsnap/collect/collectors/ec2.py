# Upstream: labsnap/labsnap/collect/collectors/ec2.py @ 1b1d437 — adapted for aws95; owned here from now on.
from aws95.aws import gate
from decimal import Decimal
from aws95.aws.engines.labsnap.collect.base import register
from aws95.aws.engines.labsnap.collect.probe import probe
from aws95.aws.engines.labsnap.model import CollectorResult, Metric, Resource

# A wider range than this is not "a handful of open ports" - safely enumerating
# it would blow up the attribute for no analytical benefit, so it is folded
# into all_ports_open instead of being expanded port-by-port.
_MAX_EXPANDED_PORTS = 1024

# NAT gateways in these states are not costing anything - a deleted gateway
# is not a cost finding, and collecting it would just be noise.
_NAT_GATEWAY_DEAD_STATES = {"deleted", "deleting", "failed"}


# A range broader than this is treated as "the internet" even when it isn't all of it (aws95 ruling, 2026-10-15).
_BROAD_PREFIX = {4: 8, 6: 16}


def _world_open(ipv4: list[str], ipv6: list[str]) -> bool:
    """True when the rule's ranges reach the public internet: together they cover the whole address space (so
    0.0.0.0/1 + 128.0.0.0/1 counts like 0.0.0.0/0), or one public range is broader than /8 (IPv4) or /16 (IPv6).
    Private ranges (10/8, 172.16/12, 192.168/16, fc00::/7) never count; a range that can't be parsed counts as open."""
    import ipaddress
    for version, cidrs, whole in ((4, ipv4, "0.0.0.0/0"), (6, ipv6, "::/0")):
        nets = []
        for c in (c for c in cidrs if c):
            try:
                nets.append(ipaddress.ip_network(c, strict=False))
            except ValueError:
                return True                     # a range we can't read is treated as open (fail closed)
        if not nets:
            continue
        if list(ipaddress.collapse_addresses(nets)) == [ipaddress.ip_network(whole)]:
            return True
        if any(n.prefixlen < _BROAD_PREFIX[version] and not n.is_private for n in nets):
            return True
    return False


def _open_ports(perms):
    """Two views of world-facing ingress.

    A downstream rule matches `open_ports` against a fixed list of ints such
    as [22, 3389, 5432, 3306] - it cannot parse a list mixing ints, "from-to"
    range strings, and an "all" sentinel. So this returns two separate things:

    - open_ports: sorted individual port numbers only, with small ranges
      expanded so a plain `port in open_ports` check works.
    - all_ports_open: True when the rule is an explicit all-protocols rule
      (-1), already spans essentially every port (0-65535), or the range was
      too wide to safely expand (see _MAX_EXPANDED_PORTS). In all three cases
      no specific port list can honestly answer a fixed-port membership check,
      so the caller must also check this flag.
    """
    ports, all_open = set(), False
    for perm in perms:
        # ::/0 in Ipv6Ranges is IPv6's "open to the whole internet", exactly
        # as 0.0.0.0/0 is for IPv4 - a rule scoped to it must be treated
        # identically, or an IPv6-only open rule is invisible to every
        # downstream check.
        open_to_world = _world_open([r.get("CidrIp", "") for r in perm.get("IpRanges", [])],
                                    [r.get("CidrIpv6", "") for r in perm.get("Ipv6Ranges", [])])
        if not open_to_world:
            continue
        if perm.get("IpProtocol") == "-1":
            all_open = True
            continue
        from_port, to_port = perm.get("FromPort"), perm.get("ToPort")
        if from_port is None or to_port is None:
            continue
        if from_port <= 0 and to_port >= 65535:
            all_open = True
            continue
        if to_port - from_port + 1 > _MAX_EXPANDED_PORTS:
            all_open = True
            continue
        ports.update(range(from_port, to_port + 1))
    return sorted(ports), all_open


@register
class Ec2Collector:
    name = "ec2"
    scope = "regional"
    timeout_s = 180
    volatile_keys = frozenset()

    def collect(self, session, region, cancel=None):
        ec2 = gate.bind(session, "ec2", region, cancel=cancel)
        resources, denials = [], 0

        # describe_instances is the primary listing call - if we cannot list
        # instances at all, whole-collector failure is the honest outcome.
        instances = []
        for page in ec2.pages("describe_instances"):
            for reservation in page["Reservations"]:
                instances.extend(reservation["Instances"])

        # Volume data only enriches an instance attr (attached_volume_gb).
        # Instances are the resource of record, so a denial here degrades
        # that one attr rather than losing the instances already listed.
        vol_pages, vol_denied = probe(
            lambda: list(ec2.pages("describe_volumes")))
        denials += vol_denied
        volumes = []
        if not vol_denied:
            for page in vol_pages:
                volumes.extend(page["Volumes"])

        attached_gb_by_instance = {}
        for vol in volumes:
            for att in vol.get("Attachments", []):
                iid = att.get("InstanceId")
                if iid:
                    attached_gb_by_instance[iid] = (
                        attached_gb_by_instance.get(iid, 0) + vol.get("Size", 0))

        # Counted only over instances whose attached_volume_gb is actually
        # known - a denial must never look like "0 GB on stopped instances".
        stopped_known_gb = Decimal(0)
        for inst in instances:
            iid = inst["InstanceId"]
            attrs = {
                "state": inst["State"]["Name"],
                "instance_type": inst["InstanceType"],
            }
            if not vol_denied:
                gb = attached_gb_by_instance.get(iid, 0)
                attrs["attached_volume_gb"] = gb
                if attrs["state"] == "stopped":
                    stopped_known_gb += gb
            resources.append(Resource("ec2.instance", region, iid, attrs,
                                      partial=vol_denied))

        for vol in volumes:
            atts = vol.get("Attachments", [])
            resources.append(Resource("ec2.volume", region, vol["VolumeId"], {
                "state": vol["State"],
                "size_gb": vol["Size"],
                "attached_to": atts[0]["InstanceId"] if atts else None,
            }))

        # Each of the remaining listing calls stands on its own - a denial to
        # any one of them only empties that resource type, it never touches
        # instances/volumes or any other type collected above.
        sg_pages, sg_denied = probe(
            lambda: list(ec2.pages("describe_security_groups")))
        denials += sg_denied
        if not sg_denied:
            for page in sg_pages:
                for sg in page["SecurityGroups"]:
                    open_ports, all_open = _open_ports(sg.get("IpPermissions", []))
                    resources.append(Resource(
                        "ec2.security_group", region, sg["GroupId"],
                        {"open_ports": open_ports, "all_ports_open": all_open}))

        # Elastic IPs: the whole point is spotting the unassociated ones,
        # which is exactly the kind of thing an AccessDenied must never mask
        # as "none exist."
        addr_resp, addr_denied = probe(ec2.describe_addresses)
        denials += addr_denied
        if not addr_denied:
            for addr in addr_resp["Addresses"]:
                target = addr.get("InstanceId") or addr.get("NetworkInterfaceId")
                associated = bool(addr.get("AssociationId") or target)
                attrs = {"associated": associated, "public_ip": addr.get("PublicIp")}
                if associated and target:
                    attrs["association_target"] = target
                resources.append(Resource(
                    "ec2.elastic_ip", region, addr["AllocationId"], attrs))

        nat_pages, nat_denied = probe(
            lambda: list(ec2.pages("describe_nat_gateways")))
        denials += nat_denied
        if not nat_denied:
            for page in nat_pages:
                for nat in page["NatGateways"]:
                    if nat["State"] in _NAT_GATEWAY_DEAD_STATES:
                        continue
                    resources.append(Resource(
                        "ec2.nat_gateway", region, nat["NatGatewayId"], {
                            "state": nat["State"],
                            "vpc_id": nat.get("VpcId"),
                            "subnet_id": nat.get("SubnetId"),
                        }))

        # OwnerIds=["self"] is not optional - without it this call returns
        # every public snapshot on all of AWS, which is both enormous and
        # meaningless for an account snapshot.
        snap_pages, snap_denied = probe(
            lambda: list(ec2.pages("describe_snapshots", 
                OwnerIds=["self"])))
        denials += snap_denied
        if not snap_denied:
            for page in snap_pages:
                for snap in page["Snapshots"]:
                    resources.append(Resource(
                        "ec2.snapshot", region, snap["SnapshotId"], {
                            "volume_id": snap.get("VolumeId"),
                            "size_gb": snap["VolumeSize"],
                            "state": snap["State"],
                        }))

        # Emitted only when describe_volumes actually succeeded - a denied
        # call must never surface as a confident "0 GB of stopped-instance
        # EBS", which reads identically to a genuinely clean account.
        metrics = []
        if not vol_denied:
            metrics.append(Metric("ec2.stopped_instance_ebs_gb", stopped_known_gb,
                                  dims={"region": region}))
        return CollectorResult(resources, metrics, [region], denials)
