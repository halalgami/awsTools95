import threading
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from botocore.exceptions import ClientError

from aws95.aws.engines.labsnap import service as service_mod
from aws95.aws.engines.labsnap.collect.collectors.cost import CostCollector
from aws95.aws.engines.labsnap.model import CollectorResult, Finding, Metric, Resource
from aws95.aws.engines.labsnap.service import SnapshotBusy, SnapshotService
from aws95.contracts.accounts import RosterEntry
from aws95.core.cancel import CancelToken, raise_if_cancelled
from aws95.store import Store

A, OTHER = "123456789012", "111111111111"
DEV = RosterEntry("dev", A)
NOW = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
REGIONS = ("us-east-1", "eu-west-1")


class FakeCollector:
    volatile_keys = frozenset()

    def __init__(self, name, scope="regional", *, billed=False, fail_regions=(), denials=0, block=None,
                 timeout_s=60):
        self.name, self.scope, self.billed, self.timeout_s = name, scope, billed, timeout_s
        self.fail_regions, self.denials, self.block = set(fail_regions), denials, block
        self.seen = []

    def collect(self, session, region, cancel=None):
        self.seen.append(region)
        if self.block is not None:                      # wait until released or cancelled
            while not self.block.wait(0.01):
                raise_if_cancelled(cancel)
        if region in self.fail_regions:
            raise RuntimeError("secret-looking text from AWS")
        return CollectorResult([Resource(f"{self.name}.thing", region, f"{self.name}-{region}", {"ok": True})],
                               [Metric(f"{self.name}.count", Decimal(1), dims={"region": region})],
                               [region], self.denials)


class AllRule:
    id = "t.all"
    severity = "low"
    params: dict = {}

    def evaluate(self, snap, params):
        for r in snap.resources():
            yield Finding(self.id, r.rtype, r.region, r.rid, self.severity)


@pytest.fixture
def caller(monkeypatch):
    who = {"account": A}

    def fake(session, cancel=None):
        if isinstance(who["account"], Exception):
            raise who["account"]
        return who["account"], f"arn:aws:iam::{who['account']}:user/auditor"
    monkeypatch.setattr(service_mod.identity, "caller", fake)
    return who


def make(tmp_path, collectors, session_factory=None, **kw):
    store = Store(tmp_path / "s.db")
    svc = SnapshotService(store, session_factory=session_factory or (lambda profile: object()),
                          collectors=lambda: list(collectors), rules=lambda: [AllRule()], utcnow=lambda: NOW,
                          poll_s=0.01, **kw)
    return store, svc


def runs(store, sid):
    with store.reader() as db:
        return {r["collector"]: dict(r) for r in db.execute("SELECT * FROM collector_runs WHERE snapshot_id=?", (sid,))}


def count(store, table):
    with store.reader() as db:
        return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_complete_snapshot_saves_resources_metrics_and_findings(tmp_path, caller):
    store, svc = make(tmp_path, [FakeCollector("ec2"), FakeCollector("iam", "global")])
    out = svc.take([DEV], set(), {A: REGIONS})
    [run] = out.runs
    assert (run.account_id, run.status, out.cancelled) == (A, "complete", False)
    r = runs(store, run.snapshot_id)
    assert r["ec2"]["regions_covered"] == '["eu-west-1", "us-east-1"]' and r["iam"]["regions_covered"] == '["global"]'
    assert count(store, "resource_state") == 3 and count(store, "metrics") == 3 and count(store, "findings") == 3
    assert store.latest_snapshot(A)["regions"] == '["us-east-1", "eu-west-1"]'


def test_progress_reaches_total_and_names_regions(tmp_path, caller):
    seen = []
    store, svc = make(tmp_path, [FakeCollector("ec2"), FakeCollector("iam", "global")])
    svc.take([DEV], set(), {A: REGIONS}, progress=seen.append, labels={A: "dev"})
    assert seen[0].done == 0 and seen[-1].done == seen[-1].total == 3
    assert any(p.region == "eu-west-1" for p in seen)
    assert seen[-1].text == "dev: 3/3 sections"


def test_billing_opt_in_is_honoured(tmp_path, caller):
    store, svc = make(tmp_path, [FakeCollector("ec2"), FakeCollector("cost", "global", billed=True)])
    off = svc.take([DEV], set(), {A: REGIONS}).runs[0]
    on = svc.take([DEV], {A}, {A: REGIONS}).runs[0]
    assert "cost" not in runs(store, off.snapshot_id) and "cost" in runs(store, on.snapshot_id)


class RecordingSession:
    def __init__(self, asked):
        self.asked = asked

    def client(self, name, **kw):
        self.asked.append(name)
        raise RuntimeError("no network in tests")


def test_no_cost_explorer_call_without_billing(tmp_path, caller):
    asked = []
    store, svc = make(tmp_path, [CostCollector()], session_factory=lambda profile: RecordingSession(asked))
    svc.take([DEV], set(), {A: REGIONS})
    assert asked == []                     # the billed collector never ran: no client at all
    svc.take([DEV], {A}, {A: REGIONS})
    assert asked == ["sts"]                # it ran (its free identity call comes first, then fails here)


def test_region_failure_is_partial_and_hides_aws_text(tmp_path, caller):
    store, svc = make(tmp_path, [FakeCollector("ec2", fail_regions={"eu-west-1"})])
    [run] = svc.take([DEV], set(), {A: REGIONS}).runs
    r = runs(store, run.snapshot_id)["ec2"]
    assert run.status == "partial" and r["status"] == "ok" and r["regions_covered"] == '["us-east-1"]'
    assert r["error"].startswith("eu-west-1: ") and "secret-looking" not in r["error"]


def test_denials_are_counted(tmp_path, caller):
    store, svc = make(tmp_path, [FakeCollector("ec2", denials=2)])
    [run] = svc.take([DEV], set(), {A: REGIONS}).runs
    assert run.denials == 4 and runs(store, run.snapshot_id)["ec2"]["denials"] == 4


def test_cancel_saves_partial_with_exact_regions(tmp_path, caller):
    """Review Focus 2."""
    gate_open = threading.Event()
    slow = FakeCollector("lambda", block=gate_open)
    fast = FakeCollector("ec2")
    store, svc = make(tmp_path, [fast, slow])
    stop = CancelToken()
    result = []
    t = threading.Thread(target=lambda: result.append(svc.take([DEV], set(), {A: REGIONS}, cancel=stop)))
    t.start()
    for _ in range(500):
        if slow.seen and len(fast.seen) == 2:          # ec2 has looked everywhere; lambda is stuck in its first region
            break
        threading.Event().wait(0.01)
    stop.cancel()
    t.join(10)
    [out] = result
    [run] = out.runs
    assert out.cancelled and run.status == "partial" and run.snapshot_id is not None
    r = runs(store, run.snapshot_id)
    assert r["ec2"]["status"] == "ok"
    assert r["lambda"]["status"] == "failed" and r["lambda"]["regions_requested"] == '["eu-west-1", "us-east-1"]'
    assert r["lambda"]["regions_covered"] == "[]" and "stopped" in r["lambda"]["error"]
    assert not svc.running


def test_one_snapshot_at_a_time(tmp_path, caller):
    gate_open = threading.Event()
    slow = FakeCollector("ec2", block=gate_open)
    store, svc = make(tmp_path, [slow])
    t = threading.Thread(target=svc.take, args=([DEV], set(), {A: REGIONS}))
    t.start()
    for _ in range(500):
        if slow.seen:
            break
        threading.Event().wait(0.01)
    assert svc.running
    with pytest.raises(SnapshotBusy):
        svc.take([DEV], set(), {A: REGIONS})
    gate_open.set()
    t.join(10)
    assert not svc.running


def test_timeout_stops_only_that_collector(tmp_path, caller):
    store, svc = make(tmp_path, [FakeCollector("ec2"), FakeCollector("slow", block=threading.Event(), timeout_s=0.05)])
    [run] = svc.take([DEV], set(), {A: REGIONS}).runs
    r = runs(store, run.snapshot_id)
    assert r["ec2"]["status"] == "ok"
    assert r["slow"]["status"] == "failed" and "timed out after 0.05s" in r["slow"]["error"]


class Stubborn(FakeCollector):
    """Ignores its cancel token, like a worker stuck inside a botocore call."""

    def collect(self, session, region, cancel=None):
        self.seen.append(region)
        self.block.wait(10)
        return CollectorResult([], [], [region], 0)


def test_abandoned_worker_does_not_block_process_exit():
    """Re-review Minor 4: a worker stuck forever must not hold up interpreter exit (daemon pool)."""
    import subprocess
    import sys
    import textwrap
    code = textwrap.dedent("""
        import threading
        from aws95.aws.engines.labsnap.service import _DaemonPool
        pool = _DaemonPool(1, "t")
        fut = pool.submit(threading.Event().wait)          # blocks forever
        pool.shutdown([fut])
        print("exiting")
    """)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=10)
    assert out.returncode == 0 and "exiting" in out.stdout


def test_cancel_does_not_wait_for_a_stuck_worker(tmp_path, caller):
    """Review Minor 12: quitting mid-snapshot returns within the grace period, not botocore's timeout."""
    import time
    release = threading.Event()
    stuck = Stubborn("stuck", block=release)
    store, svc = make(tmp_path, [stuck], grace_s=0.1)
    stop = CancelToken()
    result = []
    t = threading.Thread(target=lambda: result.append(svc.take([DEV], set(), {A: REGIONS}, cancel=stop)))
    t.start()
    for _ in range(500):
        if stuck.seen:
            break
        threading.Event().wait(0.01)
    started = time.monotonic()
    stop.cancel()
    t.join(5)
    assert time.monotonic() - started < 2 and not t.is_alive()
    [run] = result[0].runs
    r = runs(store, run.snapshot_id)["stuck"]
    assert r["status"] == "failed" and "stopped before it finished" in r["error"] and r["regions_covered"] == "[]"
    release.set()                                    # let the abandoned thread end; its result is discarded
    threading.Event().wait(0.05)
    assert runs(store, run.snapshot_id)["stuck"]["status"] == "failed"


def test_identity_mismatch_writes_nothing(tmp_path, caller):
    """Review Focus 1."""
    caller["account"] = OTHER
    ec2 = FakeCollector("ec2")
    store, svc = make(tmp_path, [ec2])
    [run] = svc.take([DEV], set(), {A: REGIONS}).runs
    assert run.status == "failed" and run.snapshot_id is None and "different account" in run.message
    assert ec2.seen == [] and count(store, "snapshots") == 0


def test_identity_failure_is_classified(tmp_path, caller):
    caller["account"] = ClientError({"Error": {"Code": "AccessDenied", "Message": "arn:aws:iam::123456789012:x"}},
                                    "GetCallerIdentity")
    store, svc = make(tmp_path, [FakeCollector("ec2")])
    [run] = svc.take([DEV], set(), {A: REGIONS}).runs
    assert run.message == "Permission denied for sts:GetCallerIdentity." and count(store, "snapshots") == 0


def test_no_regions_and_cancel_before_start(tmp_path, caller):
    store, svc = make(tmp_path, [FakeCollector("ec2")])
    assert svc.take([DEV], set(), {}).runs[0].message == "No regions chosen for this account."
    stop = CancelToken()
    stop.cancel()
    out = svc.take([DEV, RosterEntry("prod", OTHER)], set(), {A: REGIONS, OTHER: REGIONS}, cancel=stop)
    assert out.cancelled and [r.status for r in out.runs] == ["failed", "failed"]
    assert count(store, "snapshots") == 0


# ----- sections and resume (resumable-snapshots spec) -----

def sections(store, sid):
    return {(r["collector"], r["region"]): r["state"] for r in store.sections(sid)}


def test_take_plans_and_ends_every_section(tmp_path, caller):
    store, svc = make(tmp_path, [FakeCollector("ec2"), FakeCollector("iam", "global")])
    [run] = svc.take([DEV], set(), {A: REGIONS}).runs
    assert sections(store, run.snapshot_id) == {("ec2", "us-east-1"): "done", ("ec2", "eu-west-1"): "done",
                                               ("iam", "global"): "done"}
    assert run.message == "complete (3/3 sections)"


def test_one_region_timing_out_keeps_the_other_region(tmp_path, caller):
    """Review Focus 2."""
    gate = threading.Event()

    class SlowInOneRegion(FakeCollector):
        def collect(self, session, region, cancel=None):
            if region == "eu-west-1":
                while not gate.wait(0.01):
                    raise_if_cancelled(cancel)
            return super().collect(session, region, cancel)
    slow = SlowInOneRegion("lambda", timeout_s=0.05)
    store, svc = make(tmp_path, [slow], grace_s=0.05)
    [run] = svc.take([DEV], set(), {A: REGIONS}).runs
    gate.set()
    s = sections(store, run.snapshot_id)
    assert s[("lambda", "us-east-1")] == "done" and s[("lambda", "eu-west-1")] == "timed_out"
    r = runs(store, run.snapshot_id)["lambda"]
    assert r["status"] == "ok" and r["regions_covered"] == '["us-east-1"]'
    assert count(store, "resource_state") == 1 and run.status == "partial"


def test_denied_collector_ends_denied_and_inner_denials_end_partial(tmp_path, caller):
    class Refused(FakeCollector):
        def collect(self, session, region, cancel=None):
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "ListKeys")
    store, svc = make(tmp_path, [Refused("kms"), FakeCollector("s3", "global", denials=3)])
    [run] = svc.take([DEV], set(), {A: REGIONS}).runs
    s = sections(store, run.snapshot_id)
    assert s[("kms", "us-east-1")] == s[("kms", "eu-west-1")] == "denied" and s[("s3", "global")] == "partial"
    assert "2 denied" in run.message and run.denials == 3


def test_cancel_leaves_waiting_sections_waiting(tmp_path, caller):
    gate = threading.Event()
    blockers = [FakeCollector(f"c{i}", block=gate) for i in range(6)]
    store, svc = make(tmp_path, blockers, max_parallel=2, grace_s=0.05)
    stop = CancelToken()
    result = []
    t = threading.Thread(target=lambda: result.append(svc.take([DEV], set(), {A: ("us-east-1",)}, cancel=stop)))
    t.start()
    for _ in range(500):
        if sum(bool(b.seen) for b in blockers) >= 2:
            break
        threading.Event().wait(0.01)
    stop.cancel()
    t.join(10)
    gate.set()
    sid = result[0].runs[0].snapshot_id
    states = sorted(sections(store, sid).values())
    assert states.count("stopped") == 2 and states.count("waiting") == 4


def test_resume_runs_only_unfinished_sections(tmp_path, caller):
    flaky = FakeCollector("ec2", fail_regions={"eu-west-1"})
    iam = FakeCollector("iam", "global")
    store, svc = make(tmp_path, [flaky, iam])
    [run] = svc.take([DEV], set(), {A: REGIONS}).runs
    assert sections(store, run.snapshot_id)[("ec2", "eu-west-1")] == "failed"
    flaky.fail_regions.clear()
    flaky.seen.clear()
    iam.seen.clear()
    [again] = svc.resume([DEV], set()).runs
    assert again.snapshot_id == run.snapshot_id and again.status == "complete"
    assert flaky.seen == ["eu-west-1"] and iam.seen == []
    assert store.latest_snapshot(A)["status"] == "complete"
    assert count(store, "findings") == 3                     # recomputed over the whole snapshot


def test_resume_skips_denied_unless_asked(tmp_path, caller):
    calls = []

    class Refused(FakeCollector):
        def collect(self, session, region, cancel=None):
            calls.append(region)
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "ListKeys")
    store, svc = make(tmp_path, [Refused("kms")])
    svc.take([DEV], set(), {A: ("us-east-1",)})
    calls.clear()
    [r] = svc.resume([DEV], set()).runs
    assert calls == [] and r.message == "Nothing left to resume."
    svc.resume([DEV], set(), retry_denied=True)
    assert calls == ["us-east-1"]


def test_resume_with_nothing_left_writes_nothing(tmp_path, caller):
    store, svc = make(tmp_path, [FakeCollector("ec2")])
    svc.take([DEV], set(), {A: REGIONS})
    before = count(store, "resource_state")
    [r] = svc.resume([DEV], set()).runs
    assert r.message == "Nothing left to resume." and count(store, "resource_state") == before


def test_resume_refuses_old_snapshots_and_identity_mismatch(tmp_path, caller):
    """Review Focus 3."""
    from datetime import timedelta
    ec2 = FakeCollector("ec2", fail_regions={"eu-west-1"})
    store, svc = make(tmp_path, [ec2])
    svc.take([DEV], set(), {A: REGIONS})
    later = SnapshotService(store, session_factory=lambda p: object(), collectors=lambda: [ec2],
                            rules=lambda: [AllRule()], utcnow=lambda: NOW + timedelta(hours=25), poll_s=0.01)
    [old] = later.resume([DEV], set()).runs
    assert old.message == "This snapshot is too old to resume; take a new one."
    caller["account"] = OTHER
    ec2.seen.clear()
    [mismatch] = svc.resume([DEV], set()).runs
    assert "different account" in mismatch.message and ec2.seen == []


def test_resume_honours_the_billing_opt_in(tmp_path, caller):
    """Review Focus 4."""
    cost = FakeCollector("cost", "global", billed=True, fail_regions={"global"})
    store, svc = make(tmp_path, [cost])
    [run] = svc.take([DEV], {A}, {A: REGIONS}).runs
    assert sections(store, run.snapshot_id)[("cost", "global")] == "failed"
    cost.fail_regions.clear()
    cost.seen.clear()
    [r] = svc.resume([DEV], set()).runs                     # Billing turned off since: never resumed
    assert cost.seen == [] and r.message == "Nothing left to resume."
    svc.resume([DEV], {A})
    assert cost.seen == ["global"]


def test_progress_counts_sections_and_names_the_running_one(tmp_path, caller):
    seen = []
    store, svc = make(tmp_path, [FakeCollector("ec2"), FakeCollector("iam", "global")])
    svc.take([DEV], set(), {A: REGIONS}, progress=seen.append, labels={A: "dev"})
    assert seen[-1].text == "dev: 3/3 sections"
    assert any(p.collector == "ec2" and p.region == "eu-west-1" for p in seen)
