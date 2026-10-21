"""AI Insights payload (ai-insights spec §4): aggregates plus AWS service and rule names, never an id. Qt-free and
AWS-free; demo and live share it. `scrub_check` is defence in depth: a payload that looks like it carries an id is
refused before anyone can review or send it."""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from aws95.contracts.insights import estimate_tokens  # noqa: F401 - shared with the window

TOP_SERVICES = 10
_ACCOUNT_ID = re.compile(r"(?<!\d)\d{12}(?!\d)")
_IPV4 = re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")
_TOKEN = re.compile(r"[A-Za-z0-9_-]+")
_IPV6 = re.compile(r"(?i)(?<![0-9a-f:])(?:[0-9a-f]{1,4}:){2,7}[0-9a-f]{0,4}(?![0-9a-f:])|::")


def build_payload(kind: str, entries: list[dict]) -> dict:
    return {"analysis": kind,
            "accounts": [{"label": f"Account {i}", **e} for i, e in enumerate(entries, 1)]}


def _strings(value: Any, path: str):
    if isinstance(value, dict):
        for k, v in value.items():
            yield f"{path}.{k}", str(k)
            yield from _strings(v, f"{path}.{k}")
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            yield from _strings(v, f"{path}[{i}]")
    elif isinstance(value, str):
        yield path, value


def scrub_check(payload: dict, forbidden: Iterable[str]) -> str | None:
    words = {w for w in forbidden if w}
    for path, text in _strings(payload, "payload"):
        lowered = text.lower()
        # Profile names match whole tokens only: "prod" must not refuse "product-analytics" (review 2026-10-21).
        tokens = set(_TOKEN.findall(text))
        if (_ACCOUNT_ID.search(text) or "arn:" in lowered or _IPV4.search(text) or _IPV6.search(text)
                or words & tokens):
            return f"{path} looks like it holds an identifier, so the preview was refused. Nothing was sent."
    return None

