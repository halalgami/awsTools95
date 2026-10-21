"""pyqtgraph wrappers themed by tokens. Floats are used only for plot geometry."""
from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

import pyqtgraph as pg

from aws95.kit.theme import Theme, current_theme, theme_bus

pg.setConfigOptions(antialias=False)

Number = Decimal | int | float


def _geometry(value: Number | None) -> float:
    """Convert to float for plotting; reject None/NaN/inf rather than draw garbage."""
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"not a plottable number: {value!r}") from exc
    if not math.isfinite(f):
        raise ValueError(f"not a finite number: {value!r}")
    return f


class ThemedPlot(pg.PlotWidget):
    def __init__(self, parent=None, **kwargs) -> None:
        super().__init__(parent=parent, **kwargs)
        self.setMenuEnabled(False)
        self.hideButtons()
        self.showGrid(x=False, y=True, alpha=0.25)
        self.apply_theme(current_theme())
        # Bound method (not a lambda): Qt auto-disconnects when the widget is destroyed.
        theme_bus().changed.connect(self.apply_theme)
        self._bus_connected = True

    def close(self) -> bool:
        # pg.PlotWidget.close() tears down its PlotItem; stop following the theme first,
        # otherwise a later theme switch hits the dead widget before Qt destroys it.
        if self._bus_connected:
            self._bus_connected = False
            try:
                theme_bus().changed.disconnect(self.apply_theme)
            except (RuntimeError, TypeError):
                pass
        if self.plotItem is None:  # already closed; pyqtgraph's close() is not re-entrant
            return True
        return super().close()

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        if self.plotItem is None:  # closed
            return
        self.setBackground(theme.well)
        for name in ("left", "bottom"):
            axis = self.getAxis(name)
            axis.setPen(pg.mkPen(theme.lo))
            axis.setTextPen(pg.mkPen(theme.text))
        self._restyle()

    def _restyle(self) -> None:
        """Subclasses re-apply series colours."""


class BarChart(ThemedPlot):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)  # calls apply_theme -> _restyle, which tolerates missing attributes
        self._bar: pg.BarGraphItem | None = None
        self.labels: tuple[str, ...] = ()
        self._values: tuple[float, ...] = ()

    @property
    def bar_count(self) -> int:
        return len(getattr(self, "_values", ()))

    def set_data(self, labels: Sequence[str], values: Sequence[Number]) -> None:
        if len(labels) != len(values):
            raise ValueError(f"{len(labels)} labels but {len(values)} values")
        geometry = tuple(_geometry(v) for v in values)  # validate before mutating state
        self.labels = tuple(labels)
        self._values = geometry
        self._draw()

    def _draw(self) -> None:
        if self._bar is not None:
            self.removeItem(self._bar)
            self._bar = None
        if not self._values:
            self.getAxis("bottom").setTicks([[]])
            return
        t = self._theme
        xs = list(range(len(self._values)))
        self._bar = pg.BarGraphItem(x=xs, height=list(self._values), width=0.7,
                                    brush=t.sel, pen=pg.mkPen(t.dark))
        self.addItem(self._bar)
        self.getAxis("bottom").setTicks([list(zip(xs, self.labels))])

    def _restyle(self) -> None:
        if getattr(self, "_values", None):
            self._draw()


class LineChart(ThemedPlot):
    def __init__(self, parent=None) -> None:
        super().__init__(parent, axisItems={"bottom": pg.DateAxisItem()})
        self._points: tuple[tuple[float, float], ...] = ()
        self._marked: tuple[int, ...] = ()
        self._curve = None
        self._mark_item = None

    @property
    def point_count(self) -> int:
        return len(self._points)

    @property
    def marked_count(self) -> int:
        return len(self._marked)

    def set_series(self, points: Sequence[tuple[datetime, Number]], marked: Sequence[int] = ()) -> None:
        # validate before mutating state; geometry only
        geometry = tuple((_geometry(ts.timestamp()), _geometry(v)) for ts, v in points)
        indices = tuple(dict.fromkeys(marked))
        if any(not isinstance(i, int) or not 0 <= i < len(geometry) for i in indices):
            raise ValueError("marked index out of range")
        self._points, self._marked = geometry, indices
        self._draw()

    def _draw(self) -> None:
        for item in (self._curve, self._mark_item):
            if item is not None:
                self.removeItem(item)
        self._curve = self._mark_item = None
        if not self._points:
            return
        t = self._theme
        xs, ys = zip(*self._points)
        self._curve = self.plot(list(xs), list(ys), pen=pg.mkPen(t.sel, width=2),
                                symbol="s", symbolSize=5, symbolBrush=t.sel, symbolPen=t.dark)
        if self._marked:
            self._mark_item = pg.ScatterPlotItem(x=[xs[i] for i in self._marked], y=[ys[i] for i in self._marked],
                                                 symbol="o", size=12, brush=None, pen=pg.mkPen(t.warn, width=2))
            self.addItem(self._mark_item)

    def _restyle(self) -> None:
        if getattr(self, "_points", None):
            self._draw()
