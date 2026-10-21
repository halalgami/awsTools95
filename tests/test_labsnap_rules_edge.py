import pytest

from aws95.aws.engines.labsnap.model import Resource
from aws95.aws.engines.labsnap.rules.base import load_rules


class Snap:
    def __init__(self, *rows):
        self.rows = rows

    def resources(self, rtype=None):
        return [r for r in self.rows if rtype is None or r.rtype == rtype]


RULES = {r.id: r for r in load_rules()}


def fired(rule_id, *rows):
    rule = RULES[rule_id]
    return [f.rid for f in rule.evaluate(Snap(*rows), rule.params)]


def cf(rid, **attrs):
    return Resource("cloudfront.distribution", "global", rid, attrs)


@pytest.mark.parametrize("rule_id, hit, miss", [
    ("cloudfront.http_allowed", cf("E1", viewer_protocol="allow-all"), cf("E2", viewer_protocol="https-only")),
    ("cloudfront.no_waf", cf("E1", enabled=True, waf=False), cf("E2", enabled=False, waf=False)),
    ("cloudfront.no_access_logging", cf("E1", access_logging=False), cf("E2", access_logging=True)),
    ("apigateway.stage_no_access_logging",
     Resource("apigateway.stage", "eu-west-1", "a/prod", {"access_logging": False}),
     Resource("apigateway.stage", "eu-west-1", "a/dev", {"access_logging": True})),
    ("route53.public_zone_no_query_logging",
     Resource("route53.hosted_zone", "global", "Z1", {"private": False, "query_logging": False}),
     Resource("route53.hosted_zone", "global", "Z2", {"private": True})),
])
def test_rule_fires_and_does_not(rule_id, hit, miss):
    assert fired(rule_id, hit, miss) == [hit.rid]


def test_old_tls_only_for_custom_certificates():
    """Review Focus 4."""
    assert fired("cloudfront.old_tls",
                 cf("E1", custom_certificate=True, min_tls="TLSv1_2016"),
                 cf("E2", custom_certificate=True, min_tls="TLSv1.2_2021"),
                 cf("E3", custom_certificate=False)) == ["E1"]


def test_rules_skip_absent_attributes():
    """Review Focus 2: an attribute that couldn't be read never fires a rule."""
    assert fired("cloudfront.no_access_logging", cf("E1")) == []
    assert fired("route53.public_zone_no_query_logging",
                 Resource("route53.hosted_zone", "global", "Z1", {"private": False}, partial=True)) == []
    assert fired("apigateway.stage_no_access_logging", Resource("apigateway.stage", "x", "a/b", {})) == []


def test_old_tls_does_not_flag_tls13():
    """Final review: CloudFront's TLSv1.3_2025 policy is stronger than TLS 1.2, not older."""
    assert fired("cloudfront.old_tls",
                 cf("E1", custom_certificate=True, min_tls="TLSv1.3_2025"),
                 cf("E2", custom_certificate=True, min_tls="TLSv1.2_2025"),
                 cf("E3", custom_certificate=True, min_tls="SSLv3"),
                 cf("E4", custom_certificate=True, min_tls="TLSv1.1_2016")) == ["E3", "E4"]
