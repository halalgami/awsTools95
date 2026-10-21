import pytest

from aws95.aws import identity, session
from tests.aws_stubs import ARN, FakeSession, sts_ok


@pytest.mark.parametrize("name", ["", "   ", "\t"])
def test_session_for_rejects_blank(name, monkeypatch):
    import boto3
    monkeypatch.setattr(boto3, "Session", lambda *a, **k: pytest.fail("a Session was built"))
    with pytest.raises(ValueError):
        session.session_for(name)


def test_region_for():
    assert session.region_for(None) == "us-east-1"
    assert session.region_for("") == "us-east-1"
    assert session.region_for("me-south-1") == "me-south-1"


def test_caller_uses_sts_region_and_returns_account_and_arn():
    fake = FakeSession({"sts": sts_ok()})
    assert identity.caller(fake) == ("123456789012", ARN)
    assert fake.requested == [("sts", session.STS_REGION)]
    assert session.STS_REGION == "us-east-1"
