"""Program status bar: counts │ coverage │ freshness (spec §7 skeleton)."""
from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from aws95.contracts.common import Result, Status
from aws95.kit.widgets.bevel import BevelPanel


def coverage_text(result: Result) -> str:
    parts: list[str] = []
    cov = result.coverage
    if cov is not None and cov.regions_requested:
        requested = set(cov.regions_requested)
        covered = len(set(cov.regions_covered) & requested)
        parts.append(f"{covered}/{len(requested)} regions")
    if cov is not None and cov.denials:
        parts.append(f"{cov.denials} denied")
    if result.status in (Status.PARTIAL, Status.DENIED, Status.UNREACHABLE, Status.STALE):
        parts.append(result.status.value)
    return " · ".join(parts)


def _ago(seconds: float) -> str:
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)                # tests pin this for goldens


def freshness_text(result: Result, now: datetime) -> str:
    if result.status is Status.NOT_CONNECTED:
        return "Not connected"
    fr = result.freshness
    if fr is None:
        return ""
    if fr.observed_at is None:
        return fr.source
    ago = _ago(max(0.0, (now - fr.observed_at).total_seconds()))
    return f"Snapshot {ago}" if fr.source == "snapshot" else f"Synced {ago}"


def _pane(parent: QWidget) -> tuple[BevelPanel, QLabel]:
    pane = BevelPanel("sunken", parent)
    label = QLabel("", pane)
    label.setTextFormat(Qt.TextFormat.PlainText)
    # Long text is clipped like a Win95 status bar (full text in the tooltip); it never widens the window.
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    lay = QHBoxLayout(pane)
    lay.setContentsMargins(4, 1, 4, 1)
    lay.addWidget(label)
    return pane, label


class StatusStrip(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        counts, self.counts_label = _pane(self)
        coverage, self.coverage_label = _pane(self)
        fresh, self.freshness_label = _pane(self)
        lay.addWidget(counts, 2)
        lay.addWidget(coverage, 2)
        lay.addWidget(fresh, 1)
        self.last_result: Result | None = None

    def set_counts(self, text: str) -> None:
        self.counts_label.setText(text)
        self.counts_label.setToolTip(text)

    def set_result(self, result: Result, now: datetime | None = None) -> None:
        self.last_result = result
        when = now or _utcnow()
        self.coverage_label.setText(coverage_text(result))
        self.freshness_label.setText(freshness_text(result, when))
        fr = result.freshness
        self.freshness_label.setToolTip(fr.source if fr is not None else "")

    def clear(self) -> None:
        for label in (self.counts_label, self.coverage_label, self.freshness_label):
            label.setText("")
