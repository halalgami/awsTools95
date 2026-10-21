import threading
from datetime import date, datetime, timezone

import pytest

from aws95.contracts.common import Scope, Status
from aws95.core.cancel import CancelToken
from aws95.core.jobs import JobLock, SNAPSHOT_RUNNING
from aws95.providers.demo import demo_provider_set
from aws95.providers.live.findings import LiveFindings
from tests.store_seed import DEV, PINNED, ROSTER, seed_store

ALL = Scope()


@pytest.fixture
def env(tmp_path):
    s = seed_store(tmp_path / "aws95.db")
    jobs = JobLock()
    yield s, LiveFindings(s, lambda: ROSTER, clock=lambda: PINNED, jobs=jobs), jobs
    s.close()


def open_rules(p):
    return {f.rule for f in p.list_findings(ALL, "open").data.items}


def test_set_rule_saves_rechecks_and_reports(env):
    s, p, _ = env
    r = p.set_rule(ALL, "s3.no_lifecycle", enabled=False, severity=None, params=None)
    assert r.status is Status.OK and r.data.snapshots == 2 and r.message.startswith("Re-checked 2 snapshots:")
    assert "s3.no_lifecycle" not in open_rules(p)
    info = {i.id: i for i in p.rules(ALL).data}["s3.no_lifecycle"]
    assert info.tuned and not info.enabled and info.default_severity == "medium"
    assert p.reset_rule(ALL, "s3.no_lifecycle").status is Status.OK
    assert "s3.no_lifecycle" in open_rules(p) and not {i.id: i for i in p.rules(ALL).data}["s3.no_lifecycle"].tuned


def test_set_rule_rejects_bad_params_and_saves_nothing(env):
    """Review Focus 2."""
    s, p, _ = env
    r = p.set_rule(ALL, "ec2.sg_open_admin_port", enabled=True, severity=None, params={"ports": "22"})
    assert r.status is Status.ERROR and "ports" in r.message and s.rule_overrides() == {}


def test_shipped_values_clear_the_override(env):
    s, p, _ = env
    p.set_rule(ALL, "iam.key_stale", enabled=True, severity="high", params={"max_age_days": 180})
    assert s.rule_overrides() == {}


def test_tuned_findings_are_marked(env):
    s, p, _ = env
    p.set_rule(ALL, "iam.user_no_mfa", enabled=True, severity="low", params=None)
    [f] = [f for f in p.list_findings(ALL, "open").data.items if f.rule == "iam.user_no_mfa"]
    assert f.tuned and f.severity == "low"


def test_seed_must_be_earlier(env):
    """Review Focus 5."""
    s, p, _ = env
    [f] = [f for f in p.list_findings(ALL, "open").data.items if f.rule == "iam.user_no_mfa"]
    assert p.seed(ALL, f, f.first_seen.date()).status is Status.ERROR
    assert p.seed(ALL, f, date(2026, 1, 1), "Q2 audit").status is Status.OK
    [g] = [g for g in p.list_findings(ALL, "open").data.items if g.rule == "iam.user_no_mfa"]
    assert g.seeded and g.first_seen.date() == date(2026, 1, 1)
    assert p.unseed(ALL, g).status is Status.OK
    [h] = [h for h in p.list_findings(ALL, "open").data.items if h.rule == "iam.user_no_mfa"]
    assert not h.seeded


def test_backfill_since_date_oldest_first(env):
    s, p, _ = env
    seen = []
    r = p.backfill(Scope(accounts=(DEV,)), date(2026, 9, 1), progress=lambda d, t: seen.append((d, t)))
    assert r.status is Status.OK and r.data.snapshots == r.data.total == 2 and seen[-1] == (2, 2)
    assert r.message.startswith("Backfilled 2 snapshots since 2026-09-01:")
    none = p.backfill(ALL, date(2030, 1, 1))
    assert none.status is Status.OK and none.data.total == 0


def test_backfill_cancel_keeps_finished_work(env):
    """Review Focus 3."""
    s, p, _ = env
    stop = CancelToken()

    def progress(done, total):
        if done == 1:
            stop.cancel()
    r = p.backfill(ALL, date(2026, 1, 1), progress=progress, stop=stop)
    assert r.data.cancelled and r.data.snapshots == 1 and "cancelled after 1 of 3" in r.message


def test_backfill_and_snapshot_refuse_each_other(env, tmp_path):
    """Review Focus 4."""
    s, p, jobs = env
    assert jobs.try_acquire("snapshot")
    try:
        r = p.backfill(ALL, date(2026, 1, 1))
        assert r.status is Status.ERROR and r.message == SNAPSHOT_RUNNING
        assert p.set_rule(ALL, "s3.no_lifecycle", enabled=False, severity=None, params=None).message == SNAPSHOT_RUNNING
    finally:
        jobs.release()
    from aws95.aws.engines.labsnap.service import SnapshotBusy, SnapshotService
    assert jobs.try_acquire("backfill")
    try:
        with pytest.raises(SnapshotBusy, match="Backfill is running"):
            SnapshotService(s, jobs=jobs).take([], set(), {})
    finally:
        jobs.release()


def test_demo_refuses_every_write():
    f = demo_provider_set().findings
    any_finding = f.list_findings(ALL, "open").data.items[0]
    for r in (f.set_rule(ALL, "s3.no_lifecycle", enabled=False, severity=None, params=None),
              f.reset_rule(ALL, "s3.no_lifecycle"), f.seed(ALL, any_finding, date(2020, 1, 1)),
              f.unseed(ALL, any_finding), f.backfill(ALL, date(2020, 1, 1))):
        assert r.status is Status.NOT_CONNECTED and r.is_demo and "demo mode" in r.message


def test_busy_text_reports_a_running_backfill(env):
    """Review 2026-10-21: Clear Local Data must not delete the store under a running backfill."""
    s, p, jobs = env
    assert p.busy_text() is None
    assert jobs.try_acquire("backfill")
    try:
        assert p.busy_text() == "Backfill is running. Try again when it finishes."
    finally:
        jobs.release()
