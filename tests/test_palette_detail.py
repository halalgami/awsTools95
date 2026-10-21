from PySide6.QtGui import QFontMetrics

from aws95.shell.palette import _DETAIL_ROLE, CommandPalette, PaletteItem, fit_detail


def test_fit_detail_elides_then_drops(qapp):
    """Review Focus 4."""
    fm = QFontMetrics(qapp.font())
    title, detail = "prod-data", "s3.bucket · global · 210987654321"
    wide = fm.horizontalAdvance(title) + fm.horizontalAdvance(detail) + 100
    assert fit_detail(fm, wide, title, detail) == detail
    mid = fit_detail(fm, fm.horizontalAdvance(title) + fm.horizontalAdvance("s3.bucket") + 16, title, detail)
    assert mid.endswith("…") and len(mid) < len(detail)
    assert fit_detail(fm, fm.horizontalAdvance(title) + 4, title, detail) == ""


def test_rows_keep_title_and_detail_apart(qtbot):
    pal = CommandPalette()
    qtbot.addWidget(pal)
    pal.set_items([PaletteItem("Commands", "Shut Down…", lambda: None, "Ctrl+Q")])
    pal.show()
    row = next(pal.list_widget.item(i) for i in range(pal.list_widget.count())
               if pal.list_widget.item(i).data(_DETAIL_ROLE))
    assert row.text() == "Shut Down…" and row.data(_DETAIL_ROLE) == "Ctrl+Q"
