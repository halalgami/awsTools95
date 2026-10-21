# Upstream: labsnap/labsnap/collect/probe.py @ 1b1d437 — adapted for aws95; owned here from now on.
"""Denied, absent or unreachable (labsnap's completeness model). botocore is imported only while an error is being
classified, so importing the engine never loads it (spec §2 boot cost)."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

DENIED_CODES = frozenset({"AccessDenied", "AccessDeniedException", "UnauthorizedOperation", "AuthorizationError"})
# "This thing is genuinely not configured": an absence, not a permission gap.
ABSENT_CODES = frozenset({"NoSuchPublicAccessBlockConfiguration", "NoSuchLifecycleConfiguration",
                          "ServerSideEncryptionConfigurationNotFoundError", "NoSuchBucketPolicy", "NoSuchEntity",
                          "ResourceNotFoundException", "RepositoryPolicyNotFoundException",
                          "LifecyclePolicyNotFoundException"})


def error_code(exc: BaseException) -> str | None:
    """The AWS error code of a botocore ClientError; None for anything else (which the caller must re-raise)."""
    from botocore.exceptions import ClientError
    if isinstance(exc, ClientError):
        return str(exc.response.get("Error", {}).get("Code", ""))
    return None


def is_unreachable(exc: BaseException) -> bool:
    """A connect/read timeout or dropped connection: the endpoint itself could not be reached."""
    from botocore.exceptions import (ConnectionClosedError, ConnectTimeoutError, EndpointConnectionError,
                                     ReadTimeoutError)
    return isinstance(exc, (EndpointConnectionError, ConnectTimeoutError, ReadTimeoutError, ConnectionClosedError))


def probe(fn: Callable[..., Any], *args: Any, **kw: Any) -> tuple[Any, bool]:
    """Return (value, unreadable). Denied and unreachable both mean "could not read"; an absence is a real answer.
    `collector_runs.denials` sums `unreadable`, so it counts unreadable attributes, not only AccessDenied."""
    value, state = probe_detailed(fn, *args, **kw)
    return value, state != "ok"


def probe_detailed(fn: Callable[..., Any], *args: Any, **kw: Any) -> tuple[Any, str]:
    """Return (value, "ok" | "denied" | "unreachable"). An unrecognised error re-raises: we do not guess."""
    try:
        return fn(*args, **kw), "ok"
    except Exception as exc:  # noqa: BLE001 - classified below; anything unrecognised is re-raised
        if is_unreachable(exc):
            return None, "unreachable"
        code = error_code(exc)
        if code is None:
            raise
        if code in ABSENT_CODES:
            return None, "ok"
        if code in DENIED_CODES:
            return None, "denied"
        raise
