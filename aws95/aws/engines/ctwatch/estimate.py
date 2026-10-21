"""Load-time estimates (first-drop spec §5.1): about 50 events per call at about 2 calls a second."""
from __future__ import annotations

import math

EVENTS_PER_CALL = 50
CALLS_PER_SECOND = 2
WINDOW_HOURS = 4


def calls_needed(events_last_hour: int, days: int, targeted_passes: int) -> int:
    """Bulk: every 4-hour window costs at least one call, more when the sampled rate fills pages.
    Targeted passes: at least one call per day each."""
    windows = days * 24 // WINDOW_HOURS
    per_window = max(1, math.ceil(events_last_hour * WINDOW_HOURS / EVENTS_PER_CALL))
    return windows * per_window + targeted_passes * days


def minutes(calls: int) -> int:
    return max(1, math.ceil(calls / CALLS_PER_SECOND / 60))
