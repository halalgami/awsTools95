from __future__ import annotations

import json
import math

from dataclasses import dataclass
from typing import Literal

# Payload note: cost aggregates (cost_mtd_usd, cost_last_month_usd) are account-scoped, not region-filtered.
InsightKind = Literal["insights", "oddities", "improvements", "advice"]


@dataclass(frozen=True)
class InsightSection:
    title: str
    bullets: tuple[str, ...]


@dataclass(frozen=True)
class ModelChoice:
    model_id: str
    name: str


@dataclass(frozen=True)
class AccountInsight:
    """One account's answer (ai-insights spec §6). `label` is the anonymous "Account N" that was sent."""
    label: str
    model: str
    sections: tuple[InsightSection, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None


@dataclass(frozen=True)
class InsightReport:
    kind: InsightKind
    model: str
    sections: tuple[InsightSection, ...]
    accounts: tuple[AccountInsight, ...] = ()


def estimate_tokens(entry: dict) -> int:
    """About one token per four characters of the JSON sent; labelled "about" wherever it is shown."""
    return max(1, math.ceil(len(json.dumps(entry, sort_keys=True)) / 4))


# README "What costs money" (ai-insights spec §8): one constant, never typed by hand.
INSIGHTS_COST_NOTE = "billed per token by the model you choose"
