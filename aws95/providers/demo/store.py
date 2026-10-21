"""Fixture loading and scope helpers shared by the demo providers."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from aws95.contracts.common import Freshness, Scope

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
DEMO_FRESHNESS = Freshness(observed_at=None, source="demo fixture")


@lru_cache(maxsize=None)
def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def now() -> datetime:
    """Demo 'now'; tests monkeypatch this to pin rebased activity times."""
    return datetime.now(timezone.utc)


def in_scope(account: str, region: str, scope: Scope) -> bool:
    if scope.accounts and account not in scope.accounts:
        return False
    if scope.regions and region not in scope.regions and region != "global":
        return False
    return True
