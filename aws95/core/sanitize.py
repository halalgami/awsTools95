"""Untrusted-text cleaning for model output shown in the UI."""
from __future__ import annotations

import re

# Stripped: bidi embedding/override/isolate controls (U+202A-202E, U+2066-2069), zero-width and directional marks
# (U+200B-200F), the Arabic letter mark (U+061C), the word joiner (U+2060), the BOM (U+FEFF), and C0/C1 controls
# except \n and \t. Written as escapes on purpose: raw bidi characters in source are a Trojan Source risk.
# Ordinary Arabic letters are not in these ranges and survive.
_UNSAFE = re.compile(
    "[\u200b-\u200f\u061c\u2060\ufeff\u202a-\u202e\u2066-\u2069\x00-\x08\x0b-\x1f\x7f-\x9f]")


def clean_model_text(text: str) -> str:
    return _UNSAFE.sub("", text)
