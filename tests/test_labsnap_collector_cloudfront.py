from botocore.exceptions import ClientError

from aws95.aws.engines.labsnap.collect.collectors.cloudfront import CloudFrontCollector


def dist(i, *, default="redirect-to-https", others=(), waf="", cert=None, enabled=True):
    return {"Id": f"E{i}", "Enabled": enabled, "WebACLId": waf, "PriceClass": "PriceClass_100",
            "DomainName": f"d{i}.cloudfront.net",
            "Aliases": {"Quantity": 2, "Items": ["a.example.com", "b.example.com"]},
            "ViewerCertificate": cert or {"CloudFrontDefaultCertificate": True},
            "DefaultCacheBehavior": {"ViewerProtocolPolicy": default},
            "CacheBehaviors": {"Quantity": len(others), "Items": [{"ViewerProtocolPolicy": p} for p in others]}}


class FakeCF:
    def __init__(self, items, deny_config=False, logging=True):
        self.items, self.deny_config, self.logging = items, deny_config, logging

    def get_paginator(self, op):
        items = self.items

        class P:
            def paginate(self_inner, **kw):
                return iter([{"DistributionList": {"Items": items}}])
        return P()

    def get_distribution_config(self, Id):
        if self.deny_config:
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "GetDistributionConfig")
        return {"DistributionConfig": {"Logging": {"Enabled": self.logging}}}


class FakeSession:
    def __init__(self, c):
        self.c, self.asked = c, []

    def client(self, name, **kw):
        self.asked.append((name, kw.get("region_name")))
        return self.c


def test_distribution_attributes_without_domains():
    s = FakeSession(FakeCF([dist(1, waf="arn:aws:wafv2:us-east-1:123456789012:global/webacl/x/1",
                                 cert={"ACMCertificateArn": "arn:aws:acm:us-east-1:123456789012:certificate/x",
                                       "MinimumProtocolVersion": "TLSv1.2_2021"})]))
    [r] = CloudFrontCollector().collect(s, "global").resources
    assert r.rtype == "cloudfront.distribution" and r.rid == "E1" and r.region == "global"
    assert r.attrs == {"enabled": True, "viewer_protocol": "redirect-to-https", "custom_certificate": True,
                       "min_tls": "TLSv1.2_2021", "waf": True, "access_logging": True, "price_class": "PriceClass_100",
                       "alias_count": 2}
    assert "example.com" not in str(r.attrs) and "cloudfront.net" not in str(r.attrs)
    assert s.asked[0] == ("cloudfront", "us-east-1")


def test_weakest_viewer_protocol_across_behaviours():
    """Review Focus 5."""
    [r] = CloudFrontCollector().collect(FakeSession(FakeCF([dist(2, default="https-only",
                                                                  others=("redirect-to-https", "allow-all"))])),
                                        "global").resources
    assert r.attrs["viewer_protocol"] == "allow-all"


def test_default_certificate():
    [r] = CloudFrontCollector().collect(FakeSession(FakeCF([dist(3)])), "global").resources
    assert r.attrs["custom_certificate"] is False and r.attrs["waf"] is False


def test_denied_config_marks_partial():
    """Review Focus 2."""
    res = CloudFrontCollector().collect(FakeSession(FakeCF([dist(4)], deny_config=True)), "global")
    [r] = res.resources
    assert r.partial and "access_logging" not in r.attrs and res.denials == 1
