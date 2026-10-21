# Upstream (in part): ctwatch/tests/test_fetcher.py @ 254ff31 — adapted for aws95; owned here from now on.
from datetime import UTC, datetime, timedelta

import pytest

from aws95.aws.engines.ctwatch.estimate import calls_needed, minutes
from aws95.aws.engines.ctwatch.fetcher import (SETTLE, RateLimiter, RepeatedToken, day_slices, gaps, lookup,
                                               newest_first, sample, windows)
from aws95.aws.engines.ctwatch.scope import SCOPE
from tests.ctwatch_fakes import A, FakeTrail, RegionSession, ev

T0 = datetime(2026, 8, 1, 0, tzinfo=UTC)
H = timedelta(hours=1)
BULK = next(r for r in SCOPE if r.key == "bulk")
ASSUME = next(r for r in SCOPE if r.key == "name:AssumeRole")


class NoWait:
    def wait(self):
        pass


def session(trail, region="us-east-1"):
    return RegionSession({("cloudtrail", region): trail})


def test_windows_split():
    ws = windows(T0, T0 + 12 * H, hours=4)
    assert len(ws) == 3 and ws[0][0].hour == 0 and ws[-1][1].hour == 12


def test_gaps_skip_covered_and_tiny_pieces():
    assert gaps([(T0, T0 + 4 * H)], T0 - 4 * H, T0 + 8 * H) == [(T0 - 4 * H, T0), (T0 + 4 * H, T0 + 8 * H)]
    assert gaps([(T0, T0 + 4 * H), (T0 + 4 * H, T0 + 8 * H)], T0, T0 + 8 * H) == []
    assert gaps([(T0, T0 + 4 * H - timedelta(seconds=30))], T0, T0 + 4 * H) == []      # under a minute
    assert gaps([], T0, T0 + H) == [(T0, T0 + H)]


def test_newest_first_windows():
    assert newest_first([(T0, T0 + 10 * H)], 4) == [(T0 + 6 * H, T0 + 10 * H), (T0 + 2 * H, T0 + 6 * H),
                                                     (T0, T0 + 2 * H)]
    assert newest_first([(T0, T0 + H), (T0 + 5 * H, T0 + 6 * H)], 4) == [(T0 + 5 * H, T0 + 6 * H), (T0, T0 + H)]


def test_day_slices_newest_first():
    s = day_slices(T0 - timedelta(days=7), T0)
    assert len(s) == 7 and s[0] == (T0 - timedelta(days=1), T0) and s[-1][0] == T0 - timedelta(days=7)
    assert day_slices(T0 - 3 * H, T0) == [(T0 - 3 * H, T0)]


def test_rate_limiter_spaces_calls():
    now, slept = [0.0], []

    def sleep(s):
        slept.append(s)
        now[0] += s
    limiter = RateLimiter(2.0, clock=lambda: now[0], sleep=sleep)
    for _ in range(3):
        limiter.wait()
    assert slept == [0.5, 0.5]


def test_lookup_paginates_and_normalises():
    trail = FakeTrail([ev(f"e{i}", T0 + timedelta(minutes=i)) for i in range(60)])
    rows = lookup(session(trail), "us-east-1", BULK, T0, T0 + 4 * H, account=A, limiter=NoWait())
    assert len(rows) == 60 and len(trail.calls) == 2 and rows[0].event_id == "e59"
    assert trail.calls[0]["LookupAttributes"] == [{"AttributeKey": "ReadOnly", "AttributeValue": "false"}]
    assert trail.calls[0]["MaxResults"] == 50 and trail.calls[1]["NextToken"] == "50"
    assert {r.account for r in rows} == {A} and {r.origin for r in rows} == {"lookup"}


def test_targeted_pass_keeps_only_human_principals():
    trail = FakeTrail([ev("r1", T0 + H, "AssumeRole", source="sts.amazonaws.com", read_only=True),
                       ev("r2", T0 + H, "AssumeRole", ptype="Root", source="sts.amazonaws.com", read_only=True),
                       ev("r3", T0 + H, "AssumeRole", ptype="AssumedRole", source="sts.amazonaws.com", read_only=True),
                       ev("r4", T0 + H, "AssumeRole", ptype="AWSService", source="sts.amazonaws.com", read_only=True)])
    rows = lookup(session(trail), "us-east-1", ASSUME, T0, T0 + 24 * H, account=A, limiter=NoWait())
    assert sorted(r.event_id for r in rows) == ["r1", "r2"]


def test_bulk_sweep_is_not_filtered_by_principal():
    trail = FakeTrail([ev("s1", T0 + H, "RunInstances", ptype="AssumedRole", source="ec2.amazonaws.com")])
    assert len(lookup(session(trail), "us-east-1", BULK, T0, T0 + 4 * H, account=A, limiter=NoWait())) == 1


def test_repeated_next_token_stops():
    class Looping(FakeTrail):
        def lookup_events(self, **kw):
            self.calls.append(kw)
            return {"Events": [], "NextToken": "same"}
    with pytest.raises(RepeatedToken):
        lookup(session(Looping()), "us-east-1", BULK, T0, T0 + H, account=A, limiter=NoWait())


def test_sample_is_one_call_over_the_last_hour():
    trail = FakeTrail([ev(f"e{i}", T0 - timedelta(minutes=i + 1)) for i in range(60)])
    assert sample(session(trail), "us-east-1", T0) == (50, True)
    (call,) = trail.calls
    assert call["StartTime"] == T0 - H and call["EndTime"] == T0 and call["MaxResults"] == 50
    trail2 = FakeTrail([ev("e1", T0 - timedelta(minutes=5))])
    assert sample(session(trail2), "us-east-1", T0) == (1, False)


def test_settle_margin_is_fifteen_minutes():
    assert SETTLE == timedelta(minutes=15)


def test_estimate_maths():
    assert calls_needed(10, 7, 13) == 7 * 6 * 1 + 13 * 7           # one call per 4 h window, one per day per pass
    assert calls_needed(0, 7, 13) == calls_needed(10, 7, 13)        # an idle window still costs a call
    assert calls_needed(50, 90, 13) == 90 * 6 * 4 + 13 * 90         # 200 events per window = 4 calls
    assert minutes(133) == 2 and minutes(3330) == 28 and minutes(1) == 1
