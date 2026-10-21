"""Tiny filter language for DataGrid. See the module docstring of Task 8 in the M1 plan."""
from __future__ import annotations

import operator
import re
import shlex
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fnmatch import translate
from typing import Any, Callable

Predicate = Callable[[Mapping[str, Any], str | None], bool]

_TERM = re.compile(r"^(?P<neg>-)?(?:(?P<field>[A-Za-z_][\w.]*)(?P<op>>=|<=|:|~|>|<))?(?P<value>.*)$", re.S)
_NUMERIC_OPS = {">": operator.gt, "<": operator.lt, ">=": operator.ge, "<=": operator.le}


class FilterError(ValueError):
    pass


@dataclass(frozen=True)
class Term:
    field: str | None
    op: str
    value: str
    negate: bool


def haystack(row: Mapping[str, Any]) -> str:
    return " ".join(str(v) for v in row.values() if v is not None).lower()


def _as_number(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        n = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except InvalidOperation:
        return None
    return n if n.is_finite() else None


def _compile(term: Term) -> Predicate:
    value = term.value
    if term.field is None:
        needle = value.lower()
        pred: Predicate = lambda row, hay: needle in (hay if hay is not None else haystack(row))
    elif term.op in _NUMERIC_OPS:
        try:
            target = Decimal(value)
            if not target.is_finite():
                raise InvalidOperation
        except InvalidOperation:
            raise FilterError(f"'{value}' is not a number in {term.field}{term.op}{value}") from None
        cmp, key = _NUMERIC_OPS[term.op], term.field

        def pred(row, hay):  # type: ignore[misc]
            n = _as_number(row.get(key))
            return n is not None and cmp(n, target)
    else:
        key, wanted = term.field, value.lower()
        if term.op == "~":
            pred = lambda row, hay: (v := row.get(key)) is not None and wanted in str(v).lower()
        elif any(ch in wanted for ch in "*?["):
            try:
                glob = re.compile(translate(wanted)).match
            except re.error:
                raise FilterError(f"invalid pattern '{value}' in {term.field}{term.op}{value}") from None
            pred = lambda row, hay: (v := row.get(key)) is not None and glob(str(v).lower()) is not None
        else:
            pred = lambda row, hay: (v := row.get(key)) is not None and str(v).lower() == wanted
    if term.negate:
        inner = pred
        return lambda row, hay: not inner(row, hay)
    return pred


class Query:
    def __init__(self, terms: tuple[Term, ...]) -> None:
        self.terms = terms
        self._preds = tuple(_compile(t) for t in terms)
        self.needs_haystack = any(t.field is None for t in terms)

    @property
    def is_empty(self) -> bool:
        return not self.terms

    def matches(self, row: Mapping[str, Any], haystack_text: str | None = None) -> bool:
        if self.needs_haystack and haystack_text is None:
            haystack_text = haystack(row)
        return all(p(row, haystack_text) for p in self._preds)


def parse(text: str, fields: Iterable[str] | None = None) -> Query:
    known = {f.lower() for f in fields} if fields is not None else None
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError as exc:
        raise FilterError(f"could not read filter: {exc}") from None
    terms: list[Term] = []
    for token in tokens:
        m = _TERM.match(token)
        neg = bool(m and m.group("neg"))
        field = m.group("field") if m else None
        op = m.group("op") if m else None
        value = m.group("value") if m else token
        if field is not None and known is not None and field.lower() not in known:
            field, op, value = None, None, token[1:] if neg else token
        if field is None:
            word = value if value else token
            if not word:
                continue
            terms.append(Term(None, "contains", word, neg))
            continue
        if op in _NUMERIC_OPS and value == "":
            raise FilterError(f"missing number after {field}{op}")
        terms.append(Term(field.lower(), op, value, neg))
    return Query(tuple(terms))
