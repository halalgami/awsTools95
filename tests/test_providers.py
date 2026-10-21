import ast
import inspect
import json
import re
from pathlib import Path

import pytest

from aws95.contracts.common import Scope, Status
from aws95.contracts.resources import ResourceRow
from aws95.providers.base import DOMAINS, ProviderSet
from aws95.providers.demo import FIXTURE_DIR, demo_provider_set
from aws95.providers.demo.builder import FixtureError, build
from aws95.providers.empty import empty_provider_set

ALLOWED_FAKE_ACCOUNTS = {"111111111111", "222222222222", "333333333333"}


def _methods(proto):
    return [n for n, v in vars(proto).items() if inspect.isfunction(v) and not n.startswith("_")]


def test_every_domain_has_a_protocol_and_label():
    assert set(DOMAINS) == {f for f in ProviderSet.__dataclass_fields__ if f not in ("is_demo", "is_live", "store")}


def test_empty_provider_answers_every_method_not_connected():
    ps = empty_provider_set()
    assert ps.is_demo is False
    for attr, (proto, label) in DOMAINS.items():
        provider = getattr(ps, attr)
        for name in _methods(proto):
            result = getattr(provider, name)(Scope())
            assert result.status is Status.NOT_CONNECTED, f"{attr}.{name}"
            assert label in result.message and not result.is_demo


def test_empty_provider_rejects_unknown_methods():
    with pytest.raises(AttributeError):
        empty_provider_set().resources.drop_table()


def test_demo_accounts_and_resources():
    ps = demo_provider_set()
    assert ps.is_demo
    profiles = ps.accounts.list_profiles()
    assert profiles.is_demo and profiles.status is Status.OK and len(profiles.data) == 3
    types = ps.resources.resource_types(Scope())
    counts = {t.rtype: t.count for t in types.data}
    page = ps.resources.list_resources(Scope(), "ec2.instance")
    assert page.is_demo and counts["ec2.instance"] == len(page.data.items)
    assert all(isinstance(r, ResourceRow) for r in page.data.items)


def test_demo_scope_filters_accounts():
    ps = demo_provider_set()
    only = ps.resources.list_resources(Scope(accounts=("222222222222",)), "ec2.instance")
    assert {r.account for r in only.data.items} == {"222222222222"}


def test_demo_paging():
    ps = demo_provider_set()
    first = ps.resources.list_resources(Scope(), "s3.bucket", None, page_size=2)
    assert len(first.data.items) == 2 and first.data.next_cursor == "2"
    second = ps.resources.list_resources(Scope(), "s3.bucket", first.data.next_cursor, page_size=2)
    assert second.data.items[0].rid != first.data.items[0].rid


def test_demo_search_answers_short_queries_with_nothing():
    r = demo_provider_set().search.search(Scope())
    assert r.status is Status.OK and r.is_demo and r.data == ()


def test_builder_validates():
    from decimal import Decimal
    from aws95.contracts.billing import CostPoint
    assert build(CostPoint, {"period": "2026-09", "amount": "1.10", "unit": "USD"}).amount == Decimal("1.10")
    with pytest.raises(FixtureError):
        build(CostPoint, {"period": "2026-09", "amount": 1.1, "unit": "USD"})
    with pytest.raises(FixtureError):
        build(CostPoint, {"period": "2026-09", "amount": "1", "unit": "USD", "bogus": 1})


def test_fixtures_contain_no_real_identifiers():
    email = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
    akia = re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")
    acct = re.compile(r"\b\d{12}\b")
    for path in FIXTURE_DIR.glob("*.json"):
        text = path.read_text()
        assert not email.search(text), path.name
        assert not akia.search(text), path.name
        assert set(acct.findall(text)) <= ALLOWED_FAKE_ACCOUNTS, path.name


def test_empty_provider_answers_new_account_methods():
    from aws95.contracts.common import Scope, Status
    from aws95.providers.empty import empty_provider_set
    acc = empty_provider_set().accounts
    for r in (acc.config_sources(), acc.check_permissions(Scope(), "dev"), acc.sign_in("dev"), acc.required_policy()):
        assert r.status is Status.NOT_CONNECTED and "offline" in r.message


def test_demo_new_account_methods_are_demo():
    from aws95.contracts.common import Scope
    from aws95.providers.demo import demo_provider_set
    acc = demo_provider_set().accounts
    for r in (acc.config_sources(), acc.check_permissions(Scope(), "dev"), acc.sign_in("dev"), acc.required_policy()):
        assert r.is_demo


def test_demo_accounts_satisfy_protocol():
    from aws95.providers.base import AccountsProvider
    from aws95.providers.demo import demo_provider_set
    assert isinstance(demo_provider_set().accounts, AccountsProvider)


def test_demo_check_permissions_only_billing():
    from aws95.contracts.common import Scope
    from aws95.providers.demo import demo_provider_set
    rows = demo_provider_set().accounts.check_permissions(Scope(), "dev", True, only_billing=True).data
    assert [r.capability for r in rows] == ["billing"]
