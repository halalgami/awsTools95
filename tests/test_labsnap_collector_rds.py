# Upstream: labsnap/tests/test_collector_rds.py @ 1b1d437 — adapted for aws95; owned here from now on.
from botocore.exceptions import ClientError

from aws95.aws.engines.labsnap.collect.collectors.rds import RdsCollector, _open_to_world


class FakeRDS:
    def get_paginator(self, op):
        # The collector now unconditionally calls both describe_db_instances
        # and describe_db_clusters (real boto3 routes each to a distinct
        # operation), so this fake - which predates cluster collection -
        # must answer both: instances as before, and an empty cluster list
        # since these tests are not exercising clusters.
        pages = {
            "describe_db_instances": [{"DBInstances": [{
                "DBInstanceIdentifier": "db1",
                "DBInstanceClass": "db.t4g.micro",
                "Engine": "postgres", "EngineVersion": "15.1",
                "PubliclyAccessible": True,
                "AllocatedStorage": 20,
                "MultiAZ": False,
                "VpcSecurityGroups": [{"VpcSecurityGroupId": "sg-1"}]}]}],
            "describe_db_clusters": [{"DBClusters": []}],
        }

        class P:
            def paginate(self_inner):
                return iter(pages[op])
        return P()


class FakeEC2:
    def describe_security_groups(self, GroupIds):
        return {"SecurityGroups": [{"GroupId": "sg-1", "IpPermissions": [
            {"FromPort": 5432, "ToPort": 5432,
             "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}]}]}


class FakeSession:
    def client(self, name, **kw):
        return {"rds": FakeRDS(), "ec2": FakeEC2()}[name]


def test_records_public_accessibility_and_open_ingress():
    res = RdsCollector().collect(FakeSession(), "us-east-1")
    a = res.resources[0].attrs
    assert a["publicly_accessible"] is True
    assert a["open_to_world"] is True
    assert a["engine_version"] == "15.1"


def test_region_is_carried_on_the_resource():
    res = RdsCollector().collect(FakeSession(), "eu-west-1")
    assert res.resources[0].region == "eu-west-1"


class DeniedEC2:
    def describe_security_groups(self, GroupIds):
        raise ClientError({"Error": {"Code": "AccessDenied"}}, "DescribeSecurityGroups")


class DeniedSession:
    def client(self, name, **kw):
        return {"rds": FakeRDS(), "ec2": DeniedEC2()}[name]


def test_denied_security_group_is_partial_with_attribute_omitted_not_false():
    res = RdsCollector().collect(DeniedSession(), "us-east-1")
    r = res.resources[0]
    assert r.partial is True
    assert "open_to_world" not in r.attrs
    assert res.denials == 1


# --- I5: ::/0 in Ipv6Ranges must be treated exactly like 0.0.0.0/0 -----------

def test_open_to_world_true_for_ipv6_only_world_open_rule():
    class Ec2Ipv6:
        def describe_security_groups(self, GroupIds):
            return {"SecurityGroups": [{"GroupId": "sg-1", "IpPermissions": [
                {"FromPort": 5432, "ToPort": 5432,
                 "Ipv6Ranges": [{"CidrIpv6": "::/0"}]}]}]}

    assert _open_to_world(Ec2Ipv6(), ["sg-1"]) is True


def test_open_to_world_false_for_non_world_ipv6_rule():
    class Ec2Ipv6Scoped:
        def describe_security_groups(self, GroupIds):
            return {"SecurityGroups": [{"GroupId": "sg-1", "IpPermissions": [
                {"FromPort": 5432, "ToPort": 5432,
                 "Ipv6Ranges": [{"CidrIpv6": "2001:db8::/32"}]}]}]}

    assert _open_to_world(Ec2Ipv6Scoped(), ["sg-1"]) is False


class FakeRDSIpv6:
    def get_paginator(self, op):
        # Same reason as FakeRDS above: the collector always asks for
        # clusters too, so this fake must answer that call as well.
        pages = {
            "describe_db_instances": [{"DBInstances": [{
                "DBInstanceIdentifier": "db1",
                "DBInstanceClass": "db.t4g.micro",
                "Engine": "postgres", "EngineVersion": "15.1",
                "PubliclyAccessible": False,
                "AllocatedStorage": 20,
                "MultiAZ": False,
                "VpcSecurityGroups": [{"VpcSecurityGroupId": "sg-1"}]}]}],
            "describe_db_clusters": [{"DBClusters": []}],
        }

        class P:
            def paginate(self_inner):
                return iter(pages[op])
        return P()


class FakeEC2Ipv6Open:
    def describe_security_groups(self, GroupIds):
        return {"SecurityGroups": [{"GroupId": "sg-1", "IpPermissions": [
            {"FromPort": 5432, "ToPort": 5432,
             "Ipv6Ranges": [{"CidrIpv6": "::/0"}]}]}]}


class Ipv6Session:
    def client(self, name, **kw):
        return {"rds": FakeRDSIpv6(), "ec2": FakeEC2Ipv6Open()}[name]


def test_rds_collector_flags_ipv6_only_open_security_group():
    res = RdsCollector().collect(Ipv6Session(), "us-east-1")
    assert res.resources[0].attrs["open_to_world"] is True


class FakeRDSWithCluster:
    def get_paginator(self, op):
        pages = {
            "describe_db_instances": [{"DBInstances": [{
                "DBInstanceIdentifier": "member-1",
                "DBInstanceClass": "db.r6g.large",
                "Engine": "aurora-postgresql", "EngineVersion": "15.4",
                "PubliclyAccessible": True,
                "AllocatedStorage": 100,
                "MultiAZ": True,
                "VpcSecurityGroups": [{"VpcSecurityGroupId": "sg-9"}]}]}],
            "describe_db_clusters": [{"DBClusters": [{
                "DBClusterIdentifier": "aurora-1",
                "Engine": "aurora-postgresql", "EngineVersion": "15.4",
                "EngineMode": "provisioned",
                "MultiAZ": True,
                "StorageEncrypted": True,
                "BackupRetentionPeriod": 7,
                "DBClusterMembers": [{"DBInstanceIdentifier": "member-1"}],
                "VpcSecurityGroups": [{"VpcSecurityGroupId": "sg-9"}]}]}],
        }

        class P:
            def paginate(self_inner):
                return iter(pages[op])
        return P()


class FakeEC2Open:
    def describe_security_groups(self, GroupIds):
        return {"SecurityGroups": [{"GroupId": "sg-9", "IpPermissions": [
            {"FromPort": 5432, "ToPort": 5432,
             "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}]}]}


class ClusterSession:
    def client(self, name, **kw):
        return {"rds": FakeRDSWithCluster(), "ec2": FakeEC2Open()}[name]


def test_clusters_are_collected_as_their_own_resource_type():
    res = RdsCollector().collect(ClusterSession(), "eu-west-1")
    clusters = [r for r in res.resources if r.rtype == "rds.cluster"]
    assert [c.rid for c in clusters] == ["aurora-1"]
    a = clusters[0].attrs
    assert a["engine"] == "aurora-postgresql"
    assert a["engine_mode"] == "provisioned"
    assert a["storage_encrypted"] is True
    assert a["backup_retention_days"] == 7
    assert a["member_count"] == 1


def test_cluster_is_public_when_any_member_instance_is_public():
    res = RdsCollector().collect(ClusterSession(), "eu-west-1")
    cluster = [r for r in res.resources if r.rtype == "rds.cluster"][0]
    assert cluster.attrs["publicly_accessible"] is True
    assert cluster.attrs["open_to_world"] is True


def test_cluster_count_metric_is_emitted():
    res = RdsCollector().collect(ClusterSession(), "eu-west-1")
    counts = {m.name: m.value for m in res.metrics}
    assert counts["rds.cluster_count"] == 1.0
    assert counts["rds.instance_count"] == 1.0


class DeniedClusterEC2:
    def describe_security_groups(self, GroupIds):
        raise ClientError({"Error": {"Code": "AccessDenied"}},
                          "DescribeSecurityGroups")


class DeniedClusterSession:
    def client(self, name, **kw):
        return {"rds": FakeRDSWithCluster(), "ec2": DeniedClusterEC2()}[name]


def test_denied_sg_read_leaves_cluster_partial_with_attribute_omitted():
    res = RdsCollector().collect(DeniedClusterSession(), "eu-west-1")
    cluster = [r for r in res.resources if r.rtype == "rds.cluster"][0]
    assert cluster.partial is True
    assert "open_to_world" not in cluster.attrs
    # publicly_accessible comes from the instance list, which was readable,
    # so it must still be present - a denial on ONE attribute must not
    # suppress another that was read cleanly.
    assert cluster.attrs["publicly_accessible"] is True


class FakeRDSZeroMemberCluster:
    # An Aurora Serverless v1 cluster genuinely has no DBClusterMembers - not
    # because anything was denied, but because there is nothing to list.
    def get_paginator(self, op):
        pages = {
            "describe_db_instances": [{"DBInstances": []}],
            "describe_db_clusters": [{"DBClusters": [{
                "DBClusterIdentifier": "serverless-1",
                "Engine": "aurora-postgresql", "EngineVersion": "15.4",
                "EngineMode": "serverless",
                "MultiAZ": False,
                "StorageEncrypted": True,
                "BackupRetentionPeriod": 7,
                "DBClusterMembers": [],
                "VpcSecurityGroups": []}]}],
        }

        class P:
            def paginate(self_inner):
                return iter(pages[op])
        return P()


class NeverCalledEC2:
    def describe_security_groups(self, GroupIds):
        raise AssertionError(
            "describe_security_groups must not be called with no security "
            "groups on the cluster")


class ZeroMemberClusterSession:
    def client(self, name, **kw):
        return {"rds": FakeRDSZeroMemberCluster(), "ec2": NeverCalledEC2()}[name]


def test_zero_member_cluster_is_not_partial_but_omits_publicly_accessible():
    # M2: a cluster with no members (e.g. Aurora Serverless v1) used to take
    # the "not all members readable" branch and be marked partial forever,
    # even though nothing was actually denied. Zero members is a known fact,
    # not a gap - it should omit publicly_accessible without being partial.
    res = RdsCollector().collect(ZeroMemberClusterSession(), "eu-west-1")
    cluster = [r for r in res.resources if r.rtype == "rds.cluster"][0]
    assert cluster.attrs["member_count"] == 0
    assert "publicly_accessible" not in cluster.attrs
    assert cluster.partial is False
    assert res.denials == 0
