from __future__ import annotations

import json
from typing import Any

from PySide6.QtWidgets import QPlainTextEdit, QWidget

from aws95.kit.style import scaled_fixed_font


def _safe_repr(obj: Any) -> str:
    try:
        return repr(obj)
    except Exception:
        return f"<unrepresentable object: {type(obj).__name__}>"


def _dump(obj: Any) -> str:
    """Pretty JSON for any object; never raises."""
    kwargs: dict[str, Any] = {"indent": 2, "default": str, "ensure_ascii": False}
    try:
        return json.dumps(obj, sort_keys=True, **kwargs)
    except Exception:  # e.g. mixed-type keys cannot be sorted; retry unsorted
        pass
    try:
        return json.dumps(obj, **kwargs)
    except Exception:  # circular/too deep, or a raising __str__
        return _safe_repr(obj)


class JsonView(QPlainTextEdit):
    MAX_CHARS = 2_000_000

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setFont(scaled_fixed_font())

    def set_data(self, obj: Any) -> None:
        text = _dump(obj)
        if len(text) > self.MAX_CHARS:
            text = text[: self.MAX_CHARS] + f"\n… truncated ({len(text):,} characters total)"
        self.setPlainText(text)
