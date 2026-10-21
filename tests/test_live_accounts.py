import json

import pytest
from botocore.exceptions import EndpointConnectionError, ProfileNotFound, TokenRetrievalError

from aws95.contracts.accounts import RosterEntry
from aws95.contracts.common import Scope, Status
from aws95.providers.base import AccountsProvider
from aws95.providers.live.accounts import LiveAccountsProvider
from tests.aws_stubs import FakeSession, Raising, sts_ok
from tests.test_aws_discover import aws_files  # noqa: F401  (fixture)


def test_satisfies_protocol():
    assert isinstance(LiveAccountsProvider(), AccountsProvider)


def test_list_profiles_joins_roster_without_sessions(aws_files):
    p = LiveAccountsProvider(session_factory=lambda name: pytest.fail("list_profiles must not open a session"))
    p.set_roster((RosterEntry("dev", "123456789012"), RosterEntry("gone", "210987654321")))
    by = {x.name: x for x in p.list_profiles().data}
    assert by["dev"].selected and by["dev"].verified.account_id == "123456789012"
    assert by["corp"].selected is False and by["corp"].verified is None
    assert by["gone"].issue == "Not in config any more" and by["gone"].selected


def test_list_profiles_bad_file_is_error(tmp_path, monkeypatch):
    d = tmp_path / "config"
    d.mkdir()
    monkeypatch.setenv("AWS_CONFIG_FILE", str(d))
    r = LiveAccountsProvider().list_profiles()
    assert r.status is Status.ERROR and str(d) in r.message


def test_verify_profile_ok(aws_files):
    s = FakeSession({"sts": sts_ok()})
    r = LiveAccountsProvider(session_factory=lambda name: s).verify_profile(Scope(), "dev")
    assert r.status is Status.OK and r.data.account_id == "123456789012" and r.data.profile == "dev"
    assert s.requested == [("sts", "us-east-1")]


@pytest.mark.parametrize("exc,status", [(EndpointConnectionError(endpoint_url="https://x"), Status.UNREACHABLE),
                                        (TokenRetrievalError(provider="sso", error_msg="x"), Status.NOT_CONNECTED)])
def test_verify_failures_are_results(aws_files, exc, status):
    p = LiveAccountsProvider(session_factory=lambda name: FakeSession({"sts": Raising(exc)}))
    r = p.verify_profile(Scope(), "dev")
    assert r.status is status and r.freshness is not None


def test_session_construction_failure_is_result(aws_files):
    def boom(name):
        raise ProfileNotFound(profile=name)
    assert LiveAccountsProvider(session_factory=boom).verify_profile(Scope(), "nope").status is Status.NOT_CONNECTED


@pytest.mark.parametrize("name", ["", "   "])
def test_empty_profile_rejected(name):
    p = LiveAccountsProvider(session_factory=lambda n: pytest.fail("no session for an empty name"))
    for r in (p.verify_profile(Scope(), name), p.check_permissions(Scope(), name), p.sign_in(name)):
        assert r.status is Status.ERROR and r.message == "No profile selected."


def test_every_method_never_raises(aws_files):
    p = LiveAccountsProvider(session_factory=lambda name: FakeSession({}))      # client lookups raise KeyError
    for r in (p.list_profiles(), p.verify_profile(Scope(), "dev"), p.config_sources(),
              p.check_permissions(Scope(), "dev"), p.collector_health(Scope()), p.sign_in("bad name"),
              p.required_policy()):
        assert r.status in tuple(Status)


def test_required_policy():
    r = LiveAccountsProvider().required_policy()
    assert r.status is Status.OK and json.loads(r.data)["Version"] == "2012-10-17"


def test_check_permissions_only_billing(aws_files, monkeypatch):
    from aws95.aws import permissions
    seen = {}
    monkeypatch.setattr(permissions, "check", lambda s, region, billing, cancel, only=None:
                        seen.update(region=region, billing=billing, only=only) or ())
    p = LiveAccountsProvider(session_factory=lambda n: object())
    p.check_permissions(Scope(), "dev", True, only_billing=True)
    assert seen == {"region": "me-south-1", "billing": True, "only": {"billing"}}


def test_sign_in_maps_statuses_and_uses_sign_in_profile(aws_files, monkeypatch):
    from aws95.aws import sso
    calls = []
    p = LiveAccountsProvider()
    for raw, status in [((0, "ok"), Status.OK), ((3, "failed"), Status.ERROR), ((None, "no_cli"), Status.NOT_CONNECTED),
                        ((None, "bad_name"), Status.ERROR), ((None, "timeout"), Status.ERROR)]:
        monkeypatch.setattr(sso, "sign_in", lambda profile, cancel, on_prompt=None, raw=raw:
                            calls.append(profile) or raw)
        assert p.sign_in("hop").status is status
    assert set(calls) == {"corp"}                        # hop's SSO source


def test_collector_health_not_connected_until_2b():
    assert LiveAccountsProvider().collector_health(Scope()).status is Status.NOT_CONNECTED


def test_live_set_tracks_roster(tmp_path, qapp, aws_files):
    from PySide6.QtCore import QSettings
    from aws95.core.settings import Settings
    from aws95.providers.live import live_provider_set
    s = Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    ps = live_provider_set(s)
    assert ps.is_live and not ps.is_demo
    assert ps.billing.summary(Scope()).message.startswith("No accounts yet")      # Billing is live since 2b
    s.set_roster((RosterEntry("dev", "123456789012"),))
    assert {p.name for p in ps.accounts.list_profiles().data if p.selected} == {"dev"}


def test_never_profile_is_not_verified_or_probed(aws_files):
    p = LiveAccountsProvider(session_factory=lambda n: pytest.fail("a never profile must not open a session"))
    for r in (p.verify_profile(Scope(), "mfa"), p.check_permissions(Scope(), "mfa"),
              p.check_permissions(Scope(), "via-mfa", only_billing=True)):
        assert r.status is Status.NOT_CONNECTED and "MFA" in r.message
