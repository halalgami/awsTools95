"""Default provider set: every call answers NOT_CONNECTED honestly."""
from __future__ import annotations

from typing import Any, Callable

from aws95.contracts.common import Result
from aws95.providers.base import DOMAINS, ProviderSet


class NotConnectedProvider:
    def __init__(self, protocol: type, message: str, *, is_demo: bool = False) -> None:
        self._protocol = protocol
        self._message = message
        self._is_demo = is_demo

    def __getattr__(self, name: str) -> Callable[..., Result[Any]]:
        if name.startswith("_") or not callable(getattr(self._protocol, name, None)):
            raise AttributeError(f"{self._protocol.__name__} has no method {name!r}")

        def _answer(*_args: Any, **_kwargs: Any) -> Result[Any]:
            return Result.not_connected(self._message, is_demo=self._is_demo)

        return _answer


def empty_provider_set() -> ProviderSet:
    providers = {attr: NotConnectedProvider(proto, f"{label} is offline (--offline).")
                 for attr, (proto, label) in DOMAINS.items()}
    return ProviderSet(**providers, is_demo=False)
