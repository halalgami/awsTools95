# Upstream: labsnap/tests/test_collector_elb.py @ 1b1d437 — adapted for aws95; owned here from now on.
from botocore.exceptions import ClientError

from aws95.aws.engines.labsnap.collect.collectors.elb import ElbCollector


class FakeElbv2:
    def __init__(self, deny_listeners=False, deny_health=False):
        self.deny_listeners = deny_listeners
        self.deny_health = deny_health

    def get_paginator(self, op):
        pages = {
            "describe_load_balancers": [{"LoadBalancers": [{
                "LoadBalancerArn": "arn:aws:elasticloadbalancing:eu-west-1:"
                                   "123456789012:loadbalancer/app/web/abc",
                "LoadBalancerName": "web",
                "DNSName": "web-1234567890.eu-west-1.elb.amazonaws.com",
                "Scheme": "internet-facing",
                "Type": "application",
                "VpcId": "vpc-1",
                "State": {"Code": "active"},
                "AvailabilityZones": [{"ZoneName": "eu-west-1a"},
                                      {"ZoneName": "eu-west-1b"}]}]}],
            "describe_target_groups": [{"TargetGroups": [{
                "TargetGroupArn": "arn:aws:elasticloadbalancing:eu-west-1:"
                                  "123456789012:targetgroup/web-tg/def",
                "TargetGroupName": "web-tg",
                "Protocol": "HTTP", "Port": 8080, "TargetType": "ip",
                "LoadBalancerArns": ["arn:aws:elasticloadbalancing:eu-west-1:"
                                     "123456789012:loadbalancer/app/web/abc"]}]}],
        }

        class P:
            def paginate(self_inner, **kw):
                return iter(pages[op])
        return P()

    def describe_listeners(self, LoadBalancerArn):
        if self.deny_listeners:
            raise ClientError({"Error": {"Code": "AccessDenied"}},
                              "DescribeListeners")
        return {"Listeners": [
            {"Port": 80, "Protocol": "HTTP"},
            {"Port": 443, "Protocol": "HTTPS",
             "SslPolicy": "ELBSecurityPolicy-TLS13-1-2-2021-06"}]}

    def describe_target_health(self, TargetGroupArn):
        if self.deny_health:
            raise ClientError({"Error": {"Code": "AccessDenied"}},
                              "DescribeTargetHealth")
        return {"TargetHealthDescriptions": [
            {"Target": {"Id": "10.0.0.1"}, "TargetHealth": {"State": "healthy"}},
            {"Target": {"Id": "10.0.0.2"}, "TargetHealth": {"State": "unhealthy"}}]}


class Session:
    def __init__(self, **kw):
        self._elb = FakeElbv2(**kw)

    def client(self, name, **kw):
        assert name == "elbv2"
        return self._elb


def test_load_balancer_scheme_type_and_listeners_are_recorded():
    res = ElbCollector().collect(Session(), "eu-west-1")
    lb = [r for r in res.resources if r.rtype == "elb.load_balancer"][0]
    assert lb.rid == "web"
    assert lb.attrs["scheme"] == "internet-facing"
    assert lb.attrs["lb_type"] == "application"
    assert lb.attrs["az_count"] == 2
    assert lb.attrs["listeners"] == [
        {"port": 80, "protocol": "HTTP", "ssl_policy": None},
        {"port": 443, "protocol": "HTTPS",
         "ssl_policy": "ELBSecurityPolicy-TLS13-1-2-2021-06"}]


def test_dns_name_is_never_stored():
    # It embeds the load balancer name and no rule needs it.
    res = ElbCollector().collect(Session(), "eu-west-1")
    for r in res.resources:
        assert "dns_name" not in r.attrs
        assert not any("elb.amazonaws.com" in str(v) for v in r.attrs.values())


def test_target_group_health_counts_are_recorded():
    res = ElbCollector().collect(Session(), "eu-west-1")
    tg = [r for r in res.resources if r.rtype == "elb.target_group"][0]
    assert tg.attrs["total_target_count"] == 2
    assert tg.attrs["healthy_target_count"] == 1
    assert tg.attrs["attached_lb_count"] == 1


def test_denied_listener_read_omits_listeners_and_marks_partial():
    res = ElbCollector().collect(Session(deny_listeners=True), "eu-west-1")
    lb = [r for r in res.resources if r.rtype == "elb.load_balancer"][0]
    assert lb.partial is True
    assert "listeners" not in lb.attrs
    # scheme came from the list call, which succeeded - it must survive.
    assert lb.attrs["scheme"] == "internet-facing"
    assert res.denials >= 1


def test_denied_target_health_omits_counts_and_marks_partial():
    res = ElbCollector().collect(Session(deny_health=True), "eu-west-1")
    tg = [r for r in res.resources if r.rtype == "elb.target_group"][0]
    assert tg.partial is True
    assert "healthy_target_count" not in tg.attrs
    assert "total_target_count" not in tg.attrs


def test_region_is_carried_and_counted():
    res = ElbCollector().collect(Session(), "us-east-1")
    assert all(r.region == "us-east-1" for r in res.resources)
    assert res.regions_covered == ["us-east-1"]
    counts = {m.name: m.value for m in res.metrics}
    assert counts["elb.load_balancer_count"] == 1.0
