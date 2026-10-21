# Upstream: labsnap/tests/test_probe.py @ 1b1d437 — adapted for aws95; owned here from now on.
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

from aws95.aws.engines.labsnap.collect.probe import probe, probe_detailed


def denied():
    raise ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "Op")


def absent():
    raise ClientError(
        {"Error": {"Code": "ResourceNotFoundException", "Message": "gone"}}, "Op")


def unknown_error():
    raise ClientError({"Error": {"Code": "SomeWeirdCode", "Message": "?"}}, "Op")


def ok():
    return {"x": 1}


# ---------------------------------------------------------------------------
# BUG 2a - a connect timeout (or other network-unreachable error) escaping
# probe() sank the whole s3 collector, because probe() only ever caught
# ClientError. An unreachable endpoint must be treated as unreadable, not
# fatal, exactly like a denial.
# ---------------------------------------------------------------------------

def test_probe_treats_connect_timeout_as_unreadable_not_fatal():
    def raises():
        raise ConnectTimeoutError(endpoint_url="https://s3.me-south-1.amazonaws.com")

    value, unreadable = probe(raises)
    assert value is None
    assert unreadable is True


def test_probe_treats_endpoint_connection_error_as_unreadable():
    def raises():
        raise EndpointConnectionError(endpoint_url="https://s3.me-south-1.amazonaws.com")

    value, unreadable = probe(raises)
    assert value is None
    assert unreadable is True


def test_probe_treats_read_timeout_as_unreadable():
    def raises():
        raise ReadTimeoutError(endpoint_url="https://s3.me-south-1.amazonaws.com")

    value, unreadable = probe(raises)
    assert value is None
    assert unreadable is True


def test_probe_treats_connection_closed_error_as_unreadable():
    def raises():
        raise ConnectionClosedError(endpoint_url="https://s3.me-south-1.amazonaws.com")

    value, unreadable = probe(raises)
    assert value is None
    assert unreadable is True


def test_probe_still_treats_denial_as_unreadable():
    value, unreadable = probe(denied)
    assert value is None
    assert unreadable is True


def test_probe_still_treats_absence_as_not_unreadable():
    value, unreadable = probe(absent)
    assert value is None
    assert unreadable is False


def test_probe_still_reraises_unrecognised_client_error():
    import pytest
    with pytest.raises(ClientError):
        probe(unknown_error)


def test_probe_still_returns_value_on_success():
    value, unreadable = probe(ok)
    assert value == {"x": 1}
    assert unreadable is False


# ---------------------------------------------------------------------------
# probe_detailed() - the three-state variant callers use when they need to
# tell a denial apart from an unreachable endpoint (e.g. to short-circuit
# further calls to an endpoint already known to be unreachable).
# ---------------------------------------------------------------------------

def test_probe_detailed_distinguishes_denied_from_unreachable():
    def timeout():
        raise ConnectTimeoutError(endpoint_url="https://s3.me-south-1.amazonaws.com")

    assert probe_detailed(timeout) == (None, "unreachable")
    assert probe_detailed(denied) == (None, "denied")
    assert probe_detailed(absent) == (None, "ok")
    assert probe_detailed(ok) == ({"x": 1}, "ok")


def test_missing_ecr_repository_policy_is_an_absence_not_a_denial():
    # ECR raises this when a repository simply has no policy attached.
    # "No policy" is a fact we can act on; "denied" is a fact we cannot.
    def fn():
        raise ClientError(
            {"Error": {"Code": "RepositoryPolicyNotFoundException"}},
            "GetRepositoryPolicy")

    value, unreadable = probe(fn)
    assert value is None
    assert unreadable is False


def test_missing_ecr_lifecycle_policy_is_an_absence_not_a_denial():
    def fn():
        raise ClientError(
            {"Error": {"Code": "LifecyclePolicyNotFoundException"}},
            "GetLifecyclePolicy")

    value, unreadable = probe(fn)
    assert value is None
    assert unreadable is False
