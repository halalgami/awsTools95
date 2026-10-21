import time
from pathlib import Path

import pytest
from PySide6.QtGui import QFont, QPalette
from PySide6.QtWidgets import QApplication

from aws95.kit.widgets.stateview import StateView, ViewState
from aws95.kit.widgets.watermark import Watermark
from aws95.programs import DEFAULT_MANIFESTS
from aws95.programs.common import ProgramBase
from aws95.providers.demo import demo_provider_set
from aws95.shell.desktop import DEFAULT_SIZE, Desktop
from aws95.shell.registry import Registry

PROGRAM_IDS = [m.id for m in DEFAULT_MANIFESTS if m.category == "programs"]
REG = Registry(DEFAULT_MANIFESTS)


def _views(w) -> list[StateView]:
    return w.findChildren(StateView)


def _none_loading(w) -> bool:
    return all(v.state is not ViewState.LOADING for v in _views(w))


def _empty_settled(w) -> bool:
    # StateView starts in CONTENT and the Inspector's related-activity view is NOT_CONNECTED from birth, so
    # neither "nothing is loading" nor "some view is NOT_CONNECTED" proves a load ran. The primary load owns the
    # status strip, so a non-empty freshness label is the evidence that it finished.
    return (bool(w.status.freshness_label.text())
            and any(v.state is ViewState.NOT_CONNECTED for v in _views(w)) and _none_loading(w))


def _open(program_env, qtbot, pid, providers=None):
    w, ctx = program_env(REG.get(pid).load_factory(), providers)
    qtbot.addWidget(w)
    w.resize(900, 600)
    w.show()
    qtbot.waitUntil(lambda: _empty_settled(w), timeout=5000)
    return w, ctx


def test_nine_programs_no_placeholders_left():
    assert len(PROGRAM_IDS) == 9
    assert not (Path(__file__).resolve().parents[1] / "aws95" / "programs" / "placeholders.py").exists()
    assert all("placeholders" not in m.factory for m in DEFAULT_MANIFESTS)


@pytest.mark.parametrize("pid", PROGRAM_IDS)
def test_empty_mode_is_honest(program_env, qtbot, pid):
    w, _ = _open(program_env, qtbot, pid)
    assert isinstance(w, ProgramBase)
    assert isinstance(w.primary_view, StateView) and w.primary_view.state is ViewState.NOT_CONNECTED
    assert ViewState.ERROR not in {v.state for v in _views(w)}
    assert not w.is_demo and not w.findChildren(Watermark)
    assert w.status.freshness_label.text() == "Not connected"


@pytest.mark.parametrize("pid", PROGRAM_IDS)
def test_demo_mode_is_watermarked_and_titled(qtbot, program_env, pid):
    _, ctx = program_env(lambda c: None, demo_provider_set())
    desk = Desktop(REG, lambda: ctx, ctx.settings)
    qtbot.addWidget(desk)
    desk.resize(1100, 750)
    desk.show()
    win = desk.open_program(pid)
    content = win.content()
    qtbot.waitUntil(lambda: content.is_demo and _none_loading(content), timeout=5000)
    assert win.windowTitle().endswith("[DEMO]")
    assert len(content.body.findChildren(Watermark)) == 1
    assert ViewState.ERROR not in {v.state for v in _views(content)}


@pytest.mark.parametrize("pid", PROGRAM_IDS)
def test_menu_mnemonics_are_unique(program_env, qtbot, pid):
    w, _ = program_env(REG.get(pid).load_factory())        # menus exist from construction: no data needed
    qtbot.addWidget(w)
    letters = []
    for a in w.menu_bar.actions():
        text = a.text()
        assert "&" in text, text
        letters.append(text[text.index("&") + 1].lower())
    assert len(letters) == len(set(letters)), letters


@pytest.mark.parametrize("pid", PROGRAM_IDS)
def test_first_open_frame_budget(program_env, qtbot, pid):
    factory = REG.get(pid).load_factory()
    _, ctx = program_env(lambda c: None, demo_provider_set())
    warm = factory(ctx)                     # warm up imports, fonts and style caches: construct and discard one
    qtbot.addWidget(warm)
    warm.close()
    start = time.perf_counter()
    w = factory(ctx)
    qtbot.addWidget(w)
    w.resize(900, 600)
    w.show()
    QApplication.processEvents()
    elapsed_ms = (time.perf_counter() - start) * 1000
    # Spec 4.5: <=150 ms to frame on a packaged warm build; offscreen CI gets a generous smoke guard.
    assert elapsed_ms < 250, f"{pid} took {elapsed_ms:.0f} ms to first frame"


def _on_screen(widget, root) -> bool:
    """Visible and laid out inside root (a toolbar hides widgets it moves into its overflow menu)."""
    if not widget.isVisible() or widget.width() <= 0:
        return False
    top_left = widget.mapTo(root, widget.rect().topLeft())
    bottom_right = widget.mapTo(root, widget.rect().bottomRight())
    return root.rect().contains(top_left) and root.rect().contains(bottom_right)


@pytest.fixture
def app_theme(qapp):
    """Apply a theme and font scale app-wide, then put the app font, style, palette and theme back."""
    from aws95.kit.style import apply_theme
    from aws95.kit.theme import LIGHT

    saved = (QFont(qapp.font()), qapp.style(), QPalette(qapp.palette()), qapp.styleSheet())

    def apply(theme, scale):
        apply_theme(qapp, theme, font_scale=scale)

    yield apply
    font, style, palette, sheet = saved
    apply_theme(qapp, LIGHT)
    qapp.setFont(font)
    qapp.setPalette(palette)
    qapp.setStyleSheet(sheet)


@pytest.mark.parametrize("theme_name", ["LIGHT", "DARK"])
@pytest.mark.parametrize("scale", [100, 150])
@pytest.mark.parametrize("pid", PROGRAM_IDS)
def test_scope_pickers_visible_at_default_size(qapp, qtbot, program_env, app_theme, pid, scale, theme_name):
    """Ruling M3-R21: the scope bar (and Activity's range picker) never hides in toolbar overflow, at any font
    scale the Settings offer. The scale goes through apply_theme, as in the app, so it reaches toolbar widgets."""
    from aws95.kit import theme as themes

    app_theme(getattr(themes, theme_name), scale)
    _, ctx = program_env(lambda c: None, demo_provider_set())
    desk = Desktop(REG, lambda: ctx, ctx.settings)
    qtbot.addWidget(desk)
    desk.resize(1100, 750)
    desk.show()
    win = desk.open_program(pid)
    assert (win.width(), win.height()) == DEFAULT_SIZE
    content = win.content()
    if content.scope_bar is None:
        pytest.skip(f"{pid} has no scope bar")
    pickers = [content.scope_bar.account_combo]
    if content.scope_bar.region_button.isVisibleTo(content.scope_bar):
        pickers.append(content.scope_bar.region_button)
    if hasattr(content, "range_combo"):
        pickers.append(content.range_combo)
    qtbot.wait(50)                          # let the toolbars relayout before judging
    hidden = [p.objectName() or type(p).__name__ for p in pickers if not _on_screen(p, content)]
    assert not hidden, f"{pid} at {scale}% {theme_name}: pickers off screen: {hidden}"
    if hasattr(content, "range_combo"):
        content.set_range("custom")
        qtbot.wait(50)
        hidden = [n for n, e in (("from_edit", content.from_edit), ("to_edit", content.to_edit))
                  if not _on_screen(e, content)]
        assert not hidden, f"{pid} at {scale}% {theme_name}: custom editors off screen: {hidden}"


def test_activity_custom_editors_on_third_row_at_default_size(qapp, qtbot, program_env):
    """Ruling M3-R25: Custom's From/To get their own row below the range row, shown only while Custom is selected, and stay reachable."""
    _, ctx = program_env(lambda c: None, demo_provider_set())
    desk = Desktop(REG, lambda: ctx, ctx.settings)
    qtbot.addWidget(desk)
    desk.resize(1100, 750)
    desk.show()
    win = desk.open_program("activity")
    assert (win.width(), win.height()) == DEFAULT_SIZE
    content = win.content()
    font = content.font()
    font.setPointSizeF(font.pointSizeF() * 1.25)    # same headroom as the picker test
    content.setFont(font)
    assert content.range_toolbar.isVisible() and content.range_combo.isVisible()    # the range row is permanent
    assert not content.custom_toolbar.isVisible()
    content.set_range("custom")
    qtbot.wait(50)
    assert content.custom_toolbar.isVisible()
    assert content.custom_toolbar.y() > content.range_toolbar.y() > content.scope_toolbar.y()
    assert _on_screen(content.from_edit, content) and _on_screen(content.to_edit, content)
    assert _on_screen(content.range_combo, content)
    content.set_range("24h")
    assert not content.custom_toolbar.isVisible()
    assert content.range_toolbar.isVisible() and content.range_combo.isVisible()
