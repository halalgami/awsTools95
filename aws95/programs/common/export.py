"""CSV / Markdown export of grid rows. Values stay exact; text cells are defanged against formula injection."""
from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from aws95.kit.widgets.datagrid import Column

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str, ensure_ascii=False, sort_keys=True)
    if isinstance(value, Decimal):
        return format(value, "f")      # exact digits, never scientific notation
    return str(value)


def _csv_cell(value: Any) -> str:
    text = _text(value)
    if isinstance(value, str) and text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def _is_number(cell: str) -> bool:
    """A cell that reads exactly as a finite Decimal: once in CSV text a number cannot be told from text."""
    if cell != cell.strip():
        return False
    try:
        return Decimal(cell).is_finite()
    except InvalidOperation:
        return False


def defang_csv_text(text: str) -> str:
    """Defang provider CSV with the export rule (ruling M3-R24): text cells starting with = + - @, tab or CR get
    a leading ' and number cells stay exactly as written. Line endings follow the source (CRLF if it used CRLF);
    a missing final newline stays missing."""
    if not text:
        return text
    terminator = "\r\n" if "\r\n" in text else "\n"
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator=terminator)
    for row in csv.reader(io.StringIO(text, newline="")):
        writer.writerow(["'" + c if c.startswith(_FORMULA_PREFIXES) and not _is_number(c) else c for c in row])
    out = buf.getvalue()
    if not text.endswith(("\n", "\r")) and out.endswith(terminator):
        out = out[: -len(terminator)]
    return out


def rows_to_csv(columns: Sequence[Column], rows: Iterable[Mapping[str, Any]], notice: str | None = None) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    if notice:
        writer.writerow([_csv_cell(notice)])          # provenance first: a PARTIAL export must say so
    writer.writerow([c.title for c in columns])
    for row in rows:
        writer.writerow([_csv_cell(row.get(c.key)) for c in columns])
    return buf.getvalue()


def _md_cell(value: Any) -> str:
    return _text(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def rows_to_markdown(title: str, columns: Sequence[Column], rows: Iterable[Mapping[str, Any]],
                     preamble: Sequence[str] = ()) -> str:
    """`preamble` lines (plain text, escaped like cells) become a bullet list under the title."""
    head = [f"- {_md_cell(line)}" for line in preamble]
    lines = [f"# {title}", "", *head, *([""] if head else []), "| " + " | ".join(_md_cell(c.title) for c in columns) + " |",
             "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join(_md_cell(row.get(c.key)) for c in columns) + " |" for row in rows]
    return "\n".join(lines) + "\n"


def write_text(path: str | Path, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8", newline="")
