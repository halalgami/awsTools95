# Upstream: labsnap/tests/test_collector_secrets.py @ 1b1d437 — adapted for aws95; owned here from now on.
from datetime import UTC, datetime, timedelta

from botocore.exceptions import ClientError

from aws95.aws.engines.labsnap.collect.collectors.secrets import SecretsCollector


def _days_ago(n):
    return datetime.now(UTC) - timedelta(days=n)


class FakeSecretsManager:
    def __init__(self, deny=False):
        self.deny = deny
        self.calls = []
        # Computed once, eagerly, here - not inside paginate(). Real AWS
        # timestamps are fixed historical values recorded server-side,
        # decoupled from whenever the caller happens to read its own clock;
        # a fake that instead reads the wall clock lazily on each call would
        # manufacture a timestamp that drifts relative to itself between
        # calls, which no real response ever does.
        self._pages = [{"SecretList": [
            {"ARN": "arn:aws:secretsmanager:eu-west-1:123456789012:"
                    "secret:app/db-AbCdEf",
             "Name": "app/db",
             "RotationEnabled": True,
             "RotationRules": {"AutomaticallyAfterDays": 30},
             "LastAccessedDate": _days_ago(3),
             "LastRotatedDate": _days_ago(10),
             "KmsKeyId": "arn:aws:kms:eu-west-1:123456789012:key/cmk-1",
             "Tags": [{"Key": "owner", "Value": "platform"}]},
            {"ARN": "arn:aws:secretsmanager:eu-west-1:123456789012:"
                    "secret:legacy/api-GhIjKl",
             "Name": "legacy/api",
             "RotationEnabled": False,
             "LastAccessedDate": _days_ago(400),
             "Tags": []},
            {"ARN": "arn:aws:secretsmanager:eu-west-1:123456789012:"
                    "secret:never/used-MnOpQr",
             "Name": "never/used",
             "RotationEnabled": False,
             "Tags": []}]}]

    def get_paginator(self, op):
        self.calls.append(op)
        deny = self.deny
        pages = self._pages

        class P:
            def paginate(self_inner, **kw):
                if deny:
                    raise ClientError({"Error": {"Code": "AccessDeniedException"}},
                                      "ListSecrets")
                return iter(pages)
        return P()


class Session:
    def __init__(self, **kw):
        self.sm = FakeSecretsManager(**kw)

    def client(self, name, **kw):
        assert name == "secretsmanager"
        return self.sm


def test_rotation_and_access_metadata_are_recorded():
    res = SecretsCollector().collect(Session(), "eu-west-1")
    by_id = {r.rid: r.attrs for r in res.resources}
    assert by_id["app/db"]["rotation_enabled"] is True
    assert by_id["app/db"]["rotation_days"] == 30
    assert by_id["app/db"]["days_since_accessed"] == 3
    assert by_id["app/db"]["has_custom_kms_key"] is True
    assert by_id["app/db"]["tag_count"] == 1
    assert by_id["legacy/api"]["rotation_enabled"] is False
    assert by_id["legacy/api"]["has_custom_kms_key"] is False


def test_a_secret_never_accessed_has_days_since_accessed_none():
    # "Never accessed" is a real state, distinct from "we could not read it".
    # It must be a value, not an omission, or the unused rule cannot fire.
    res = SecretsCollector().collect(Session(), "eu-west-1")
    never = [r for r in res.resources if r.rid == "never/used"][0]
    assert never.attrs["days_since_accessed"] is None
    assert never.attrs["last_accessed_date"] is None
    assert never.partial is False


def test_no_secret_value_is_ever_read_or_stored():
    s = Session()
    res = SecretsCollector().collect(s, "eu-west-1")
    # Only the list call is ever made. This is the assertion that actually
    # carries weight: it proves the collector never calls the value-reading
    # API, regardless of whether the fake happens to define it. (A prior
    # `assert not hasattr(s.sm, "get_secret_value")` here tested the fake,
    # not the collector, and was removed.)
    assert s.sm.calls == ["list_secrets"]
    for r in res.resources:
        assert "value" not in r.attrs
        assert "secret_string" not in r.attrs


def test_denied_list_call_propagates_so_the_runner_records_a_failure():
    # A denial on the ONLY call is not partial data - it is no data. The
    # runner records the collector as failed, and the diff will refuse to
    # compare it, which is what stops "we could not look" reading as "they
    # are all gone".
    try:
        SecretsCollector().collect(Session(deny=True), "eu-west-1")
    except ClientError as e:
        assert e.response["Error"]["Code"] == "AccessDeniedException"
    else:
        raise AssertionError("expected the denial to propagate")


def test_region_is_carried_and_counted():
    res = SecretsCollector().collect(Session(), "us-east-1")
    assert all(r.region == "us-east-1" for r in res.resources)
    counts = {m.name: m.value for m in res.metrics}
    assert counts["secrets.secret_count"] == 3.0
