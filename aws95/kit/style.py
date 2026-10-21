"""Win95Style: a QProxyStyle over Fusion that paints Classic+ bevels from theme tokens.

M1 custom-paints buttons, tool buttons, frames, line edits, focus rects, the default-button
ring, scrollbars, block progress bars, checkboxes and radio buttons. Tabs use Fusion with the
Classic+ palette.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QRect
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPalette, QPen, Qt
from PySide6.QtWidgets import (QApplication, QProxyStyle, QStyle, QStyleFactory,
                               QStyleOptionProgressBar, QStyleOptionSlider,
                               QStyleOptionTitleBar)

from aws95.core.settings import Settings
from aws95.kit.paint import draw_arrow, draw_bevel, draw_focus_rect, draw_title_glyph
from aws95.kit.qss import build_qss
from aws95.kit.theme import Theme, ThemeManager, relative_luminance, set_current, theme_bus

BASE_POINT_SIZE = 12.0 if sys.platform == "darwin" else 9.0
BLOCK_W, BLOCK_GAP = 8, 2

PE = QStyle.PrimitiveElement
CE = QStyle.ControlElement
CC = QStyle.ComplexControl
SC = QStyle.SubControl
PM = QStyle.PixelMetric
S = QStyle.StateFlag

_METRICS = {
    PM.PM_DefaultFrameWidth: 2,
    PM.PM_ButtonShiftHorizontal: 1,
    PM.PM_ButtonShiftVertical: 1,
    PM.PM_ScrollBarExtent: 16,
    PM.PM_ButtonMargin: 8,
    PM.PM_TitleBarHeight: 20,
    PM.PM_MdiSubWindowFrameWidth: 4,
}


def build_palette(t: Theme) -> QPalette:
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: t.face, QPalette.ColorRole.WindowText: t.text,
        QPalette.ColorRole.Base: t.well, QPalette.ColorRole.AlternateBase: t.zebra,
        QPalette.ColorRole.Text: t.text, QPalette.ColorRole.Button: t.face,
        QPalette.ColorRole.ButtonText: t.text, QPalette.ColorRole.Highlight: t.sel,
        QPalette.ColorRole.HighlightedText: t.sel_text, QPalette.ColorRole.ToolTipBase: t.tooltip_bg,
        QPalette.ColorRole.ToolTipText: t.tooltip_text, QPalette.ColorRole.Light: t.hi,
        QPalette.ColorRole.Midlight: t.face2, QPalette.ColorRole.Mid: t.lo,
        QPalette.ColorRole.Dark: t.dark, QPalette.ColorRole.Shadow: t.dark,
        QPalette.ColorRole.PlaceholderText: t.muted, QPalette.ColorRole.Link: t.link, QPalette.ColorRole.LinkVisited: t.link,
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(t.disabled_text))
    return pal


class Win95Style(QProxyStyle):
    def __init__(self, theme: Theme) -> None:
        super().__init__(QStyleFactory.create("Fusion"))
        self.theme = theme

    def standardPalette(self) -> QPalette:  # noqa: N802 - Qt override
        return build_palette(self.theme)

    def pixelMetric(self, metric, option=None, widget=None):  # noqa: N802
        if metric in _METRICS:
            return _METRICS[metric]
        return super().pixelMetric(metric, option, widget)

    def drawPrimitive(self, element, option, painter, widget=None):  # noqa: N802
        t, r, st = self.theme, option.rect, option.state
        if element in (PE.PE_IndicatorCheckBox, PE.PE_IndicatorItemViewItemCheck):
            box = r.adjusted(0, 0, -1, -1) if r.width() > 13 else r
            side = min(box.width(), box.height(), 13)
            box = QRect(box.x(), box.y() + (box.height() - side) // 2, side, side)
            fill = t.well if st & S.State_Enabled else t.face
            draw_bevel(painter, box, t, kind="field", fill=fill)
            painter.save()                                  # a 1px outline that stays visible in Dark
            painter.setPen(QPen(QColor(t.indicator_border), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(box.adjusted(0, 0, -1, -1))
            painter.restore()
            if st & S.State_On or st & S.State_NoChange:
                painter.save()
                pen = QPen(QColor(t.text if st & S.State_On and st & S.State_Enabled else t.muted), 2)
                painter.setPen(pen)
                x, y, s = box.x(), box.y(), box.width()
                painter.drawLine(x + 3, y + s // 2, x + s // 2 - 1, y + s - 4)
                painter.drawLine(x + s // 2 - 1, y + s - 4, x + s - 4, y + 3)
                painter.restore()
            return
        if element == PE.PE_IndicatorRadioButton:
            side = min(r.width(), r.height(), 13)
            dot = QRect(r.x() + (r.width() - side) // 2, r.y() + (r.height() - side) // 2, side, side)
            painter.save()                                  # crisp pixels, like the checkbox; a ring visible in Dark
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setPen(QPen(QColor(t.indicator_border), 1))
            painter.setBrush(QBrush(QColor(t.well if st & S.State_Enabled else t.face)))
            painter.drawEllipse(dot.adjusted(0, 0, -1, -1))
            if st & S.State_On:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(QColor(t.text if st & S.State_Enabled else t.muted)))
                inner = max(3, side // 3)
                painter.drawEllipse(QRect(dot.center().x() - inner // 2, dot.center().y() - inner // 2, inner, inner))
            painter.restore()
            return
        if element in (PE.PE_PanelButtonCommand, PE.PE_PanelButtonBevel):
            draw_bevel(painter, r, t, sunken=bool(st & (S.State_Sunken | S.State_On)), kind="button", fill=t.face)
            return
        if element == PE.PE_PanelButtonTool:
            if st & (S.State_Sunken | S.State_On):
                draw_bevel(painter, r, t, sunken=True, kind="button", fill=t.face2)
            elif not st & S.State_AutoRaise or (st & S.State_MouseOver and st & S.State_Enabled):
                draw_bevel(painter, r, t, sunken=False, kind="button", fill=t.face)
            return
        if element == PE.PE_PanelLineEdit:
            draw_bevel(painter, r, t, kind="field", fill=t.well)
            return
        if element == PE.PE_Frame and st & S.State_Raised:
            draw_bevel(painter, r, t, kind="panel")
            return
        if element in (PE.PE_FrameLineEdit, PE.PE_Frame):
            draw_bevel(painter, r, t, kind="field")
            return
        if element == PE.PE_FrameWindow:
            draw_bevel(painter, r, t, kind="panel", fill=t.face)
            return
        if element == PE.PE_FrameFocusRect:
            draw_focus_rect(painter, r, t.text)
            return
        if element == PE.PE_FrameDefaultButton:
            painter.save()
            painter.setPen(QColor(t.text))
            painter.drawRect(r.adjusted(0, 0, -1, -1))
            painter.restore()
            return
        super().drawPrimitive(element, option, painter, widget)

    def drawControl(self, element, option, painter, widget=None):  # noqa: N802
        t = self.theme
        if element == CE.CE_ProgressBarGroove:
            draw_bevel(painter, option.rect, t, kind="field", fill=t.well)
            return
        if element == CE.CE_ProgressBarContents and isinstance(option, QStyleOptionProgressBar):
            span = option.maximum - option.minimum
            inner = option.rect.adjusted(3, 3, -3, -3)
            horizontal = bool(option.state & S.State_Horizontal)
            length = inner.width() if horizontal else inner.height()
            if span <= 0:  # indeterminate: static segment of 3 blocks at the start
                filled = 3 * BLOCK_W + 2 * BLOCK_GAP
            else:
                filled = int(length * (option.progress - option.minimum) / span)
            inverted = option.invertedAppearance
            # horizontal grows left->right, vertical bottom->top; inverted flips both
            from_start = (horizontal and not inverted) or (not horizontal and inverted)
            painter.save()
            offset = 0
            while offset + BLOCK_W <= min(filled, length):
                if horizontal:
                    x = inner.left() + offset if from_start else inner.right() + 1 - offset - BLOCK_W
                    rect = QRect(x, inner.top(), BLOCK_W, inner.height())
                else:
                    y = inner.top() + offset if from_start else inner.bottom() + 1 - offset - BLOCK_W
                    rect = QRect(inner.left(), y, inner.width(), BLOCK_W)
                painter.fillRect(rect, QColor(t.sel))
                offset += BLOCK_W + BLOCK_GAP
            painter.restore()
            return
        if element == CE.CE_ProgressBarLabel:
            return  # Win95 progress bars have no text
        super().drawControl(element, option, painter, widget)

    def drawComplexControl(self, control, option, painter, widget=None):  # noqa: N802
        if control == CC.CC_ScrollBar and isinstance(option, QStyleOptionSlider):
            self._draw_scrollbar(option, painter, widget)
            return
        if control == CC.CC_TitleBar and isinstance(option, QStyleOptionTitleBar):
            self._draw_title_bar(option, painter, widget)
            return
        super().drawComplexControl(control, option, painter, widget)

    _TITLE_BUTTONS = (
        (SC.SC_TitleBarMinButton, "min"),
        (SC.SC_TitleBarMaxButton, "max"),
        (SC.SC_TitleBarNormalButton, "restore"),
        (SC.SC_TitleBarCloseButton, "close"),
    )

    def _draw_title_bar(self, opt: QStyleOptionTitleBar, p, widget) -> None:
        t = self.theme
        active = bool(opt.state & S.State_Active)
        start, end = ((t.title_active_start, t.title_active_end) if active
                      else (t.title_inactive_start, t.title_inactive_end))
        rect = opt.rect
        grad = QLinearGradient(rect.topLeft(), rect.topRight())
        grad.setColorAt(0.0, QColor(start))
        grad.setColorAt(1.0, QColor(end))
        p.save()
        p.fillRect(rect, grad)
        label = self.subControlRect(CC.CC_TitleBar, opt, SC.SC_TitleBarLabel, widget)
        font = QFont(p.font())
        font.setBold(True)
        p.setFont(font)
        fm = p.fontMetrics()
        chip = self._chip_spec(widget)
        if chip is None:
            avail = label.width() - 4          # keep a gap before the caption buttons
        else:  # the chip identifies the account: reserve its room first, elide the title instead
            chip_w = min(fm.horizontalAdvance(chip[0]) + 10, label.width())
            avail = label.width() - chip_w - 8
        text = fm.elidedText(opt.text, Qt.TextElideMode.ElideRight, max(0, avail))
        p.setPen(QColor(t.title_text if active else t.title_text_inactive))
        p.drawText(label, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), text)
        if chip is not None:
            self._draw_title_chip(p, chip, label, fm.horizontalAdvance(text) if text else -8)
        for sc, glyph in self._TITLE_BUTTONS:
            if not (opt.subControls & sc):
                continue
            br = self.subControlRect(CC.CC_TitleBar, opt, sc, widget)
            if br.isEmpty():
                continue
            br = br.adjusted(1, 2, -1, -2)
            pressed = bool(opt.activeSubControls & sc) and bool(opt.state & S.State_Sunken)
            draw_bevel(p, br, t, sunken=pressed, kind="button", fill=t.face)
            draw_title_glyph(p, br.translated(1, 1) if pressed else br, glyph, t.text)
        p.restore()

    @staticmethod
    def _chip_spec(widget) -> tuple[str, str] | None:
        if widget is None:
            return None
        chip_text = widget.property("aws95ChipText")
        chip_color = widget.property("aws95ChipColor")
        if not chip_text or not chip_color or not QColor(str(chip_color)).isValid():
            return None
        return str(chip_text), QColor(str(chip_color)).name()

    @staticmethod
    def _draw_title_chip(p, chip: tuple[str, str], label: QRect, text_width: int) -> None:
        chip_text, chip_color = chip
        fm = p.fontMetrics()
        x = label.left() + text_width + 8
        width = min(fm.horizontalAdvance(chip_text) + 10, label.right() + 1 - x)
        if width <= 10:
            return
        r = QRect(x, label.top() + 2, width, label.height() - 4)
        chip_text = fm.elidedText(chip_text, Qt.TextElideMode.ElideRight, width - 10)
        p.fillRect(r, QColor(chip_color))
        p.setPen(QColor("#000000"))
        p.drawRect(r.adjusted(0, 0, -1, -1))
        p.setPen(QColor("#000000" if relative_luminance(chip_color) > 0.18 else "#ffffff"))
        p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), chip_text)

    def _draw_scrollbar(self, opt: QStyleOptionSlider, p, widget) -> None:
        t = self.theme
        horizontal = opt.orientation == Qt.Orientation.Horizontal
        groove = self.subControlRect(CC.CC_ScrollBar, opt, SC.SC_ScrollBarGroove, widget)
        p.fillRect(groove, QColor(t.face))
        p.fillRect(groove, QBrush(QColor(t.hi), Qt.BrushStyle.Dense4Pattern))
        enabled = bool(opt.state & S.State_Enabled)
        arrow_color = t.text if enabled else t.disabled_text
        for sc, direction in ((SC.SC_ScrollBarSubLine, "left" if horizontal else "up"),
                              (SC.SC_ScrollBarAddLine, "right" if horizontal else "down")):
            rect = self.subControlRect(CC.CC_ScrollBar, opt, sc, widget)
            pressed = bool(opt.activeSubControls & sc) and bool(opt.state & S.State_Sunken)
            draw_bevel(p, rect, t, sunken=pressed, kind="button", fill=t.face)
            draw_arrow(p, rect.translated(1, 1) if pressed else rect, direction, arrow_color)
        slider = self.subControlRect(CC.CC_ScrollBar, opt, SC.SC_ScrollBarSlider, widget)
        if slider.isValid() and enabled:
            draw_bevel(p, slider, t, kind="button", fill=t.face)


def scaled_fixed_font() -> QFont:
    """The system fixed-pitch font at the app's current font scale (the app font is BASE_POINT_SIZE x scale)."""
    from PySide6.QtGui import QFontDatabase
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    app = QApplication.instance()
    if app is not None and BASE_POINT_SIZE > 0:
        ratio = app.font().pointSizeF() / BASE_POINT_SIZE
        if ratio > 0 and font.pointSizeF() > 0:
            font.setPointSizeF(font.pointSizeF() * ratio)
    return font


def apply_theme(app: QApplication, theme: Theme, font_scale: int = 100) -> Win95Style:
    set_current(theme, notify=False)  # the bus is emitted once, after style/palette/QSS are applied
    style = Win95Style(theme)
    app.setStyle(style)
    app.setPalette(build_palette(theme))
    font = QFont(app.font())
    font.setPointSizeF(BASE_POINT_SIZE * font_scale / 100)
    app.setFont(font)
    # Without a QSS repolish nothing flushes the posted font-change propagation, and the taskbar
    # sizes itself from it, so deliver just the application font-change events now.
    QCoreApplication.sendPostedEvents(None, QEvent.Type.ApplicationFontChange)
    sheet = build_qss(theme)
    if sheet or app.styleSheet():
        app.setStyleSheet(sheet)
    # Single bus emitter: live-restyle pinned colours after Qt's repolish has reset widget palettes.
    theme_bus().changed.emit(theme)
    return style


class _ThemingBinding(QObject):
    """Re-applies the theme on theme or font-scale changes. Owned by the manager, so its connections
    (bound methods, no lambdas) die with it and never pin an old Settings across restarts."""

    def __init__(self, app: QApplication, manager: ThemeManager, settings: Settings) -> None:
        super().__init__(manager)
        self._app = app
        self._manager = manager
        self._settings = settings
        manager.changed.connect(self.apply)
        settings.changed.connect(self._on_setting)

    def apply(self, *_args) -> None:
        apply_theme(self._app, self._manager.theme, self._settings.font_scale)

    def _on_setting(self, key: str) -> None:
        if key == "appearance/font_scale":
            self.apply()


def install_theming(app: QApplication, manager: ThemeManager, settings: Settings) -> None:
    _ThemingBinding(app, manager, settings).apply()
