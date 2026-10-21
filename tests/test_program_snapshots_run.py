import dataclasses
from datetime import datetime, timedelta, timezone

from aws95.contracts.common import Result
from aws95.contracts.billing import BILLING_COST_NOTE
from aws95.contracts.snapshots import AccountStage, SnapshotSection
from aws95.programs.snapshots import make_snapshots
from aws95.providers.demo import demo_provider_set

A, B = "123456789012", "210987654321"
NOW = datetime.now(timezone.utc)


class Fake:
    def __init__(self, stages, sections):
        self._stages, self._sections, self.calls = stages, sections, 0

    def stages(self, scope, *, cancel=None):
        self.calls += 1
        return Result.ok(self._stages)

    def sections(self, scope, snapshot_id=None, *, cancel=None):
        return Result.ok(self._sections)

    def __getattr__(self, name):
        return getattr(demo_provider_set().snapshots, name)


def make(program_env, qtbot, stages, sections):
    fake = Fake(stages, sections)
    ps = dataclasses.replace(demo_provider_set(), snapshots=fake, is_demo=False, is_live=True)
    w, ctx = program_env(make_snapshots, ps)
    qtbot.addWidget(w)
    w.show()
    w.tabs.setCurrentWidget(w.run_tab)
    return w, ctx, fake


STAGES = (AccountStage(A, 1, NOW, 41, 42, 1, 0, (), False, NOW + timedelta(hours=1)),
          AccountStage(B, 2, NOW, 2, 42, 0, 40))
SECTIONS = (SnapshotSection(A, 1, "lambda", "eu-west-1", "timed_out", error="timed out after 300s"),
            SnapshotSection(A, 1, "ec2", "eu-west-1", "done", rows=10),
            SnapshotSection(B, 2, "kms", "eu-west-1", "denied", error="Permission denied for this request."))


def test_run_tab_shows_each_accounts_stage_and_every_section(program_env, qtbot):
    w, _, _ = make(program_env, qtbot, STAGES, SECTIONS)
    tab = w.run_tab
    qtbot.waitUntil(lambda: len(tab.grid.visible_rows()) == 3, timeout=3000)
    texts = [l.text() for l in tab.stage_labels.values()]
    assert any("41/42 sections" in t and "1 to resume" in t for t in texts)
    assert any("40 denied" in t for t in texts)
    assert {r["state"] for r in tab.grid.visible_rows()} == {"timed_out", "done", "denied"}


def test_resume_enabled_only_when_something_can_resume(program_env, qtbot):
    w, ctx, _ = make(program_env, qtbot, STAGES, SECTIONS)
    tab = w.run_tab
    qtbot.waitUntil(lambda: tab.resume_button.isEnabled(), timeout=3000)
    tab.retry_denied_box.setChecked(True)
    tab.resume_button.click()
    assert ("resume_snapshot", ((), True)) in ctx.shell.calls
    w2, _, _ = make(program_env, qtbot, (AccountStage(A, 1, NOW, 42, 42, 0, 0),), ())
    qtbot.wait(200)
    assert not w2.run_tab.resume_button.isEnabled()


def test_resume_states_the_billing_cost_when_a_billing_section_is_unfinished(program_env, qtbot):
    stages = (dataclasses.replace(STAGES[0], billing_unfinished=True),)
    w, _, _ = make(program_env, qtbot, stages, SECTIONS[:1])
    qtbot.waitUntil(lambda: w.run_tab.cost_note.isVisible(), timeout=3000)
    assert w.run_tab.cost_note.text() == BILLING_COST_NOTE


def test_run_tab_rereads_at_most_twice_a_second(program_env, qtbot):
    """Review Focus 5."""
    w, ctx, fake = make(program_env, qtbot, STAGES, SECTIONS)
    qtbot.waitUntil(lambda: fake.calls >= 1, timeout=3000)
    before = fake.calls
    for _ in range(20):
        ctx.shell.snapshot_progress.emit("dev: 1/42 sections · ec2 us-east-1")
    qtbot.wait(600)                                  # 20 progress signals in one burst: one re-read, not 20
    assert 1 <= fake.calls - before <= 2


def test_history_shows_section_counts(program_env, qtbot):
    from aws95.contracts.snapshots import Snapshot
    w, _, _ = make(program_env, qtbot, STAGES, SECTIONS)
    w._render_history((Snapshot(1, A, "arn", NOW, "3.0.0", "partial", (), ("us-east-1",), 41, 42),))
    assert w.history.visible_rows()[0]["sections"] == "41/42"


def test_history_account_and_regions_never_elide(program_env, qtbot):
    from aws95.contracts.snapshots import Snapshot
    w, _, _ = make(program_env, qtbot, STAGES, SECTIONS)
    regions = ("us-east-1", "eu-west-1", "me-south-1", "ap-southeast-2")
    w._render_history((Snapshot(1, A, "arn", NOW, "3.0.0", "partial", (), regions, 41, 42),))
    view, fm = w.history.view, w.history.view.fontMetrics()
    cols = [c.key for c in w.history.model.columns]
    assert view.columnWidth(cols.index("account")) >= fm.horizontalAdvance(A)
    assert view.columnWidth(cols.index("regions")) >= fm.horizontalAdvance(", ".join(regions))
