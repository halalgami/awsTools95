from PySide6.QtCore import QSettings

from aws95.contracts.common import Result, Scope, Status
from aws95.core.settings import Settings
from aws95.providers.composed import COST_NOTE, MISSING, ComposedReports, table
from aws95.providers.demo import demo_provider_set
from aws95.providers.live import live_provider_set
from aws95.store import Store
from tests.store_seed import DEV, PINNED, PROD, ROSTER, seed_store


def live(tmp_path, store):
    settings = Settings(QSettings(str(tmp_path / "r.ini"), QSettings.Format.IniFormat))
    settings.set_roster(ROSTER)
    return live_provider_set(settings, store=store, clock=lambda: PINNED)


def test_table_escapes_markdown_and_keeps_money_exact():
    from decimal import Decimal
    md, csv_text = table("T", ("a", "b"), [("x|y\nz", Decimal("0.0000001")), (None, Decimal("1E+2"))])
    assert "x\\|y z" in md and "0.0000001" in md and "100" in md and "E+" not in md
    assert csv_text.splitlines()[0] == "a,b" and "0.0000001" in csv_text


def test_demo_and_live_share_one_implementation():
    assert isinstance(demo_provider_set().reports, ComposedReports)


def test_live_inventory_names_unfinished_accounts(tmp_path):
    """Review Focus 3."""
    store = seed_store(tmp_path / "aws95.db")
    try:
        r = live(tmp_path, store).reports.render(Scope(), "inventory")
        assert r.status is Status.PARTIAL and r.message == MISSING and not r.is_demo
        md = r.data.markdown
        assert md.startswith("# Resource inventory\n\n## Coverage\n")
        assert f"- {DEV} — complete" in md and f"- {PROD} — " in md and "to resume" in md
        for rid in ("i-0aaa", "i-0bbb", "sg-0123", "example-logs", "alice", "i-0ddd", "prod-data"):
            assert rid in r.data.csv
        assert "i-0ccc" not in r.data.csv                       # only the latest snapshot
    finally:
        store.close()


def test_live_cost_without_billing_for_an_account(tmp_path):
    """Review Focus 3: prod has no Billing; dev's figures are copied exactly."""
    store = seed_store(tmp_path / "aws95.db")
    try:
        r = live(tmp_path, store).reports.render(Scope(), "cost")
        assert r.status is Status.PARTIAL
        assert COST_NOTE in r.data.markdown and "Month to date (provider total)" in r.data.csv
        assert "1234.56" in r.data.csv                           # last month, exactly as stored
    finally:
        store.close()


def test_live_findings_excludes_muted(tmp_path):
    store = seed_store(tmp_path / "aws95.db")
    try:
        r = live(tmp_path, store).reports.render(Scope(), "findings")
        assert "s3.no_lifecycle" in r.data.csv and "sg-0123" not in r.data.csv
    finally:
        store.close()


def test_empty_store_is_not_connected_never_an_empty_table(tmp_path):
    store = Store(tmp_path / "empty.db")
    try:
        ps = live(tmp_path, store)
        for rid in ("inventory", "cost", "findings", "activity"):
            r = ps.reports.render(Scope(), rid)
            assert r.status is Status.NOT_CONNECTED and r.data is None and r.message, rid
    finally:
        store.close()


def test_a_source_error_is_an_error(tmp_path):
    ps = demo_provider_set()

    class Broken:
        def __getattr__(self, name):
            return lambda *a, **k: Result.error("disk said no")
    reports = ComposedReports(ps.resources, Broken(), ps.findings, ps.activity, ps.snapshots)
    r = reports.render(Scope(), "cost")
    assert r.status is Status.ERROR and "disk said no" in r.message


def test_unknown_report_is_an_error():
    assert demo_provider_set().reports.render(Scope(), "bogus").status is Status.ERROR


def test_demo_reports_are_marked_demo():
    r = demo_provider_set().reports.render(Scope(), "inventory")
    assert r.is_demo and r.data.markdown.startswith("# Resource inventory\n\n## Coverage\n")
