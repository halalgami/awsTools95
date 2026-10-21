# Upstream: labsnap/tests/test_collector_logs.py @ 1b1d437 — adapted for aws95; owned here from now on.
from aws95.aws.engines.labsnap.collect.collectors.logs import LogsCollector


class FakePaginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **kw):
        return iter(self._pages)


PAGES = [{"logGroups": [
    {"logGroupName": "/aws/lambda/fn-a", "retentionInDays": 14, "storedBytes": 1024},
    {"logGroupName": "/aws/lambda/fn-b", "storedBytes": 2048},  # no retention key at all
]}]


class FakeLogs:
    def get_paginator(self, op):
        assert op == "describe_log_groups"
        return FakePaginator(PAGES)


class FakeSession:
    def client(self, name, **kw):
        assert name == "logs"
        return FakeLogs()


def test_retention_none_is_a_real_value_not_a_missing_attr():
    res = LogsCollector().collect(FakeSession(), "us-east-1")
    groups = {r.rid: r for r in res.resources}
    assert groups["/aws/lambda/fn-a"].attrs["retention_days"] == 14
    # key must be PRESENT and explicitly None - "never expires" is a fact, not an omission
    assert "retention_days" in groups["/aws/lambda/fn-b"].attrs
    assert groups["/aws/lambda/fn-b"].attrs["retention_days"] is None
    assert groups["/aws/lambda/fn-b"].attrs["stored_bytes"] == 2048


def test_region_is_carried_through():
    res = LogsCollector().collect(FakeSession(), "eu-west-1")
    assert all(r.region == "eu-west-1" for r in res.resources)


def test_groups_without_retention_metric():
    res = LogsCollector().collect(FakeSession(), "us-east-1")
    m = [m for m in res.metrics if m.name == "logs.groups_without_retention"][0]
    assert m.value == 1.0
