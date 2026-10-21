from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SearchHit:
    kind: str               # "resource" | "principal" | "finding"
    title: str
    subtitle: str
    target_program: str     # program id to open, e.g. "resources"
    payload: Mapping[str, Any] = field(default_factory=dict)
