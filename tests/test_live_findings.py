from datetime import timedelta

import pytest

from aws95.contracts.common import Scope, Status
from aws95.providers.base import FindingsProvider
from aws95.providers.live.common import NO_SNAPSHOT
from aws95.providers.live.findings import MAX_REASON, UNVERIFIED_NOTE, LiveFindings
from tests.engine_helpers import build_snapshot
from tests.store_seed import DEV, PINNED, PROD, ROSTER, F, seed_store

ALL = Scope()


@pytest.fixture
def store(tmp_path):
    s = seed_store(tmp_path / "aws95.db")
    yield s
    s.close()


def live(store, clock=lambda: PINNED):
    return LiveFindings(store, lambda: ROSTER, clock=clock)


def rows(result):
    return [(f.account, f.rule, f.rid) for f in result.data.items]


def test_protocol(store):
    assert isinstance(live(store), FindingsProvider)


def test_open_accepted_and_resolved(store):
    p = live(store)
    open_ = p.list_findings(ALL, "open")
    assert rows(open_) == [(DEV, "iam.user_no_mfa", "alice"), (DEV, "s3.no_lifecycle", "example-logs"),
                           (PROD, "s3.no_lifecycle", "prod-data")]
    alice = open_.data.items[0]
    assert alice.first_seen == PINNED - timedelta(days=8) and alice.age_days == 8 and alice.state == "open"
    [accepted] = p.list_findings(ALL, "accepted").data.items
    assert (accepted.rid, accepted.mute.reason, accepted.mute.expires_at) == \
        ("sg-0123", "Bastion host; the NACL limits SSH to the office.", None)
    assert rows(p.list_findings(ALL, "resolved")) == [(DEV, "iam.user_no_mfa", "carol")]
    assert open_.freshness.source == "snapshot"


def test_region_filter_keeps_global_findings(store):
    p = live(store)
    assert p.list_findings(Scope(regions=("eu-west-1",)), "accepted").data.items == ()
    assert len(p.list_findings(Scope(regions=("eu-west-1",)), "open").data.items) == 3


def test_accept_and_unaccept_round_trip(store):
    p = live(store)
    alice = p.list_findings(ALL, "open").data.items[0]
    r = p.accept(ALL, alice, "resource", "  Service account; MFA not possible  ", PINNED + timedelta(days=30))
    assert r.status is Status.OK and r.data.reason == "Service account; MFA not possible"
    assert r.data.account == DEV and r.data.rid == "alice"
    assert "alice" not in [f.rid for f in p.list_findings(ALL, "open").data.items]
    assert "alice" in [f.rid for f in p.list_findings(ALL, "accepted").data.items]
    assert p.unaccept(ALL, r.data.id).status is Status.OK
    assert "alice" in [f.rid for f in p.list_findings(ALL, "open").data.items]
    assert p.unaccept(ALL, r.data.id).status is Status.ERROR


def test_accept_targets(store):
    p = live(store)
    f = p.list_findings(ALL, "open").data.items[1]
    by_account = p.accept(ALL, f, "account", "whole account", None).data
    assert (by_account.account, by_account.rid) == (DEV, None)
    by_rule = p.accept(ALL, f, "rule", "everywhere", None).data
    assert (by_rule.account, by_rule.rid) == (None, None)
    assert p.list_findings(ALL, "open").data.items[-1].rid != "prod-data"     # the rule-wide accept covers prod too


@pytest.mark.parametrize("reason,expires,message", [
    ("", None, "Give a reason"),
    ("x" * (MAX_REASON + 1), None, "under 500"),
    ("ok", PINNED - timedelta(minutes=1), "in the future"),
])
def test_accept_validation(store, reason, expires, message):
    p = live(store)
    f = p.list_findings(ALL, "open").data.items[0]
    r = p.accept(ALL, f, "resource", reason, expires)
    assert r.status is Status.ERROR and message in r.message
    assert p.accept(ALL, None, "resource", "why").status is Status.ERROR


def test_accept_reason_is_sanitised(store):
    p = live(store)
    f = p.list_findings(ALL, "open").data.items[0]
    assert p.accept(ALL, f, "resource", "fine\u202e reason", None).data.reason == "fine reason"


def test_accept_never_touches_aws(store, monkeypatch):
    import aws95.aws.session as session_mod
    monkeypatch.setattr(session_mod, "session_for", lambda *a, **k: pytest.fail("Accept opened an AWS session"))
    p = live(store)
    f = p.list_findings(ALL, "open").data.items[0]
    assert p.accept(ALL, f, "resource", "local only", None).status is Status.OK


def test_rules_are_the_44_shipped(store):
    rules = live(store).rules(ALL).data
    assert len(rules) == 44 and all(r.source == "built-in" and r.enabled for r in rules)


def test_fleet_matrix(store):
    cells = {(c.rule, c.account): c for c in live(store).fleet(ALL).data}
    assert len(cells) == 44 * 2
    assert cells[("s3.no_lifecycle", DEV)].symbol == "✗" and cells[("s3.no_lifecycle", PROD)].symbol == "✗"
    assert cells[("iam.user_no_mfa", PROD)].symbol == "?"            # iam was denied in prod
    assert cells[("budget.ineffective", PROD)].symbol == "?"         # no Billing in prod
    assert cells[("ec2.sg_open_admin_port", DEV)].symbol == "✓"      # accepted, so not open
    assert cells[("iam.user_no_mfa", DEV)].symbol == "✗"


def test_unverified_finding_is_open_with_a_note(make_store):
    s = make_store("u.db")
    t = PINNED - timedelta(days=2)
    build_snapshot(s, t, findings=[F("s3.no_lifecycle", "s3.bucket", "global", "b1", "medium")])
    build_snapshot(s, t + timedelta(days=1), status="failed", covered=(), findings=[], rules={"s3.no_lifecycle"})
    r = LiveFindings(s, lambda: ROSTER[:1], clock=lambda: PINNED).list_findings(ALL, "open")
    [f] = r.data.items
    assert f.detail["note"] == UNVERIFIED_NOTE
    assert r.status is Status.PARTIAL and "not re-checked" in r.message


def test_no_snapshot(make_store):
    r = LiveFindings(make_store("e.db"), lambda: ROSTER, clock=lambda: PINNED).list_findings(ALL)
    assert r.status is Status.NOT_CONNECTED and r.message == NO_SNAPSHOT


def test_demo_never_writes():
    from aws95.providers.demo import demo_provider_set
    r = demo_provider_set().findings.accept(ALL, None, "resource", "x", None)
    assert r.status is Status.NOT_CONNECTED and r.is_demo
