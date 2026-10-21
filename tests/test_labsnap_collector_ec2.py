# Upstream: labsnap/tests/test_collector_ec2.py @ 1b1d437 — adapted for aws95; owned here from now on.
from botocore.exceptions import ClientError
from aws95.aws.engines.labsnap.collect.collectors.ec2 import Ec2Collector, _open_ports


def denied(op):
    return ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, op)


class FakePaginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **kw):
        return iter(self._pages)


class DenyingPaginator:
    def __init__(self, op):
        self._op = op

    def paginate(self, **kw):
        raise denied(self._op)


class SnapshotPaginator:
    """Asserts OwnerIds=["self"] is always sent - without it describe_snapshots
    returns every public snapshot on AWS, per the plan's explicit ruling."""

    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **kw):
        assert kw.get("OwnerIds") == ["self"]
        return iter(self._pages)


INSTANCES_PAGES = [{"Reservations": [{"Instances": [
    {"InstanceId": "i-1", "State": {"Name": "running"}, "InstanceType": "t3.micro"},
    {"InstanceId": "i-2", "State": {"Name": "stopped"}, "InstanceType": "t3.small"},
]}]}]

VOLUMES_PAGES = [{"Volumes": [
    {"VolumeId": "vol-1", "State": "in-use", "Size": 8,
     "Attachments": [{"InstanceId": "i-1"}]},
    {"VolumeId": "vol-2", "State": "in-use", "Size": 20,
     "Attachments": [{"InstanceId": "i-2"}]},
]}]

SG_PAGES = [{"SecurityGroups": [
    {"GroupId": "sg-1", "IpPermissions": [
        {"FromPort": 22, "ToPort": 22, "IpProtocol": "tcp",
         "IpRanges": [{"CidrIp": "0.0.0.0/0"}]},
        {"FromPort": 443, "ToPort": 443, "IpProtocol": "tcp",
         "IpRanges": [{"CidrIp": "10.0.0.0/8"}]}]},
    {"GroupId": "sg-2", "IpPermissions": []},
]}]

ADDRESSES = {"Addresses": [
    {"AllocationId": "eipalloc-1", "PublicIp": "1.2.3.4", "InstanceId": "i-1",
     "AssociationId": "eipassoc-1"},
    {"AllocationId": "eipalloc-2", "PublicIp": "5.6.7.8"},  # unassociated
]}

NAT_PAGES = [{"NatGateways": [
    {"NatGatewayId": "nat-1", "State": "available", "VpcId": "vpc-1",
     "SubnetId": "subnet-1"},
    {"NatGatewayId": "nat-2", "State": "deleted", "VpcId": "vpc-1",
     "SubnetId": "subnet-2"},
]}]

SNAPSHOT_PAGES = [{"Snapshots": [
    {"SnapshotId": "snap-1", "VolumeId": "vol-1", "VolumeSize": 8, "State": "completed"},
]}]


class FakeEC2:
    def __init__(self, deny_volumes=False, deny_sgs=False, deny_addresses=False,
                 deny_nat=False, deny_snapshots=False):
        self.deny_volumes = deny_volumes
        self.deny_sgs = deny_sgs
        self.deny_addresses = deny_addresses
        self.deny_nat = deny_nat
        self.deny_snapshots = deny_snapshots

    def get_paginator(self, op):
        if op == "describe_instances":
            return FakePaginator(INSTANCES_PAGES)
        if op == "describe_volumes":
            if self.deny_volumes:
                return DenyingPaginator("DescribeVolumes")
            return FakePaginator(VOLUMES_PAGES)
        if op == "describe_security_groups":
            if self.deny_sgs:
                return DenyingPaginator("DescribeSecurityGroups")
            return FakePaginator(SG_PAGES)
        if op == "describe_nat_gateways":
            if self.deny_nat:
                return DenyingPaginator("DescribeNatGateways")
            return FakePaginator(NAT_PAGES)
        if op == "describe_snapshots":
            if self.deny_snapshots:
                return DenyingPaginator("DescribeSnapshots")
            return SnapshotPaginator(SNAPSHOT_PAGES)
        raise AssertionError(f"unexpected paginator op: {op}")

    def describe_addresses(self):
        if self.deny_addresses:
            raise denied("DescribeAddresses")
        return ADDRESSES


class FakeSession:
    def __init__(self, **kw):
        self._ec2 = FakeEC2(**kw)

    def client(self, name, **kw):
        assert name == "ec2"
        return self._ec2


def test_instance_and_volume_attrs_populated():
    res = Ec2Collector().collect(FakeSession(), "us-east-1")
    instances = {r.rid: r for r in res.resources if r.rtype == "ec2.instance"}
    volumes = {r.rid: r for r in res.resources if r.rtype == "ec2.volume"}

    assert instances["i-2"].attrs["state"] == "stopped"
    assert instances["i-2"].attrs["instance_type"] == "t3.small"
    assert instances["i-2"].attrs["attached_volume_gb"] == 20

    assert volumes["vol-1"].attrs["state"] == "in-use"
    assert volumes["vol-1"].attrs["size_gb"] == 8
    assert volumes["vol-1"].attrs["attached_to"] == "i-1"


def test_security_group_open_ports_from_world_only():
    res = Ec2Collector().collect(FakeSession(), "us-east-1")
    sgs = {r.rid: r for r in res.resources if r.rtype == "ec2.security_group"}
    assert sgs["sg-1"].attrs["open_ports"] == [22]
    assert sgs["sg-1"].attrs["all_ports_open"] is False
    assert sgs["sg-2"].attrs["open_ports"] == []
    assert sgs["sg-2"].attrs["all_ports_open"] is False


def test_elastic_ip_attrs_and_unassociated_case():
    res = Ec2Collector().collect(FakeSession(), "us-east-1")
    eips = {r.rid: r for r in res.resources if r.rtype == "ec2.elastic_ip"}
    assert eips["eipalloc-1"].attrs["associated"] is True
    assert eips["eipalloc-1"].attrs["public_ip"] == "1.2.3.4"
    assert eips["eipalloc-1"].attrs["association_target"] == "i-1"
    # the whole point: an unassociated EIP must be visible and clearly marked
    assert eips["eipalloc-2"].attrs["associated"] is False
    assert "association_target" not in eips["eipalloc-2"].attrs


def test_nat_gateway_only_collects_live_states():
    res = Ec2Collector().collect(FakeSession(), "us-east-1")
    nats = {r.rid: r for r in res.resources if r.rtype == "ec2.nat_gateway"}
    assert set(nats) == {"nat-1"}   # nat-2 is "deleted" - not a cost, not collected
    assert nats["nat-1"].attrs["state"] == "available"
    assert nats["nat-1"].attrs["vpc_id"] == "vpc-1"
    assert nats["nat-1"].attrs["subnet_id"] == "subnet-1"


def test_snapshot_attrs_and_owner_filter_is_sent():
    res = Ec2Collector().collect(FakeSession(), "us-east-1")
    snaps = {r.rid: r for r in res.resources if r.rtype == "ec2.snapshot"}
    assert snaps["snap-1"].attrs["volume_id"] == "vol-1"
    assert snaps["snap-1"].attrs["size_gb"] == 8
    assert snaps["snap-1"].attrs["state"] == "completed"


def test_region_is_carried_on_every_resource():
    res = Ec2Collector().collect(FakeSession(), "eu-west-1")
    assert res.resources
    assert all(r.region == "eu-west-1" for r in res.resources)


def test_stopped_instance_ebs_gb_only_counts_stopped_instances():
    res = Ec2Collector().collect(FakeSession(), "us-east-1")
    m = [m for m in res.metrics if m.name == "ec2.stopped_instance_ebs_gb"][0]
    assert m.value == 20.0   # only i-2 (stopped) counts; i-1 is running


def test_denied_volumes_marks_instances_partial_without_dropping_them():
    res = Ec2Collector().collect(FakeSession(deny_volumes=True), "us-east-1")
    instances = {r.rid: r for r in res.resources if r.rtype == "ec2.instance"}
    assert set(instances) == {"i-1", "i-2"}
    assert all(r.partial for r in instances.values())
    assert all("attached_volume_gb" not in r.attrs for r in instances.values())
    assert [r for r in res.resources if r.rtype == "ec2.volume"] == []
    assert res.denials == 1
    # a denied describe_volumes must never surface as a confident "0 GB" -
    # the metric itself must be absent, not zero
    assert [m for m in res.metrics if m.name == "ec2.stopped_instance_ebs_gb"] == []


def test_denied_security_groups_omits_that_resource_type_only():
    res = Ec2Collector().collect(FakeSession(deny_sgs=True), "us-east-1")
    assert [r for r in res.resources if r.rtype == "ec2.security_group"] == []
    instances = [r for r in res.resources if r.rtype == "ec2.instance"]
    assert len(instances) == 2
    assert all(not r.partial for r in instances)
    assert res.denials == 1


def test_denied_addresses_omits_only_elastic_ips():
    res = Ec2Collector().collect(FakeSession(deny_addresses=True), "us-east-1")
    assert [r for r in res.resources if r.rtype == "ec2.elastic_ip"] == []
    instances = [r for r in res.resources if r.rtype == "ec2.instance"]
    assert len(instances) == 2
    assert all(not r.partial for r in instances)
    assert res.denials == 1


def test_denied_nat_gateways_and_snapshots_are_each_isolated():
    res = Ec2Collector().collect(
        FakeSession(deny_nat=True, deny_snapshots=True), "us-east-1")
    assert [r for r in res.resources if r.rtype == "ec2.nat_gateway"] == []
    assert [r for r in res.resources if r.rtype == "ec2.snapshot"] == []
    # unrelated resource types are untouched
    assert [r for r in res.resources if r.rtype == "ec2.elastic_ip"]
    assert [r for r in res.resources if r.rtype == "ec2.security_group"]
    assert res.denials == 2


# --- _open_ports normalisation (Finding 2) -----------------------------------

def _rule_matches(port, open_ports, all_ports_open):
    """The shape a downstream rule is expected to use."""
    return port in open_ports or all_ports_open


def test_single_port_rule():
    perms = [{"FromPort": 22, "ToPort": 22, "IpProtocol": "tcp",
              "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}]
    ports, all_open = _open_ports(perms)
    assert ports == [22]
    assert all_open is False
    assert _rule_matches(22, ports, all_open) is True


def test_small_range_expands_and_includes_target_port():
    perms = [{"FromPort": 20, "ToPort": 25, "IpProtocol": "tcp",
              "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}]
    ports, all_open = _open_ports(perms)
    assert ports == [20, 21, 22, 23, 24, 25]
    assert all_open is False
    assert _rule_matches(22, ports, all_open) is True


def test_all_protocols_rule_sets_flag_not_the_int_list():
    perms = [{"IpProtocol": "-1", "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}]
    ports, all_open = _open_ports(perms)
    assert ports == []                 # no ints fabricated for "-1"
    assert all_open is True
    assert _rule_matches(22, ports, all_open) is True   # via the flag, not the list


def test_wide_range_beyond_cap_is_not_expanded_but_flagged():
    perms = [{"FromPort": 1, "ToPort": 5000, "IpProtocol": "tcp",
              "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}]
    ports, all_open = _open_ports(perms)
    assert ports == []                 # suppressed, not partially expanded
    assert all_open is True
    assert _rule_matches(3306, ports, all_open) is True


def test_non_world_rule_contributes_nothing():
    perms = [{"FromPort": 22, "ToPort": 22, "IpProtocol": "tcp",
              "IpRanges": [{"CidrIp": "10.0.0.0/8"}]}]
    ports, all_open = _open_ports(perms)
    assert ports == []
    assert all_open is False


# --- I5: ::/0 in Ipv6Ranges must be treated exactly like 0.0.0.0/0 -----------

def test_ipv6_world_open_rule_is_treated_like_ipv4():
    perms = [{"FromPort": 22, "ToPort": 22, "IpProtocol": "tcp",
              "Ipv6Ranges": [{"CidrIpv6": "::/0"}]}]
    ports, all_open = _open_ports(perms)
    assert ports == [22]
    assert all_open is False
    assert _rule_matches(22, ports, all_open) is True


def test_non_world_ipv6_rule_contributes_nothing():
    perms = [{"FromPort": 22, "ToPort": 22, "IpProtocol": "tcp",
              "Ipv6Ranges": [{"CidrIpv6": "2001:db8::/32"}]}]
    ports, all_open = _open_ports(perms)
    assert ports == []
    assert all_open is False


# --- the whole internet in other shapes (final-review ruling, owner 2026-10-15) ----------------------------------

def _tcp22(*cidrs, v6=()):
    return [{"FromPort": 22, "ToPort": 22, "IpProtocol": "tcp",
             "IpRanges": [{"CidrIp": c} for c in cidrs], "Ipv6Ranges": [{"CidrIpv6": c} for c in v6]}]


def test_split_halves_of_the_internet_are_world_open():
    assert _open_ports(_tcp22("0.0.0.0/1", "128.0.0.0/1")) == ([22], False)
    assert _open_ports(_tcp22(v6=("::/1", "8000::/1"))) == ([22], False)


def test_a_very_broad_public_range_is_world_open():
    assert _open_ports(_tcp22("64.0.0.0/2")) == ([22], False)
    assert _open_ports(_tcp22(v6=("2000::/3",))) == ([22], False)


def test_private_and_narrow_ranges_stay_closed():
    assert _open_ports(_tcp22("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")) == ([], False)
    assert _open_ports(_tcp22("203.0.113.0/24")) == ([], False)



def test_an_unreadable_range_fails_closed():
    """Security review: a range the parser can't read is treated as open, never silently skipped."""
    assert _open_ports(_tcp22("not-a-cidr")) == ([22], False)
    assert _open_ports(_tcp22(v6=("::zz/0",))) == ([22], False)
