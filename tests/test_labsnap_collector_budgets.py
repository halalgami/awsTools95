# Upstream: labsnap/tests/test_collector_budgets.py @ 1b1d437 — adapted for aws95; owned here from now on.
from botocore.exceptions import ClientError
from aws95.aws.engines.labsnap.collect.collectors.budgets import BudgetsCollector


def denied(op):
    return ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, op)


BUDGETS_PAGE = [{"Budgets": [
    {"BudgetName": "monthly-cap", "BudgetLimit": {"Amount": "500.0", "Unit": "USD"},
     "CostFilters": {"TagKeyValue": ["user:project$example-lab"]}},
    {"BudgetName": "no-tag-budget", "BudgetLimit": {"Amount": "100.0", "Unit": "USD"},
     "CostFilters": {}},
]}]


class FakePaginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **kw):
        # request params matter: describe_budgets requires the caller's
        # AccountId, and a wrong or missing one would silently ask for the
        # wrong account's budgets.
        assert kw.get("AccountId") == "112233445566"
        return iter(self._pages)


class FakeBudgets:
    def get_paginator(self, op):
        assert op == "describe_budgets"
        return FakePaginator(BUDGETS_PAGE)


class FakeCE:
    def __init__(self, deny=False):
        self.deny = deny

    def list_cost_allocation_tags(self, **kw):
        assert kw.get("Status") == "Active"
        if self.deny:
            raise denied("ListCostAllocationTags")
        return {"CostAllocationTags": [{"TagKey": "project"}, {"TagKey": "env"}]}


class FakeSTS:
    def get_caller_identity(self):
        return {"Account": "112233445566"}


class FakeSession:
    def __init__(self, deny_tags=False):
        self._budgets = FakeBudgets()
        self._ce = FakeCE(deny=deny_tags)
        self._sts = FakeSTS()

    def client(self, name, **kw):
        return {"budgets": self._budgets, "ce": self._ce, "sts": self._sts}[name]


def test_budget_attrs_populated():
    res = BudgetsCollector().collect(FakeSession(), "global")
    budgets = {r.rid: r for r in res.resources}
    assert budgets["monthly-cap"].attrs["limit_amount"] == "500.0"
    assert budgets["monthly-cap"].attrs["limit_unit"] == "USD"
    assert budgets["monthly-cap"].attrs["filters_on_tag"] is True
    assert budgets["no-tag-budget"].attrs["filters_on_tag"] is False


def test_region_is_global_on_every_resource():
    res = BudgetsCollector().collect(FakeSession(), "global")
    assert all(r.region == "global" for r in res.resources)


class RecordingSession(FakeSession):
    def __init__(self):
        super().__init__()
        self.asked = []

    def client(self, name, **kw):
        self.asked.append(name)
        return super().client(name, **kw)


def test_budgets_never_calls_cost_explorer():
    s = RecordingSession()
    res = BudgetsCollector().collect(s, "global")
    assert "ce" not in s.asked
    assert res.metrics == [] and res.denials == 0
