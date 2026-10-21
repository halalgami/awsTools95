from datetime import datetime, timedelta, timezone

from aws95.contracts.common import Scope, Status
from aws95.contracts.snapshots import (RESUME_WINDOW, AccountStage, SnapshotProgress, SnapshotSection)
from aws95.providers.demo import demo_provider_set

T0 = datetime(2026, 10, 16, 9, tzinfo=timezone.utc)


def test_progress_text_names_sections_and_the_running_one():
    assert SnapshotProgress("123456789012", "prod", 12, 42, "eu-west-1", "lambda").text == \
        "prod: 12/42 sections · lambda eu-west-1"
    assert SnapshotProgress("123456789012", "prod", 0, 42).text == "prod: 0/42 sections"


def test_section_resumable_rules():
    s = lambda state: SnapshotSection("123456789012", 1, "ec2", "eu-west-1", state)  # noqa: E731
    assert [x for x in ("waiting", "stopped", "timed_out", "failed") if s(x).resumable()] == \
        ["waiting", "stopped", "timed_out", "failed"]
    assert not s("done").resumable() and not s("partial").resumable() and not s("denied").resumable()
    assert s("denied").resumable(retry_denied=True)


def test_stage_text_and_resume_window():
    st = AccountStage("123456789012", 7, T0, 12, 42, 30, 0, ("lambda eu-west-1",), False, T0 + RESUME_WINDOW)
    assert st.text("prod") == "prod — 12/42 sections · running: lambda eu-west-1"
    done = AccountStage("123456789012", 7, T0, 42, 42, 0, 0)
    assert done.text("prod") == "prod — complete (42/42 sections)"
    gaps = AccountStage("123456789012", 7, T0, 2, 42, 0, 40, (), False, None)
    assert gaps.text("prod") == "prod — 2/42 sections · 40 denied"
    assert st.can_resume(T0 + timedelta(hours=23)) and not st.can_resume(T0 + timedelta(hours=25))
    assert not done.can_resume(T0)


def test_demo_sections_and_stages_derive_from_fixtures():
    ps = demo_provider_set()
    secs = ps.snapshots.sections(Scope()).data
    assert secs and all(s.state in ("done", "failed") for s in secs)
    stages = ps.snapshots.stages(Scope()).data
    assert stages and all(st.total >= st.done for st in stages)
    r = ps.snapshots.resume_snapshot(Scope())
    assert r.status is Status.NOT_CONNECTED and r.is_demo
