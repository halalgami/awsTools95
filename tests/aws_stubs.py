"""Test doubles for aws95.aws. Clients use explicit fake keys so no profile/config is read."""
from __future__ import annotations

import boto3
from botocore.stub import Stubber

ARN = "arn:aws:iam::123456789012:user/tester"     # Arn has a 20-char minimum in the STS model


def stubbed(service: str, region: str = "us-east-1"):
    client = boto3.Session(aws_access_key_id="AKIDTEST", aws_secret_access_key="SECRETTEST",
                           region_name=region).client(service, region_name=region)
    stub = Stubber(client)
    stub.activate()
    return client, stub


def sts_ok(account: str = "123456789012"):
    client, stub = stubbed("sts")
    stub.add_response("get_caller_identity", {"Account": account, "Arn": ARN, "UserId": "AIDATESTUSERID"})
    return client


class Raising:
    def __init__(self, exc: BaseException):
        self._exc = exc

    def __getattr__(self, name):
        def boom(*a, **k):
            raise self._exc
        return boom


class FakeSession:
    def __init__(self, clients: dict[str, object]):
        self._clients = clients
        self.requested: list[tuple[str, str | None]] = []

    def client(self, service, region_name=None, config=None):
        self.requested.append((service, region_name))
        return self._clients[service]
