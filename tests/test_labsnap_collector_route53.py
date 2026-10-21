from botocore.exceptions import ClientError

from aws95.aws.engines.labsnap.collect.collectors.route53 import Route53Collector

ZONES = [{"Id": "/hostedzone/Z0PUBLIC", "Name": "example.com.", "Config": {"PrivateZone": False},
          "ResourceRecordSetCount": 12},
         {"Id": "/hostedzone/Z0PRIVATE", "Name": "internal.example.net.", "Config": {"PrivateZone": True},
          "ResourceRecordSetCount": 3}]


def _denied(op):
    return ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, op)


class FakeRoute53:
    def __init__(self, deny_dnssec=False, logging=True):
        self.deny_dnssec, self.logging, self.calls = deny_dnssec, logging, []

    def get_paginator(self, op):
        fake = self

        class P:
            def paginate(self_inner, **kw):
                fake.calls.append((op, kw))
                if op == "list_hosted_zones":
                    return iter([{"HostedZones": ZONES[:1]}, {"HostedZones": ZONES[1:]}])
                return iter([{"QueryLoggingConfigs": [{"Id": "q1"}] if fake.logging else []}])
        return P()

    def get_dnssec(self, HostedZoneId):
        self.calls.append(("get_dnssec", HostedZoneId))
        if self.deny_dnssec:
            raise _denied("GetDNSSEC")
        return {"Status": {"ServeSignature": "NOT_SIGNING"}}


class FakeSession:
    def __init__(self, client):
        self.c, self.asked = client, []

    def client(self, name, **kw):
        self.asked.append((name, kw.get("region_name")))
        return self.c


def test_zones_are_collected_with_public_only_details():
    s = FakeSession(FakeRoute53())
    res = Route53Collector().collect(s, "global")
    by = {r.rid: r for r in res.resources}
    assert set(by) == {"Z0PUBLIC", "Z0PRIVATE"} and all(r.rtype == "route53.hosted_zone" for r in res.resources)
    pub = by["Z0PUBLIC"].attrs
    assert pub == {"name": "example.com.", "private": False, "record_count": 12, "dnssec": "NOT_SIGNING",
                   "query_logging": True}
    assert s.asked[0] == ("route53", "us-east-1") and res.regions_covered == ["global"]
    assert [m.value for m in res.metrics if m.name == "route53.zone_count"] == [2]


def test_private_zone_skips_public_only_calls():
    """Review Focus 3."""
    fake = FakeRoute53()
    res = Route53Collector().collect(FakeSession(fake), "global")
    assert {r.rid: r.attrs for r in res.resources}["Z0PRIVATE"] == {"name": "internal.example.net.", "private": True,
                                                                     "record_count": 3}
    assert all("Z0PRIVATE" not in str(c) for c in fake.calls if c[0] != "list_hosted_zones")


def test_denied_dnssec_marks_the_zone_partial():
    """Review Focus 2."""
    res = Route53Collector().collect(FakeSession(FakeRoute53(deny_dnssec=True)), "global")
    pub = {r.rid: r for r in res.resources}["Z0PUBLIC"]
    assert pub.partial and "dnssec" not in pub.attrs and res.denials == 1
