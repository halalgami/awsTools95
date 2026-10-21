from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ReportFormat = Literal["md", "csv"]


@dataclass(frozen=True)
class ReportType:
    id: str
    title: str
    formats: tuple[ReportFormat, ...]


@dataclass(frozen=True)
class RenderedReport:
    title: str
    markdown: str
    csv: str | None = None
