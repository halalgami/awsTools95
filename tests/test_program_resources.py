import dataclasses
import re

import pytest

from aws95.contracts.common import Coverage, Page, Result, Status
from aws95.contracts.resources import ResourceRow, ResourceType
from aws95.kit.widgets.stateview import ViewState
from aws95.programs.resources import ResourcesProgram, fetch_all_resources, make_resources
from aws95.programs.resources.catalog import rtypes_for_service
from aws95.providers.demo import demo_provider_set


def _open(program_env, qtbot, providers):
    w, ctx = program_env(make_resources, providers)
    qtbot.addWidget(w)
    w.show()
    return w, ctx


@pytest.fixture
def demo(program_env, qtbot):
    w, ctx = _open(program_env, qtbot, demo_provider_set())
    qtbot.waitUntil(lambda: w.grid_view.state is ViewState.CONTENT)
    return w, ctx


class FakeResources:
    def __init__(self, rows, status=Status.OK, coverage=None):
        self.rows, self.status, self.coverage = rows, status, coverage

    def resource_types(self, scope, *, cancel=None):
        return Result(self.status, (ResourceType("ec2.instance", "Compute", "EC2 Instances", len(self.rows)),),
                      self.coverage)

    def list_resources(self, scope, rtype="", cursor=None, *, page_size=500, cancel=None):
        start = int(cursor or 0)
        nxt = start + page_size
        return Result(self.status, Page(tuple(self.rows[start:nxt]), str(nxt) if nxt < len(self.rows) else None,
                                        len(self.rows)), self.coverage)


def _row(i, name=None, partial=False):
    return ResourceRow("111111111111", "ec2.instance", "us-east-1", f"i-{i:05d}", name or f"vm-{i}", "running",
                       {"type": "t3.micro"}, {"env": "lab"}, partial)


def test_empty_mode_not_connected(program_env, qtbot):
    w, _ = _open(program_env, qtbot, None)
    qtbot.waitUntil(lambda: w.tree_view.state is ViewState.NOT_CONNECTED)


def test_tree_groups_types_by_domain_with_counts(demo):
    w, _ = demo
    domains = [w.tree.topLevelItem(i).text(0) for i in range(w.tree.topLevelItemCount())]
    assert domains == ["Compute", "Storage", "Database", "Identity"]
    compute = w.tree.topLevelItem(0)
    assert compute.child(0).text(0) == "EC2 Instances (4)"
    assert w.current_rtype() == "ec2.instance"
    assert len(w.grid.visible_rows()) == 4
    assert w.is_demo


def test_partial_row_badge_and_inspector(demo):
    w, _ = demo
    rows = {r["name"]: r for r in w.grid.visible_rows()}
    assert rows["etl-node"]["badge"] == "⚠" and rows["web-01"]["badge"] == ""
    idx = [r["name"] for r in w.grid.visible_rows()].index("web-01")
    w.grid.view.selectRow(idx)
    assert w.inspector.title_label.text() == "web-01"
    assert w.inspector.attrs_table.rowCount() >= 1


def test_partial_rows_show_banner_and_badge(program_env, qtbot):
    ps = dataclasses.replace(demo_provider_set(), resources=FakeResources(
        [_row(1, partial=True)], Status.PARTIAL, Coverage(("us-east-1", "eu-west-1"), ("us-east-1",), 3)))
    w, _ = _open(program_env, qtbot, ps)
    qtbot.waitUntil(lambda: w.grid_view.state is ViewState.CONTENT)
    assert "1 of 2 regions covered" in w.grid_view.banner_text and "3 attributes denied" in w.grid_view.banner_text
    assert w.grid.visible_rows()[0]["badge"] == "⚠"
    assert "partial" in w.status.coverage_label.text()


def test_markup_names_render_plain(program_env, qtbot):
    ps = dataclasses.replace(demo_provider_set(), resources=FakeResources([_row(1, name="<b>x</b>")]))
    w, _ = _open(program_env, qtbot, ps)
    qtbot.waitUntil(lambda: w.grid_view.state is ViewState.CONTENT)
    w.grid.view.selectRow(0)
    assert w.inspector.title_label.text() == "<b>x</b>"
    index = w.grid.model.index(0, 1)
    assert w.grid.model.data(index) == "<b>x</b>"


def test_fetch_all_follows_pages(program_env, qtbot):
    fake = FakeResources([_row(i) for i in range(1200)])
    result = fetch_all_resources(fake, None, "ec2.instance")
    assert result.status is Status.OK and len(result.data) == 1200
    ps = dataclasses.replace(demo_provider_set(), resources=fake)
    w, _ = _open(program_env, qtbot, ps)
    qtbot.waitUntil(lambda: len(w.grid.visible_rows()) == 1200)
    assert "1,200" in w.status.counts_label.text()


def test_navigation_by_service(demo, qtbot):
    w, _ = demo
    w.apply_navigation({"service": "Amazon Simple Storage Service"})
    qtbot.waitUntil(lambda: w.current_rtype() == "s3.bucket" and len(w.grid.visible_rows()) == 3)
    w.apply_navigation({"service": "AmazonCloudWatch"})
    assert "No resource type is collected for AmazonCloudWatch" in w.status.counts_label.text()
    assert rtypes_for_service("AWS Lambda") == ("lambda.function",)


def test_navigation_before_tree_loads_is_kept(program_env, qtbot):
    w, _ = program_env(make_resources, demo_provider_set())
    qtbot.addWidget(w)
    w.apply_navigation({"rtype": "rds.instance", "filter": "name~orders"})
    w.show()
    qtbot.waitUntil(lambda: w.current_rtype() == "rds.instance" and w.grid_view.state is ViewState.CONTENT)
    assert w.grid.filter_edit.text() == "name~orders"
    assert [r["name"] for r in w.grid.visible_rows()] == ["demo-orders-db"]


def test_export_uses_grid(demo, tmp_path, monkeypatch):
    w, _ = demo
    target = tmp_path / "r.csv"
    monkeypatch.setattr(ResourcesProgram, "ask_save_path", staticmethod(lambda *a: str(target)))
    assert w.export()
    assert target.read_text().splitlines()[0].endswith("Name,ID,State,Region,Account")


def test_no_duplicate_mnemonics(demo):
    w, _ = demo

    def letter(text):
        m = re.search(r"(?<!&)&([A-Za-z])", text.replace("&&", ""))
        return m.group(1).lower() if m else None

    bar = [letter(a.text()) for a in w.menu_bar.actions()]
    assert len(bar) == len(set(bar)), bar
    own = [letter(a.text()) for a in w.program_menu.actions()]
    assert not (set(own) - {None}) & set(bar), own


def test_empty_or_error_type_clears_stale_rows(program_env, qtbot, tmp_path, monkeypatch):
    class Switch(FakeResources):
        def resource_types(self, scope, *, cancel=None):
            return Result.ok((ResourceType("a.x", "Compute", "A", 2), ResourceType("b.x", "Compute", "B", 0),
                              ResourceType("c.x", "Compute", "C", 0)))

        def list_resources(self, scope, rtype="", cursor=None, *, page_size=500, cancel=None):
            if rtype == "c.x":
                return Result.error("boom")
            if rtype == "b.x":
                return Result.ok(Page((), None, 0))
            return super().list_resources(scope, rtype, cursor, page_size=page_size, cancel=cancel)

    ps = dataclasses.replace(demo_provider_set(), resources=Switch([_row(1), _row(2)]))
    w, _ = _open(program_env, qtbot, ps)
    qtbot.waitUntil(lambda: len(w.grid.visible_rows()) == 2)
    monkeypatch.setattr(ResourcesProgram, "ask_save_path", staticmethod(lambda *a: str(tmp_path / "x.csv")))
    for rtype, state in (("b.x", ViewState.EMPTY), ("c.x", ViewState.ERROR)):
        w.select_rtype("a.x")
        qtbot.waitUntil(lambda: len(w.grid.visible_rows()) == 2)
        w.select_rtype(rtype)
        qtbot.waitUntil(lambda: w.grid_view.state is state)
        assert w.grid.visible_rows() == []
        assert not w.export()


def test_scope_refresh_with_error_types_clears_rows(program_env, qtbot):
    class Flaky(FakeResources):
        broken = False

        def resource_types(self, scope, *, cancel=None):
            return Result.error("down") if self.broken else super().resource_types(scope, cancel=cancel)

    fake = Flaky([_row(1), _row(2)])
    w, _ = _open(program_env, qtbot, dataclasses.replace(demo_provider_set(), resources=fake))
    qtbot.waitUntil(lambda: len(w.grid.visible_rows()) == 2)
    fake.broken = True
    w.refresh()
    qtbot.waitUntil(lambda: w.tree_view.state is ViewState.ERROR)
    qtbot.wait(100)
    assert w.grid.visible_rows() == []


def test_fetch_all_merges_status_and_coverage():
    class Paged:
        def list_resources(self, scope, rtype="", cursor=None, *, page_size=500, cancel=None):
            if cursor is None:
                return Result(Status.OK, Page((_row(1),), "1", 2), Coverage(("r1", "r2"), ("r1", "r2"), 0), is_demo=True)
            return Result(Status.PARTIAL, Page((_row(2),), None, 2), Coverage(("r2", "r3"), ("r2",), 3))

    r = fetch_all_resources(Paged(), None, "ec2.instance")
    assert r.status is Status.PARTIAL and len(r.data) == 2
    assert r.coverage == Coverage(("r1", "r2", "r3"), ("r1", "r2"), 3)


def test_fetch_all_repeated_cursor_is_error():
    class Loop:
        def list_resources(self, scope, rtype="", cursor=None, *, page_size=500, cancel=None):
            return Result(Status.OK, Page((_row(1),), "same", 9), is_demo=True)

    r = fetch_all_resources(Loop(), None, "ec2.instance")
    assert r.status is Status.ERROR and r.is_demo and "repeated" in r.message


def test_navigation_to_absent_rtype_drops_pending(demo, qtbot):
    w, _ = demo
    w.apply_navigation({"rtype": "nope.x", "filter": "name~zzz"})
    assert "No resource type nope.x in this scope" in w.status.counts_label.text()
    w.select_rtype("s3.bucket")
    qtbot.waitUntil(lambda: w.current_rtype() == "s3.bucket" and len(w.grid.visible_rows()) == 3)
    assert w.grid.filter_edit.text() == ""


def test_unmapped_service_invalidates_inflight_rows(demo, qtbot):
    w, _ = demo
    w.apply_navigation({"rtype": "s3.bucket"})
    w.apply_navigation({"service": "AmazonCloudWatch"})
    qtbot.wait(300)
    assert "No resource type is collected for AmazonCloudWatch" in w.status.counts_label.text()
    assert w.current_rtype() is None
    assert w.grid.visible_rows() == []
    assert w.grid_view.state is not ViewState.CONTENT
