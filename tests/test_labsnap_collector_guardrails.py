# Upstream: labsnap/tests/test_collector_guardrails.py @ 1b1d437 — adapted for aws95; owned here from now on.
from botocore.exceptions import ClientError
from aws95.aws.engines.labsnap.collect.collectors.guardrails import GuardrailsCollector


class FakeGD:
    def list_detectors(self):
        return {"DetectorIds": []}


class FakeAA:
    def list_analyzers(self):
        return {"analyzers": [{"arn": "a"}]}


class FakeSH:
    def describe_hub(self):
        raise ClientError({"Error": {"Code": "InvalidAccessException"}}, "DescribeHub")


class FakeSession:
    def client(self, name, **kw):
        return {"guardduty": FakeGD(), "accessanalyzer": FakeAA(),
                "securityhub": FakeSH()}[name]


def test_reports_each_service_without_requiring_any_of_them():
    res = GuardrailsCollector().collect(FakeSession(), "us-east-1")
    state = {r.rid: r.attrs["enabled"] for r in res.resources}
    assert state == {"guardduty": False, "access_analyzer": True, "securityhub": False}


def test_not_subscribed_is_enabled_false_and_not_partial():
    res = GuardrailsCollector().collect(FakeSession(), "us-east-1")
    sh = next(r for r in res.resources if r.rid == "securityhub")
    assert sh.attrs["enabled"] is False
    assert sh.partial is False


class DeniedGD:
    def list_detectors(self):
        raise ClientError({"Error": {"Code": "AccessDenied"}}, "ListDetectors")


class MixedSession:
    def client(self, name, **kw):
        return {"guardduty": DeniedGD(), "accessanalyzer": FakeAA(),
                "securityhub": FakeSH()}[name]


def test_denied_check_is_partial_with_enabled_omitted_and_counts_a_denial():
    res = GuardrailsCollector().collect(MixedSession(), "us-east-1")
    gd = next(r for r in res.resources if r.rid == "guardduty")
    assert gd.partial is True
    assert "enabled" not in gd.attrs
    assert res.denials == 1


def test_a_denied_check_does_not_lose_the_other_services_in_the_same_run():
    res = GuardrailsCollector().collect(MixedSession(), "us-east-1")
    aa = next(r for r in res.resources if r.rid == "access_analyzer")
    sh = next(r for r in res.resources if r.rid == "securityhub")
    assert aa.attrs["enabled"] is True and aa.partial is False
    assert sh.attrs["enabled"] is False and sh.partial is False
    assert len(res.resources) == 3
