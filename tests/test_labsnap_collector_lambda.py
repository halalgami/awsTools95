# Upstream: labsnap/tests/test_collector_lambda.py @ 1b1d437 — adapted for aws95; owned here from now on.
from botocore.exceptions import ClientError
from aws95.aws.engines.labsnap.collect.collectors.lambda_ import LambdaCollector


def denied(op):
    return ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, op)


def absent(op):
    return ClientError(
        {"Error": {"Code": "ResourceNotFoundException", "Message": "gone"}}, op)


class FakePaginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **kw):
        return iter(self._pages)


FUNCTIONS_PAGE = [{"Functions": [
    {"FunctionName": "fn-a", "Runtime": "python3.12", "MemorySize": 256, "Timeout": 30},
    {"FunctionName": "fn-b", "Runtime": "python3.12", "MemorySize": 512, "Timeout": 15},
    {"FunctionName": "fn-c", "Runtime": "nodejs20.x", "MemorySize": 128, "Timeout": 10},
]}]


class FakeLambda:
    def __init__(self, deny_pc_for=(), gone_pc_for=()):
        self.deny_pc_for = set(deny_pc_for)
        self.gone_pc_for = set(gone_pc_for)

    def get_paginator(self, op):
        assert op == "list_functions"
        return FakePaginator(FUNCTIONS_PAGE)

    def list_provisioned_concurrency_configs(self, FunctionName):
        if FunctionName in self.deny_pc_for:
            raise denied("ListProvisionedConcurrencyConfigs")
        if FunctionName in self.gone_pc_for:
            raise absent("ListProvisionedConcurrencyConfigs")
        configs = {"fn-a": [{"FunctionArn": "x"}], "fn-b": [], "fn-c": []}
        return {"ProvisionedConcurrencyConfigs": configs[FunctionName]}


class FakeSession:
    def __init__(self, **kw):
        self._lam = FakeLambda(**kw)

    def client(self, name, **kw):
        assert name == "lambda"
        return self._lam


def test_function_attrs_populated():
    res = LambdaCollector().collect(FakeSession(), "us-east-1")
    fns = {r.rid: r for r in res.resources}
    assert fns["fn-a"].attrs["runtime"] == "python3.12"
    assert fns["fn-a"].attrs["memory_mb"] == 256
    assert fns["fn-a"].attrs["timeout_s"] == 30
    assert fns["fn-a"].attrs["provisioned_concurrency"] == 1
    assert fns["fn-b"].attrs["provisioned_concurrency"] == 0


def test_region_is_carried_through():
    res = LambdaCollector().collect(FakeSession(), "eu-west-1")
    assert all(r.region == "eu-west-1" for r in res.resources)


def test_function_count_metric_dimensioned_by_runtime():
    res = LambdaCollector().collect(FakeSession(), "us-east-1")
    counts = {m.dims["runtime"]: m.value for m in res.metrics
              if m.name == "lambda.function_count"}
    assert counts == {"python3.12": 2.0, "nodejs20.x": 1.0}


def test_denied_provisioned_concurrency_marks_only_that_function_partial():
    res = LambdaCollector().collect(FakeSession(deny_pc_for=["fn-a"]), "us-east-1")
    fns = {r.rid: r for r in res.resources}
    assert fns["fn-a"].partial is True
    assert "provisioned_concurrency" not in fns["fn-a"].attrs
    assert fns["fn-a"].attrs["runtime"] == "python3.12"  # other attrs still present
    assert fns["fn-b"].partial is False
    assert fns["fn-b"].attrs["provisioned_concurrency"] == 0
    assert res.denials == 1


# ---------------------------------------------------------------------------
# I1 - probe() also returns (None, False) for an ABSENT_CODES error (the
# function deleted mid-scan), which is not a denial and must not crash.
# ---------------------------------------------------------------------------

def test_mid_scan_function_deletion_does_not_crash_the_collector():
    res = LambdaCollector().collect(FakeSession(gone_pc_for=["fn-a"]), "us-east-1")
    fns = {r.rid: r for r in res.resources}
    assert "provisioned_concurrency" not in fns["fn-a"].attrs
    # An absence is not a denial: no permission was denied.
    assert fns["fn-a"].partial is False
    assert res.denials == 0
    assert fns["fn-b"].attrs["provisioned_concurrency"] == 0


# ---------------------------------------------------------------------------
# I3 - lambda.function_count is missing its `region` dim, unlike logs.py,
# rds.py, and ec2.py. metrics' primary key is (snapshot_id, name, dims,
# period); two regions with the same runtime produce identical dims, so
# "INSERT OR REPLACE" silently drops one region's count.
# ---------------------------------------------------------------------------

def test_function_count_metric_is_dimensioned_by_region():
    res_east = LambdaCollector().collect(FakeSession(), "us-east-1")
    res_west = LambdaCollector().collect(FakeSession(), "eu-west-1")
    for m in res_east.metrics + res_west.metrics:
        assert m.dims.get("region") is not None


CONTAINER_FUNCTIONS_PAGE = [{"Functions": [
    {"FunctionName": "fn-a", "Runtime": "python3.12", "MemorySize": 256,
     "Timeout": 30},
    {"FunctionName": "fn-image", "MemorySize": 1024, "Timeout": 60},
]}]


class FakeLambdaWithContainerImages(FakeLambda):
    def get_paginator(self, op):
        assert op == "list_functions"
        return FakePaginator(CONTAINER_FUNCTIONS_PAGE)

    def list_provisioned_concurrency_configs(self, FunctionName):
        return {"ProvisionedConcurrencyConfigs": []}


class FakeSessionWithContainerImages(FakeSession):
    def __init__(self, **kw):
        self._lam = FakeLambdaWithContainerImages(**kw)


def test_container_image_function_does_not_crash_the_collector():
    res = LambdaCollector().collect(FakeSessionWithContainerImages(), "us-east-1")
    fns = {r.rid: r for r in res.resources}
    assert set(fns) == {"fn-a", "fn-image"}
    assert fns["fn-a"].attrs["runtime"] == "python3.12"
    assert fns["fn-image"].attrs["runtime"] == "(container)"
    # A container image genuinely has no managed runtime - that is known,
    # not unreadable, so it must not be marked partial.
    assert fns["fn-image"].partial is False


def test_container_image_runtime_counted_in_its_own_metric_bucket():
    res = LambdaCollector().collect(FakeSessionWithContainerImages(), "us-east-1")
    counts = {m.dims["runtime"]: m.value for m in res.metrics
              if m.name == "lambda.function_count"}
    assert counts == {"python3.12": 1.0, "(container)": 1.0}
