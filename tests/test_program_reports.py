import re

import pytest
from PySide6.QtWidgets import QAbstractButton

from aws95.contracts.common import Result
from aws95.contracts.reports import RenderedReport
from aws95.kit.widgets.stateview import ViewState
from aws95.programs.reports import ReportsProgram, make_reports
from aws95.providers.demo import demo_provider_set


@pytest.fixture
def demo(program_env, qtbot):
    w, ctx = program_env(make_reports, demo_provider_set())
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: w.types.count() == 4)
    return w, ctx


def _rendered(w, qtbot, report_id):
    assert w.select_report(report_id)
    qtbot.waitUntil(lambda: w.save_md_button.isEnabled())


def test_empty_mode(program_env, qtbot):
    w, _ = program_env(make_reports)
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: w.types_view.state is ViewState.NOT_CONNECTED)
    assert not w.save_md_button.isEnabled() and not w.save_csv_button.isEnabled()


def test_types_and_preview(demo, qtbot):
    w, _ = demo
    assert [w.types.item(i).text() for i in range(w.types.count())] == [
        "Resource inventory", "Cost summary", "Open findings", "Activity watchlist"]
    assert not w.save_md_button.isEnabled()
    _rendered(w, qtbot, "cost")
    assert w.preview_view.state is ViewState.CONTENT
    assert "412.3415" in w.preview.toPlainText()
    assert w.save_md_button.isEnabled() and w.save_csv_button.isEnabled()


def test_save_both_formats(demo, qtbot, tmp_path, monkeypatch):
    w, ctx = demo
    _rendered(w, qtbot, "findings")
    md, csv_path = tmp_path / "f.md", tmp_path / "f.csv"
    paths = iter([str(md), str(csv_path)])
    monkeypatch.setattr(ReportsProgram, "ask_save_path", staticmethod(lambda *a: next(paths)))
    assert w.save_markdown() and w.save_csv()
    text = md.read_text()
    assert text.startswith("# Open findings")
    assert "Report type: findings" in text and "Result status: PARTIAL (Some data is missing — see Coverage.)" in text and "accounts: all" in text
    assert csv_path.read_text().splitlines()[0] == "severity,rule,account,region,resource,age_days"
    assert "Report type" not in csv_path.read_text()
    assert ctx.settings.recent_documents()[:2] == (str(csv_path), str(md))


def test_save_cancelled(demo, qtbot, monkeypatch):
    w, ctx = demo
    _rendered(w, qtbot, "cost")
    monkeypatch.setattr(ReportsProgram, "ask_save_path", staticmethod(lambda *a: None))
    assert w.save_markdown() is False and ctx.settings.recent_documents() == ()


def test_save_failure_reports_in_status(demo, qtbot, tmp_path, monkeypatch):
    w, ctx = demo
    _rendered(w, qtbot, "cost")
    monkeypatch.setattr(ReportsProgram, "ask_save_path",
                        staticmethod(lambda *a: str(tmp_path / "no-such-dir" / "x.md")))
    assert w.save_markdown() is False
    assert "Save failed" in w.status.counts_label.text() and ctx.settings.recent_documents() == ()


def test_refused_recent_document_still_saves(demo, qtbot, tmp_path, monkeypatch):
    w, ctx = demo
    _rendered(w, qtbot, "cost")
    bad = tmp_path / "a\nb.md"
    monkeypatch.setattr(ReportsProgram, "ask_save_path", staticmethod(lambda *a: str(bad)))
    assert w.save_markdown() is True
    assert ctx.settings.recent_documents() == () and "not added" in w.status.counts_label.text()


def test_scope_change_makes_preview_unsaveable(demo, qtbot):
    w, _ = demo
    _rendered(w, qtbot, "cost")
    w.refresh()                                  # what a scope change does: reload types and re-render
    assert not w.save_md_button.isEnabled() and w.preview.toPlainText() == ""
    assert w.save_markdown() is False
    qtbot.waitUntil(lambda: w.save_md_button.isEnabled())
    assert "412.3415" in w.preview.toPlainText()


def test_quick_selection_shows_last_only(demo, qtbot):
    w, _ = demo
    w.select_report("cost")
    w.select_report("findings")
    qtbot.waitUntil(lambda: w.save_md_button.isEnabled())
    qtbot.wait(100)
    text = w.preview.toPlainText()
    assert text.startswith("# Open findings") and "412.3415" not in text


def test_not_ok_render_is_not_saveable(demo, qtbot, monkeypatch):
    w, _ = demo
    monkeypatch.setattr(w.ctx.providers.reports, "render", lambda *a, **k: Result.error("boom"))
    w.select_report("cost")
    qtbot.waitUntil(lambda: w.preview_view.state is ViewState.ERROR)
    assert not w.save_md_button.isEnabled() and not w.save_csv_button.isEnabled()


def test_csv_button_needs_csv(demo, qtbot, monkeypatch):
    w, _ = demo
    monkeypatch.setattr(w.ctx.providers.reports, "render",
                        lambda *a, **k: Result.ok(RenderedReport("T", "# T\n", None)))
    w.select_report("cost")
    qtbot.waitUntil(lambda: w.save_md_button.isEnabled())
    assert not w.save_csv_button.isEnabled() and not w.save_csv_action.isEnabled()


def _letter(text):
    m = re.search(r"(?<!&)&([A-Za-z])", text.replace("&&", ""))
    return m.group(1).lower() if m else None


def test_mnemonics_are_unique(demo, qtbot):
    w, _ = demo
    bar = [_letter(a.text()) for a in w.menu_bar.actions()]
    assert len(bar) == len(set(bar)), bar
    own = [_letter(a.text()) for a in w.program_menu.actions()]
    assert not (set(own) - {None}) & set(bar), own
    assert len(own) == len(set(own)), own
    buttons = [_letter(b.text()) for b in w.findChildren(QAbstractButton) if _letter(b.text())]
    assert len(buttons) == len(set(buttons)), buttons
    assert not set(buttons) & set(bar)


def _fake_render(w, monkeypatch, report, formats=("md", "csv")):
    from aws95.contracts.reports import ReportType
    types = (ReportType("x", "Type X", formats),)
    monkeypatch.setattr(w.ctx.providers.reports, "report_types", lambda *a, **k: Result.ok(types))
    monkeypatch.setattr(w.ctx.providers.reports, "render", lambda *a, **k: Result.ok(report))
    w.refresh()
    qtbot_wait = None  # noqa: F841


def test_csv_is_saved_byte_exact_except_defanged_formulas(demo, qtbot, tmp_path, monkeypatch):
    """Ruling M3-R24: formula-like text cells get a leading quote; every other byte (Decimal text, quoting,
    line endings) is the provider's."""
    w, _ = demo
    csv_text = ('a,b,amount\r\n=1,"x ""q"" y",-12.3400\r\n"line1\nline2",z,+0.000001\r\n'
                '@SUM(A1),-2+3,1E+3\r\n')
    expected = ('a,b,amount\r\n\'=1,"x ""q"" y",-12.3400\r\n"line1\nline2",z,+0.000001\r\n'
                "'@SUM(A1),'-2+3,1E+3\r\n")
    _fake_render(w, monkeypatch, RenderedReport("T", "# T\n", csv_text))
    qtbot.waitUntil(lambda: w.types.count() == 1)
    assert w.select_report("x")
    qtbot.waitUntil(lambda: w.save_csv_button.isEnabled())
    out = tmp_path / "o.csv"
    monkeypatch.setattr(ReportsProgram, "ask_save_path", staticmethod(lambda *a: str(out)))
    assert w.save_csv()
    assert out.read_bytes() == expected.encode("utf-8")


@pytest.mark.parametrize("source, expected", [
    ("a,b\n1,2\n", "a,b\n1,2\n"),                                # LF stays LF
    ("a,b\r\n1,2", "a,b\r\n1,2"),                                # no final newline stays without one
    ("x\n=cmd|' /C calc'!A0\n", "x\n'=cmd|' /C calc'!A0\n"),
    ("x\n\tTAB\n", "x\n'\tTAB\n"),
    ("x\n\"\rCR\"\n", "x\n\"'\rCR\"\n"),
    ("x\n-0.10\n-Infinity\n", "x\n-0.10\n'-Infinity\n"),       # numbers kept exactly; non-finite is text
    ("", ""),
])
def test_defang_csv_text(source, expected):
    from aws95.programs.common.export import defang_csv_text
    assert defang_csv_text(source) == expected


@pytest.mark.parametrize("formats,md,csv", [(("md",), True, False), (("csv",), False, True)])
def test_format_gating(demo, qtbot, monkeypatch, formats, md, csv):
    w, _ = demo
    _fake_render(w, monkeypatch, RenderedReport("T", "# T\n", "a\n"), formats)
    qtbot.waitUntil(lambda: w.types.count() == 1)
    assert w.select_report("x")
    qtbot.waitUntil(lambda: w.preview.toPlainText() != "")
    assert (w.save_md_button.isEnabled(), w.save_md_action.isEnabled()) == (md, md)
    assert (w.save_csv_button.isEnabled(), w.save_csv_action.isEnabled()) == (csv, csv)


def test_stale_render_after_refresh_never_lands(demo, qtbot, monkeypatch):
    import threading
    w, _ = demo
    gate, done = threading.Event(), threading.Event()
    real = w.ctx.providers.reports.render

    def slow(*a, **k):
        gate.wait(5)
        try:
            return real(*a, **k)
        finally:
            done.set()

    monkeypatch.setattr(w.ctx.providers.reports, "render", slow)
    w.select_report("cost")
    monkeypatch.setattr(w.ctx.providers.reports, "report_types", lambda *a, **k: Result.error("down"))
    w.refresh()                                   # scope changed while the render was in flight
    gate.set()
    qtbot.waitUntil(done.is_set)
    qtbot.wait(150)
    assert w.preview.toPlainText() == ""
    assert not w.save_md_button.isEnabled() and not w.save_csv_button.isEnabled()


def test_export_saves_markdown_else_csv(demo, qtbot, tmp_path, monkeypatch):
    w, _ = demo
    assert w.export() is False and "Nothing to export" in w.status.counts_label.text()
    _rendered(w, qtbot, "cost")
    out = tmp_path / "e.md"
    monkeypatch.setattr(ReportsProgram, "ask_save_path", staticmethod(lambda *a: str(out)))
    assert w.export() and out.read_text().startswith("# Cost summary")      # Markdown carries provenance
    assert "> Report type: cost" in out.read_text()
    w.save_md_button.setEnabled(False)          # only CSV left enabled: Ctrl+E falls back to it
    csv = tmp_path / "e.csv"
    monkeypatch.setattr(ReportsProgram, "ask_save_path", staticmethod(lambda *a: str(csv)))
    assert w.export() and csv.read_text().splitlines()[0] == "service,period,amount,unit"
    _fake_render(w, monkeypatch, RenderedReport("T", "# T\n", None), ("md",))
    qtbot.waitUntil(lambda: w.types.count() == 1)
    w.select_report("x")
    qtbot.waitUntil(lambda: w.save_md_button.isEnabled())
    md = tmp_path / "e.md"
    monkeypatch.setattr(ReportsProgram, "ask_save_path", staticmethod(lambda *a: str(md)))
    assert w.export() and md.read_text().startswith("# T")


def test_provenance_layout_and_time_range():
    from datetime import datetime, timezone
    from aws95.contracts.common import Scope, TimeRange
    from aws95.programs.reports import with_provenance
    tr = TimeRange(datetime(2026, 9, 1, tzinfo=timezone.utc), datetime(2026, 10, 1, 12, 30, tzinfo=timezone.utc))
    out = with_provenance("# T\n\n| a |\n", "cost", Scope(("111111111111",), (), tr), Result.ok(1, message="m"))
    lines = out.split("\n")
    assert lines[0] == "# T" and lines[1] == ""
    body = lines.index("| a |")
    assert lines[body - 1] == ""                              # blank line before the provider body
    quote = [ln for ln in lines if ln.startswith(">")]
    assert all(ln.strip() in (">", ) or ln.startswith("> ") for ln in quote)
    assert [ln for ln in quote if ln != ">"] and quote[1] == ">"      # lines separated by a bare '>'
    assert "time range: 2026-09-01T00:00:00+00:00 → 2026-10-01T12:30:00+00:00" in out
    assert "TimeRange(" not in out
