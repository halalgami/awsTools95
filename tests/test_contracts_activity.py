from datetime import date, datetime, timezone

import pytest

from aws95.contracts.accounts import RosterEntry
from aws95.contracts.activity import (ALL_REGIONS, DEFAULT_HISTORY_DAYS, DENIED_NOTE, HISTORY_DAYS, LOAD_WORDING,
                                      AccountHistory, ActivityEvent, CoverageSpan, HistoryEstimate, HistoryProgress,
                                      HistoryRequest, HistoryStatus, ImportPlan, RegionHistory, TrailInfo,
                                      activity_regions)

T = datetime(2026, 10, 1, tzinfo=timezone.utc)
A = "123456789012"
TRAIL = TrailInfo("main", f"arn:aws:cloudtrail:us-east-1:{A}:trail/main", "us-east-1", "example-cloudtrail-bucket",
                  "logs", f"arn:aws:kms:us-east-1:{A}:key/abcd", False, A, True, False)


def test_wording_and_choices():
    assert HISTORY_DAYS == (7, 30, 90) and DEFAULT_HISTORY_DAYS == 7
    assert LOAD_WORDING == ("Large ranges take a while. Loading runs in the background — keep working; "
                            "the newest events appear first.")
    assert DENIED_NOTE == "Denied change attempts: covered. Denied read attempts: from imported history only."


def test_activity_regions_always_add_us_east_1():
    assert activity_regions(("eu-west-1",), "me-south-1") == ("eu-west-1", "us-east-1")
    assert activity_regions((), "me-south-1") == ("me-south-1", "us-east-1")
    assert activity_regions((), None) == ("us-east-1",)
    assert activity_regions(("us-east-1", "eu-west-1"), None) == ("us-east-1", "eu-west-1")


def test_progress_text():
    assert HistoryProgress("load", "dev", 23, 90, "me-south-1").text == "Activity dev: 23/90 days · me-south-1"
    assert HistoryProgress("import", "dev", 3, 30).text == "Import dev: 3/30 days"


def test_estimate_text():
    assert HistoryEstimate(A, "us-east-1", {7: 2, 30: 6, 90: 18}).text(7) == "≈ 2 min"
    assert HistoryEstimate("", ALL_REGIONS, {7: 2}, at_least=True).text(7) == "≈ 2 min or more"
    assert HistoryEstimate(A, "us-east-1", {}, error="Permission denied for cloudtrail:LookupEvents.").text(7) == "unknown"


def test_history_status_flags():
    empty = HistoryStatus()
    assert not empty.has_history and not empty.unfinished and empty.running is None
    acct = AccountHistory(A, 7, (RegionHistory("us-east-1", (CoverageSpan(T, T),)),), unfinished=True)
    assert acct.has_history and HistoryStatus((acct,)).has_history and HistoryStatus((acct,)).unfinished
    assert not AccountHistory(A, 7, (RegionHistory("us-east-1"),)).has_history


def test_import_plan_days_newest_first_and_order_checked():
    p = ImportPlan(RosterEntry("dev", A), "dev", TRAIL, date(2026, 9, 28), date(2026, 9, 30), ("us-east-1",))
    assert p.days == (date(2026, 9, 30), date(2026, 9, 29), date(2026, 9, 28))
    with pytest.raises(ValueError):
        ImportPlan(RosterEntry("dev", A), "dev", TRAIL, date(2026, 9, 30), date(2026, 9, 28), ())


def test_trail_describe():
    assert TRAIL.describe() == "main · s3://example-cloudtrail-bucket/logs · encrypted with KMS"
    org = TrailInfo("org", "arn", "us-east-1", "b-logs", "", None, True, "111111111111", True, True)
    assert org.describe() == "org · s3://b-logs · not KMS-encrypted · organisation trail · Insights on"


def test_request_defaults_and_event_error_code_default():
    r = HistoryRequest((RosterEntry("dev", A),))
    assert r.mode == "load" and r.days is None and r.retention_days == 90 and dict(r.regions_for) == {}
    e = ActivityEvent("e1", A, T, "IAMUser", "alice", True, "iam.amazonaws.com", "CreateUser", False, None, None, (),
                      "us-east-1", 3, "iam-escalation")
    assert e.error_code is None
