import ast
import subprocess
import sys
from pathlib import Path

import pytest

from aws95.aws import gate
from aws95.core.cancel import CancelledError, CancelToken
from tests.aws_stubs import ARN, FakeSession, Raising, stubbed, sts_ok

ROOT = Path(__file__).resolve().parents[1] / "aws95"
BANNED_ATTRS = {"client", "resource", "get_paginator", "get_waiter", "create_client", "_client", "_gate_client_once",
                "_gate_built"}


@pytest.mark.parametrize("pair", sorted(gate.ALLOWED - gate.VALIDATED))
def test_allowed_pairs(pair):
    assert gate.is_allowed(*pair)


@pytest.mark.parametrize("pair", sorted(gate.VALIDATED))
def test_validated_pairs_allowed_only_inside_a_validator(pair):
    assert not gate.is_allowed(*pair)
    with gate.validator(*pair, lambda params: None):
        assert gate.is_allowed(*pair)
    assert not gate.is_allowed(*pair)


@pytest.mark.parametrize("service,op", [
    ("s3", "put_object"), ("s3", "get_object"), ("ecr", "get_authorization_token"), ("ec2", "get_password_data"),
    ("ssm", "get_parameter_history"), ("ssm", "get_parameter"), ("dynamodb", "get_item"), ("logs", "get_log_events"),
    ("lambda", "get_function"), ("sts", "assume_role"), ("sts", "get_session_token"),
    ("redshift", "get_cluster_credentials"), ("secretsmanager", "get_secret_value"),
    ("sts", "get_waiter"), ("sts", "get_paginator"),
])
def test_everything_else_blocked(service, op):
    assert not gate.is_allowed(service, op)


def test_blocked_call_never_builds_a_client():
    s = FakeSession({})
    for service, op in (("s3", "put_object"), ("ecr", "get_authorization_token")):
        with pytest.raises(gate.WriteBlockedError):
            gate.call(s, service, op)
    assert s.requested == []


def test_call_goes_through_client():
    out = gate.call(FakeSession({"sts": sts_ok()}), "sts", "get_caller_identity")
    assert out["Account"] == "123456789012"


def test_cancelled_before_call():
    t = CancelToken()
    t.cancel()
    with pytest.raises(CancelledError):
        gate.call(FakeSession({}), "sts", "get_caller_identity", cancel=t)


def test_paginate_checks_cancel_between_pages():
    client, stub = stubbed("cloudtrail")
    stub.add_response("lookup_events", {"Events": [], "NextToken": "n"})
    stub.add_response("lookup_events", {"Events": []}, {"NextToken": "n"})
    t = CancelToken()
    it = gate.paginate(FakeSession({"cloudtrail": client}), "cloudtrail", "lookup_events", cancel=t)
    next(it)
    t.cancel()
    with pytest.raises(CancelledError):
        next(it)


def test_billed_services():
    assert gate.BILLED_SERVICES == frozenset({"ce", "bedrock-runtime"})     # ai-insights spec §3


# Non-AWS uses of a banned attribute name outside aws95/aws, as "relative/path.py:attr". Empty today.
ALLOWED_NON_AWS: set[str] = set()


def _offences(source: str) -> list[tuple[int, str]]:
    """Every way source can reach a client/resource/paginator/waiter factory: any attribute reference with a
    banned name (called or not), or a banned string passed to getattr/attrgetter/__getattribute__."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and node.attr in BANNED_ATTRS:
            found.append((node.lineno, node.attr))
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            if name in ("getattr", "attrgetter", "__getattribute__"):
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and arg.value in BANNED_ATTRS:
                        found.append((node.lineno, f"{name}:{arg.value}"))
    return found


@pytest.mark.parametrize("src", [
    "import functools\nfunctools.partial(session.client, 's3')",
    "mk = session.client",
    "getattr(s, 'client')",
    "s.__getattribute__('client')",
    "from operator import attrgetter\nattrgetter('resource')(s)",
    "s.get_paginator('x')",
])
def test_offence_predicate_catches_indirect_references(src):
    assert _offences(src)


def test_offence_predicate_ignores_innocent_code():
    assert _offences("x = obj.name\ngetattr(o, 'size')") == []


def test_only_gate_builds_clients():
    offenders = []
    for path in ROOT.rglob("*.py"):
        if path == ROOT / "aws" / "gate.py":
            continue
        for lineno, what in _offences(path.read_text()):
            key = f"{path.relative_to(ROOT)}:{what}"
            if key not in ALLOWED_NON_AWS:
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}:{what}")
    assert offenders == []


def test_paginate_blocks_eagerly_without_next():
    s = FakeSession({})
    with pytest.raises(gate.WriteBlockedError):
        gate.paginate(s, "s3", "list_objects_v2")
    assert s.requested == []


def test_live_import_does_not_load_botocore():
    code = "import sys, aws95.aws.gate, aws95.aws.errors, aws95.aws.discover, aws95.aws.session, aws95.aws.identity, aws95.providers.live; " \
           "print('botocore' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
    assert out == "False"


class RecordingSession:
    """Records every client request with its kwargs; returns the given client."""

    def __init__(self, client=None):
        self._client = client
        self.requests = []

    def client(self, service, **kwargs):
        self.requests.append((service, kwargs))
        return self._client


def test_labsnap_pair_counts():
    assert len(gate.LABSNAP) == 58
    assert gate.ALLOWED == gate.PROBES | gate.LABSNAP | gate.CTWATCH | gate.INSIGHTS
    assert len(gate.ALLOWED) == 65
    assert gate.REVIEWED_EXTRAS == gate.ALLOWED - gate.PROBES


@pytest.mark.parametrize("pair", [("secretsmanager", "get_" + "secret_value"), ("ssm", "get_parameter"),
                                  ("dynamodb", "scan"), ("dynamodb", "get_item"), ("s3", "get_object"),
                                  ("lambda", "get_function"), ("ecr", "get_authorization_token"),
                                  ("kms", "decrypt"), ("ec2", "get_password_data")])
def test_secret_and_content_reads_stay_blocked(pair):
    assert not gate.is_allowed(*pair)            # is_allowed, not membership: 2c's validated S3 pairs stay blocked here


def test_bind_blocks_eagerly_without_a_client():
    s = RecordingSession()
    svc = gate.bind(s, "s3")
    with pytest.raises(gate.WriteBlockedError):
        svc.put_object
    assert s.requests == []


def test_bind_call_goes_through_gate_call():
    s = RecordingSession(sts_ok())
    out = gate.bind(s, "sts", "us-east-1").get_caller_identity()
    assert out["Account"] == "123456789012"
    assert s.requests[0][0] == "sts" and s.requests[0][1]["region_name"] == "us-east-1"


def test_bind_builds_one_client_per_binding():
    client, stub = stubbed("sts")
    for _ in range(3):
        stub.add_response("get_caller_identity", {"Account": "123456789012", "Arn": ARN, "UserId": "AIDATESTUSERID"})
    s = RecordingSession(client)
    svc = gate.bind(s, "sts")
    for _ in range(3):
        svc.get_caller_identity()
    assert len(s.requests) == 1


def test_bind_pages():
    client, stub = stubbed("iam")
    stub.add_response("list_users", {"Users": [], "Marker": "m", "IsTruncated": True})
    stub.add_response("list_users", {"Users": []}, {"Marker": "m"})
    pages = list(gate.bind(RecordingSession(client), "iam").pages("list_users"))
    assert len(pages) == 2


def test_bind_private_names_are_not_operations():
    with pytest.raises(AttributeError):
        gate.bind(RecordingSession(), "iam")._hidden


def test_bind_cancel_is_checked_before_the_call():
    t = CancelToken()
    t.cancel()
    with pytest.raises(CancelledError):
        gate.bind(RecordingSession(sts_ok()), "sts", cancel=t).get_caller_identity()


def test_fast_config_for_s3():
    s = RecordingSession(Raising(RuntimeError("stop")))
    with pytest.raises(RuntimeError):
        gate.bind(s, "s3", fast=True).list_buckets()
    cfg = s.requests[0][1]["config"]
    assert cfg.connect_timeout == 5 and cfg.read_timeout == 10 and cfg.retries == {"max_attempts": 1}
    s2 = RecordingSession(Raising(RuntimeError("stop")))
    with pytest.raises(RuntimeError):
        gate.bind(s2, "s3").list_buckets()
    assert s2.requests[0][1]["config"] is gate.client_config()


PAIR = ("s3", "list_objects_v2")


@pytest.fixture
def validated_pair(monkeypatch):
    monkeypatch.setattr(gate, "ALLOWED", gate.ALLOWED | {PAIR})
    monkeypatch.setattr(gate, "VALIDATED", frozenset({PAIR}))


def _only_bucket(name):
    def check(params):
        if params.get("Bucket") != name:
            raise gate.WriteBlockedError("s3.list_objects_v2: other bucket")
    return check


def test_validated_pair_blocked_without_validator(validated_pair):
    assert not gate.is_allowed("s3", "list_objects_v2")
    with gate.validator("s3", "list_objects_v2", _only_bucket("b")):
        assert gate.is_allowed("s3", "list_objects_v2")
    s = RecordingSession(Raising(RuntimeError("client reached")))
    with pytest.raises(gate.WriteBlockedError):
        gate.call(s, "s3", "list_objects_v2", Bucket="b")
    with pytest.raises(gate.WriteBlockedError):
        gate.paginate(s, "s3", "list_objects_v2", Bucket="b")
    assert s.requests == []


def test_validator_allows_and_refuses_by_params(validated_pair):
    s = RecordingSession(Raising(RuntimeError("client reached")))
    with gate.validator("s3", "list_objects_v2", _only_bucket("b")):
        with pytest.raises(RuntimeError, match="client reached"):
            gate.call(s, "s3", "list_objects_v2", Bucket="b")
        with pytest.raises(gate.WriteBlockedError):
            gate.call(s, "s3", "list_objects_v2", Bucket="other")
        with pytest.raises(gate.WriteBlockedError):
            gate.paginate(s, "s3", "list_objects_v2", Bucket="other")
    with pytest.raises(gate.WriteBlockedError):          # removed again after the block
        gate.call(s, "s3", "list_objects_v2", Bucket="b")


def test_validator_error_of_another_type_becomes_write_blocked(validated_pair):
    def broken(params):
        raise ValueError("bad key")
    with gate.validator("s3", "list_objects_v2", broken):
        with pytest.raises(gate.WriteBlockedError):
            gate.call(RecordingSession(), "s3", "list_objects_v2", Bucket="b")


def test_validator_cannot_be_nested_or_installed_on_an_unvalidated_pair(validated_pair):
    with gate.validator("s3", "list_objects_v2", _only_bucket("b")):
        with pytest.raises(RuntimeError):
            with gate.validator("s3", "list_objects_v2", _only_bucket("c")):
                pass
    with pytest.raises(ValueError):
        with gate.validator("iam", "list_users", _only_bucket("b")):
            pass


def test_ctwatch_pairs_and_validated_s3_reads():
    assert gate.CTWATCH == {("cloudtrail", "lookup_events"), ("cloudtrail", "describe_trails"),
                            ("organizations", "describe_organization"), ("s3", "list_objects_v2"),
                            ("s3", "get_object")}
    assert gate.VALIDATED == {("s3", "list_objects_v2"), ("s3", "get_object"), ("bedrock-runtime", "converse")} and gate.VALIDATED <= gate.ALLOWED
    assert gate.is_allowed("organizations", "describe_organization")
    assert not gate.is_allowed("s3", "get_object") and not gate.is_allowed("s3", "list_objects_v2")
    s = RecordingSession(Raising(RuntimeError("client reached")))
    with pytest.raises(gate.WriteBlockedError):
        gate.call(s, "s3", "get_object", Bucket="b", Key="k")
    assert s.requests == []


def test_import_scope_through_the_gate():
    from aws95.aws.engines.ctwatch.s3keys import get_check, location
    loc = location("example-cloudtrail-bucket", "", "123456789012")
    key = "AWSLogs/123456789012/CloudTrail/us-east-1/2026/10/01/a.json.gz"
    s = RecordingSession(Raising(RuntimeError("client reached")))
    with gate.validator("s3", "get_object", get_check(loc)):
        assert gate.is_allowed("s3", "get_object")
        with pytest.raises(gate.WriteBlockedError):
            gate.bind(s, "s3").get_object(Bucket="other-bucket", Key=key)
        with pytest.raises(RuntimeError, match="client reached"):
            gate.bind(s, "s3").get_object(Bucket="example-cloudtrail-bucket", Key=key)
    assert not gate.is_allowed("s3", "get_object")                    # blocked again after the import
    with pytest.raises(gate.WriteBlockedError):
        gate.bind(s, "s3").get_object(Bucket="example-cloudtrail-bucket", Key=key)
