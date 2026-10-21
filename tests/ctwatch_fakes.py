"""Plain-Python AWS doubles for the ctwatch engine tests (no sockets, no botocore clients)."""
from __future__ import annotations

import gzip
import io
import json
from datetime import datetime, timezone

A = "123456789012"


def when(detail: dict) -> datetime:
    return datetime.fromisoformat(detail["eventTime"].replace("Z", "+00:00")).astimezone(timezone.utc)


def ev(eid, at, name="CreateUser", *, ptype="IAMUser", user="alice", source="iam.amazonaws.com", read_only=False,
       error=None, region="us-east-1", role=None) -> dict:
    """One CloudTrail record (the dict inside LookupEvents' CloudTrailEvent, or one S3 log record)."""
    ui = {"type": ptype, "userName": user}
    if ptype == "AssumedRole":
        role = role or "ci-role"
        ui = {"type": "AssumedRole", "arn": f"arn:aws:sts::{A}:assumed-role/{role}/{user}",
              "sessionContext": {"sessionIssuer": {"userName": role}}}
    elif ptype == "AWSService":
        ui = {"type": "AWSService", "invokedBy": source}
    elif ptype == "Root":
        ui = {"type": "Root", "arn": f"arn:aws:iam::{A}:root"}
    out = {"eventID": eid, "eventTime": at.strftime("%Y-%m-%dT%H:%M:%SZ"), "eventName": name, "eventSource": source,
           "awsRegion": region, "readOnly": read_only, "userIdentity": ui, "sourceIPAddress": "203.0.113.5",
           "userAgent": "console.amazonaws.com", "recipientAccountId": A}
    if error:
        out["errorCode"] = error
    return out


def _matches(detail: dict, key: str, value: str) -> bool:
    if key == "ReadOnly":
        return str(bool(detail.get("readOnly", False))).lower() == value
    if key == "Username":
        ui = detail.get("userIdentity") or {}
        return ui.get("type") == "Root" if value == "root" else ui.get("userName") == value
    if key == "EventName":
        return detail.get("eventName") == value
    return False


class FakeTrail:
    """cloudtrail double: answers lookup_events like the real API — one LookupAttributes filter,
    StartTime <= eventTime < EndTime, newest first, MaxResults per page, NextToken as an offset.
    `fail(kwargs)` may return an exception to raise for that call."""

    def __init__(self, details=(), *, fail=None):
        self.details = list(details)
        self.fail = fail
        self.calls = []

    def lookup_events(self, **kw):
        self.calls.append(kw)
        if self.fail is not None:
            exc = self.fail(kw)
            if exc is not None:
                raise exc
        (attr,) = kw["LookupAttributes"]
        hits = sorted((d for d in self.details if _matches(d, attr["AttributeKey"], attr["AttributeValue"])
                       and kw["StartTime"] <= when(d) < kw["EndTime"]), key=when, reverse=True)
        start = int(kw.get("NextToken") or 0)
        page = hits[start:start + kw.get("MaxResults", 50)]
        out = {"Events": [{"EventId": d["eventID"], "EventName": d["eventName"], "EventSource": d["eventSource"],
                           "EventTime": when(d), "Resources": [], "CloudTrailEvent": json.dumps(d)} for d in page]}
        if start + len(page) < len(hits):
            out["NextToken"] = str(start + len(page))
        return out


class RegionSession:
    """session double: client(service, region_name=...) returns clients[(service, region)]."""

    def __init__(self, clients):
        self.clients = clients
        self.requested = []

    def client(self, service, region_name=None, config=None):
        self.requested.append((service, region_name))
        return self.clients[(service, region_name)]


def denied(code="AccessDeniedException", message="denied"):
    from botocore.exceptions import ClientError
    return ClientError({"Error": {"Code": code, "Message": message}}, "Operation")


def gz(records) -> bytes:
    return gzip.compress(json.dumps({"Records": list(records)}).encode("utf-8"))


class FakeS3:
    """s3 double over {key: bytes}. list_objects_v2 honours Prefix and MaxKeys; `extra_keys` are listed but not
    stored (a hostile or odd listing)."""

    def __init__(self, objects, *, deny_list=False, deny_get=False, kms=False, extra_keys=()):
        self.objects = dict(objects)
        self.deny_list, self.deny_get, self.kms = deny_list, deny_get, kms
        self.extra_keys = list(extra_keys)
        self.calls = []

    def list_objects_v2(self, **kw):
        self.calls.append(("list", kw))
        if self.deny_list:
            raise denied("AccessDenied")
        keys = sorted(k for k in [*self.objects, *self.extra_keys] if k.startswith(kw["Prefix"]))
        if "MaxKeys" in kw:
            keys = keys[: kw["MaxKeys"]]
        return {"Contents": [{"Key": k} for k in keys], "KeyCount": len(keys), "IsTruncated": False}

    def get_object(self, **kw):
        self.calls.append(("get", kw))
        if self.kms:
            raise denied("AccessDenied", "User is not authorized to perform: kms:Decrypt on the resource")
        if self.deny_get:
            raise denied("AccessDenied")
        return {"Body": io.BytesIO(self.objects[kw["Key"]])}


class FakeOrgs:
    """organizations double: describe_organization returns the whole reply AWS sends (the engine keeps the id)."""

    def __init__(self, org_id="o-abcde12345", *, deny=False):
        self.org_id, self.deny, self.calls = org_id, deny, []

    def describe_organization(self, **kw):
        self.calls.append(kw)
        if self.deny:
            raise denied("AccessDeniedException")
        return {"Organization": {"Id": self.org_id, "MasterAccountId": "111111111111",
                                 "MasterAccountEmail": "admin@example.com", "FeatureSet": "ALL"}}
