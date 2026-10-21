import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from aws95.contracts.activity import ActivityQuery
from aws95.contracts.common import Scope, Status, TimeRange
from aws95.providers.demo import FIXTURE_DIR, demo_provider_set
from aws95.providers.demo import store
from tools import make_demo_fixtures

A, B = "111111111111", "222222222222"
NOW = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def ps(monkeypatch):
    monkeypatch.setattr(store, "now", lambda: NOW)
    return demo_provider_set()


def test_generator_is_reproducible(tmp_path):
    make_demo_fixtures.write_all(tmp_path)
    for name in make_demo_fixtures.fixtures():
        assert (tmp_path / name).read_bytes() == (FIXTURE_DIR / name).read_bytes(), name


def test_every_domain_is_implemented_including_search(ps):
    assert ps.is_demo
    r = ps.search.search(Scope(), "-0")                 # 2d part 1: search is live in demo too
    assert r.status is Status.OK and r.is_demo and r.data


def test_every_demo_result_is_flagged_and_fresh(ps):
    calls = [
        ps.accounts.collector_health(Scope()), ps.billing.summary(Scope()), ps.billing.breakdown(Scope(), "service"),
        ps.billing.trend(Scope(), "monthly"), ps.billing.free_tier(Scope()), ps.billing.uncovered_spend(Scope()),
        ps.findings.list_findings(Scope(), "open"), ps.findings.rules(Scope()), ps.findings.fleet(Scope()),
        ps.snapshots.list_snapshots(Scope()), ps.snapshots.diff(Scope(), 101, 102), ps.snapshots.metric_names(Scope()),
        ps.activity.events(Scope()), ps.activity.principals(Scope()), ps.activity.time_series(Scope(), "day"),
        ps.activity.top(Scope(), "principal"), ps.activity.history_status(Scope()),
        ps.insights.preview_payload(Scope(), "insights"), ps.insights.analyze(Scope(), "oddities", {"analysis": "oddities"}),
        ps.reports.report_types(Scope()),
    ]
    for r in calls:
        assert r.status is Status.OK and r.is_demo and r.freshness == store.DEMO_FRESHNESS, r
    cost = ps.reports.render(Scope(), "cost")          # the demo snapshots have gaps, so the report says PARTIAL
    assert cost.status is Status.PARTIAL and cost.is_demo and cost.freshness == store.DEMO_FRESHNESS


def test_collector_health_degraded_account(ps):
    rows = {h.collector: h for h in ps.accounts.collector_health(Scope(accounts=(B,))).data}
    assert rows["iam"].status is Status.DENIED and "AccessDenied" in rows["iam"].error
    assert rows["rds"].status is Status.PARTIAL
    assert all(h.status is Status.OK for h in ps.accounts.collector_health(Scope(accounts=(A,))).data)


def test_billing_values_are_exact_decimals(ps):
    s = ps.billing.summary(Scope()).data
    assert s.mtd == Decimal("412.3415") and s.forecast == Decimal("655.10") and s.last_month == Decimal("598.42")
    services = ps.billing.breakdown(Scope(), "service").data
    assert all(isinstance(p.amount, Decimal) for p in services)
    assert any(p.amount.as_tuple().exponent < -2 for p in services)        # sub-cent values exist
    assert len(ps.billing.trend(Scope(), "monthly").data) == 13
    assert len(ps.billing.trend(Scope(), "daily").data) == 30
    accts = ps.billing.breakdown(Scope(accounts=(A,)), "account").data
    assert [p.dims["account"] for p in accts] == [A]


def test_findings_states_rules_and_fleet(ps):
    open_ = ps.findings.list_findings(Scope(), "open").data.items
    assert open_ and all(f.state == "open" for f in open_)
    assert any(f.seeded for f in open_)
    accepted = ps.findings.list_findings(Scope(), "accepted").data.items
    assert accepted and all(f.mute is not None for f in accepted)
    only_a = ps.findings.list_findings(Scope(accounts=(A,)), "open").data.items
    assert only_a and {f.account for f in only_a} == {A}
    rules = ps.findings.rules(Scope()).data
    assert len(rules) == 12 and sum(1 for r in rules if not r.enabled) == 1
    cells = ps.findings.fleet(Scope()).data
    assert {(c.account, c.rule) for c in cells if not c.collected} == {(B, "iam-user-no-mfa"), (B, "iam-access-key-old")}


def test_snapshots_diff_and_metrics(ps):
    snaps = ps.snapshots.list_snapshots(Scope()).data
    assert [s.id for s in snaps if s.account == A] == [103, 102, 101]
    assert next(s for s in snaps if s.id == 203).status == "partial"
    diff = ps.snapshots.diff(Scope(), 202, 203).data
    excluded = [d for d in diff if d.kind == "excluded"]
    assert excluded and all(d.reason for d in excluded)
    assert ps.snapshots.diff(Scope(), 101, 202).data == ()
    names = ps.snapshots.metric_names(Scope()).data
    assert len(names) == 4
    for name in names:
        series = ps.snapshots.metric_series(Scope(), name).data
        assert series and any(p.restated for p in series)


def test_activity_rebased_to_now_and_filtered(ps):
    page = ps.activity.events(Scope()).data
    assert page.total == 260 and page.items[0].time_utc == NOW
    assert all(page.items[i].time_utc >= page.items[i + 1].time_utc for i in range(len(page.items) - 1))
    last_day = TimeRange(NOW - timedelta(days=1), NOW)
    day = ps.activity.events(Scope(time_range=last_day)).data.items
    assert day and all(e.time_utc >= NOW - timedelta(days=1) for e in day)
    risky = ps.activity.events(Scope(), ActivityQuery(risk_min=2)).data.items
    assert risky and all(e.risk_level >= 2 for e in risky)
    humans = ps.activity.events(Scope(), ActivityQuery(human_only=True)).data.items
    assert humans and all(e.is_human for e in humans)
    kinds = dict(ps.activity.top(Scope(), "actor_kind").data)
    assert set(kinds) == {"human", "service"}
    top = ps.activity.top(Scope(), "action", limit=3).data
    assert len(top) == 3 and top[0][1] >= top[1][1] >= top[2][1]
    series = ps.activity.time_series(Scope(), "day").data
    assert series.bucket == "day" and all(n > 0 for _, n in series.series)
    raw = ps.activity.raw_event(Scope(), page.items[0].event_id).data
    assert raw["eventID"] == page.items[0].event_id and "userIdentity" in raw
    assert ps.activity.raw_event(Scope(), "evt-nope").status is Status.ERROR
    st = ps.activity.history_status(Scope()).data
    assert st.has_history and st.denied_reads_since is not None and st.running is None
    assert st.accounts[0].regions[0].looked_up[0].end == NOW          # rebased to the pinned demo "now"


def test_insights_payload_has_aggregates_only(ps):
    payload = ps.insights.preview_payload(Scope(), "insights").data
    text = json.dumps(payload)
    assert "arn:" not in text and A not in text and B not in text
    report = ps.insights.analyze(Scope(), "advice", payload).data
    assert report.kind == "advice" and report.sections and report.model == "demo (no model called)"
    missing = ps.insights.analyze(Scope(), "advice")
    assert missing.status is Status.ERROR and missing.data is None and missing.is_demo


def test_reports_render_from_demo_data(ps):
    types = {t.id for t in ps.reports.report_types(Scope()).data}
    assert types == {"inventory", "cost", "findings", "activity"}
    cost = ps.reports.render(Scope(), "cost").data
    assert "412.3415" in cost.markdown and "412.3415" in cost.csv
    assert ps.reports.render(Scope(), "bogus").status is Status.ERROR


def test_activity_times_are_stable_across_calls(monkeypatch):
    ticks = iter(NOW + timedelta(seconds=n) for n in range(1000))     # a clock that moves on every read
    monkeypatch.setattr(store, "now", lambda: next(ticks))
    a = demo_provider_set().activity
    first = a.events(Scope()).data.items[0]
    again = a.events(Scope()).data.items[0]
    raw = a.raw_event(Scope(), first.event_id).data
    assert first.time_utc == again.time_utc
    assert raw["eventTime"] == first.time_utc.isoformat()


def test_activity_events_are_built_once_per_instance(ps):
    activity = ps.activity
    first = activity._events()
    assert activity._events() is first
    assert [e.time_utc for e in first] == [e.time_utc for e in demo_provider_set().activity._events()]


def test_builder_caches_type_hints_without_changing_validation():
    from aws95.contracts.activity import ActivityEvent
    from aws95.providers.demo import builder

    builder._hints.cache_clear()
    with pytest.raises(builder.FixtureError, match="unknown fields"):
        builder.build(ActivityEvent, {"bogus": 1})
    with pytest.raises(builder.FixtureError, match="unknown fields"):
        builder.build(ActivityEvent, {"bogus": 1})
    assert builder._hints.cache_info().hits >= 1
    with pytest.raises(builder.FixtureError, match="money must be written as a string"):
        builder.build(Decimal, 1.5)
    with pytest.raises(builder.FixtureError, match="must include a timezone"):
        builder.build(datetime, "2026-01-01T00:00:00")


def test_demo_activity_never_reaches_aws(ps):
    for r in (ps.activity.estimate_history(Scope()), ps.activity.load_history(Scope()),
              ps.activity.find_trails(Scope()), ps.activity.check_import(Scope()),
              ps.activity.import_history(Scope())):
        assert r.status is Status.NOT_CONNECTED and r.is_demo and "demo mode" in r.message


def test_demo_shows_edge_and_dns(ps):
    """Edge-dns spec §5: hosted zones, APIs with stages and distributions, with findings for the new rules."""
    types = {t.rtype: t.domain for t in ps.resources.resource_types(Scope()).data}
    assert types["route53.hosted_zone"] == types["cloudfront.distribution"] == "Edge & DNS"
    assert types["apigateway.api"] == types["apigateway.stage"] == "Network"
    counts = {rt: len(ps.resources.list_resources(Scope(), rt).data.items) for rt in types if "." in rt}
    assert (counts["route53.hosted_zone"], counts["apigateway.api"], counts["cloudfront.distribution"]) == (2, 3, 2)
    assert counts["apigateway.stage"] >= 3
    open_rules = {f.rule for f in ps.findings.list_findings(Scope(), "open").data.items}
    assert {"cloudfront-no-waf", "apigw-stage-no-logging"} <= open_rules
