"""Cooperative cancellation. Pure Python, no Qt: providers and engines may import it."""
from __future__ import annotations

import threading


class CancelledError(Exception):
    """Raised by long-running work that notices its token was cancelled."""


class CancelToken:
    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


def raise_if_cancelled(token: CancelToken | None) -> None:
    if token is not None and token.cancelled:
        raise CancelledError()


class AnyToken:
    """A read-only view over several tokens: cancelled as soon as any of them is (None entries are ignored).
    Lets the user's Stop and the window's or app's cancel both reach one long run."""
    __slots__ = ("_tokens",)

    def __init__(self, *tokens: "CancelToken | AnyToken | None") -> None:
        self._tokens = tuple(t for t in tokens if t is not None)

    @property
    def cancelled(self) -> bool:
        return any(t.cancelled for t in self._tokens)
