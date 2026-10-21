"""Value types shared by every domain contract. No Qt, no boto3."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Context, Decimal, Inexact, InvalidOperation, Overflow
from enum import Enum
from typing import Generic, TypeVar

T = TypeVar("T")


class Status(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    DENIED = "denied"
    UNREACHABLE = "unreachable"
    STALE = "stale"
    NOT_CONNECTED = "not_connected"
    ERROR = "error"


def require_decimal(name: str, value: object, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal, got {value}")


@dataclass(frozen=True)
class AccountRef:
    account_id: str
    nickname: str
    color: str
    profile: str


@dataclass(frozen=True)
class Coverage:
    regions_requested: tuple[str, ...] = ()
    regions_covered: tuple[str, ...] = ()
    denials: int = 0

    @property
    def complete(self) -> bool:
        return set(self.regions_requested) <= set(self.regions_covered) and self.denials == 0


@dataclass(frozen=True)
class Freshness:
    observed_at: datetime | None
    source: str


@dataclass(frozen=True)
class TimeRange:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("TimeRange requires timezone-aware datetimes")
        if self.start > self.end:
            raise ValueError("TimeRange start must not be after end")


@dataclass(frozen=True)
class Scope:
    accounts: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()
    time_range: TimeRange | None = None


@dataclass(frozen=True)
class Page(Generic[T]):
    items: tuple[T, ...]
    next_cursor: str | None = None
    total: int | None = None


@dataclass(frozen=True)
class Result(Generic[T]):
    status: Status
    data: T | None = None
    coverage: Coverage | None = None
    freshness: Freshness | None = None
    message: str | None = None
    is_demo: bool = False

    @classmethod
    def ok(cls, data: T, *, coverage: Coverage | None = None, freshness: Freshness | None = None,
           message: str | None = None, is_demo: bool = False) -> "Result[T]":
        return cls(Status.OK, data, coverage, freshness, message, is_demo)

    @classmethod
    def not_connected(cls, message: str, *, freshness: Freshness | None = None, is_demo: bool = False) -> "Result[T]":
        return cls(Status.NOT_CONNECTED, None, None, freshness, message, is_demo)

    @classmethod
    def error(cls, message: str, *, freshness: Freshness | None = None, is_demo: bool = False) -> "Result[T]":
        return cls(Status.ERROR, None, None, freshness, message, is_demo)


# Money is summed in a context wide enough never to round; if a sum ever would round, it raises instead.
_EXACT = Context(prec=1000, traps=[Inexact, InvalidOperation, Overflow])


def exact_add(a: Decimal, b: Decimal) -> Decimal:
    require_decimal("a", a)
    require_decimal("b", b)
    return _EXACT.add(a, b)


def exact_sum(values: Iterable[Decimal]) -> Decimal:
    total = Decimal(0)
    for v in values:
        total = exact_add(total, v)
    return total
